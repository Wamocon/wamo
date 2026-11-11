"""Bringt die WAMO-Oberfläche vom PC auf Sokrates-2 (Browser-Betrieb). Gibt nie Geheimnisse aus.

Die Oberfläche läuft dort als zwei Container wamo-ui-blau und wamo-ui-gruen hinter wamo-caddy auf HTTPS 55445
(infra/sokrates2/README.md, Abschnitt 12). Eine Farbe ist live, die andere trägt die vorige Fassung; Caddy schaltet per
"caddy reload" ohne Lücke um. Gebaut wird auf Sokrates-2 aus den Quellen, nicht auf dem PC. Geheimnisse gehen nicht durch
dieses Skript: Der Publishable Key (öffentlich, steht im JavaScript jedes Browsers) wird auf dem Server aus dem
Supabase-.env gelesen.

Befehle (im Repo-Hauptordner):
  python scripts/wamo_ui_sokrates2.py verteilen [--mit-firewall-und-caddy]
        Quellen von ui/ (ohne node_modules, .next und .env*) nach ~/wamo-infra/ui und die Dateien der Oberfläche
        (infra/sokrates2/wamo-ui/, wamo-ui-einrichten.sh, wamo-ui-umschalten.sh, wamo-ui-rueckweg.sh) nach ~/wamo-infra.
        Dazu eine Datei wamo-ui/bau.info mit der Commit-ID von HEAD und dem Vermerk, ob ui/ und infra/sokrates2 unverändert
        sind (die Oberfläche meldet beides in /version.json). Anderes in ~/wamo-infra bleibt unberührt. Mit
        --mit-firewall-und-caddy zusätzlich die gemeinsamen Dateien wamo-firewall.sh, wamo-firewall.service und
        wamo-caddy/docker-compose.yml (nur nötig, solange sie nicht über wamo_kern_sokrates2.py verteilen auf dem Server liegen).
  python scripts/wamo_ui_sokrates2.py umschalten [--bewaehrung SEK] [--fehlerquote PROZENT] [--ohne-bau] [--trocken]
        neue Fassung in der inaktiven Farbe bauen, am neuen Container prüfen (Version, Commit, Header, Kern), ohne Lücke
        umschalten, bewähren (Standard 30 Sekunden) und bei einem Fehler automatisch zurück. Das erste Mal löst es den
        einzelnen Container wamo-ui ohne Lücke durch wamo-ui-blau ab. Vorher "verteilen".
  python scripts/wamo_ui_sokrates2.py zurueck [--trocken]
        auf die vorige Farbe zurückschalten (eine Sekunde, solange sie läuft)
  python scripts/wamo_ui_sokrates2.py bauen        neue Fassung in der inaktiven Farbe bauen und starten, ohne umzuschalten
  python scripts/wamo_ui_sokrates2.py eintrag      Eintrag der live Fassung im Format der Zustandsdatei des Notankers (JSON)
  python scripts/wamo_ui_sokrates2.py einrichten [--neustart]
        alles: Firewall, Caddy, Umschalten, erlaubte Herkunft des Kerns, Prüfung. --neustart startet die alte Unit wamo-core
        neu, aber nur ohne Slot-Betrieb und wenn gerade keine Sitzung läuft. Im Slot-Betrieb (wamo-core@a und wamo-core@b)
        startet nichts neu: das Skript nennt den Hinweis "wamo-core-slots.sh umschalten-kern <release>" und endet mit Exit 0
  python scripts/wamo_ui_sokrates2.py pruefen      Zertifikatskette, Header, Härtung, Isolation, Ladezeit
  python scripts/wamo_ui_sokrates2.py status       Zustand anzeigen (nur lesend)
"""
from __future__ import annotations

import argparse
import os
import io
import re
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INFRA = ROOT / "infra" / "sokrates2"
UI = ROOT / "ui"
HOST = os.environ.get("WAMO_SSH_HOST", "wamo-sokrates2")  # SSH-Alias des Dienstkontos wamocon (~/.ssh/config), per WAMO_SSH_HOST änderbar
ZIEL = "wamo-infra"  # relativ zum Home von wamocon

