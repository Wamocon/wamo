"""Status der Server: Sokrates-2 lokal (/proc, statvfs, nvidia-smi, systemctl is-active), Sokrates-1 und Caesar per SSH
als wamo-status mit Zwangsbefehl (infra/sparks/wamo_status.py), LiteLLM nur über /health/liveliness.

SSH: eigener Schlüssel und eigene known_hosts des Dienstes (StateDirectory), -F none, BatchMode, kurze Zeitgrenze.
Der Befehl ist fest ("alles"); was der Server antwortet, wird als Zahlen und enge Namen übernommen."""

from __future__ import annotations

import asyncio
import json
import os

from ..http_klein import HttpFehler, anfrage
from ..konfig import Geraet
from ..text import ganzzahl, name, zahl
from . import GeraetFehler, NichtEingerichtet

SSH = "/usr/bin/ssh"
SSH_ORDNER = os.getenv("WAMO_GERAETE_SSH_ORDNER") or "/var/lib/wamo-geraete/ssh"
MAX_SSH_BYTES = 16 * 1024


async def _befehl(argv: list[str], timeout: float, max_bytes: int = 64 * 1024) -> tuple[int, bytes]:
    proc = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.DEVNULL,
                                                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
    try:
        async with asyncio.timeout(timeout):
            daten = await proc.stdout.read(max_bytes + 1)
            await proc.wait()
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise GeraetFehler("Zeitüberschreitung")
    if len(daten) > max_bytes:
        raise GeraetFehler("Antwort zu groß")
    return proc.returncode or 0, daten


def _proc_zahlen() -> dict:
    erg: dict = {}
    try:
        with open("/proc/uptime", encoding="ascii") as f:
            erg["laufzeit_stunden"] = round(float(f.read().split()[0]) / 3600, 1)
        with open("/proc/loadavg", encoding="ascii") as f:
            last = f.read().split()
        erg["last"] = [float(last[0]), float(last[1]), float(last[2])]
        erg["kerne"] = os.cpu_count() or 0
        mem: dict[str, int] = {}
        with open("/proc/meminfo", encoding="ascii") as f:
            for zeile in f:
                k, _, rest = zeile.partition(":")
                if k in ("MemTotal", "MemAvailable"):
                    mem[k] = int(rest.split()[0])
        if mem.get("MemTotal"):
            erg["speicher_gb"] = round(mem["MemTotal"] / 1048576, 1)
            erg["speicher_frei_gb"] = round(mem.get("MemAvailable", 0) / 1048576, 1)
        st = os.statvfs("/")
        erg["platte_gb"] = round(st.f_blocks * st.f_frsize / 1e9, 1)
        erg["platte_frei_gb"] = round(st.f_bavail * st.f_frsize / 1e9, 1)
    except (OSError, ValueError, IndexError):
        erg.setdefault("hinweis", "Teilwerte nicht lesbar")
    return erg


def gpu_aus_csv(text: str) -> list[dict]:
    """nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu (csv, ohne Einheiten).
    Auf GB10 können Speicherwerte "[N/A]" sein: dann fehlen sie."""
    gpus = []
    for zeile in text.splitlines()[:8]:
        teile = [t.strip() for t in zeile.split(",")]
        if len(teile) != 5:
            continue
        gpu = {"name": name(teile[0], 40)}
        for schluessel, wert, hi in (("auslastung_prozent", teile[1], 100), ("speicher_belegt_mb", teile[2], 1e7),
                                      ("speicher_gesamt_mb", teile[3], 1e7), ("temperatur_c", teile[4], 150)):
            z = zahl(wert, 0, hi)
            if z is not None:
                gpu[schluessel] = round(z)
        gpus.append(gpu)
    return gpus


async def _gpu_lokal() -> list[dict] | str:
    try:
        rc, daten = await _befehl(["/usr/bin/nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,"
                                   "temperature.gpu", "--format=csv,noheader,nounits"], 4)
    except (OSError, GeraetFehler):
        return "nicht lesbar"
    return gpu_aus_csv(daten.decode("utf-8", "replace")) if rc == 0 else "nicht lesbar"


