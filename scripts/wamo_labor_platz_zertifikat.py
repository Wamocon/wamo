"""Die WAMO-CA im Zertifikatsspeicher des Benutzers für das Platz-Skript (nur Standardbibliothek, Windows, ohne Administratorrechte).

Es wird ausschließlich der Benutzerspeicher Root angefasst (`certutil -user ... Root`), nie der Speicher des Rechners. Windows zeigt beim
Import eine Sicherheitswarnung, die die Betreuerin oder der Betreuer bestätigt. Das Skript zeigt vorher den Fingerabdruck (SHA-1) der
Datei, damit er mit dem dokumentierten Wert (infra/sokrates2/README.md, Abschnitt 5.2) verglichen werden kann. Eine Prüfung nach Hash
folgt nach dem Import und vor dem Entfernen. Der Fingerabdruck steht nicht im Code: Die Datei im Repo ist die Vertrauensquelle.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
import subprocess
from pathlib import Path
from typing import Callable

Certutil = Callable[[list[str]], tuple[int, str]]
_PEM = re.compile(r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", re.S)
FRIST_S = 300  # Windows wartet beim Import auf die Bestätigung im Dialog


class CaFehler(Exception):
    """Fehler mit Text für die Person (CA-Datei unbrauchbar oder Speicher lehnt ab)."""


def ca_lesen(pfad: Path) -> bytes:
    """Das Zertifikat (DER) aus einer PEM-Datei. Prüft nur die Form (ein Zertifikat, Beginn einer DER-Folge), mehr ist Sache von Windows."""
    try:
        text = Path(pfad).read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError):
        raise CaFehler(f"CA-Datei nicht lesbar: {pfad}") from None
    treffer = _PEM.findall(text)
    if len(treffer) != 1:
        raise CaFehler(f"CA-Datei enthält nicht genau ein Zertifikat: {pfad}")
    try:
        der = base64.b64decode("".join(treffer[0].split()), validate=True)
    except (binascii.Error, ValueError):
        raise CaFehler(f"CA-Datei ist kein gültiges PEM: {pfad}") from None
    if len(der) < 64 or der[0] != 0x30:
        raise CaFehler(f"CA-Datei enthält kein Zertifikat: {pfad}")
    return der


def sha1_hex(der: bytes) -> str:
    return hashlib.sha1(der).hexdigest()


def gruppiert(hexwert: str) -> str:
    """Der Fingerabdruck so, wie Windows ihn im Dialog zeigt: Gruppen zu acht Zeichen, groß geschrieben."""
    return " ".join(hexwert[i:i + 8] for i in range(0, len(hexwert), 8)).upper()


def im_speicher(certutil: Certutil, sha1: str) -> bool:
    rc, _ = certutil(["-user", "-store", "Root", sha1])
    return rc == 0


def importieren(certutil: Certutil, datei: Path, sha1: str) -> None:
    rc, _ = certutil(["-user", "-addstore", "Root", str(datei)])
    if rc != 0:
        raise CaFehler("Das Zertifikat wurde nicht importiert (Dialog abgelehnt oder certutil fehlgeschlagen)")
    if not im_speicher(certutil, sha1):
        raise CaFehler("Das Zertifikat ist nach dem Import nicht im Benutzerspeicher (Prüfung nach Hash)")


def entfernen(certutil: Certutil, sha1: str) -> None:
    if not im_speicher(certutil, sha1):
        return
    rc, _ = certutil(["-user", "-delstore", "Root", sha1])
    if rc != 0 or im_speicher(certutil, sha1):
        raise CaFehler("Das Zertifikat konnte nicht aus dem Benutzerspeicher entfernt werden")


def certutil_echt(args: list[str]) -> tuple[int, str]:
    """Ruft certutil.exe auf (ohne Shell, Argumente als Liste). Wird nur im Betrieb benutzt, nie in den Tests."""
    try:
        r = subprocess.run(["certutil"] + args, capture_output=True, text=True, timeout=FRIST_S, check=False)  # noqa: S603
    except (OSError, subprocess.SubprocessError) as e:
        return 1, f"certutil nicht ausführbar: {type(e).__name__}"
    return r.returncode, (r.stdout or "") + (r.stderr or "")
