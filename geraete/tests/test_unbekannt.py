"""Passive Erkennung unbekannter Geräte: Hostliste der FRITZ!Box über TR-064 (Hosts:1), Abgleich mit geraete.toml und
bekannt.toml, Zwischenspeicher, Werkzeuge unbekannt_zaehlen (alle Rollen, nur eine Zahl) und unbekannte_geraete (nur Admin).
Ohne Netz: das TR-064-Gerät ist durch eine Attrappe ersetzt. Namen und MAC-Adressen gehören nie in Audit oder Protokoll."""

import asyncio
import logging
import re
import tomllib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from wamo_geraete import dienst as d
from wamo_geraete import unbekannt
from wamo_geraete.ablage import Ablage
from wamo_geraete.audit import Audit
from wamo_geraete.grenzen import Grenzen
from wamo_geraete.http_klein import Antwort, HttpFehler
from wamo_geraete.konfig import KonfigFehler, aus_daten, bekannt_aus, lade, lade_bekannt
from wamo_geraete.treiber import GeraetFehler, NichtEingerichtet, hostliste, upnp
from wamo_geraete.treiber.hostliste import Host, Hostliste

PIN = ":".join(["AB"] * 32)
GEHEIM = {"WAMO_GERAETE_FRITZBOX_BENUTZER": "wamo-lese", "WAMO_GERAETE_FRITZBOX_PASSWORT": "geheim-pw"}
GERAETE = [
    {"kennung": "router", "klasse": "router", "treiber": "upnp-igd", "adresse": "192.168.178.1", "tr064_port": 49443,
     "tls_sha256": PIN, "zugang": "FRITZBOX"},
    {"kennung": "drucker", "klasse": "drucker", "treiber": "ipp", "adresse": "192.168.178.24"},
    {"kennung": "fernseher", "klasse": "fernseher", "treiber": "lg-webos", "adresse": "192.168.178.125",
     "mac": "00:A1:59:72:7A:92"},
    {"kennung": "imac", "klasse": "mac", "treiber": "anwesenheit", "mdns_name": "iMac.local"},
]
ANNA, BERND = str(uuid.uuid4()), str(uuid.uuid4())


HOSTLISTE_PFAD = "/devicehostlist.lua?sid=0123456789abcdef"


def eintrag(ip="192.168.178.77", mac="11:22:33:44:55:66", name="Fremdgeraet", art="802.11", aktiv="1") -> dict:
    """Ein Eintrag der Hostliste der Box (XML-Datei devicehostlist.lua), Feldnamen wie dort."""
    return {"IPAddress": ip, "MACAddress": mac.upper(), "HostName": name, "InterfaceType": art, "Active": aktiv}


def host(ip="192.168.178.77", mac="11:22:33:44:55:66", name="Fremdgeraet", art="802.11") -> Host:
    return Host(ip, mac, name, art)


def hostliste_xml(eintraege) -> bytes:
    teile = ['<?xml version="1.0" encoding="utf-8"?><List>']
    for i, e in enumerate(eintraege):
        felder = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in e.items() if v is not None)
        teile.append(f"<Item><Index>{i}</Index>{felder}<Port></Port><Speed>0</Speed><Guest>0</Guest></Item>")
    teile.append("</List>")
    return "".join(teile).encode("utf-8")


