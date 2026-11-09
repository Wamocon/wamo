"""FRITZ!Box-Status über TR-064 (lesend) als wählbare Quelle neben UPnP. Standard bleibt upnp, die Felder sind dieselben."""

import pytest

from wamo_geraete.http_klein import Antwort
from wamo_geraete.konfig import KonfigFehler, aus_daten, geraet_aus
from wamo_geraete.treiber import GeraetFehler, NichtEingerichtet, upnp

PIN = ":".join(["AB"] * 32)
GEHEIM = {"WAMO_GERAETE_FRITZBOX_BENUTZER": "wamo-lese", "WAMO_GERAETE_FRITZBOX_PASSWORT": "geheim-pw"}
ROUTER = {"kennung": "router", "klasse": "router", "treiber": "upnp-igd", "adresse": "192.168.178.1"}
TR064 = {"tr064_port": 49443, "tls_sha256": PIN, "zugang": "FRITZBOX"}

LINK = {"NewWANAccessType": "DSL", "NewLayer1UpstreamMaxBitRate": "20000000", "NewLayer1DownstreamMaxBitRate": "86400000",
        "NewPhysicalLinkStatus": "Up"}
VERBINDUNG = {"NewConnectionStatus": "Connected", "NewUptime": "360000"}
GESENDET = {"NewTotalBytesSent": "1000000000"}
EMPFANGEN = {"NewTotalBytesReceived": "2000000000"}
ERWARTET = {"erreichbar": True, "internet": "verbunden", "leitung": "steht", "zugang": "DSL", "laufzeit_stunden": 100.0,
            "hoch_mbit": 20.0, "runter_mbit": 86.4, "gesendet_gb": 1.0, "empfangen_gb": 2.0}


def konfig(**router):
    return aus_daten({"geraet": [{**ROUTER, **router}]}, GEHEIM)


@pytest.fixture
def soap(monkeypatch):
    """Ersetzt upnp.aufruf: liefert je Aktion feste Werte und merkt sich die Aufrufe."""
    aufrufe = []
    werte = {"router_link": LINK, "router_verbindung": VERBINDUNG, "router_gesendet": GESENDET,
             "router_empfangen": EMPFANGEN, "tr064_link": LINK, "tr064_verbindung": VERBINDUNG,
             "tr064_gesendet": GESENDET, "tr064_empfangen": EMPFANGEN}

    async def aufruf(geraet, aktion, argumente=None, **kw):
        aufrufe.append((aktion, kw))
        return werte[aktion]
    monkeypatch.setattr(upnp, "aufruf", aufruf)
    return aufrufe


# ---------- Konfiguration ----------
def test_standardquelle_ist_upnp():
    g = konfig().geraete["router"]
    assert g.opt("status_quelle", "upnp") == "upnp"
    assert konfig(**TR064, status_quelle="upnp").geraete["router"].opt("status_quelle") == "upnp"
    assert konfig(**TR064, status_quelle="tr064").geraete["router"].opt("status_quelle") == "tr064"


@pytest.mark.parametrize("falsch", [{"status_quelle": "soap"}, {"status_quelle": "TR064"}, {"status_quelle": True}])
def test_unbekannte_quelle_verhindert_den_start(falsch):
    with pytest.raises(KonfigFehler):
        geraet_aus({**ROUTER, **TR064, **falsch})


@pytest.mark.parametrize("fehlt", ["tr064_port", "tls_sha256", "zugang"])
def test_tr064_als_quelle_braucht_port_zertifikat_und_zugang(fehlt):
    daten = {k: v for k, v in TR064.items() if k != fehlt}
    with pytest.raises(KonfigFehler):
        geraet_aus({**ROUTER, **daten, "status_quelle": "tr064"})


# ---------- upnp bleibt wie es war ----------
async def test_upnp_ohne_anmeldung_wie_bisher(soap):
    k = konfig()
    assert await upnp.router_status(k.geraete["router"]) == ERWARTET
    assert await upnp.router_status(k.geraete["router"], k) == ERWARTET
    assert {a for a, _ in soap} == {"router_link", "router_verbindung", "router_gesendet", "router_empfangen"}
    assert all(kw == {} for _, kw in soap)  # kein Port, kein Zertifikat, keine Anmeldung


async def test_tr064_standard_aendert_nichts_auch_wenn_tr064_eingerichtet_ist(soap):
    k = konfig(**TR064)
    assert await upnp.router_status(k.geraete["router"], k) == ERWARTET
    assert {a for a, _ in soap} == {"router_link", "router_verbindung", "router_gesendet", "router_empfangen"}


