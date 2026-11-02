"""Läufer der Prüfstrecke in der Prüfer-Sandbox (Konzept docs/selbstueberarbeitung.md D.3, Schritt 1.2b).

Läuft im Prozess der Sandbox (Benutzer wamo-pruefer, ohne Geheimnisse, Netz nur über die zwei Proxys auf Loopback). Bekommt zwei
Ordner (Basis und neuer Stand), vergleicht sie, führt die Stufen nacheinander aus und schreibt den Prüfstand als JSON:

    {"runde": 1, "pruefstand": {"bauen": "ok", "tests": "fehler", ...}, "details": {"tests": {"treffer_anzahl": 0, "fehler": "", "ende": "..."}},
     "sonder": false, "neue_pakete": [["rich", "13.0.0"]]}

Stufen mit Werkzeugen (bauen, tests, typen_lint) laufen über einen austauschbaren Ausführer; die Stufen ohne Werkzeug (secret_scan,
fremdverweis, wirkungsflaechen, abhaengigkeiten) sind pruefer_scans. Die Zeilen schutzschicht (Anker) und version_bytegleich (Git) liefert
dieser Läufer nie, und migrations_lint (Schritt 2.5) nur als "fehler", sobald eine Migration geändert wurde (fail closed, bis es das Lint gibt).

Der Läufer wirft nie wegen des Inhalts des geprüften Codes: Jede Panne einer Stufe ist ein "fehler" mit kurzem Grund. Die Ausgabe der
Werkzeuge wird auf druckbare Zeichen und wenige Zeilen am Ende gekürzt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import pruefer_scans as S  # noqa: E402

DATEI_MAX = 2 * 1024 * 1024
GESAMT_MAX = 200 * 1024 * 1024
DATEIEN_MAX = 20000
ENDE_ZEICHEN = 1500
AUSLASSEN = frozenset({".git", "node_modules", ".venv", "__pycache__", ".next", "dist", ".pytest_cache", ".ruff_cache"})
PLATZHALTER_URL = "https://platzhalter.invalid"
PROXY_PAKETE = "http://127.0.0.1:18080"
RUNDEN = {1: ("bauen", "tests", "typen_lint", "secret_scan", "fremdverweis", "abhaengigkeiten", "migrations_lint", "wirkungsflaechen"),
          2: ("bauen", "tests", "typen_lint", "secret_scan", "fremdverweis", "abhaengigkeiten", "migrations_lint", "wirkungsflaechen"),
          3: ("bauen", "tests", "secret_scan", "fremdverweis")}
PYTHON_PAKETE = ("voice-core", "host", "geraete")
NODE_PAKETE = ("ui", "desktop")


@dataclass(frozen=True)
class Befehl:
    ordner: str                       # relativ zur Wurzel des neuen Stands
    argv: tuple[str, ...]             # nie eine Shell-Zeile
    zeit_s: int = 900
    env: tuple[tuple[str, str], ...] = ()


def plan_bauen() -> tuple[Befehl, ...]:
    bau_env = (("NEXT_PUBLIC_SUPABASE_URL", PLATZHALTER_URL), ("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", "platzhalter"),
               ("NEXT_PUBLIC_WAMO_KERN_BUERO", PLATZHALTER_URL), ("NEXT_TELEMETRY_DISABLED", "1"))
    return (*(Befehl(p, ("uv", "sync", "--frozen")) for p in PYTHON_PAKETE),
            *(Befehl(p, ("npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund")) for p in NODE_PAKETE),
            Befehl("ui", ("npm", "run", "build"), 1500, bau_env))


def plan_tests() -> tuple[Befehl, ...]:
    return (*(Befehl(p, ("uv", "run", "--frozen", "python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--ignore-glob=test_integration_*"), 1800)
              for p in PYTHON_PAKETE),
            *(Befehl(p, ("npm", "test"), 900) for p in NODE_PAKETE))


def plan_typen_lint() -> tuple[Befehl, ...]:
    return (Befehl("ui", ("npx", "--no-install", "tsc", "--noEmit"), 900), Befehl("ui", ("npm", "run", "lint"), 900))


PLAENE: dict[str, Callable[[], tuple[Befehl, ...]]] = {"bauen": plan_bauen, "tests": plan_tests, "typen_lint": plan_typen_lint}
AUDITS = (Befehl("ui", ("npm", "audit", "--omit=dev", "--audit-level=high"), 300), Befehl("voice-core", ("uv", "run", "--frozen", "pip-audit"), 300))


# ------------------------------------------------------------------------------------------------ Ausführung
@dataclass
class Ausgang:
    code: int | None           # None: Zeitlimit
    ende: str = ""


def kuerzen(roh: bytes | str, n: int = ENDE_ZEICHEN) -> str:
    """Die letzten Zeichen, nur druckbar, ohne Steuerzeichen und ohne ANSI-Farben."""
    text = roh.decode("utf-8", errors="replace") if isinstance(roh, bytes) else roh
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    text = "".join(c if c.isprintable() or c == "\n" else "?" for c in text)
    return text[-n:].strip()


def umgebung(befehl: Befehl, grund: dict[str, str]) -> dict[str, str]:
    """Nur diese Namen kommen in den Prozess: kein Name aus der Umgebung des Läufers (dort steht in einer Sandbox ohnehin nichts)."""
    env = {"PATH": grund.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": grund.get("HOME", "/tmp/pruefer-home"), "TMPDIR": "/tmp",
           "LANG": "C.UTF-8", "CI": "1", "HTTPS_PROXY": grund.get("HTTPS_PROXY", PROXY_PAKETE), "HTTP_PROXY": "", "NO_PROXY": "",
           "npm_config_https_proxy": grund.get("HTTPS_PROXY", PROXY_PAKETE), "npm_config_update_notifier": "false",
           "UV_NO_PROGRESS": "1", "UV_PYTHON_DOWNLOADS": "never", "PYTHONDONTWRITEBYTECODE": "1"}
    env.update(dict(befehl.env))
    return env


def ausfuehren_echt(befehl: Befehl, wurzel: str, env: dict[str, str]) -> Ausgang:
    """Startet das Werkzeug ohne Shell, ohne Eingabe, in eigener Prozessgruppe; beim Zeitlimit wird die ganze Gruppe beendet."""
    ordner = os.path.join(wurzel, befehl.ordner)
    try:
        p = subprocess.Popen(list(befehl.argv), cwd=ordner, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             start_new_session=True)
    except OSError as e:
        return Ausgang(127, f"Werkzeug nicht startbar: {type(e).__name__}")
    try:
        aus, _ = p.communicate(timeout=befehl.zeit_s)
        return Ausgang(p.returncode, kuerzen(aus or b""))
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except (OSError, AttributeError):
            p.kill()
        aus, _ = p.communicate()
        return Ausgang(None, kuerzen(aus or b""))


Ausfuehrer = Callable[[Befehl, str, dict[str, str]], Ausgang]


# ------------------------------------------------------------------------------------------------ Bäume vergleichen
@dataclass
class Baum:
    dateien: list[S.Datei]
    pfade: tuple[str, ...]
    links: list[str]
    fehler: str = ""


def _lese_baum(wurzel: str) -> tuple[dict[str, tuple[bytes | None, str]], list[str], str]:
    """(Pfad -> (Inhalt oder None bei zu großen und binären Dateien, SHA-256), Links, Fehler). Links werden nie verfolgt."""
    inhalt: dict[str, tuple[bytes | None, str]] = {}
    links: list[str] = []
    summe = 0
    for ordner, unter, dateien in os.walk(wurzel):
        for d in unter:   # Links auf Ordner werden nie betreten, aber gemeldet (sonst bliebe ein solcher Link unbemerkt)
            if os.path.islink(os.path.join(ordner, d)):
                links.append(os.path.relpath(os.path.join(ordner, d), wurzel).replace(os.sep, "/"))
        unter[:] = sorted(d for d in unter if d not in AUSLASSEN and not os.path.islink(os.path.join(ordner, d)))
        for name in sorted(dateien):
            voll = os.path.join(ordner, name)
            rel = os.path.relpath(voll, wurzel).replace(os.sep, "/")
            if os.path.islink(voll):
                links.append(rel)
                continue
            if len(inhalt) >= DATEIEN_MAX:
                return inhalt, links, "zu_viele_dateien"
            groesse = os.path.getsize(voll)
            summe += groesse
            if summe > GESAMT_MAX:
                return inhalt, links, "zu_gross"
            h = hashlib.sha256()
            behalten = bytearray()
            with open(voll, "rb") as f:
                while teil := f.read(1024 * 1024):
                    h.update(teil)
                    if groesse <= DATEI_MAX:
                        behalten += teil
            roh = bytes(behalten)
            inhalt[rel] = (None if groesse > DATEI_MAX or b"\x00" in roh[:2000] else roh, h.hexdigest())   # Binär oder groß: nur der Name zählt
    return inhalt, links, ""


def vergleiche_baeume(basis: str, neu: str) -> Baum:
    """Geänderte, neue und gelöschte Dateien als pruefer_scans.Datei (Vergleich über die Prüfsumme, auch bei Binärdateien)."""
    alt, _, f1 = _lese_baum(basis)
    jetzt, links, f2 = _lese_baum(neu)
    dateien: list[S.Datei] = []
    for pfad in sorted(set(alt) | set(jetzt)):
        if pfad in alt and pfad in jetzt and alt[pfad][1] == jetzt[pfad][1]:
            continue
        a = alt[pfad][0] if pfad in alt else None
        n = jetzt[pfad][0] if pfad in jetzt else None
        if pfad in jetzt and n is None:
            n = b""   # Binär oder groß: der Name wird geprüft, kein Inhalt
        dateien.append(S.Datei(pfad, n, a))
    return Baum(dateien, tuple(sorted(jetzt)), links, f1 or f2)


# ------------------------------------------------------------------------------------------------ Stufen
@dataclass
class Stufe:
    wert: str = "ok"
    treffer_anzahl: int = 0
    fehler: str = ""
    ende: str = ""

    def als_dict(self) -> dict:
        d: dict = {"treffer_anzahl": self.treffer_anzahl, "fehler": self.fehler}
        if self.ende:
            d["ende"] = self.ende
        return d


def werkzeugstufe(name: str, befehle: tuple[Befehl, ...], wurzel: str, ausfuehrer: Ausfuehrer, grund: dict[str, str]) -> Stufe:
    for b in befehle:
        if not os.path.isdir(os.path.join(wurzel, b.ordner)):
            continue   # Paket gibt es in diesem Stand nicht (zum Beispiel geraete in einem sehr alten Stand): nichts zu tun
        try:
            erg = ausfuehrer(b, wurzel, umgebung(b, grund))
        except Exception:  # noqa: BLE001  eine Panne des Ausführers ist ein Fehler der Stufe, nie ein Absturz des Läufers
            return Stufe("fehler", fehler=f"ausfuehrer_panne:{b.ordner}", ende="")
        if erg.code is None:
            return Stufe("fehler", fehler=f"zeitlimit:{b.ordner}:{' '.join(b.argv[:3])}", ende=erg.ende)
        if erg.code != 0:
            return Stufe("fehler", fehler=f"exit_{erg.code}:{b.ordner}:{' '.join(b.argv[:3])}", ende=erg.ende)
    return Stufe("ok")


def aus_befund(b: S.Befund) -> Stufe:
    return Stufe(b.zeile, b.treffer_anzahl, b.fehler)


def migrationen_geaendert(dateien: list[S.Datei]) -> bool:
    return any(d.pfad.startswith("supabase/migrations/") for d in dateien)


def lauf(basis: str, neu: str, runde: int, muster_zeilen: list[str], ausfuehrer: Ausfuehrer | None = None,
         grund: dict[str, str] | None = None, auch_audit: bool = True) -> dict:
    ausfuehrer = ausfuehrer or ausfuehren_echt   # erst beim Aufruf aufgelöst: Tests und Kommandozeile können ihn ersetzen
    grund = grund or {}
    if runde not in RUNDEN:
        raise ValueError("unbekannte Runde")
    baum = vergleiche_baeume(basis, neu)
    stufen: dict[str, Stufe] = {}
    scans: dict[str, S.Befund] = {}
    fruehe_fehler = "baum_" + baum.fehler if baum.fehler else ""
    for name in RUNDEN[runde]:
        if name == "bauen":
            s = Stufe("fehler", fehler="link_im_baum", ende=", ".join(baum.links[:5])) if baum.links else \
                werkzeugstufe("bauen", plan_bauen(), neu, ausfuehrer, grund)
        elif name in ("tests", "typen_lint"):
            if stufen.get("bauen") is not None and stufen["bauen"].wert != "ok":
                s = Stufe("uebersprungen", fehler="bauen_fehlgeschlagen")   # ohne Bau sind Tests und Lint sinnlos; "uebersprungen" lässt das Tor zu
            else:
                s = werkzeugstufe(name, PLAENE[name](), neu, ausfuehrer, grund)
        elif name == "secret_scan":
            scans[name] = S.secret_scan(baum.dateien, baum.pfade)
            s = aus_befund(scans[name])
        elif name == "fremdverweis":
            try:
                scans[name] = S.fremdverweis(baum.dateien, S.muster_laden(muster_zeilen))
                s = aus_befund(scans[name])
            except Exception:  # noqa: BLE001  fehlende oder ungültige Mustervorlage: fail closed
                s = Stufe("fehler", fehler="mustervorlage")
        elif name == "wirkungsflaechen":
            scans[name] = S.wirkungsflaechen(baum.dateien)
            s = aus_befund(scans[name])
        elif name == "abhaengigkeiten":
            scans[name] = S.abhaengigkeiten(baum.dateien)
            s = aus_befund(scans[name])
            if auch_audit and s.wert == "ok" and (scans[name].neue_pakete or any(d.pfad.endswith(("uv.lock", "package-lock.json")) for d in baum.dateien)):
                a = werkzeugstufe("audit", AUDITS, neu, ausfuehrer, grund)
                if a.wert != "ok":
                    s = a
        elif name == "migrations_lint":
            s = Stufe("fehler", fehler="migrations_lint_fehlt") if migrationen_geaendert(baum.dateien) else Stufe("ok")
        else:  # nicht erreichbar, solange RUNDEN stimmt
            s = Stufe("fehler", fehler="unbekannte_stufe")
        if fruehe_fehler and name in ("secret_scan", "fremdverweis", "wirkungsflaechen", "abhaengigkeiten"):
            s = Stufe("fehler", fehler=fruehe_fehler)   # der Baum war zu groß: nicht vollständig geprüft
        stufen[name] = s
    wirk = scans.get("wirkungsflaechen")
    abh = scans.get("abhaengigkeiten")
    return {"runde": runde, "pruefstand": {k: v.wert for k, v in stufen.items()}, "details": {k: v.als_dict() for k, v in stufen.items()},
            "sonder": bool(wirk and wirk.sonder), "neue_pakete": [list(p) for p in (abh.neue_pakete if abh else [])][:200],
            "geaenderte_dateien": len(baum.dateien),
            "treffer": {k: [{"pfad": t.pfad, "zeile": t.zeile, "art": t.art, "detail": t.detail} for t in b.treffer[:50]] for k, b in scans.items()}}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Läufer der Prüfstrecke")
    ap.add_argument("--basis", required=True)
    ap.add_argument("--neu", required=True)
    ap.add_argument("--runde", type=int, required=True, choices=sorted(RUNDEN))
    ap.add_argument("--fremdverweise", required=True, help="Datei mit den Mustern (liegt im Anker, nicht im Repo)")
    ap.add_argument("--ausgabe", required=True)
    ap.add_argument("--ohne-audit", action="store_true")
    a = ap.parse_args(argv)
    try:
        muster = Path(a.fremdverweise).read_text(encoding="utf-8").splitlines()
    except OSError:
        muster = []   # fehlt die Vorlage, meldet die Stufe fremdverweis "mustervorlage" und bleibt auf fehler
    t0 = time.monotonic()
    erg = lauf(a.basis, a.neu, a.runde, muster, auch_audit=not a.ohne_audit, grund={k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "HTTPS_PROXY")})
    erg["dauer_s"] = int(time.monotonic() - t0)
    Path(a.ausgabe).write_text(json.dumps(erg, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if all(v == "ok" for v in erg["pruefstand"].values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