class Box:
    """Attrappe der FRITZ!Box: der bewährte Weg in zwei Anfragen (SOAP X_AVM-DE_GetHostListPath, dann die XML-Datei)."""

    def __init__(self, eintraege):
        self.eintraege = list(eintraege)
        self.aufrufe: list[tuple[str, dict | None, dict]] = []   # SOAP-Aufrufe
        self.abrufe: list[tuple[str, int, str, dict]] = []       # Abrufe der Datei (Methode, Port, Pfad, Optionen)
        self.immer: Exception | None = None                      # Fehler beim SOAP-Aufruf
        self.datei_fehler: Exception | None = None
        self.status = 200
        self.pfad = HOSTLISTE_PFAD
        self.rumpf: bytes | None = None

    async def aufruf(self, geraet, aktion, werte=None, **kw):
        self.aufrufe.append((aktion, werte, kw))
        await asyncio.sleep(0)
        if self.immer:
            raise self.immer
        if aktion == "tr064_hostliste":
            return {"NewX_AVM-DE_HostListPath": self.pfad}
        raise AssertionError(f"unerwartete Aktion {aktion}")

    async def anfrage(self, adresse, port, methode, pfad, **kw):
        self.abrufe.append((methode, port, pfad, kw))
        await asyncio.sleep(0)
        if self.datei_fehler:
            raise self.datei_fehler
        return Antwort(self.status, {}, self.rumpf if self.rumpf is not None else hostliste_xml(self.eintraege))


@pytest.fixture
def box(monkeypatch):
    b = Box([eintrag(), eintrag(ip="192.168.178.50", mac="aa:bb:cc:00:00:01", name="Laptop-Anna"),
             eintrag(ip="192.168.178.51", mac="aa:bb:cc:00:00:02", name="Altes-Handy", aktiv="0")])
    monkeypatch.setattr(upnp, "aufruf", b.aufruf)
    monkeypatch.setattr(upnp, "anfrage", b.anfrage)
    return b


def konfig(bekannt=None, geheim=GEHEIM):
    k = aus_daten({"geraet": GERAETE}, geheim)
    if bekannt is not None:
        k.bekannt = bekannt_aus(bekannt)
    return k


# ---------- bekannt.toml ----------
def test_bekannt_toml_wird_streng_gelesen():
    b = bekannt_aus({"bekannt": [{"name": "Laptop-Anna", "mac": "AA:BB:CC:00:00:01", "notiz": "Anna"},
                                 {"adresse": "192.168.178.60"}, {"name": "Handy-Bernd"}]})
    assert b.macs == {"aa:bb:cc:00:00:01"} and b.adressen == {"192.168.178.60"} and b.namen == {"laptop-anna", "handy-bernd"}
    assert bekannt_aus(None).macs == frozenset() and bekannt_aus({}).namen == frozenset()


@pytest.mark.parametrize("falsch", [
    {"bekannt": [{}]}, {"bekannt": [{"notiz": "nur Notiz"}]}, {"bekannt": [{"mac": "keine-mac"}]},
    {"bekannt": [{"adresse": "fritz.box"}]}, {"bekannt": [{"adresse": "8.8.8.8"}]}, {"bekannt": [{"name": ""}]},
    {"bekannt": [{"name": "x" * 64}]}, {"bekannt": [{"name": "boese\n"}]}, {"bekannt": [{"mac": "aa:bb:cc:dd:ee:ff", "x": 1}]},
    {"bekannt": ["text"]}, {"bekannt": [{"name": "a"}] * 513}, {"anderes": []}, {"bekannt": [{"name": "a", "notiz": "n" * 121}]},
])
def test_falsche_bekannt_toml_verhindert_den_start(falsch):
    with pytest.raises(KonfigFehler):
        bekannt_aus(falsch)


def test_bekannt_toml_ist_optional_und_wird_von_lade_gelesen(tmp_path):
    assert lade_bekannt(str(tmp_path / "gibt-es-nicht.toml")) == bekannt_aus(None)
    datei = tmp_path / "bekannt.toml"
    datei.write_text('[[bekannt]]\nname = "Laptop-Anna"\nmac = "aa:bb:cc:00:00:01"\n', encoding="utf-8")
    assert lade_bekannt(str(datei)).namen == {"laptop-anna"}
    datei.write_text("[[bekannt\n", encoding="utf-8")
    with pytest.raises(KonfigFehler):
        lade_bekannt(str(datei))
    geraete = tmp_path / "geraete.toml"
    geraete.write_text('[[geraet]]\nkennung = "drucker"\nklasse = "drucker"\ntreiber = "ipp"\nadresse = "192.168.178.24"\n',
                       encoding="utf-8")
    datei.write_text('[[bekannt]]\nname = "Laptop-Anna"\n', encoding="utf-8")
    k = lade(str(geraete), str(tmp_path / "keine.env"), str(datei))
    assert k.bekannt.namen == {"laptop-anna"} and "drucker" in k.geraete


