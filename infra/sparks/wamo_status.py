#!/usr/bin/python3 -I
"""Nur-Lese-Status eines Servers für WAMO: /usr/local/bin/wamo-status (root, 0755), Login-Shell des Benutzers wamo-status.

sshd startet bei jeder Verbindung "<Shell> -c <ForceCommand>"; dieses Programm IST die Shell und ignoriert alle
Argumente. Welcher Bericht gewünscht ist, steht in SSH_ORIGINAL_COMMAND und darf nur sein:
  status   Laufzeit, Last, Kerne, Speicher, Platte /
  gpu      nvidia-smi: Name, Auslastung, Speicher, Temperatur (GB10: Speicher kann fehlen)
  dienste  systemctl is-active für die Units aus /etc/wamo/status-dienste.conf (eine je Zeile)
  alles    alle drei
Ausgabe: JSON, höchstens 16 KB, nach höchstens 5 Sekunden. Keine Prozesslisten, keine Pfade, keine Benutzer, keine
Umgebung, nichts Schreibendes. Unbekannte Befehle: Exit 2 ohne Ausgabe.
"""

import json
import os
import re
import subprocess
import sys

DIENSTE_DATEI = "/etc/wamo/status-dienste.conf"
UNIT_RE = re.compile(r"^[a-z0-9][a-z0-9@._-]{0,60}\.service$")
GRENZE = 16 * 1024
UMGEBUNG = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}


def _lauf(argv, timeout):
    try:
        p = subprocess.run(argv, capture_output=True, timeout=timeout, env=UMGEBUNG, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout.decode("utf-8", "replace") if p.returncode == 0 else None


def status():
    erg = {}
    try:
        with open("/proc/uptime", encoding="ascii") as f:
            erg["laufzeit_stunden"] = round(float(f.read().split()[0]) / 3600, 1)
        with open("/proc/loadavg", encoding="ascii") as f:
            erg["last"] = [float(x) for x in f.read().split()[:3]]
        erg["kerne"] = os.cpu_count() or 0
        mem = {}
        with open("/proc/meminfo", encoding="ascii") as f:
            for zeile in f:
                k, _, rest = zeile.partition(":")
                if k in ("MemTotal", "MemAvailable"):
                    mem[k] = int(rest.split()[0])
        erg["speicher_gb"] = round(mem.get("MemTotal", 0) / 1048576, 1)
        erg["speicher_frei_gb"] = round(mem.get("MemAvailable", 0) / 1048576, 1)
        st = os.statvfs("/")
        erg["platte_gb"] = round(st.f_blocks * st.f_frsize / 1e9, 1)
        erg["platte_frei_gb"] = round(st.f_bavail * st.f_frsize / 1e9, 1)
    except (OSError, ValueError, IndexError):
        pass
    return erg


def gpu():
    roh = _lauf(["/usr/bin/nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                 "--format=csv,noheader,nounits"], 3)
    if roh is None:
        return "nicht lesbar"
    gpus = []
    for zeile in roh.splitlines()[:8]:
        teile = [t.strip() for t in zeile.split(",")]
        if len(teile) != 5:
            continue
        g = {"name": re.sub(r"[^A-Za-z0-9 ._-]", "", teile[0])[:40]}
        for k, v in zip(("auslastung_prozent", "speicher_belegt_mb", "speicher_gesamt_mb", "temperatur_c"), teile[1:]):
            try:
                g[k] = int(float(v))
            except ValueError:
                pass
        gpus.append(g)
    return gpus


def dienste():
    try:
        with open(DIENSTE_DATEI, encoding="utf-8") as f:
            units = [z.strip() for z in f if UNIT_RE.fullmatch(z.strip())][:20]
    except OSError:
        units = []
    erg = {}
    for unit in units:
        roh = subprocess.run(["/usr/bin/systemctl", "is-active", unit], capture_output=True, timeout=2, env=UMGEBUNG,
                             check=False)
        erg[unit] = re.sub(r"[^a-z-]", "", roh.stdout.decode("ascii", "replace"))[:20] or "unbekannt"
    return erg


def main():
    befehl = (os.environ.get("SSH_ORIGINAL_COMMAND") or "").strip()
    if befehl == "status":
        daten = {"status": status()}
    elif befehl == "gpu":
        daten = {"gpu": gpu()}
    elif befehl == "dienste":
        daten = {"dienste": dienste()}
    elif befehl == "alles":
        daten = {"status": status(), "gpu": gpu(), "dienste": dienste()}
    else:
        return 2
    ausgabe = json.dumps(daten, ensure_ascii=False)
    if len(ausgabe.encode("utf-8")) > GRENZE:
        return 3
    sys.stdout.write(ausgabe + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.TimeoutExpired:
        sys.exit(4)
