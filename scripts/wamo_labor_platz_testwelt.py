"""Gemeinsame Testwelt für die Tests von scripts/wamo_labor_platz.py (nur für Tests).

Sie baut eine Umgebung aus Attrappen auf: Registrierung und Zertifikatsspeicher im Speicher, Prozesse nur im Mitschnitt, alle Dateien in
einem temporären Ordner. Dazu stellt `fallen_stellen` Fallen auf, damit ein Fehler im Skript nie den echten Rechner berührt: Jeder
echte Prozessstart, jeder Zugriff auf winreg, getpass und die Standard-Umgebung löst einen Fehler des Tests aus.

Testwerte (Zertifikat, Platz-Token) werden aus Zeichen berechnet, nie als lange Zeichenfolge eingetragen.
"""
from __future__ import annotations

import base64
import hashlib
import subprocess
import sys
from pathlib import Path

DER = bytes([0x30, 0x82, 0x01, 0x0A]) + bytes((i * 7 + 3) % 256 for i in range(266))
SHA1 = hashlib.sha1(DER).hexdigest()
SHA256 = hashlib.sha256(DER).hexdigest()
TOKEN = "".join("0123456789abcdef"[(i * 5 + 3) % 16] for i in range(32))  # genau 32 Hexzeichen, berechnet
JETZT = 1_800_000_000.0


def pem_aus(der: bytes) -> str:
    b64 = base64.b64encode(der).decode()
    zeilen = [b64[i:i + 64] for i in range(0, len(b64), 64)]
    return "-----BEGIN CERTIFICATE-----\n" + "\n".join(zeilen) + "\n-----END CERTIFICATE-----\n"


class Falle:
    """Ersatz für ein Modul, das im Test nie benutzt werden darf."""

    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, attribut: str):
        raise AssertionError(f"Der Test greift auf {self._name}.{attribut} zu (echter Rechner)")


def _verboten(name: str):
    def _aufruf(*_args, **_kwargs):
        raise AssertionError(f"Der Test ruft {name} auf (echter Rechner)")
    return _aufruf


def fallen_stellen(monkeypatch, tmp_path: Path) -> None:
    for name in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, _verboten("subprocess." + name))
    monkeypatch.setitem(sys.modules, "winreg", Falle("winreg"))
    monkeypatch.setattr("os.system", _verboten("os.system"))
    monkeypatch.setattr("os.startfile", _verboten("os.startfile"), raising=False)
    monkeypatch.setattr("webbrowser.open", _verboten("webbrowser.open"))
    monkeypatch.setattr("getpass.getpass", _verboten("getpass.getpass"))
    heim = tmp_path / "heim"
    heim.mkdir(parents=True, exist_ok=True)
    for var in ("USERPROFILE", "HOME", "LOCALAPPDATA", "APPDATA"):
        monkeypatch.setenv(var, str(heim))
    import wamo_labor_platz_umgebung as umgmod
    monkeypatch.setattr(umgmod, "echte_umgebung", _verboten("echte_umgebung"))


class Welt:
    """Alles, was ein Test braucht: die Umgebung, ihre Attrappen und die Pfade."""

    def __init__(self, tmp_path: Path) -> None:
        import wamo_labor_platz_attrappe as at
        import wamo_labor_platz_umgebung as umgmod
        self.tmp = tmp_path
        self.lokal = tmp_path / "lokal"
        self.desktop = tmp_path / "desktop"
        self.ca = tmp_path / "ca" / "wamo-ca.crt"
        self.ca.parent.mkdir(parents=True)
        self.ca.write_text(pem_aus(DER), encoding="ascii", newline="\n")
        self.chrome = tmp_path / "programme" / "chrome.exe"
        self.chrome.parent.mkdir(parents=True)
        self.chrome.write_bytes(b"")
        self.reg = at.FakeRegistrierung()
        self.zert = at.FakeZertifikatsspeicher()
        self.prozesse = at.ProzessMitschnitt()
        self.lnk = at.VerknuepfungAttrappe()
        self.zeilen: list[str] = []
        self.tasten: list[int] = []
        self.admin = False
        self.umg = umgmod.Umgebung(
            reg=self.reg, certutil=self.zert, lokal=self.lokal, desktop=self.desktop,
            skript=tmp_path / "scripts" / "wamo_labor_platz.py", python=str(tmp_path / "py" / "python.exe"),
            chrome_kandidaten=(self.chrome,), ca_kandidaten=(self.ca,),
            prozess_starten=self.prozesse, verknuepfung=self.lnk, abruf=_kein_abruf, jetzt=lambda: JETZT,
            token_eingabe=lambda: TOKEN, warten=lambda _s: None, arbeitsbereich=lambda: (0, 0, 1920, 1040),
            ausgabe=self.zeilen.append, weiter_port=0, weiter_frist_s=3.0, chrome_wartezeit_s=0.0,
            taste=lambda: self.tasten.append(1), ca_sha256=SHA256, ca_sha1=SHA1, ist_admin=lambda: self.admin)

    @property
    def platz_datei(self) -> Path:
        return self.lokal / "WAMO" / "platz.json"

    @property
    def sicherung(self) -> Path:
        return self.lokal / "WAMO" / "platz-sicherung.json"

    @property
    def profil(self) -> Path:
        return self.lokal / "wamo-chrome-labor"

    @property
    def verknuepfungen(self) -> list[Path]:
        return [self.desktop / "WAMO Labor starten.lnk", self.desktop / "WAMO Labor pruefen.lnk"]

    @property
    def text(self) -> str:
        return "\n".join(self.zeilen)

    def lauf(self, *argv: str) -> int:
        import wamo_labor_platz as platz
        return platz.main(list(argv), self.umg)

    def dateien(self) -> dict[str, bytes]:
        """Alle Dateien im temporären Ordner mit Inhalt (für Vergleiche vorher und nachher)."""
        return {str(p.relative_to(self.tmp)): p.read_bytes() for p in sorted(self.tmp.rglob("*")) if p.is_file()}


def _kein_abruf(*_args, **_kwargs):
    raise AssertionError("Dieser Test erlaubt keinen Netzabruf")