def test_beispieldatei_ist_gueltig():
    beispiel = Path(__file__).resolve().parents[2] / "infra" / "sokrates2" / "bekannt.toml.example"
    b = bekannt_aus(tomllib.loads(beispiel.read_text(encoding="utf-8")))
    assert isinstance(b.macs, frozenset)  # nur Platzhalter in Kommentaren, keine echten Geräte


# ---------- Hostliste über TR-064 ----------
GERAET = konfig().geraete["router"]


async def test_hostliste_nur_aktive_eintraege_und_bereinigte_felder(box):
    box.eintraege[0] = eintrag(ip="192.168.178.77", mac="AA:BB:CC:DD:EE:FF", name="Boese [Gerät]\n" + "x" * 100,
                               art="802.11\x00")
    hosts = (await hostliste.lese(GERAET, konfig())).hosts
    assert [h.ip for h in hosts] == ["192.168.178.77", "192.168.178.50"]  # der inaktive Eintrag fehlt
    h = hosts[0]
    assert h.mac == "aa:bb:cc:dd:ee:ff" and len(h.name) <= 63 and "[" not in h.name and "\n" not in h.name
    assert h.schnittstelle == "802.11"


async def test_hostliste_holt_alles_in_zwei_anfragen_mit_anmeldung_und_zertifikat(box):
    """Der bewährte Weg der Geräteliste: ein SOAP-Aufruf für den Pfad, eine Anfrage für die Datei, unabhängig von der Zahl der Einträge."""
    box.eintraege = [eintrag(ip=f"192.168.178.{i % 200 + 10}", mac=f"aa:bb:cc:00:{i // 256:02x}:{i % 256:02x}", name=f"h{i}")
                     for i in range(200)]
    hosts = (await hostliste.lese(GERAET, konfig())).hosts
    assert len(hosts) == 200
    assert [a for a, _, _ in box.aufrufe] == ["tr064_hostliste"]  # nie GetGenericHostEntry je Eintrag
    assert box.aufrufe[0][2] == {"port": 49443, "tls_sha256": PIN, "digest": ("wamo-lese", "geheim-pw")}
    ((methode, port, pfad, kw),) = box.abrufe
    assert (methode, port, pfad) == ("GET", 49443, HOSTLISTE_PFAD) and kw["tls_sha256"] == PIN
    assert len(box.aufrufe) + len(box.abrufe) == 2


async def test_hostliste_liefert_die_mac_aus_der_liste_und_ohne_mac_bleiben_name_und_adresse(box):
    box.eintraege = [eintrag(ip="192.168.178.60", mac="AA:BB:CC:00:00:09", name="mit-mac"),
                     {**eintrag(ip="192.168.178.61", name="ohne-mac"), "MACAddress": None}]
    hosts = (await hostliste.lese(GERAET, konfig())).hosts
    assert [(h.ip, h.mac, h.name) for h in hosts] == [("192.168.178.60", "aa:bb:cc:00:00:09", "mit-mac"),
                                                       ("192.168.178.61", "", "ohne-mac")]
    k = konfig({"bekannt": [{"mac": "aa:bb:cc:00:00:09"}, {"name": "ohne-mac"}]})
    assert unbekannt.vergleichen(list(hosts), k) == []  # per MAC und, ohne MAC, per Name erkannt


async def test_hostliste_ungueltige_adressen_und_macs_werden_leer(box):
    box.eintraege = [eintrag(ip="8.8.8.8", mac="kaputt", name="a"), eintrag(ip="192.168.178.0/24", mac="aa:bb", name="b"),
                     eintrag(ip="", mac="", name="c")]
    hosts = (await hostliste.lese(GERAET, konfig())).hosts
    assert [(h.ip, h.mac) for h in hosts] == [("", "")] * 3