async def _dienste_lokal(units: tuple[str, ...]) -> dict[str, str]:
    erg: dict[str, str] = {}
    for unit in units:
        try:
            _, daten = await _befehl(["/usr/bin/systemctl", "is-active", unit], 2, 200)
            erg[unit] = name(daten.decode("ascii", "replace"), 20) or "unbekannt"
        except (OSError, GeraetFehler):
            erg[unit] = "unbekannt"
    return erg


async def lokal(geraet: Geraet) -> dict:
    zahlen = await asyncio.to_thread(_proc_zahlen)
    gpu, dienste = await asyncio.gather(_gpu_lokal(), _dienste_lokal(tuple(geraet.opt("dienste", ()))))
    return {"erreichbar": True, **zahlen, "gpu": gpu, "dienste": dienste}


def aus_spark(roh: bytes) -> dict:
    """Antwort von wamo-status alles (JSON) in feste Felder übernehmen."""
    try:
        d = json.loads(roh.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise GeraetFehler("Antwort des Servers ist kein JSON") from e
    if not isinstance(d, dict):
        raise GeraetFehler("Antwort des Servers unerwartet")
    erg: dict = {"erreichbar": True}
    s = d.get("status") if isinstance(d.get("status"), dict) else {}
    for k, hi in (("laufzeit_stunden", 1e6), ("speicher_gb", 1e5), ("speicher_frei_gb", 1e5), ("platte_gb", 1e6),
                  ("platte_frei_gb", 1e6)):
        z = zahl(s.get(k), 0, hi)
        if z is not None:
            erg[k] = round(z, 1)
    if isinstance(s.get("last"), list):
        last = [zahl(x, 0, 1e4) for x in s["last"][:3]]
        if all(x is not None for x in last):
            erg["last"] = [round(x, 2) for x in last]
    if (kerne := ganzzahl(s.get("kerne"), 0, 4096)) is not None:
        erg["kerne"] = kerne
    g = d.get("gpu")
    if isinstance(g, list):
        erg["gpu"] = [{k: (name(v, 40) if k == "name" else ganzzahl(v, 0, 10**7)) for k, v in x.items()
                       if k in ("name", "auslastung_prozent", "speicher_belegt_mb", "speicher_gesamt_mb", "temperatur_c")
                       and v is not None} for x in g[:8] if isinstance(x, dict)]
    elif isinstance(g, str):
        erg["gpu"] = "nicht lesbar"
    ds = d.get("dienste")
    if isinstance(ds, dict):
        erg["dienste"] = {name(k, 60): name(v, 20) for k, v in list(ds.items())[:20]}
    return erg


async def per_ssh(geraet: Geraet) -> dict:
    if not geraet.adresse:
        raise NichtEingerichtet("Adresse fehlt")
    schluessel = os.path.join(SSH_ORDNER, "id_ed25519")
    bekannt = os.path.join(SSH_ORDNER, "known_hosts")
    if not (os.path.isfile(schluessel) and os.path.isfile(bekannt)):
        raise NichtEingerichtet("SSH-Schlüssel des Gerätedienstes fehlt (wamo-geraete-einrichten.sh)")
    argv = [SSH, "-F", "none", "-i", schluessel, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={bekannt}", "-o", "GlobalKnownHostsFile=/dev/null",
            "-o", "HostKeyAlgorithms=ssh-ed25519", "-o", "ConnectTimeout=3", "-o", "ServerAliveInterval=2",
            "-o", "ServerAliveCountMax=2", "-o", "RequestTTY=no", "-o", "ControlMaster=no", "-o", "LogLevel=ERROR",
            "-o", "ClearAllForwardings=yes", "-p", str(geraet.port or 22), f"wamo-status@{geraet.adresse}", "alles"]
    try:
        rc, daten = await _befehl(argv, 6, MAX_SSH_BYTES)
    except OSError as e:
        raise GeraetFehler("SSH nicht startbar") from e
    if rc != 0:
        raise GeraetFehler("Server nicht erreichbar oder Zugang abgelehnt")
    erg = aus_spark(daten)
    if port := geraet.opt("litellm_port"):
        try:
            antwort = await anfrage(geraet.adresse, int(port), "GET", "/health/liveliness", timeout=2, max_bytes=1024)
            erg["litellm"] = "läuft" if antwort.status == 200 else f"HTTP {antwort.status}"
        except HttpFehler:
            erg["litellm"] = "nicht erreichbar"
    return erg
