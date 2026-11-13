"""Tests für die echte Anbindung des Platz-Skripts (wamo_labor_platz_betrieb.py und der Adapter WinRegistrierung).

Auch hier wird nichts Echtes berührt: winreg ist ein Ersatzmodul im Speicher, Prozesse und Netz werden abgefangen. Nicht prüfbar ohne
echten Laptop: das Schreiben der .lnk-Datei über die Shell (COM), der echte Zertifikatsdialog, Chrome selbst und die Fensterlage.
"""
from __future__ import annotations

import http.client
import socket
import ssl
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wamo_labor_platz_testwelt as tw  # noqa: E402

REPO_CA = Path(__file__).resolve().parent.parent / "infra" / "sokrates2" / "wamo-ca.crt"


@pytest.fixture(autouse=True)
def _fallen(monkeypatch, tmp_path):
    tw.fallen_stellen(monkeypatch, tmp_path / "echt")


class FakeWinreg:
    """winreg im Speicher: nur die Teile, die WinRegistrierung benutzt."""
    HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE = "HKCU", "HKLM"
    KEY_READ, KEY_SET_VALUE = 1, 2
    REG_SZ, REG_DWORD, REG_BINARY = 1, 4, 3

    def __init__(self) -> None:
        self.daten: dict[tuple[str, str], dict[str, tuple[object, int]]] = {("HKCU", "software"): {}, ("HKCU", "software\\policies"): {}}

    @staticmethod
    def _k(hive: str, pfad: str) -> tuple[str, str]:
        return hive, pfad.strip("\\").lower()

    def OpenKey(self, hive, pfad, _res, _zugriff):  # noqa: N802
        if self._k(hive, pfad) not in self.daten:
            raise FileNotFoundError(pfad)
        return _Handle(self._k(hive, pfad))

    def CreateKeyEx(self, hive, pfad, _res, _zugriff):  # noqa: N802
        teile = pfad.strip("\\").split("\\")
        for i in range(1, len(teile) + 1):
            self.daten.setdefault(self._k(hive, "\\".join(teile[:i])), {})
        return _Handle(self._k(hive, pfad))

    def EnumValue(self, k, i):  # noqa: N802
        eintraege = list(self.daten[k.kennung].items())
        if i >= len(eintraege):
            raise OSError("keine weiteren Daten")
        name, (wert, art) = eintraege[i]
        return name, wert, art

    def EnumKey(self, k, i):  # noqa: N802
        vor = k.kennung[1] + "\\"
        kinder = sorted(p[len(vor):] for (h, p) in self.daten if h == k.kennung[0] and p.startswith(vor) and "\\" not in p[len(vor):])
        if i >= len(kinder):
            raise OSError("keine weiteren Daten")
        return kinder[i]

    def SetValueEx(self, k, name, _res, art, wert):  # noqa: N802
        self.daten[k.kennung][name] = (wert, art)

    def DeleteValue(self, k, name):  # noqa: N802
        if name not in self.daten[k.kennung]:
            raise FileNotFoundError(name)
        del self.daten[k.kennung][name]

    def DeleteKey(self, hive, pfad):  # noqa: N802
        kennung = self._k(hive, pfad)
        if any(p.startswith(kennung[1] + "\\") for (h, p) in self.daten if h == hive):
            raise PermissionError("Schlüssel hat Unterschlüssel")
        del self.daten[kennung]


class _Handle:
    def __init__(self, kennung):
        self.kennung = kennung

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def test_winreg_adapter_arbeitet_wie_die_attrappe_bis_zum_rueckweg(tmp_path):
    import wamo_labor_platz_registrierung as rg
    welt = tw.Welt(tmp_path / "w")
    modul = FakeWinreg()
    welt.umg.reg = rg.WinRegistrierung(modul)
    vorher = {k: dict(v) for k, v in modul.daten.items()}
    assert welt.lauf("einrichten", "--kiosk-konto", "--platz", "3") == 0
    assert welt.lauf("status") == 0
    assert modul.daten[("HKCU", "software\\policies\\google\\chrome")]["TranslateEnabled"] == (0, FakeWinreg.REG_DWORD)
    assert modul.daten[("HKCU", "software\\policies\\google\\chrome\\urlblocklist")]["1"] == ("*", FakeWinreg.REG_SZ)
    assert welt.lauf("rueckweg") == 0
    assert modul.daten == vorher