async def test_hostliste_gleiches_geraet_nur_einmal(box):
    box.eintraege = [eintrag(mac="aa:bb:cc:00:00:09", ip="192.168.178.60", art="Ethernet"),
                     eintrag(mac="AA:BB:CC:00:00:09", ip="192.168.178.60", art="802.11")]
    assert len((await hostliste.lese(GERAET, konfig())).hosts) == 1


async def test_hostliste_xml_zeichen_im_namen_werden_zuerst_aufgeloest(box):
    box.rumpf = hostliste_xml([eintrag(name="Annas Laptop & Co <b> Handy's")]).replace(b"Handy's", b"Handy&apos;s")
    assert b"&amp;" in box.rumpf and b"&lt;b&gt;" in box.rumpf and b"&apos;" in box.rumpf  # so liefert die Box es
    (h,) = (await hostliste.lese(GERAET, konfig())).hosts
    assert h.name == "Annas Laptop Co b Handys"  # erst &amp; zu &, dann entfernt der Namensfilter alles außer A-Z 0-9 Leerzeichen . _ -


async def test_hostliste_leere_liste_ist_eine_liste_eine_anmeldeseite_nicht(box):
    box.rumpf = b'<?xml version="1.0"?><List></List>'
    liste = await hostliste.lese(GERAET, konfig())
    assert liste.hosts == () and liste.abgeschnitten is False
    box.rumpf = b"<html><body>Anmeldung</body></html>"
    with pytest.raises(GeraetFehler):
        await hostliste.lese(GERAET, konfig())


async def test_hostliste_fehler_der_box_sind_fehler_ohne_geraetedaten(box):
    box.status = 500
    with pytest.raises(GeraetFehler):
        await hostliste.lese(GERAET, konfig())
    box.status = 200
    box.datei_fehler = HttpFehler("Antwort zu groß")
    with pytest.raises(GeraetFehler):
        await hostliste.lese(GERAET, konfig())
    box.datei_fehler = None
    abrufe = len(box.abrufe)
    box.pfad = "/anderer/pfad?sid=0123456789abcdef"
    with pytest.raises(GeraetFehler):
        await hostliste.lese(GERAET, konfig())
    assert len(box.abrufe) == abrufe  # ein ungültiger Pfad wird nie abgerufen


async def test_hostliste_anmeldung_abgelehnt_bricht_ab(box):
    box.immer = NichtEingerichtet("Gerät verlangt eine Anmeldung")
    with pytest.raises(NichtEingerichtet):
        await hostliste.lese(GERAET, konfig())
    assert box.abrufe == []


async def test_hostliste_ohne_zugangsdaten_nicht_eingerichtet(box):
    with pytest.raises(NichtEingerichtet):
        await hostliste.lese(konfig(geheim={}).geraete["router"], konfig(geheim={}))
    assert box.aufrufe == [] and box.abrufe == []


async def test_hostliste_zeitgrenze_fuer_den_ganzen_abruf(box, monkeypatch):
    async def haengt(*_a, **_k):
        await asyncio.sleep(5)
    monkeypatch.setattr(hostliste, "LESE_ZEIT", 0.05)
    monkeypatch.setattr(upnp, "anfrage", haengt)
    with pytest.raises(GeraetFehler):
        await hostliste.lese(GERAET, konfig())


async def test_hostliste_zeigt_an_wenn_die_obergrenze_greift(box):
    def mit(n):
        return [eintrag(ip=f"192.168.178.{i % 200 + 10}", mac=f"aa:bb:cc:00:{i // 256:02x}:{i % 256:02x}", name=f"h{i}")
                for i in range(n)]
    box.eintraege = mit(hostliste.MAX_HOSTS)
    liste = await hostliste.lese(GERAET, konfig())
    assert len(liste.hosts) == hostliste.MAX_HOSTS and liste.abgeschnitten is False  # genau die Grenze: nichts fehlt
    box.eintraege = mit(hostliste.MAX_HOSTS + 100)
    liste = await hostliste.lese(GERAET, konfig())
    assert len(liste.hosts) == hostliste.MAX_HOSTS and liste.abgeschnitten is True
    assert liste.hosts[-1].name == f"h{hostliste.MAX_HOSTS - 1}"
    assert len(box.aufrufe) == 2 and len(box.abrufe) == 2  # auch das sind je Abruf nur zwei Anfragen