# Nicht in die Quellen: lokal Gebautes, Abhängigkeiten, Umgebungsdateien (.env, .env.local ... nie auf den Server)
UI_AUSGESCHLOSSEN_ORDNER = {"node_modules", ".next", "out", "coverage", ".git", ".vscode", "__pycache__"}
UI_AUSGESCHLOSSEN_MUSTER = (re.compile(r"^\.env"), re.compile(r".*\.log$"), re.compile(r".*\.tsbuildinfo$"),
                            re.compile(r"^next-env\.d\.ts$"))
BINAER = {".png", ".ico", ".jpg", ".jpeg", ".gif", ".webp", ".woff", ".woff2", ".ttf", ".otf", ".pdf"}

INFRA_DATEIEN = [
    "wamo-ui/Dockerfile",
    "wamo-ui/docker-compose.yml",
    "wamo-ui/wamo-ui.caddy.vorlage",
    "wamo-ui/wamo-ui.conf.beispiel",
    "wamo-ui-einrichten.sh",
    "wamo-ui-umschalten.sh",
    "wamo-ui-rueckweg.sh",
]
GEMEINSAME_DATEIEN = ["wamo-firewall.sh", "wamo-firewall.service", "wamo-caddy/docker-compose.yml"]

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def ssh(befehl: str, eingabe: bytes | None = None, ausgabe: bool = True) -> subprocess.CompletedProcess[bytes]:
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", HOST, befehl],
                       input=eingabe, capture_output=True)
    if ausgabe:
        sys.stdout.buffer.write(r.stdout)
        sys.stdout.flush()
    if r.returncode != 0 and r.stderr:
        sys.stderr.buffer.write(r.stderr)
        sys.stderr.flush()
    return r


def _eintrag(tar: tarfile.TarFile, name: str, daten: bytes, ausfuehrbar: bool = False) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(daten)
    info.mode = 0o755 if ausfuehrbar else 0o644
    info.mtime = 0
    info.uname = info.gname = ""
    tar.addfile(info, io.BytesIO(daten))


def _ordner(tar: tarfile.TarFile, name: str) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    info.mtime = 0
    tar.addfile(info)


def _text(pfad: Path) -> bytes:
    """Dateiinhalt mit Unix-Zeilenenden (Skripte und Dockerfile vertragen kein CRLF); Binärdateien unverändert."""
    daten = pfad.read_bytes()
    return daten if pfad.suffix.lower() in BINAER else daten.replace(b"\r\n", b"\n")


def ui_dateien() -> list[Path]:
    """Nur von Git verfolgte Dateien unter ui/ (Positivliste), zusätzlich ohne die Ausschlüsse oben. Ungeprüfte Dateien
    wie .npmrc, Schlüssel oder Editor-Ordner, die nur lokal liegen, gelangen so nie auf den Server."""
    r = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--", "ui"], capture_output=True, check=True)
    dateien = []
    for name in sorted(n for n in r.stdout.decode("utf-8").split("\0") if n):
        pfad = ROOT / name
        rel = pfad.relative_to(UI)
        if any(teil in UI_AUSGESCHLOSSEN_ORDNER for teil in rel.parts) or not pfad.is_file():
            continue
        if any(m.match(pfad.name) for m in UI_AUSGESCHLOSSEN_MUSTER):
            continue
        dateien.append(pfad)
    return dateien


def git_stand() -> tuple[str, bool]:
    """Commit-ID von HEAD und ob ui/ und infra/sokrates2 gegenüber HEAD unverändert sind (verfolgte Dateien, gestaged
    oder nicht). Nur diese Quellen gehen auf den Server; was nicht in Git steht, kommt nie mit."""
    r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, check=True)
    commit = r.stdout.decode("ascii").strip()
    if not COMMIT_RE.match(commit):
        sys.exit("git rev-parse HEAD liefert keine gültige Commit-ID.")
    r = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no", "--",
                        "ui", "infra/sokrates2"], capture_output=True, check=True)
    return commit, r.stdout.strip() == b""