def test_winreg_adapter_meldet_fremde_typen_als_andere_und_fehlende_schluessel_leer():
    import wamo_labor_platz_registrierung as rg
    modul = FakeWinreg()
    reg = rg.WinRegistrierung(modul)
    modul.daten[("HKLM", "x")] = {"bin": (b"\x00", FakeWinreg.REG_BINARY), "text": ("a", FakeWinreg.REG_SZ), "zahl": (7, FakeWinreg.REG_DWORD)}
    assert reg.werte("HKLM", "x") == {"bin": ("andere", b"\x00"), "text": ("sz", "a"), "zahl": ("dword", 7)}
    assert reg.werte("HKLM", "fehlt") == {} and reg.unterschluessel("HKLM", "fehlt") == []
    assert reg.schluessel_vorhanden("HKLM", "x") and not reg.schluessel_vorhanden("HKLM", "fehlt")
    reg.wert_loeschen("HKLM", "fehlt", "egal")  # kein Fehler
    assert reg.leeren_schluessel_loeschen("HKLM", "x") is False and reg.leeren_schluessel_loeschen("HKLM", "fehlt") is False


# ---------- Netzabruf ----------

class _Antwort:
    def __init__(self, status: int, datum: str | None):
        self.status, self._d = status, datum

    def getheader(self, name):
        return self._d if name == "Date" else None

    def read(self, *_a):
        return b""


def _verbindung_bauen(monkeypatch, verhalten):
    aufrufe = []

    class Verbindung:
        def __init__(self, host, port, timeout=None, context=None):
            aufrufe.append({"host": host, "port": port, "timeout": timeout, "context": context})

        def request(self, methode, pfad, headers=None):
            aufrufe[-1].update(methode=methode, pfad=pfad)
            if isinstance(verhalten, Exception):
                raise verhalten

        def getresponse(self):
            return verhalten

        def close(self):
            aufrufe[-1]["zu"] = True

    monkeypatch.setattr(http.client, "HTTPSConnection", Verbindung)
    return aufrufe


def test_abruf_prueft_gegen_die_ca_und_liefert_status_und_datum(monkeypatch):
    import wamo_labor_platz_betrieb as be
    aufrufe = _verbindung_bauen(monkeypatch, _Antwort(404, "Sat, 14 Nov 2026 10:00:00 GMT"))
    antwort = be.abruf_https("https://192.168.178.136:55445/", REPO_CA)
    assert (antwort.status, antwort.datum) == (404, "Sat, 14 Nov 2026 10:00:00 GMT")
    a = aufrufe[0]
    assert (a["host"], a["port"], a["methode"], a["pfad"], a["zu"]) == ("192.168.178.136", 55445, "GET", "/", True)
    assert a["context"].verify_mode == ssl.CERT_REQUIRED and a["context"].check_hostname is True
    assert len(a["context"].get_ca_certs()) == 1  # nur die WAMO-CA, keine Systemzertifikate


@pytest.mark.parametrize("fehler,art", [
    (ssl.SSLCertVerificationError(1, "unable to get local issuer certificate"), "tls"),
    (ssl.SSLError("handshake"), "tls"), (socket.timeout("zu langsam"), "frist"), (ConnectionRefusedError(), "verbindung"),
    (http.client.RemoteDisconnected("weg"), "verbindung"),
])
def test_abruf_fehler_werden_eingeordnet(monkeypatch, fehler, art):
    import wamo_labor_platz_betrieb as be
    from wamo_labor_platz_umgebung import AbrufFehler
    _verbindung_bauen(monkeypatch, fehler)
    with pytest.raises(AbrufFehler) as e:
        be.abruf_https("https://192.168.178.136:55445/", REPO_CA)
    assert e.value.art == art and "token" not in e.value.text.lower()