async def test_geraeteliste_und_hostliste_teilen_denselben_weg_und_parser(box):
    """router_geraete (seit 21.10. im Betrieb) und die Erkennung lesen dieselbe Datei mit demselben Parser."""
    liste = await upnp.router_geraete(GERAET, konfig())
    assert liste == [
        {"name": "Fremdgeraet", "adresse": "192.168.178.77", "aktiv": True, "verbindung": "802.11"},
        {"name": "Laptop-Anna", "adresse": "192.168.178.50", "aktiv": True, "verbindung": "802.11"},
        {"name": "Altes-Handy", "adresse": "192.168.178.51", "aktiv": False, "verbindung": "802.11"},
    ]
    gelesen = (len(box.aufrufe), len(box.abrufe))
    await hostliste.lese(GERAET, konfig())
    assert (len(box.aufrufe) - gelesen[0], len(box.abrufe) - gelesen[1]) == gelesen == (1, 1)
    eintraege, abgeschnitten = await upnp.hostliste_holen(GERAET, konfig())
    assert abgeschnitten is False and eintraege[1]["MACAddress"] == "AA:BB:CC:00:00:01"


async def test_geraeteliste_begrenzt_wie_bisher_auf_300_eintraege(box):
    box.eintraege = [eintrag(ip="192.168.178.9", mac="aa:bb:cc:00:00:01", name=f"h{i}") for i in range(350)]
    assert len(await upnp.router_geraete(GERAET, konfig())) == 300
    eintraege, abgeschnitten = await upnp.hostliste_holen(GERAET, konfig(), maximum=100)
    assert len(eintraege) == 100 and abgeschnitten is True


# ---------- Abgleich ----------
def test_abgleich_nach_adresse_mac_und_name():
    k = konfig({"bekannt": [{"name": "Laptop-Anna", "mac": "aa:bb:cc:00:00:01"}, {"adresse": "192.168.178.88"},
                            {"name": "Handy-Bernd"}]})
    hosts = [
        host("192.168.178.24", "00:00:00:00:00:24", "Drucker"),          # Adresse aus geraete.toml
        host("192.168.178.99", "00:a1:59:72:7a:92", "tv"),               # MAC des Fernsehers aus geraete.toml
        host("192.168.178.136", "00:00:00:00:01:36", "sokrates-2"),      # Sokrates-2 ist immer bekannt
        host("192.168.178.1", "", "fritz.box"),                          # der Router selbst
        host("192.168.178.60", "", "iMac"),                              # mdns_name iMac.local
        host("192.168.178.61", "", "imac.fritz.box"),
        host("192.168.178.50", "aa:bb:cc:00:00:01", "anders"),           # bekannt.toml: MAC
        host("192.168.178.88", "", ""),                                  # bekannt.toml: Adresse
        host("", "", "HANDY-BERND"),                                     # bekannt.toml: Name, Groß- und Kleinschreibung egal
        host("192.168.178.77", "11:22:33:44:55:66", "Fremdgeraet"),      # unbekannt
        host("", "", "Kamera"),                                          # unbekannt
    ]
    assert [(h.ip, h.name) for h in unbekannt.vergleichen(hosts, k)] == [("", "Kamera"), ("192.168.178.77", "Fremdgeraet")]


def test_abgleich_ohne_bekannt_toml_zaehlen_laptops_als_unbekannt():
    erg = unbekannt.vergleichen([host("192.168.178.50", "aa:bb:cc:00:00:01", "Laptop-Anna")], konfig())
    assert len(erg) == 1


