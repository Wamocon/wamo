"""Start des Voice-Core als Dienst: Log-Stufe halten und optional ein Unix-Socket statt TCP (Sokrates-2).

Pipecats Entwicklungs-Runner (pipecat.runner.run.main) setzt Loguru vor dem Serverstart fest auf DEBUG. DEBUG
enthält Gesprächsinhalte (Transkripte, Werkzeugargumente) und gehört nicht ins Journal eines Servers. Der
Runner ruft am Ende uvicorn.run(app, host=..., port=...) auf; dieser Aufruf läuft über die Hülle hier:

  * Direkt vor dem Serverstart wird die Log-Stufe aus WAMO_LOG_LEVEL wiederhergestellt (Standard INFO).
  * Ohne Zugangskontrolle (auth_gate.AccessMiddleware) startet der Server nicht.
  * Ist WAMO_CORE_UDS gesetzt (Betriebsart Büro, systemd-Dienst wamo-core), lauscht der Kern nur auf diesem
    Unix-Socket. Caddy (wamo-caddy, HTTPS 55444) spricht ihn an; einen TCP-Port gibt es dann nicht. uvicorn legt
    den Socket mit 0666 an, wer ihn erreicht, regelt also allein das Verzeichnis: Es muss dem Dienstbenutzer
    gehören und darf für "andere" weder les- noch betretbar sein (infra/sokrates2/wamo-core-einrichten.sh legt
    es als 0750 wamo-core:wamocon an). Sonst bricht der Start ab.

Ersetzt wird nur der Name "uvicorn" im Modul des Runners, nicht das uvicorn-Modul selbst.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from pathlib import PurePosixPath
from types import ModuleType

ZUGANG_MIDDLEWARE = "AccessMiddleware"


def pruefe_zugang(app) -> None:
    """Bricht ab, wenn die Zugangskontrolle nicht vor dem Runner hängt."""
    namen = {getattr(getattr(m, "cls", None), "__name__", "") for m in getattr(app, "user_middleware", [])}
    if ZUGANG_MIDDLEWARE not in namen:
        raise SystemExit("Zugangskontrolle (auth_gate) fehlt: Voice-Core startet nicht")


def pruefe_socket_verzeichnis(pfad: PurePosixPath, *, stat_fn=os.stat, uid: int | None = None) -> None:
    """Verzeichnis des Sockets: vorhanden, dem Dienstbenutzer eigen, für andere gesperrt."""
    try:
        info = stat_fn(str(pfad.parent))
    except OSError as e:
        raise SystemExit(f"Socket-Verzeichnis {pfad.parent} fehlt ({type(e).__name__})") from e
    eigene = os.getuid() if uid is None and hasattr(os, "getuid") else uid
    if not stat.S_ISDIR(info.st_mode):
        raise SystemExit(f"{pfad.parent} ist kein Verzeichnis")
    if eigene is not None and info.st_uid != eigene:
        raise SystemExit(f"{pfad.parent} gehört nicht dem Dienstbenutzer")
    if info.st_mode & 0o007:
        raise SystemExit(f"{pfad.parent} ist für andere zugänglich (Rechte {stat.S_IMODE(info.st_mode):o})")


class _UvicornHuelle:
    """Steht im Runner-Modul an der Stelle von uvicorn; nur run() wird dort benutzt."""

    def __init__(self, echt: ModuleType, logging_neu: Callable[[], None], uds: str | None):
        self._echt = echt
        self._logging_neu = logging_neu
        self._uds = uds

    def run(self, app, host: str | None = None, port: int | None = None, **kwargs) -> None:
        self._logging_neu()
        pruefe_zugang(app)
        if self._uds:
            # Unix-Socket gibt es nur auf Linux (Sokrates-2): absoluter POSIX-Pfad, keine Teile mit ".."
            pfad = PurePosixPath(self._uds)
            if not pfad.is_absolute() or ".." in pfad.parts:
                raise SystemExit("WAMO_CORE_UDS muss ein absoluter Pfad sein")
            pruefe_socket_verzeichnis(pfad)
            self._echt.run(app, uds=str(pfad), **kwargs)
        else:
            self._echt.run(app, host=host, port=port, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._echt, name)


def runner_vorbereiten(runner: ModuleType, logging_neu: Callable[[], None]) -> str | None:
    """Hängt die Hülle in pipecat.runner.run ein. Liefert den Socket-Pfad (oder None für TCP)."""
    uds = (os.getenv("WAMO_CORE_UDS") or "").strip() or None
    echt = runner.uvicorn
    if isinstance(echt, _UvicornHuelle):  # schon vorbereitet
        echt = echt._echt
    runner.uvicorn = _UvicornHuelle(echt, logging_neu, uds)
    return uds
