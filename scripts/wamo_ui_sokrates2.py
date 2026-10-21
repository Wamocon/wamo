"""Bringt die WAMO-Oberfläche vom PC auf Sokrates-2 (Browser-Betrieb). Gibt nie Geheimnisse aus.

Die Oberfläche läuft dort als Container wamo-ui hinter wamo-caddy auf HTTPS 55445 (infra/sokrates2/README.md,
Abschnitt 12). Gebaut wird auf Sokrates-2 aus den Quellen, nicht auf dem PC. Geheimnisse gehen nicht durch dieses Skript:
Der Publishable Key (öffentlich, steht im JavaScript jedes Browsers) wird auf dem Server aus dem Supabase-.env gelesen.

Befehle (im Repo-Hauptordner):
  python scripts/wamo_ui_sokrates2.py verteilen [--mit-firewall-und-caddy]
        Quellen von ui/ (ohne node_modules, .next und .env*) nach ~/wamo-infra/ui und die Dateien der Oberfläche
        (infra/sokrates2/wamo-ui/, wamo-ui-einrichten.sh, wamo-ui-rueckweg.sh) nach ~/wamo-infra. Anderes in
        ~/wamo-infra bleibt unberührt. Mit --mit-firewall-und-caddy zusätzlich die gemeinsamen Dateien
        wamo-firewall.sh, wamo-firewall.service und wamo-caddy/docker-compose.yml (nur nötig, solange sie nicht
        über wamo_kern_sokrates2.py verteilen auf dem Server liegen).
  python scripts/wamo_ui_sokrates2.py bauen        Image bauen und Container neu starten (wamo-ui-einrichten.sh bauen)
  python scripts/wamo_ui_sokrates2.py einrichten [--neustart]
        alles: Firewall, Caddy, Bau, erlaubte Herkunft des Kerns, Prüfung. --neustart startet wamo-core neu, aber nur,
        wenn gerade keine Sitzung läuft (sonst bleibt es ein Schritt für später)
  python scripts/wamo_ui_sokrates2.py pruefen      Zertifikatskette, Header, Härtung, Isolation, Ladezeit
  python scripts/wamo_ui_sokrates2.py status       Zustand anzeigen (nur lesend)
  python scripts/wamo_ui_sokrates2.py zurueck      das vorige Image wieder starten
"""
from __future__ import annotations

import argparse
import io
import re
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INFRA = ROOT / "infra" / "sokrates2"
UI = ROOT / "ui"
HOST = "sokrates2"
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
    "wamo-ui-rueckweg.sh",
]
GEMEINSAME_DATEIEN = ["wamo-firewall.sh", "wamo-firewall.service", "wamo-caddy/docker-compose.yml"]


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
    dateien = []
    for pfad in sorted(UI.rglob("*")):
        rel = pfad.relative_to(UI)
        if any(teil in UI_AUSGESCHLOSSEN_ORDNER for teil in rel.parts) or not pfad.is_file():
            continue
        if any(m.match(pfad.name) for m in UI_AUSGESCHLOSSEN_MUSTER):
            continue
        dateien.append(pfad)
    return dateien


def verteilen(args) -> None:
    infra = list(INFRA_DATEIEN) + (GEMEINSAME_DATEIEN if args.mit_firewall_und_caddy else [])
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
    daten = puffer.getvalue()
    # Nur die eigenen Teile ersetzen: ~/wamo-infra/ui und ~/wamo-infra/wamo-ui; Dateien anderer Stränge bleiben
    befehl = (f"set -e; mkdir -p ~/{ZIEL}; rm -rf ~/{ZIEL}/ui ~/{ZIEL}/wamo-ui; "
              f"tar -xf - -C ~/{ZIEL} --no-same-owner; chmod -R go-w ~/{ZIEL}")
    r = ssh(befehl, eingabe=daten, ausgabe=False)
    if r.returncode != 0:
        sys.exit("Verteilen fehlgeschlagen.")
    print(f"Verteilt nach {HOST}:~/{ZIEL}: {len(dateien)} Dateien der Oberfläche, {len(infra)} Dateien aus infra/sokrates2"
          f" ({len(daten) // 1024} KB)")


def auf_server(*teile: str) -> int:
    r = ssh("bash ~/wamo-infra/wamo-ui-einrichten.sh " + " ".join(teile))
    return r.returncode


def main() -> None:
    parser = argparse.ArgumentParser(description="WAMO-Oberfläche auf Sokrates-2 (Browser-Betrieb)")
    parser.add_argument("befehl", choices=["verteilen", "bauen", "einrichten", "pruefen", "status", "zurueck"])
    parser.add_argument("--mit-firewall-und-caddy", action="store_true",
                        help="verteilen: auch wamo-firewall.sh/.service und wamo-caddy/docker-compose.yml")
    parser.add_argument("--neustart", action="store_true", help="einrichten: wamo-core neu starten, wenn keine Sitzung läuft")
    args = parser.parse_args()
    if args.befehl == "verteilen":
        verteilen(args)
        return
    zuordnung = {"bauen": ["bauen"], "einrichten": ["alles"], "pruefen": ["pruefen"], "status": ["status"],
                 "zurueck": ["zurueck"]}
    teile = zuordnung[args.befehl] + (["--neustart"] if args.neustart else [])
    sys.exit(auf_server(*teile))


if __name__ == "__main__":
    main()