def test_oeffentliche_form_hat_nur_vier_felder_und_gekuerzte_werte():
    h = Host("192.168.178.77", "aa:bb:cc:dd:ee:ff", "n" * 63, "802.11")
    assert h.oeffentlich() == {"ip": "192.168.178.77", "mac_vorsatz": "aa:bb:cc", "name": "n" * 24,
                               "schnittstelle": "802.11"}
    assert Host("", "", "", "").oeffentlich() == {"ip": "", "mac_vorsatz": "", "name": "", "schnittstelle": ""}


# ---------- Zwischenspeicher ----------
class Uhr:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Quelle:
    def __init__(self, hosts=None):
        self.hosts = hosts if hosts is not None else [host()]
        self.n = 0
        self.fehler: Exception | None = None
        self.dauer = 0.0
        self.abgeschnitten = False

    async def __call__(self):
        self.n += 1
        if self.dauer:
            await asyncio.sleep(self.dauer)
        if self.fehler:
            raise self.fehler
        return Hostliste(tuple(self.hosts), self.abgeschnitten)


def erkennung(quelle, uhr, **kw):
    jetzt = datetime(2026, 11, 9, 8, 30, 15, tzinfo=timezone.utc)
    return unbekannt.Erkennung(quelle, konfig(), uhr=uhr, jetzt=lambda: jetzt, **kw)


async def test_ergebnis_hoechstens_alle_60_sekunden_neu_holen():
    uhr, q = Uhr(), Quelle()
    e = erkennung(q, uhr)
    r1 = await e.ergebnis()
    assert r1.stand == "2026-11-09T08:30:15Z" and len(r1.unbekannte) == 1
    uhr.t += 59
    assert await e.ergebnis() is r1 and q.n == 1
    uhr.t += 2  # 61 Sekunden: veraltet, gilt aber weiter, bis die neue Messung da ist
    assert await e.ergebnis() is r1
    await e.warten()
    assert q.n == 2


async def test_veraltetes_ergebnis_gilt_hoechstens_10_minuten():
    uhr, q = Uhr(), Quelle()
    e = erkennung(q, uhr)
    await e.ergebnis()
    uhr.t += 601
    q.hosts = [host(), host("192.168.178.78", "11:22:33:44:55:77", "zwei")]
    r = await e.ergebnis()  # zu alt: es wird gewartet
    assert len(r.unbekannte) == 2 and q.n == 2


async def test_gleichzeitige_aufrufe_teilen_sich_eine_messung():
    q = Quelle()
    q.dauer = 0.05
    e = erkennung(q, Uhr())
    ergebnisse = await asyncio.gather(*(e.ergebnis() for _ in range(6)))
    assert q.n == 1 and all(r is ergebnisse[0] for r in ergebnisse)


async def test_abbruch_des_aufrufers_bricht_die_messung_nicht_ab():
    q = Quelle()
    q.dauer = 0.1
    e = erkennung(q, Uhr())
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(e.ergebnis(), 0.01)
    await e.warten()
    assert (await e.ergebnis()).unbekannte and q.n == 1


async def test_fehler_wird_30_sekunden_lang_nicht_neu_versucht():
    uhr, q = Uhr(), Quelle()
    q.fehler = GeraetFehler("Gerät nicht erreichbar")
    e = erkennung(q, uhr)
    with pytest.raises(GeraetFehler):
        await e.ergebnis()
    with pytest.raises(GeraetFehler):
        await e.ergebnis()
    assert q.n == 1
    uhr.t += 31
    q.fehler = None
    assert (await e.ergebnis()).unbekannte and q.n == 2


async def test_nicht_eingerichtet_bleibt_nicht_eingerichtet():
    q = Quelle()
    q.fehler = NichtEingerichtet("TR-064 ist nicht eingerichtet")
    e = erkennung(q, Uhr())
    with pytest.raises(NichtEingerichtet):
        await e.ergebnis()
    with pytest.raises(NichtEingerichtet):
        await e.ergebnis()