def bau_info(commit: str, sauber: bool) -> bytes:
    """Inhalt von wamo-ui/bau.info: das Umschalt-Skript liest genau diese zwei Zeilen (strenges Muster, nie sourcen)."""
    if not COMMIT_RE.match(commit):
        raise ValueError("Commit-ID ungültig")
    return f"WAMO_COMMIT={commit}\nWAMO_SAUBER={'ja' if sauber else 'nein'}\n".encode("ascii")


def tar_bauen(mit_firewall_und_caddy: bool, commit: str, sauber: bool) -> tuple[bytes, int, int]:
    """Tar-Archiv für ~/wamo-infra: Skripte und Dateien der Oberfläche, bau.info und die Quellen von ui/.
    Ergebnis (Daten, Zahl der UI-Dateien, Zahl der Infra-Dateien)."""
    infra = list(INFRA_DATEIEN) + (GEMEINSAME_DATEIEN if mit_firewall_und_caddy else [])
    for rel in infra:
        if not (INFRA / rel).is_file():
            sys.exit(f"{INFRA / rel} fehlt.")
    puffer = io.BytesIO()
    with tarfile.open(fileobj=puffer, mode="w") as tar:
        gesehen: set[str] = set()
        for rel in infra:
            ordner = ""
            for teil in Path(rel).parent.parts:
                ordner = f"{ordner}/{teil}".lstrip("/")
                if ordner not in gesehen:
                    _ordner(tar, ordner)
                    gesehen.add(ordner)
            _eintrag(tar, rel, _text(INFRA / rel), ausfuehrbar=rel.endswith(".sh"))
        _eintrag(tar, "wamo-ui/bau.info", bau_info(commit, sauber))
        _ordner(tar, "ui")
        gesehen = {"ui"}
        dateien = ui_dateien()
        for pfad in dateien:
            rel = pfad.relative_to(UI)
            ordner = "ui"
            for teil in rel.parent.parts:
                ordner = f"{ordner}/{teil}"
                if ordner not in gesehen:
                    _ordner(tar, ordner)
                    gesehen.add(ordner)
            _eintrag(tar, f"ui/{rel.as_posix()}", _text(pfad))
    return puffer.getvalue(), len(dateien), len(infra)


def verteilen(args) -> None:
    commit, sauber = git_stand()
    daten, n_ui, n_infra = tar_bauen(args.mit_firewall_und_caddy, commit, sauber)
    # Nur die eigenen Teile ersetzen: ~/wamo-infra/ui und ~/wamo-infra/wamo-ui; Dateien anderer Stränge bleiben
    befehl = (f"set -e; mkdir -p ~/{ZIEL}; rm -rf ~/{ZIEL}/ui ~/{ZIEL}/wamo-ui; "
              f"tar -xf - -C ~/{ZIEL} --no-same-owner; chmod -R go-w ~/{ZIEL}")
    r = ssh(befehl, eingabe=daten, ausgabe=False)
    if r.returncode != 0:
        sys.exit("Verteilen fehlgeschlagen.")
    print(f"Verteilt nach {HOST}:~/{ZIEL}: {n_ui} Dateien der Oberfläche, {n_infra} Dateien aus infra/sokrates2"
          f" ({len(daten) // 1024} KB), Stand {commit[:12]}{'' if sauber else ' (mit lokalen Änderungen, sauber=nein)'}")


def auf_server(*teile: str) -> int:
    r = ssh("bash ~/wamo-infra/wamo-ui-einrichten.sh " + " ".join(teile))
    return r.returncode