# ---------- tr064 ----------
async def test_tr064_liefert_dieselben_felder(soap):
    k = konfig(**TR064, status_quelle="tr064")
    erg = await upnp.router_status(k.geraete["router"], k)
    assert erg == ERWARTET
    assert set(erg) == set(ERWARTET)


async def test_tr064_nutzt_nur_lesende_wan_aktionen_mit_anmeldung_und_zertifikat(soap):
    k = konfig(**TR064, status_quelle="tr064")
    await upnp.router_status(k.geraete["router"], k)
    assert {a for a, _ in soap} == {"tr064_link", "tr064_verbindung", "tr064_gesendet", "tr064_empfangen"}
    for _, kw in soap:
        assert kw == {"port": 49443, "tls_sha256": PIN, "digest": ("wamo-lese", "geheim-pw")}


async def test_tr064_ohne_zugangsdaten_nicht_eingerichtet(soap):
    k = aus_daten({"geraet": [{**ROUTER, **TR064, "status_quelle": "tr064"}]}, {})
    with pytest.raises(NichtEingerichtet):
        await upnp.router_status(k.geraete["router"], k)
    assert soap == []
    k = konfig(**TR064, status_quelle="tr064")
    with pytest.raises(NichtEingerichtet):  # ohne Konfiguration weiß der Treiber nichts von den Zugangsdaten
        await upnp.router_status(k.geraete["router"])


async def test_tr064_fehler_des_routers_bleiben_fehler(monkeypatch):
    async def kaputt(*_a, **_k):
        raise GeraetFehler("Gerät nicht erreichbar")
    monkeypatch.setattr(upnp, "aufruf", kaputt)
    k = konfig(**TR064, status_quelle="tr064")
    with pytest.raises(GeraetFehler):
        await upnp.router_status(k.geraete["router"], k)


# ---------- SOAP-Aktionen ----------
def test_tr064_aktionen_sind_feste_lesende_aktionen():
    erwartet = {
        "tr064_link": ("/upnp/control/wancommonifconfig1", "urn:dslforum-org:service:WANCommonInterfaceConfig:1",
                       "GetCommonLinkProperties"),
        "tr064_gesendet": ("/upnp/control/wancommonifconfig1", "urn:dslforum-org:service:WANCommonInterfaceConfig:1",
                           "GetTotalBytesSent"),
        "tr064_empfangen": ("/upnp/control/wancommonifconfig1", "urn:dslforum-org:service:WANCommonInterfaceConfig:1",
                            "GetTotalBytesReceived"),
        "tr064_verbindung": ("/upnp/control/wanipconnection1", "urn:dslforum-org:service:WANIPConnection:1",
                             "GetStatusInfo"),
        "tr064_hostliste": ("/upnp/control/hosts", "urn:dslforum-org:service:Hosts:1", "X_AVM-DE_GetHostListPath"),
    }
    for schluessel, (pfad, dienst, aktion) in erwartet.items():
        assert upnp.AKTIONEN[schluessel][:3] == (pfad, dienst, aktion), schluessel
    assert upnp.AKTIONEN["tr064_hostliste"][3] == () and upnp.AKTIONEN["tr064_link"][3] == ()
    # Die Hostliste gibt es nur als Datei in zwei Anfragen: Einzelabfragen je Eintrag (GetGenericHostEntry) fehlen
    assert not [k for k, (_p, _d, a, _n) in upnp.AKTIONEN.items() if a in ("GetGenericHostEntry", "GetHostNumberOfEntries")]


async def test_tr064_anfrage_geht_an_den_richtigen_pfad_mit_der_richtigen_aktion(monkeypatch):
    gesehen = []

    async def anfrage(adresse, port, methode, pfad, **kw):
        gesehen.append((adresse, port, methode, pfad, kw))
        return Antwort(200, {}, b"<s:Envelope><s:Body><u:R><NewConnectionStatus>Connected</NewConnectionStatus>"
                                b"<NewUptime>7200</NewUptime></u:R></s:Body></s:Envelope>")
    monkeypatch.setattr(upnp, "anfrage", anfrage)
    k = konfig(**TR064, status_quelle="tr064")
    werte = await upnp.aufruf(k.geraete["router"], "tr064_verbindung", port=49443, tls_sha256=PIN, digest=("u", "p"))
    adresse, port, methode, pfad, kw = gesehen[0]
    assert (adresse, port, methode, pfad) == ("192.168.178.1", 49443, "POST", "/upnp/control/wanipconnection1")
    assert kw["kopf"]["SOAPAction"] == '"urn:dslforum-org:service:WANIPConnection:1#GetStatusInfo"'
    assert kw["tls_sha256"] == PIN and kw["digest"] == ("u", "p") and kw["tls"] is not None
    assert werte == {"NewConnectionStatus": "Connected", "NewUptime": "7200"}