async def test_fehler_im_hintergrund_laesst_das_alte_ergebnis_stehen():
    uhr, q = Uhr(), Quelle()
    e = erkennung(q, uhr)
    r1 = await e.ergebnis()
    uhr.t += 61
    q.fehler = GeraetFehler("weg")
    assert await e.ergebnis() is r1
    await e.warten()
    assert await e.ergebnis() is r1 and q.n == 2  # Wartezeit nach dem Fehler: kein dritter Versuch
    uhr.t += 31
    await e.ergebnis()
    await e.warten()
    assert q.n == 3


# ---------- Werkzeuge im Dienst ----------
@pytest.fixture
def dienst(tmp_path, box):
    k = konfig({"bekannt": [{"name": "Laptop-Anna", "mac": "aa:bb:cc:00:00:01"}]})
    return d.Dienst(k, ablage=Ablage(str(tmp_path / "ablage")), audit=Audit(str(tmp_path / "audit.jsonl")), grenzen=Grenzen())


def anfrage(werkzeug, rolle="mitglied", user=ANNA, **argumente):
    return {"v": 1, "werkzeug": werkzeug, "argumente": argumente,
            "kontext": {"user_id": user, "rolle": rolle, "session_id": str(uuid.uuid4()), "name": "Anna"}}


async def test_zaehlen_liefert_nur_zahl_und_zeitstempel_fuer_alle_rollen(dienst):
    for rolle in ("mitglied", "admin"):
        antwort, anhang = await dienst.bearbeiten(anfrage("unbekannt_zaehlen", rolle))
        assert set(antwort) == {"status", "anzahl", "stand"} and anhang == b""
        assert antwort["status"] == "success" and antwort["anzahl"] == 1  # Laptop-Anna ist bekannt, "Fremdgeraet" nicht
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", antwort["stand"])


async def test_liste_nur_fuer_admin_mit_vier_feldern(dienst):
    antwort, _ = await dienst.bearbeiten(anfrage("unbekannte_geraete", "mitglied"))
    assert antwort["status"] == "rejected" and "geraete" not in antwort
    antwort, _ = await dienst.bearbeiten(anfrage("unbekannte_geraete", "admin"))
    assert antwort["status"] == "success" and antwort["anzahl"] == 1
    assert antwort["geraete"] == [{"ip": "192.168.178.77", "mac_vorsatz": "11:22:33", "name": "Fremdgeraet",
                                   "schnittstelle": "802.11"}]


async def test_liste_ist_begrenzt(dienst, box):
    box.eintraege = [eintrag(ip=f"192.168.178.{i % 90 + 160}", mac=f"aa:bb:dd:00:{i // 256:02x}:{i % 256:02x}", name=f"h{i}")
                     for i in range(150)]
    antwort, _ = await dienst.bearbeiten(anfrage("unbekannte_geraete", "admin"))
    assert antwort["anzahl"] == 150 and len(antwort["geraete"]) == d.MAX_UNBEKANNTE


async def test_abgeschnitten_steht_in_den_antworten_nur_wenn_die_grenze_greift(tmp_path, box):
    k = konfig()

    def neu(ordner: str) -> d.Dienst:
        return d.Dienst(k, ablage=Ablage(str(tmp_path / ordner)), audit=Audit(None), grenzen=Grenzen())

    antwort, _ = await neu("a").bearbeiten(anfrage("unbekannt_zaehlen"))
    assert antwort["status"] == "success" and "abgeschnitten" not in antwort
    box.eintraege = [eintrag(ip=f"192.168.178.{i % 90 + 160}", mac=f"aa:bb:dd:00:{i // 256:02x}:{i % 256:02x}", name=f"h{i}")
                     for i in range(hostliste.MAX_HOSTS + 20)]
    zaehlen, _ = await neu("b").bearbeiten(anfrage("unbekannt_zaehlen"))
    liste, _ = await neu("c").bearbeiten(anfrage("unbekannte_geraete", "admin"))
    assert zaehlen["abgeschnitten"] is True and liste["abgeschnitten"] is True
    assert zaehlen["anzahl"] == liste["anzahl"] == hostliste.MAX_HOSTS  # gezählt wird, was gelesen wurde