def auf_umschalter(*teile: str) -> int:
    """Ruft wamo-ui-umschalten.sh auf dem Server auf. Die Teile sind feste Wörter oder ganze Zahlen (argparse prüft sie)."""
    r = ssh("bash ~/wamo-infra/wamo-ui-umschalten.sh " + " ".join(teile))
    return r.returncode


def umschalt_argumente(args) -> list[str]:
    """Optionen für umschalten, nur geprüfte Werte (ganze Zahlen im erlaubten Bereich)."""
    teile: list[str] = []
    if args.bewaehrung is not None:
        teile += ["--bewaehrung", str(args.bewaehrung)]
    if args.fehlerquote is not None:
        teile += ["--fehlerquote", str(args.fehlerquote)]
    if args.ohne_bau:
        teile.append("--ohne-bau")
    if args.trocken:
        teile.append("--trocken")
    return teile


def _zahl(von: int, bis: int):
    def pruefen(text: str) -> int:
        if not re.fullmatch(r"[0-9]{1,4}", text) or not von <= int(text) <= bis:
            raise argparse.ArgumentTypeError(f"ganze Zahl von {von} bis {bis} erwartet")
        return int(text)
    return pruefen


def parser_bauen() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WAMO-Oberfläche auf Sokrates-2 (Browser-Betrieb, blau/grün)")
    parser.add_argument("befehl", choices=["verteilen", "bauen", "umschalten", "zurueck", "eintrag", "einrichten",
                                           "pruefen", "status"])
    parser.add_argument("--mit-firewall-und-caddy", action="store_true",
                        help="verteilen: auch wamo-firewall.sh/.service und wamo-caddy/docker-compose.yml")
    parser.add_argument("--neustart", action="store_true", help="einrichten: die alte Unit wamo-core neu starten, wenn keine Sitzung läuft; im Slot-Betrieb nie "
                             "(stattdessen der Hinweis auf wamo-core-slots.sh umschalten-kern)")
    parser.add_argument("--bewaehrung", type=_zahl(0, 1800), default=None,
                        help="umschalten: Sekunden der Bewährung nach dem Umschalten (Standard 30)")
    parser.add_argument("--fehlerquote", type=_zahl(0, 50), default=None,
                        help="umschalten: erlaubter Anteil fehlgeschlagener Proben in Prozent (Standard 10)")
    parser.add_argument("--ohne-bau", action="store_true",
                        help="umschalten: nicht bauen, die inaktive Farbe trägt die Fassung schon (zuvor bauen)")
    parser.add_argument("--trocken", action="store_true", help="umschalten, zurueck, bauen: nur den Plan zeigen")
    return parser


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = parser_bauen()
    args = parser.parse_args()
    nur_umschalten = {"bewaehrung": args.bewaehrung is not None, "fehlerquote": args.fehlerquote is not None,
                      "ohne_bau": args.ohne_bau}
    if args.befehl != "umschalten" and any(nur_umschalten.values()):
        parser.error("--bewaehrung, --fehlerquote und --ohne-bau gelten nur für umschalten")
    if args.trocken and args.befehl not in ("umschalten", "zurueck", "bauen"):
        parser.error("--trocken gilt nur für umschalten, zurueck und bauen")
    if args.befehl == "verteilen":
        verteilen(args)
        return
    if args.befehl == "umschalten":
        sys.exit(auf_umschalter("umschalten", *umschalt_argumente(args)))
    if args.befehl == "zurueck":
        sys.exit(auf_umschalter("zurueck", *(["--trocken"] if args.trocken else [])))
    if args.befehl == "bauen":
        sys.exit(auf_umschalter("bauen", *(["--trocken"] if args.trocken else [])))
    if args.befehl == "eintrag":
        sys.exit(auf_umschalter("eintrag"))
    zuordnung = {"einrichten": ["alles"], "pruefen": ["pruefen"], "status": ["status"]}
    teile = zuordnung[args.befehl] + (["--neustart"] if args.neustart else [])
    sys.exit(auf_server(*teile))


if __name__ == "__main__":
    main()
