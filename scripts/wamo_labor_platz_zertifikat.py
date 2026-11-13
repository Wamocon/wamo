"""Die WAMO-CA im Zertifikatsspeicher des Benutzers für das Platz-Skript (nur Standardbibliothek, Windows, ohne Administratorrechte).

Es wird ausschließlich der Benutzerspeicher Root angefasst (`certutil -user ... Root`), nie der Speicher des Rechners. Windows zeigt beim
Import eine Sicherheitswarnung, die die Betreuerin oder der Betreuer bestätigt. Das Skript zeigt vorher den Fingerabdruck (SHA-1) und
prüft nach dem Import nach genau diesem Hash.

Festgenagelt: CA_SHA256 und CA_SHA1 gehören zu infra/sokrates2/wamo-ca.crt (Hash des DER-Inhalts, aus der Datei berechnet, nicht
abgetippt). Jede CA-Datei, die das Skript benutzt (--ca-datei, WAMO_CA_FILE, die Datei neben dem Skript), muss genau diesen SHA-256 haben,
sonst Abbruch. Die Bytes werden einmal gelesen und geprüft, der Import läuft aus einer geprüften Kopie im eigenen Ordner, die Quelldatei
wird nie ein zweites Mal gelesen. Ein Test hält die Konstanten mit der Datei im Repo zusammen: Bei einer neuen CA bricht er sichtbar.
"""
from __future__ import annotations

import base64
import binascii
import ctypes
import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import Callable

Certutil = Callable[[list[str]], tuple[int, str]]
_PEM = re.compile(r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", re.S)
FRIST_S = 300  # Windows wartet beim Import auf die Bestätigung im Dialog
MAX_CA_BYTES = 65536
CA_SHA256 = "a97e9eda3d9ec67432b0597e2f16acfb455f807f3fc58821556fe35ee69ce023"
CA_SHA1 = "9f5de37f855fd083142e9e148a0954eab2817200"


class CaFehler(Exception):
    """Fehler mit Text für die Person (CA-Datei unbrauchbar oder Speicher lehnt ab)."""


def _der_aus_pem(roh: bytes, name: str) -> bytes:
    """Das Zertifikat (DER) aus den Bytes einer PEM-Datei. Prüft die Form (genau ein Zertifikat, Beginn einer DER-Folge)."""
    try:
        text = roh.decode("ascii")
    except UnicodeDecodeError:
        raise CaFehler(f"CA-Datei ist kein PEM-Text: {name}") from None
    treffer = _PEM.findall(text)
    if len(treffer) != 1:
        raise CaFehler(f"CA-Datei enthält nicht genau ein Zertifikat: {name}")
    try:
        der = base64.b64decode("".join(treffer[0].split()), validate=True)
    except (binascii.Error, ValueError):
        raise CaFehler(f"CA-Datei ist kein gültiges PEM: {name}") from None
    if len(der) < 64 or der[0] != 0x30:
        raise CaFehler(f"CA-Datei enthält kein Zertifikat: {name}")
    return der


def ca_lesen(pfad: Path, erwartet_sha256: str) -> bytes:
    """Liest die CA-Datei genau einmal und gibt das Zertifikat (DER) nur zurück, wenn sein SHA-256 der festgelegte Wert ist."""
    try:
        roh = Path(pfad).read_bytes()
    except OSError:
        raise CaFehler(f"CA-Datei nicht lesbar: {pfad}") from None
    if len(roh) > MAX_CA_BYTES:
        raise CaFehler(f"CA-Datei ist zu groß für ein Zertifikat: {pfad}")
    der = _der_aus_pem(roh, str(pfad))
    if sha256_hex(der) != erwartet_sha256:
        raise CaFehler(f"CA-Datei weicht von der mitgelieferten WAMO-CA ab (SHA-256 {gruppiert(sha256_hex(der))[:20]}... statt "
                       f"{gruppiert(erwartet_sha256)[:20]}...): {pfad}. Nur die Datei infra/sokrates2/wamo-ca.crt wird benutzt")
    return der


def ca_suchen(kandidaten: tuple[Path, ...], erwartet_sha256: str) -> tuple[Path, bytes]:
    """Die erste vorhandene Kandidatendatei muss die festgelegte CA sein (sonst Fehler, es wird nicht zur nächsten gewechselt)."""
    for pfad in kandidaten:
        if pfad.is_file():
            return pfad, ca_lesen(pfad, erwartet_sha256)
    raise CaFehler("CA-Datei nicht gefunden (--ca-datei PFAD, im Repo infra/sokrates2/wamo-ca.crt)")


def ca_pem(der: bytes) -> str:
    """Das Zertifikat als PEM-Text mit LF (der Inhalt der geprüften Kopie, die importiert wird)."""
    b64 = base64.b64encode(der).decode("ascii")
    zeilen = [b64[i:i + 64] for i in range(0, len(b64), 64)]
    return "-----BEGIN CERTIFICATE-----\n" + "\n".join(zeilen) + "\n-----END CERTIFICATE-----\n"


def sha1_hex(der: bytes) -> str:
    return hashlib.sha1(der).hexdigest()


def sha256_hex(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def gruppiert(hexwert: str) -> str:
    """Der Fingerabdruck so, wie Windows ihn im Dialog zeigt: Gruppen zu acht Zeichen, groß geschrieben."""
    return " ".join(hexwert[i:i + 8] for i in range(0, len(hexwert), 8)).upper()


def im_speicher(certutil: Certutil, sha1: str) -> bool:
    rc, _ = certutil(["-user", "-store", "Root", sha1])
    return rc == 0


def importieren(certutil: Certutil, kopie: Path, sha1: str, erwartet_sha256: str) -> None:
    """Importiert die geprüfte Kopie. Sie wird unmittelbar davor noch einmal gegen den festgelegten SHA-256 geprüft."""
    ca_lesen(kopie, erwartet_sha256)
    rc, _ = certutil(["-user", "-addstore", "Root", str(kopie)])
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


def system32_ordner() -> Path:
    """Der Systemordner von Windows (nicht aus einer Umgebungsvariable des Benutzers), Rückfall SystemRoot plus System32."""
    try:
        puffer = ctypes.create_unicode_buffer(260)
        if ctypes.windll.kernel32.GetSystemDirectoryW(puffer, 260):  # type: ignore[attr-defined]
            return Path(puffer.value)
    except (AttributeError, OSError):
        pass
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"


def certutil_echt(args: list[str]) -> tuple[int, str]:
    """Ruft certutil.exe mit vollem Pfad aus System32 auf (ohne Shell, Argumente als Liste). Nur im Betrieb, nie in den Tests."""
    programm = str(system32_ordner() / "certutil.exe")
    try:
        r = subprocess.run([programm] + args, capture_output=True, text=True, timeout=FRIST_S, check=False)  # noqa: S603
    except (OSError, subprocess.SubprocessError) as e:
        return 1, f"certutil nicht ausführbar: {type(e).__name__}"
    return r.returncode, (r.stdout or "") + (r.stderr or "")