async def test_zwischenspeicher_ein_abruf_fuer_beide_werkzeuge_und_alle_profile(dienst, box):
    await dienst.bearbeiten(anfrage("unbekannt_zaehlen"))
    await dienst.bearbeiten(anfrage("unbekannt_zaehlen", user=BERND))
    await dienst.bearbeiten(anfrage("unbekannte_geraete", "admin"))
    assert len(box.aufrufe) == 1 and len(box.abrufe) == 1  # ein Abruf in zwei Anfragen für alle Werkzeuge und Profile


async def test_ohne_router_oder_zugang_nicht_eingerichtet(tmp_path):
    ohne_router = aus_daten({"geraet": [GERAETE[1]]})
    dienst = d.Dienst(ohne_router, ablage=Ablage(str(tmp_path / "a")), audit=Audit(None), grenzen=Grenzen())
    antwort, _ = await dienst.bearbeiten(anfrage("unbekannt_zaehlen"))
    assert antwort["status"] == "nicht_eingerichtet" and "anzahl" not in antwort
    ohne_zugang = d.Dienst(konfig(geheim={}), ablage=Ablage(str(tmp_path / "b")), audit=Audit(None), grenzen=Grenzen())
    antwort, _ = await ohne_zugang.bearbeiten(anfrage("unbekannt_zaehlen"))
    assert antwort["status"] == "nicht_eingerichtet"


async def test_fehler_der_box_ist_ein_fehler_ohne_geraetedaten(dienst, box):
    box.immer = GeraetFehler("Gerät nicht erreichbar (OSError)")
    antwort, _ = await dienst.bearbeiten(anfrage("unbekannt_zaehlen"))
    assert antwort["status"] == "error" and "anzahl" not in antwort


async def test_grenze_je_profil(dienst):
    for _ in range(12):
        antwort, _ = await dienst.bearbeiten(anfrage("unbekannt_zaehlen"))
        assert antwort["status"] == "success"
    antwort, _ = await dienst.bearbeiten(anfrage("unbekannt_zaehlen"))
    assert antwort["status"] == "grenze"
    antwort, _ = await dienst.bearbeiten(anfrage("unbekannt_zaehlen", user=BERND))
    assert antwort["status"] == "success"


async def test_audit_und_protokoll_ohne_namen_macs_und_adressen(dienst, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    await dienst.bearbeiten(anfrage("unbekannt_zaehlen"))
    await dienst.bearbeiten(anfrage("unbekannte_geraete", "admin"))
    await dienst.bearbeiten(anfrage("unbekannte_geraete", "mitglied"))
    audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "unbekannt_zaehlen" in audit and "unbekannte_geraete" in audit
    for geheim in ("Fremdgeraet", "Laptop-Anna", "11:22:33", "aa:bb:cc", "192.168.178.77", "192.168.178.50"):
        assert geheim not in audit and geheim not in caplog.text, geheim


async def test_geraeteliste_nennt_die_faehigkeit_nur_dem_admin(dienst):
    for rolle, soll in (("admin", True), ("mitglied", False)):
        antwort, _ = await dienst.bearbeiten(anfrage("geraete", rolle))
        router = next(x for x in antwort["geraete"] if x["kennung"] == "router")
        assert ("unbekannte_geraete" in router["faehigkeiten"]) is soll
        assert "unbekannt_zaehlen" not in router["faehigkeiten"]


def test_werkzeugtabelle_risiko_und_rollen():
    z, g = d.WERKZEUGE["unbekannt_zaehlen"], d.WERKZEUGE["unbekannte_geraete"]
    assert z.risiko == g.risiko == "read" and z.klassen is None and g.klassen is None
    assert z.rollen == d.ROLLEN and g.rollen == frozenset({"admin"})
    assert z.grenze == g.grenze == "unbekannt"