def test_abruf_mit_fehlender_ca_datei_ist_ein_tls_fehler(tmp_path):
    import wamo_labor_platz_betrieb as be
    from wamo_labor_platz_umgebung import AbrufFehler
    with pytest.raises(AbrufFehler) as e:
        be.abruf_https("https://192.168.178.136:55445/", tmp_path / "gibtsnicht.crt")
    assert e.value.art == "tls"


def test_abruf_lehnt_fremde_adressen_ab(monkeypatch):
    import wamo_labor_platz_betrieb as be
    from wamo_labor_platz_umgebung import AbrufFehler
    aufrufe = _verbindung_bauen(monkeypatch, _Antwort(200, None))
    for url in ("http://192.168.178.136:55445/", "https://example.com/", "https://192.168.178.136:1/"):
        with pytest.raises(AbrufFehler):
            be.abruf_https(url, REPO_CA)
    assert aufrufe == []


# ---------- Prozess, Eingabe, Umgebung ----------

def test_prozess_wird_ohne_shell_ohne_umgebung_und_abgekoppelt_gestartet(monkeypatch):
    import wamo_labor_platz_betrieb as be
    gesehen = {}

    def popen(args, **kwargs):
        gesehen.update(args=args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)
    be.prozess_starten_echt(["chrome.exe", "--app=https://192.168.178.136:55445"])
    assert gesehen["args"] == ["chrome.exe", "--app=https://192.168.178.136:55445"]
    assert not gesehen.get("shell") and gesehen.get("env") is None and gesehen["close_fds"] is True
    assert gesehen["stdin"] == gesehen["stdout"] == gesehen["stderr"] == subprocess.DEVNULL


def test_token_eingabe_ohne_anzeige_und_enter_zum_schliessen(monkeypatch):
    import wamo_labor_platz_betrieb as be
    fragen = []
    monkeypatch.setattr("getpass.getpass", lambda prompt="": fragen.append(prompt) or "wert")
    monkeypatch.setattr("builtins.input", lambda prompt="": fragen.append(prompt) or "")
    assert be.token_eingabe_echt() == "wert" and "nicht angezeigt" in fragen[0]
    be.taste_echt()
    assert len(fragen) == 2


def test_kandidaten_kommen_aus_der_umgebung(monkeypatch, tmp_path):
    import wamo_labor_platz_betrieb as be
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "pf86"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    chrome = be.chrome_kandidaten()
    assert chrome[0] == tmp_path / "pf" / "Google" / "Chrome" / "Application" / "chrome.exe" and len(chrome) == 3
    monkeypatch.setenv("WAMO_CA_FILE", str(tmp_path / "meine.crt"))
    ca = be.ca_kandidaten()
    assert ca[0] == tmp_path / "meine.crt" and ca[-1].name == "wamo-ca.crt" and ca[-1].parent.name == "sokrates2"


def test_echte_umgebung_ist_vollstaendig_und_nutzt_den_festen_port(monkeypatch, tmp_path):
    import wamo_labor_platz_adressen as adr
    import wamo_labor_platz_betrieb as be
    import wamo_labor_platz_registrierung as rg
    monkeypatch.setattr(be, "desktop_ordner", lambda: tmp_path / "desktop")
    monkeypatch.setitem(sys.modules, "winreg", FakeWinreg())  # statt der Falle: der Aufbau holt das Modul, ändert aber nichts
    umg = be.echte_umgebung()
    assert isinstance(umg.reg, rg.WinRegistrierung) and umg.weiter_port == adr.WEITER_PORT
    assert umg.desktop == tmp_path / "desktop" and umg.skript.name == "wamo_labor_platz.py" and umg.python.lower().endswith("python.exe")
    assert str(umg.lokal) == str(tmp_path / "echt" / "heim") and umg.profil.name == "wamo-chrome-labor"
    assert callable(umg.abruf) and callable(umg.verknuepfung) and callable(umg.token_eingabe)
