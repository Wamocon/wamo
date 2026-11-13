"""Die echte Anbindung des Platz-Skripts an Windows (nur Standardbibliothek): baut die `Umgebung` für den Betrieb.

Hier stehen die Teile, die den Rechner wirklich berühren: winreg (über WinRegistrierung), certutil, Prozessstart, Netzabruf mit TLS
gegen die WAMO-CA, Eingabe ohne Anzeige, Desktop und Arbeitsbereich. Die Tests prüfen diese Teile mit Ersatzmodulen, der echte Lauf
ist nur auf einem Laptop prüfbar (docs/labor-platz-einrichten.md).
"""
from __future__ import annotations

import getpass
import http.client
import os
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from wamo_labor_platz_adressen import WEITER_PORT, adresse_zulaessig
from wamo_labor_platz_registrierung import WinRegistrierung
from wamo_labor_platz_umgebung import Abruf, AbrufFehler, Umgebung
from wamo_labor_platz_verknuepfung import desktop_ordner, verknuepfung_schreiben
from wamo_labor_platz_zertifikat import certutil_echt

SKRIPT = Path(__file__).resolve().parent / "wamo_labor_platz.py"
CA_NAME = "wamo-ca.crt"
ABRUF_FRIST_S = 5.0
SPI_GETWORKAREA = 0x0030


def tls_kontext(ca_datei: Path) -> ssl.SSLContext:
    """Prüft Zertifikat und Rechnername gegen genau diese CA-Datei (keine Systemzertifikate)."""
    try:
        kontext = ssl.create_default_context(cafile=str(ca_datei))
    except (OSError, ssl.SSLError):
        raise AbrufFehler("tls", "CA-Datei nicht lesbar") from None
    kontext.verify_mode = ssl.CERT_REQUIRED
    kontext.check_hostname = True
    kontext.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)  # die CA ist fest vorgegeben, die strenge Formprüfung brächte nur Ablehnungen
    return kontext


def abruf_https(url: str, ca_datei: Path, frist_s: float = ABRUF_FRIST_S) -> Abruf:
    """GET mit TLS-Prüfung gegen die CA-Datei. Keine Umleitung, kein Proxy, nur die festen Labor-Adressen."""
    if not adresse_zulaessig(url):
        raise AbrufFehler("verbindung", "Adresse gehört nicht zu den festen Adressen des Labors")
    teile = urlsplit(url)
    verbindung = http.client.HTTPSConnection(teile.hostname, teile.port, timeout=frist_s, context=tls_kontext(ca_datei))
    try:
        verbindung.request("GET", teile.path or "/", headers={"User-Agent": "wamo-labor-platz", "Connection": "close"})
        antwort = verbindung.getresponse()
        return Abruf(antwort.status, antwort.getheader("Date"))
    except ssl.SSLError as e:
        raise AbrufFehler("tls", getattr(e, "verify_message", "") or type(e).__name__) from None
    except (socket.timeout, TimeoutError):
        raise AbrufFehler("frist", "keine Antwort") from None
    except (OSError, http.client.HTTPException) as e:
        raise AbrufFehler("verbindung", type(e).__name__) from None
    finally:
        verbindung.close()


def prozess_starten_echt(args: list[str]) -> None:
    """Startet ein Programm abgekoppelt, ohne Shell und ohne eigene Umgebung (Argumente nur als Liste)."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,  # noqa: S603
                     close_fds=True, creationflags=flags)


def token_eingabe_echt() -> str:
    return getpass.getpass("Platz-Token vom Betreuer (Eingabe wird nicht angezeigt): ")


def taste_echt() -> None:
    input("Zum Schließen Enter drücken ...")


def arbeitsbereich_echt() -> tuple[int, int, int, int]:
    """Arbeitsbereich ohne Taskleiste (x, y, Breite, Höhe). Rückfall: 1920 mal 1040."""
    try:
        import ctypes
        from ctypes import wintypes
        rechteck = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rechteck), 0):
            return rechteck.left, rechteck.top, rechteck.right - rechteck.left, rechteck.bottom - rechteck.top
    except (AttributeError, OSError, ValueError):
        pass
    return 0, 0, 1920, 1040


def _umgebungsordner(name: str, *teile: str) -> Path | None:
    wert = os.environ.get(name)
    return Path(wert).joinpath(*teile) if wert else None


def chrome_kandidaten() -> tuple[Path, ...]:
    ende = ("Google", "Chrome", "Application", "chrome.exe")
    pfade = (_umgebungsordner("ProgramFiles", *ende), _umgebungsordner("ProgramFiles(x86)", *ende), _umgebungsordner("LOCALAPPDATA", *ende))
    return tuple(p for p in pfade if p is not None)


def ca_kandidaten() -> tuple[Path, ...]:
    aus_umgebung = os.environ.get("WAMO_CA_FILE")
    neben_skript = SKRIPT.parent / CA_NAME
    im_repo = SKRIPT.parent.parent / "infra" / "sokrates2" / CA_NAME
    return tuple(p for p in (Path(aus_umgebung) if aus_umgebung else None, neben_skript, im_repo) if p is not None)


def _python_mit_konsole() -> str:
    """Die Verknüpfungen brauchen python.exe (Konsole für die Eingabe ohne Anzeige), nie pythonw.exe."""
    pfad = Path(sys.executable)
    return str(pfad.with_name("python.exe")) if pfad.name.lower() == "pythonw.exe" else str(pfad)


def echte_umgebung() -> Umgebung:
    lokal = _umgebungsordner("LOCALAPPDATA")
    if lokal is None:
        raise OSError("LOCALAPPDATA ist nicht gesetzt: Das Skript läuft nur unter Windows im Benutzerkonto")
    return Umgebung(
        reg=WinRegistrierung(), certutil=certutil_echt, lokal=lokal, desktop=desktop_ordner(), skript=SKRIPT, python=_python_mit_konsole(),
        chrome_kandidaten=chrome_kandidaten(), ca_kandidaten=ca_kandidaten(), prozess_starten=prozess_starten_echt,
        verknuepfung=lambda pfad, ziel, argumente, ordner: verknuepfung_schreiben(pfad, ziel, argumente, ordner), abruf=abruf_https,
        jetzt=time.time, token_eingabe=token_eingabe_echt, warten=time.sleep, arbeitsbereich=arbeitsbereich_echt, ausgabe=print,
        weiter_port=WEITER_PORT, taste=taste_echt)
