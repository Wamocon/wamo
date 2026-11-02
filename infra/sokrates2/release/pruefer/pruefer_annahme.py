"""Annahme-Dienst der Prüfer-Sandbox (Konzept docs/selbstueberarbeitung.md D.3, Schritt 1.2c). Läuft als root, je Verbindung ein Prozess.

Der Release-Dienst (Benutzer wamo-release, ohne Root) kann die Sandbox nicht selbst starten. Dieser Dienst ist der Weg dorthin, ohne den Notanker zu
erweitern und ohne dass root jemals einen Ordner liest, den ein Dienst ohne Root verändern kann: Der Client schickt beide Stände als Datenstrom
(pruefer_archiv.py), der Dienst schreibt sie selbst in einen frischen Ordner (O_NOFOLLOW über Verzeichnis-Deskriptoren, O_EXCL), startet den Läufer
wamo-pruefer-lauf.sh und liefert dessen Ergebnis zurück. Nichts davon ist ein Befehl des Clients: Der Client wählt nur die Runde (1 bis 3).

Aktivierung durch systemd (wamo-pruefer-annahme.socket, Accept=yes): Der Socket gehört wamo-release (Modus 0600); zusätzlich prüft der Dienst per
SO_PEERCRED, dass die Gegenstelle root oder wamo-release ist. Eingabe und Ausgabe sind die Verbindung.

Anfrage:  WAMO-PRUEFEN 1 <runde>\\n  und danach das Archiv, danach Ende des Sendens (shutdown SHUT_WR).
Antwort:  eine JSON-Zeile  {"ok": true, "ergebnis": {...Läufer...}, "exit": 0|1, "annahme": {...}}  oder  {"ok": false, "grund": "...", "detail": "..."}.
Gründe: benutzer, kopf, archiv_ungueltig, annahme_fehler, belegt, zeitlimit, lauf_ohne_ergebnis.
Im Journal steht je Anfrage eine Zeile (Benutzer, Runde, Zahl der Dateien und Bytes, Ergebnis), nie ein Inhalt.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import migration_probe as MP  # noqa: E402
import pruefer_archiv as A  # noqa: E402

try:
    import pwd
except ImportError:  # pragma: no cover  (Windows: nur die Tests mit Attrappen)
    pwd = None

KOPF_RE = re.compile(rb"WAMO-PRUEFEN 1 ([123])\n")
ERLAUBTE_NAMEN = ("wamo-release",)
LESE_ZEIT_S = 60.0
ERGEBNIS_MAX = 2 * 1024 * 1024


@dataclass(frozen=True)
class Konfig:
    lauf_sh: str = "/opt/wamo-pruefer/app/wamo-pruefer-lauf.sh"
    annahme: str = "/srv/wamo-pruefer/annahme"
    hochladen_max_s: float = 900.0
    migprobe: bool = True        # Migrationen frisch auf einem Wegwerf-Postgres anwenden, wenn sie sich geändert haben (Schritt 2.5b)
    probe: object = None         # Ersatz für migprobe_anfrage (Tests)
    migprobe_socket: str = "/run/wamo-migprobe/probe.sock"
    lauf_max_s: float = 6300.0   # etwas mehr als das Zeitlimit des Läufers (ZEIT_S 5400): er bricht zuerst ab


def migprobe_anfrage(neu: str, wurzel: str, sockpfad: str = "/run/wamo-migprobe/probe.sock", zeit_s: float = 480.0) -> dict:
    """Bittet den Dienst der Migrationsprobe (eigener Prozess mit Docker-Zugang, Annahme hat keinen) um die Probe für den Arbeitsordner."""
    name = os.path.basename(wurzel.rstrip("/"))
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.settimeout(zeit_s)
        s.connect(sockpfad)
        s.sendall(b"MIGPROBE " + name.encode("ascii", "replace") + b"\n")
        roh = b""
        while not roh.endswith(b"\n") and len(roh) < 512:
            teil = s.recv(512)
            if not teil:
                break
            roh += teil
        antwort = json.loads(roh.decode("ascii", "replace"))
        wert = "ok" if antwort.get("wert") == "ok" else "fehler"
        code = re.sub(r"[^a-z0-9_:]", "", str(antwort.get("fehler", ""))[:80])
        return {"wert": wert, "fehler": "" if wert == "ok" else (code if code.startswith("frisch_") else "frisch_" + code)}
    except (OSError, ValueError):
        return {"wert": "fehler", "fehler": "frisch_dienst_nicht_erreichbar"}   # fail closed: ohne Probe gilt die Migration nicht als geprüft
    finally:
        s.close()


def peer_uid(conn: socket.socket) -> int | None:
    try:
        _pid, uid, _gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
        return uid
    except (OSError, AttributeError, struct.error):
        return None


def erlaubte_uids() -> frozenset[int]:
    """root und die Benutzer aus ERLAUBTE_NAMEN; die Namen werden bei jeder Verbindung frisch aufgelöst, ein fehlender passt auf niemanden."""
    uids = {0}
    for n in ERLAUBTE_NAMEN:
        try:
            uids.add(pwd.getpwnam(n).pw_uid)
        except (KeyError, AttributeError):
            pass
    return frozenset(uids)


def _antwort(raus, obj: dict) -> None:
    raus.write(json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n")
    raus.flush()


def _log(text: str) -> None:
    print(f"annahme {text}", file=sys.stderr, flush=True)


def _kurz(text: bytes) -> str:
    t = text.decode("utf-8", "replace")
    return "".join(c if c.isprintable() else " " for c in t)[-240:].strip()


def bedienen(rein, raus, peer: int | None, erlaubt: frozenset[int], cfg: Konfig = Konfig(), uhr=time.monotonic) -> int:
    """Eine Anfrage. Rückgabe: 0, wenn ein Ergebnis geliefert wurde, sonst 1."""
    if peer is None or peer not in erlaubt:
        _log(f"uid={peer} abgewiesen benutzer")
        _antwort(raus, {"ok": False, "grund": "benutzer"})
        return 1
    m = KOPF_RE.fullmatch(rein.readline(40))
    if m is None:
        _log(f"uid={peer} abgewiesen kopf")
        _antwort(raus, {"ok": False, "grund": "kopf"})
        return 1
    runde = int(m[1])
    os.makedirs(cfg.annahme, mode=0o700, exist_ok=True)
    wurzel = tempfile.mkdtemp(prefix="a.", dir=cfg.annahme)
    try:
        for name in A.BAEUME:
            os.mkdir(os.path.join(wurzel, name), 0o755)
        ziel = A.DateiZiel(wurzel)
        try:
            statistik = A.lies_archiv(rein, ziel, frist=uhr() + cfg.hochladen_max_s, uhr=uhr)
        except A.ArchivFehler as e:
            _log(f"uid={peer} runde={runde} archiv_ungueltig {e}")
            _antwort(raus, {"ok": False, "grund": "archiv_ungueltig", "detail": str(e)})
            return 1
        except OSError as e:
            _log(f"uid={peer} runde={runde} annahme_fehler {type(e).__name__}")
            _antwort(raus, {"ok": False, "grund": "annahme_fehler", "detail": type(e).__name__})
            return 1
        finally:
            ziel.schliessen()
        ausgabe = os.path.join(wurzel, "ergebnis.json")
        try:
            p = subprocess.run([cfg.lauf_sh, "lauf", "--basis", os.path.join(wurzel, "basis"), "--neu", os.path.join(wurzel, "neu"),
                                "--runde", str(runde), "--ausgabe", ausgabe], stdin=subprocess.DEVNULL, capture_output=True,
                               timeout=cfg.lauf_max_s, env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C.UTF-8"}, cwd="/")
        except subprocess.TimeoutExpired:
            _log(f"uid={peer} runde={runde} zeitlimit")
            _antwort(raus, {"ok": False, "grund": "zeitlimit"})
            return 1
        if p.returncode == 75:
            _log(f"uid={peer} runde={runde} belegt")
            _antwort(raus, {"ok": False, "grund": "belegt"})
            return 1
        ergebnis = None
        try:
            if os.path.isfile(ausgabe) and not os.path.islink(ausgabe) and os.path.getsize(ausgabe) <= ERGEBNIS_MAX:
                with open(ausgabe, encoding="utf-8") as f:
                    ergebnis = json.load(f)
        except (OSError, ValueError):
            ergebnis = None
        if not isinstance(ergebnis, dict) or "pruefstand" not in ergebnis:
            _log(f"uid={peer} runde={runde} lauf_ohne_ergebnis exit={p.returncode}")
            _antwort(raus, {"ok": False, "grund": "lauf_ohne_ergebnis", "exit": p.returncode, "detail": _kurz(p.stderr)})
            return 1
        exit_code = p.returncode
        if cfg.migprobe and MP.migrationen_geaendert(os.path.join(wurzel, "basis"), os.path.join(wurzel, "neu")):
            probe = cfg.probe or migprobe_anfrage
            try:
                pr = probe(os.path.join(wurzel, "neu"), wurzel)
            except Exception as e:  # noqa: BLE001  eine Panne der Probe ist ein Fehler der Stufe, nie ein Absturz
                pr = {"wert": "fehler", "fehler": "frisch_panne_" + type(e).__name__.lower()[:20], "phasen": {}}
            detail = ergebnis.setdefault("details", {}).setdefault("migrations_lint", {"treffer_anzahl": 0, "fehler": ""})
            detail["frisch"] = pr.get("wert")
            if pr.get("wert") != "ok":
                ergebnis["pruefstand"]["migrations_lint"] = "fehler"
                detail["fehler"] = detail.get("fehler") or str(pr.get("fehler", "frisch"))[:80]
                exit_code = exit_code or 1
        _log(f"uid={peer} runde={runde} dateien={statistik['dateien']} bytes={statistik['bytes']} exit={exit_code}")
        _antwort(raus, {"ok": True, "ergebnis": ergebnis, "exit": exit_code, "annahme": statistik})
        return 0
    finally:
        shutil.rmtree(wurzel, ignore_errors=True)   # Verknüpfungen im Baum werden gelöscht, nie verfolgt


def main() -> int:
    os.umask(0o077)
    conn = socket.socket(fileno=os.dup(0))
    conn.settimeout(LESE_ZEIT_S)
    peer = peer_uid(conn)
    rein, raus = conn.makefile("rb"), conn.makefile("wb")
    try:
        bedienen(rein, raus, peer, erlaubte_uids())
        return 0   # beantwortet ist erledigt, auch eine Abweisung: sonst bliebe jede abgewiesene Anfrage als "failed" in systemd stehen
    except (OSError, ValueError) as e:   # Gegenstelle weg oder Zeitüberschreitung beim Lesen: nichts mehr zu antworten
        _log(f"abgebrochen {type(e).__name__}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
