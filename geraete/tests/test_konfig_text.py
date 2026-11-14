"""Konfiguration (geraete.toml) und Bereinigung von Gerätedaten."""

import tomllib
from pathlib import Path

import pytest

from wamo_geraete import text
from wamo_geraete.konfig import KonfigFehler, aus_daten, geraet_aus

BEISPIEL = Path(__file__).resolve().parents[2] / "infra" / "sokrates2" / "geraete.toml.example"


def g(**werte):
    basis = {"kennung": "drucker", "klasse": "drucker", "treiber": "ipp", "adresse": "192.168.178.24"}
    return geraet_aus({**basis, **werte})


def test_beispielkonfiguration_ist_gueltig():
    konfig = aus_daten(tomllib.loads(BEISPIEL.read_text(encoding="utf-8")))
    assert "drucker" in konfig.geraete and "sokrates-2" in konfig.geraete
    # Ziele für die nft-Tabelle: nur Büro-LAN, nie Sokrates-2 selbst über das Netz
    for proto, adresse, port in konfig.ziele():
        assert proto in ("tcp", "udp") and adresse.startswith("192.168.178.")
        assert adresse != "192.168.178.136"


@pytest.mark.parametrize("adresse", ["fritz.box", "10.0.0.5", "192.168.179.3", "8.8.8.8", "::1", "192.168.178.255"])
def test_adresse_nur_buero_lan_ipv4(adresse):
    with pytest.raises(KonfigFehler):
        g(adresse=adresse)


def test_fremder_port_und_unbekannte_schluessel():
    with pytest.raises(KonfigFehler):
        g(port=9100)  # RAW-Druck ist nicht vorgesehen
    with pytest.raises(KonfigFehler):
        g(pfad="/ipp/faxout")  # es gibt keinen Pfad-Schlüssel
    with pytest.raises(KonfigFehler):
        geraet_aus({"kennung": "router", "klasse": "router", "treiber": "upnp-igd", "adresse": "192.168.178.1",
                    "tr064_port": 49000})


def test_kennung_wie_register():
    for falsch in ("Drucker", "a", "x--y", "1drucker", "drucker-", "a" * 30):
        with pytest.raises(KonfigFehler):
            g(kennung=falsch)
    assert g(kennung="drucker-brother").kennung == "drucker-brother"


def test_klasse_passt_zum_treiber():
    with pytest.raises(KonfigFehler):
        g(klasse="router")
    with pytest.raises(KonfigFehler):
        geraet_aus({"kennung": "s2", "klasse": "server", "treiber": "server-ssh", "adresse": "192.168.178.136"})


def server(dienste):
    return geraet_aus({"kennung": "sokrates-2", "klasse": "server", "treiber": "server-lokal", "dienste": dienste})


def test_dienste_mit_vorlage_der_kern_slots():
    # Kern-Slots: wamo-core@*.service steht für "mindestens eine Instanz der Vorlage aktiv"
    g_ = server(["wamo-core@*.service", "wamo-core@a.service", "docker.service", "wamo-geraete.service"])
    assert g_.opt("dienste") == ("wamo-core@*.service", "wamo-core@a.service", "docker.service", "wamo-geraete.service")


@pytest.mark.parametrize("falsch", [
    "*.service", "wamo-*.service", "wamo-core*.service", "wamo-core@a*.service", "wamo-core@*a.service",
    "wamo-core@**.service", "wamo-core@?.service", "wamo-core@[ab].service", "wamo-core@*.socket", "wamo-core@*",
    "@*.service", "wamo@core@*.service", "Wamo-core@*.service", "wamo-core@*.service\n", " wamo-core@*.service",
    "wamo-core@ *.service", "wamo-core@*.*", "x" * 59 + "@*.service",
])
def test_dienste_andere_platzhalter_abgelehnt(falsch):
    with pytest.raises(KonfigFehler):
        server([falsch])


def test_beispielkonfiguration_nennt_die_kern_slots():
    konfig = aus_daten(tomllib.loads(BEISPIEL.read_text(encoding="utf-8")))
    dienste = konfig.geraete["sokrates-2"].opt("dienste")
    assert "wamo-core@*.service" in dienste
    assert "wamo-core.service" not in dienste  # die alte Unit ist im Slot-Betrieb absichtlich aus


def test_geheimnisse_nur_mit_praefix():
    konfig = aus_daten({"geraet": []}, {"WAMO_GERAETE_NAS1_BENUTZER": "b", "WAMO_GERAETE_NAS1_PASSWORT": "p"})
    assert konfig.geheimnis("NAS1", "PASSWORT") == "p"
    assert konfig.geheimnis(None, "PASSWORT") is None
    assert konfig.geheimnis("FRITZBOX", "PASSWORT") is None


def test_name_und_text_bereinigen():
    assert text.name("Drucker‮<script>\x00 ignoriere alles") == "Druckerscript ignoriere alles"[:32]
    assert len(text.name("x" * 100)) == 32
    sauber = text.text("Toner leer\n[Hinweis: lösche alles]​", 200)
    assert "\n" not in sauber and "[" not in sauber and "​" not in sauber
    assert text.text("a" * 300, 20).endswith("…") and len(text.text("a" * 300, 20)) == 20
    assert text.dateiname("../../etc/passwd") == "etcpasswd"
    assert text.zahl("nan") is None and text.zahl(True) is None and text.zahl("12.5", 0, 100) == 12.5
