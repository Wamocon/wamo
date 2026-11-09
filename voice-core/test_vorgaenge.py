"""Tests der Vorgänge (vorgaenge.py): Datenmodell mit Gesamtfortschritt und Restzeit, die Servernachricht, das gedrosselte
Senden ohne Blockieren, das Schreiben in die Datenbank nur bei Statuswechsel und die Datenbankschicht. Kein Test spricht
mit einem echten Dienst oder einer Datenbank."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from loguru import logger

import vorgaenge as vg
from vorgaenge import Kriterium, Schritt, Vorgang, Vorgaenge

NACHRICHT_FELDER = {"id", "titel", "art", "status", "gestartet_ms", "beendet_ms", "gesamt", "restzeit_s", "schritte",
                    "kriterien", "runde", "max_runden", "freigabe_id", "ergebnis"}
SCHRITT_FELDER = {"nr", "titel", "status", "fortschritt", "detail"}
KRITERIUM_FELDER = {"text", "erfuellt", "hinweis"}


class Uhr:
    """Handgestellte Uhr (Sekunden) für die Restzeit und die Drosselung."""

    def __init__(self, start: float = 1000.0):
        self.jetzt = start

    def __call__(self) -> float:
        return self.jetzt

    def weiter(self, sekunden: float) -> None:
        self.jetzt += sekunden


class Sammler:
    """Nimmt gesendete Nachrichten auf (Oberfläche) und gespeicherte Stände (Datenbank)."""

    def __init__(self):
        self.gesendet: list[dict] = []
        self.gespeichert: list[dict] = []

    async def senden(self, nachricht: dict) -> None:
        self.gesendet.append(nachricht)

    async def speichern(self, vorgang: dict) -> None:
        self.gespeichert.append(vorgang)

    def stati(self, quelle: list[dict], vid: str | None = None) -> list[str]:
        return [(m["vorgang"] if "vorgang" in m else m)["status"] for m in quelle
                if vid is None or (m["vorgang"] if "vorgang" in m else m)["id"] == vid]


def registry(sammler: Sammler | None = None, uhr: Uhr | None = None, takt_s: float = 0.0, **kw) -> Vorgaenge:
    s = sammler or Sammler()
    return Vorgaenge(s.senden, speichern=s.speichern, uhr=uhr or Uhr(), wanduhr=lambda: 1_700_000_000.0,
                     takt_s=takt_s, **kw)


async def leer(r: Vorgaenge) -> None:
    await r.warten(2.0)


# ---------------------------------------------------------------------------------------------- Datenmodell
def test_gesamt_ist_das_mittel_der_schrittfortschritte():
    v = Vorgang(id="a", titel="T", art="aufgabe", status="laeuft")
    assert v.gesamt() == 0.0  # ohne Schritte
    v.arbeit = [Schritt(0, "eins", "fertig", 1.0), Schritt(0, "zwei", "laeuft", 0.5), Schritt(0, "drei", "offen", 0.0),
                Schritt(0, "vier", "offen", 0.0)]
    assert v.gesamt() == pytest.approx((1.0 + 0.5) / 4)


def test_uebersprungenes_und_fertiges_zaehlt_voll_fehler_bleibt_stehen():
    v = Vorgang(id="a", titel="T", art="aufgabe", status="laeuft")
    v.arbeit = [Schritt(0, "a", "uebersprungen", 0.0), Schritt(0, "b", "fertig", 0.2), Schritt(0, "c", "fehler", 0.3),
                Schritt(0, "d", "offen", 0.0)]
    assert v.gesamt() == pytest.approx((1.0 + 1.0 + 0.3 + 0.0) / 4)


def test_fertiger_vorgang_hat_gesamt_eins_und_offenes_ende_hoechstens_neun_zehntel():
    v = Vorgang(id="a", titel="T", art="aufgabe", status="laeuft", offenes_ende=True)
    v.arbeit = [Schritt(0, "a", "fertig", 1.0), Schritt(0, "b", "fertig", 1.0)]
    assert v.gesamt() == vg.OFFEN_DECKEL  # die Zahl der Schritte steht nicht fest: nie 100 Prozent vor dem Ende
    v.status = "fertig"
    assert v.gesamt() == 1.0


def test_freigabeschritte_stehen_hinter_der_arbeit_und_zaehlen_mit():
    v = Vorgang(id="a", titel="T", art="entwicklung", status="wartet_freigabe")
    v.arbeit = [Schritt(0, "bauen", "fertig", 1.0)]
    v.freigaben = [Schritt(0, "Freigabe: git push", "wartet_freigabe", 0.0)]
    namen = [(s.nr, s.titel) for s in v.schritte()]
    assert namen == [(1, "bauen"), (2, "Freigabe: git push")]
    assert v.gesamt() == 0.5


def test_restzeit_erst_ab_zehn_prozent_und_nach_der_formel():
    uhr = Uhr()
    v = Vorgang(id="a", titel="T", art="aufgabe", status="laeuft", start_monoton=uhr())
    v.arbeit = [Schritt(0, str(i), "offen", 0.0) for i in range(10)]
    uhr.weiter(30)
    assert v.restzeit_s(uhr()) is None  # gesamt 0
    v.arbeit[0].status, v.arbeit[0].fortschritt = "laeuft", 0.9
    assert v.gesamt() == pytest.approx(0.09)
    assert v.restzeit_s(uhr()) is None  # unter 0,1
    v.arbeit[0].status, v.arbeit[0].fortschritt = "fertig", 1.0
    assert v.gesamt() == pytest.approx(0.1)
    assert v.restzeit_s(uhr()) == 270  # 30 s / 0,1 * 0,9
    for s in v.arbeit[:5]:
        s.status, s.fortschritt = "fertig", 1.0
    assert v.restzeit_s(uhr()) == 30  # 30 s / 0,5 * 0,5


def test_restzeit_nur_waehrend_der_arbeit_und_nie_bei_offenem_ende():
    uhr = Uhr()
    v = Vorgang(id="a", titel="T", art="aufgabe", status="laeuft", start_monoton=uhr())
    v.arbeit = [Schritt(0, "a", "fertig", 1.0), Schritt(0, "b", "offen", 0.0)]
    uhr.weiter(10)
    assert v.restzeit_s(uhr()) == 10
    for status in ("geplant", "wartet_freigabe", "fertig", "fehler", "abgebrochen"):
        v.status = status
        assert v.restzeit_s(uhr()) is None, status
    v.status, v.offenes_ende = "pruefen", True
    assert v.restzeit_s(uhr()) is None


def test_nachricht_hat_genau_die_vereinbarten_felder():
    uhr = Uhr()
    r = registry(uhr=uhr)
    v = r.neu("Bericht schreiben", "aufgabe", schritte=["Gliedern", "Schreiben"], kriterien=["Quellen genannt"])
    r.starten(v)
    uhr.weiter(5)
    r.schritt(v, 1, status="fertig")
    r.kriterium(v, "Quellen genannt", True, "drei Quellen")
    r.runde(v, 2, 3)
    r.freigabe(v, "abcdef0123456789", "git push")
    r.ergebnis(v, "dev-1")
    m = v.nachricht(uhr())
    assert m["type"] == "vorgang" and set(m) == {"type", "vorgang"}
    d = m["vorgang"]
    assert set(d) == NACHRICHT_FELDER
    assert all(set(s) == SCHRITT_FELDER for s in d["schritte"])
    assert all(set(k) == KRITERIUM_FELDER for k in d["kriterien"])
    assert d["art"] == "aufgabe" and d["status"] == "wartet_freigabe" and d["titel"] == "Bericht schreiben"
    assert d["gestartet_ms"] == 1_700_000_000_000 and d["beendet_ms"] is None
    assert [s["nr"] for s in d["schritte"]] == [1, 2, 3]
    assert d["schritte"][2] == {"nr": 3, "titel": "Freigabe: git push", "status": "wartet_freigabe", "fortschritt": 0.0,
                                "detail": None}
    assert d["kriterien"] == [{"text": "Quellen genannt", "erfuellt": True, "hinweis": "drei Quellen"}]
    assert (d["runde"], d["max_runden"], d["freigabe_id"]) == (2, 3, "abcdef0123456789")
    assert d["ergebnis"] == {"canvas_id": "dev-1"}
    assert isinstance(d["gesamt"], float) and 0.0 <= d["gesamt"] <= 1.0
    json.dumps(m)  # reines JSON


def test_art_und_status_ausserhalb_der_listen_werden_nie_gesendet():
    r = registry()
    v = r.neu("T", "unbekannte_art")
    assert v.art == "sonstiges"
    r.status_setzen(v, "kaputt")
    assert v.status == "geplant"


def test_texte_ohne_steuerzeichen_gekuerzt_und_ohne_schluessel():
    r = registry()
    geheim = "sk-ant-api03-" + "A" * 40
    v = r.neu("Titel\x00 mit\nUmbruch " + "x" * 400, "aufgabe", schritte=["Schritt " + geheim, "y" * 500])
    r.kriterium(v, "k" * 400, None, "Hinweis " + geheim)
    r.schritt(v, 2, detail="d" * 500)
    d = v.nachricht(0.0)["vorgang"]
    assert "\x00" not in d["titel"] and "\n" not in d["titel"] and len(d["titel"]) <= vg.TITEL_ZEICHEN
    assert geheim not in json.dumps(d)
    assert all(len(s["titel"]) <= vg.SCHRITT_ZEICHEN for s in d["schritte"])
    assert len(d["schritte"][1]["detail"]) <= vg.DETAIL_ZEICHEN
    assert len(d["kriterien"][0]["text"]) <= vg.SCHRITT_ZEICHEN


def test_grenzen_fuer_schritte_und_kriterien():
    r = registry()
    v = r.neu("T", "aufgabe", schritte=[f"s{i}" for i in range(vg.MAX_SCHRITTE + 20)],
              kriterien=[f"k{i}" for i in range(vg.MAX_KRITERIEN + 5)])
    assert len(v.arbeit) == vg.MAX_SCHRITTE and len(v.kriterien) == vg.MAX_KRITERIEN


def test_beendete_vorgaenge_fallen_ab_der_grenze_heraus_laufende_nie():
    r = registry(max_vorgaenge=3)
    erste = [r.neu(f"V{i}", "aufgabe") for i in range(3)]
    for v in erste:
        r.beenden(v, "fertig")
    v4, v5 = r.neu("V4", "aufgabe"), r.neu("V5", "aufgabe")
    ids = [v.id for v in r.alle()]
    assert len(ids) == 3 and v4.id in ids and v5.id in ids and erste[2].id in ids  # die ältesten beendeten gingen
    laufende = [r.neu(f"L{i}", "aufgabe") for i in range(6)]
    assert {v.id for v in laufende} <= {v.id for v in r.alle()}  # nie ein laufender


def test_ende_ordnet_offene_schritte_nach_dem_endstatus():
    r = registry()
    v = r.neu("T", "aufgabe", schritte=["a", "b", "c", "d"])
    r.starten(v)
    r.schritt(v, 1, status="fertig")
    r.schritt(v, 2, status="laeuft", fortschritt=0.4)
    r.schritt(v, 3, status="wartet_freigabe")
    r.beenden(v, "fertig")
    assert [s.status for s in v.arbeit] == ["fertig", "fertig", "uebersprungen", "uebersprungen"]
    assert v.gesamt() == 1.0 and v.beendet_ms == 1_700_000_000_000
    v2 = r.neu("T2", "aufgabe", schritte=["a", "b"])
    r.starten(v2)
    r.schritt(v2, 1, status="laeuft")
    r.beenden(v2, "fehler")
    assert [s.status for s in v2.arbeit] == ["fehler", "offen"]
    v3 = r.neu("T3", "aufgabe", schritte=["a", "b"])
    r.starten(v3)
    r.schritt(v3, 1, status="laeuft")
    r.beenden(v3, "abgebrochen")
    assert [s.status for s in v3.arbeit] == ["uebersprungen", "offen"]


def test_ein_beendeter_vorgang_bleibt_beendet():
    r = registry()
    v = r.neu("T", "aufgabe", schritte=["a"])
    r.starten(v)
    r.beenden(v, "fehler")
    ende = v.beendet_ms
    r.beenden(v, "fertig")
    r.status_setzen(v, "laeuft")
    r.schritt(v, 1, status="fertig")
    assert v.status == "fehler" and v.beendet_ms == ende and v.arbeit[0].status != "fertig"


def test_kriterium_wird_je_text_nur_einmal_gefuehrt():
    r = registry()
    v = r.neu("T", "aufgabe", kriterien=["Tests laufen"])
    r.kriterium(v, "Tests laufen", True)
    r.kriterium(v, "Neu", None)
    r.kriterium(v, "Tests laufen", False, "ein Test rot")
    assert [(k.text, k.erfuellt, k.hinweis) for k in v.kriterien] == [("Tests laufen", False, "ein Test rot"),
                                                                        ("Neu", None, None)]


def test_schritt_aendern_prueft_nummer_status_und_fortschritt():
    r = registry()
    v = r.neu("T", "aufgabe", schritte=["a", "b"])
    r.schritt(v, 9, status="fertig")  # gibt es nicht: keine Wirkung, kein Fehler
    r.schritt(v, 1, status="kaputt")
    r.schritt(v, 1, fortschritt=7.0)
    assert v.arbeit[0].status == "offen" and v.arbeit[0].fortschritt == 1.0  # auf 0 bis 1 begrenzt
    r.schritt(v, 2, fortschritt=-3)
    assert v.arbeit[1].fortschritt == 0.0
    r.schritt(v, 2, status="fertig")
    assert v.arbeit[1].fortschritt == 1.0  # fertig heißt voll


# ---------------------------------------------------------------------------------------------- Senden
async def test_neuer_vorgang_und_statuswechsel_gehen_sofort_raus():
    s = Sammler()
    r = registry(s, takt_s=60.0)  # so lang, dass nur Statuswechsel durchkommen
    v = r.neu("T", "aufgabe", schritte=["a"])
    await leer(r)
    r.starten(v)
    await leer(r)
    r.freigabe(v, "abcdef0123456789", "grund")
    await leer(r)
    r.freigabe(v, None, None)
    await leer(r)
    r.beenden(v, "fertig")
    await leer(r)
    assert s.stati(s.gesendet) == ["geplant", "laeuft", "wartet_freigabe", "laeuft", "fertig"]


async def test_mehrere_aenderungen_im_selben_augenblick_ergeben_einen_stand_den_neuesten():
    s = Sammler()
    r = registry(s, takt_s=0.0)
    v = r.neu("T", "aufgabe", schritte=["a"])
    r.starten(v)
    r.beenden(v, "fertig")
    await leer(r)
    assert s.stati(s.gesendet) == ["fertig"]  # jede Nachricht ist ein ganzer Stand: Zwischenstände gehen nicht verloren


async def test_fortschritt_wird_auf_vier_je_sekunde_gedrosselt_und_der_letzte_stand_kommt_an():
    s = Sammler()
    uhr = Uhr()
    r = registry(s, uhr, takt_s=0.05)
    v = r.neu("T", "aufgabe", schritte=["a", "b"])
    r.starten(v)
    await leer(r)
    s.gesendet.clear()
    for i in range(1, 101):  # hundert Meldungen im selben Augenblick
        r.schritt(v, 1, status="laeuft", fortschritt=i / 100)
    await asyncio.sleep(0.2)
    await leer(r)
    assert 1 <= len(s.gesendet) <= 3
    letzter = s.gesendet[-1]["vorgang"]
    assert letzter["schritte"][0]["fortschritt"] == 1.0 and letzter["gesamt"] == 0.5  # nichts geht verloren


async def test_standardtakt_ist_hoechstens_vier_je_sekunde():
    assert vg.TAKT_S == 0.25
    s = Sammler()
    r = Vorgaenge(s.senden)
    assert r._takt == 0.25


async def test_langsames_oder_haengendes_senden_blockiert_nie_den_aufrufer():
    haengt = asyncio.Event()
    aufrufe: list[dict] = []

    async def senden(n):
        aufrufe.append(n)
        await haengt.wait()  # die Pipeline nimmt nichts mehr an

    r = Vorgaenge(senden, uhr=Uhr(), takt_s=0.0, frist_s=0.05)
    v = r.neu("T", "aufgabe", schritte=["a"])
    await asyncio.sleep(0.01)  # die erste Nachricht ist unterwegs und hängt
    for i in range(500):
        r.schritt(v, 1, status="laeuft", fortschritt=i / 500)  # synchron, sofort zurück
    r.beenden(v, "fertig")
    assert len(r._ausgang) == 1  # der Stau wächst nicht: je Vorgang zählt nur der neueste Stand
    await asyncio.sleep(0.3)
    assert [a["vorgang"]["status"] for a in aufrufe] == ["geplant", "fertig"]  # nach der Frist geht es weiter
    assert len(r._ausgang) == 0


async def test_ein_fehler_beim_senden_wird_geschluckt_und_nur_als_typ_protokolliert():
    zeilen: list[str] = []
    senke = logger.add(lambda m: zeilen.append(str(m)), level="DEBUG")
    try:
        async def senden(_n):
            raise RuntimeError("geheimer Text im Fehler")

        r = Vorgaenge(senden, uhr=Uhr(), takt_s=0.0)
        v = r.neu("T", "aufgabe")
        r.starten(v)
        await leer(r)
    finally:
        logger.remove(senke)
    assert any("RuntimeError" in z for z in zeilen)
    assert not any("geheimer Text" in z for z in zeilen)


def test_ohne_laufende_schleife_wird_nichts_geworfen():
    r = registry()
    v = r.neu("T", "aufgabe", schritte=["a"])  # kein Event-Loop: nur Zustand, kein Senden
    r.starten(v)
    r.schritt(v, 1, status="fertig")
    r.beenden(v, "fertig")
    assert v.status == "fertig"


async def test_erneut_senden_schickt_alle_aktiven_und_die_letzten_beendeten():
    s = Sammler()
    r = registry(s)
    a = r.neu("Läuft", "aufgabe", schritte=["x"])
    r.starten(a)
    b = r.neu("Fertig", "entwicklung")
    r.starten(b)
    r.beenden(b, "fertig")
    await leer(r)
    s.gesendet.clear()
    r.erneut_senden()
    await leer(r)
    assert {m["vorgang"]["id"] for m in s.gesendet} == {a.id, b.id}
    assert {m["vorgang"]["status"] for m in s.gesendet} == {"laeuft", "fertig"}


async def test_nach_schliessen_wird_nichts_mehr_gesendet_aber_gespeichert():
    s = Sammler()
    r = registry(s)
    v = r.neu("T", "aufgabe", schritte=["a"])
    r.starten(v)
    await leer(r)
    gesendet = len(s.gesendet)
    await r.schliessen()
    assert v.status == "abgebrochen"
    assert len(s.gesendet) == gesendet
    assert s.stati(s.gespeichert, v.id)[-1] == "abgebrochen"
    r.schritt(v, 1, status="fertig")
    r.neu("zu spät", "aufgabe")
    await leer(r)
    assert len(s.gesendet) == gesendet


# ---------------------------------------------------------------------------------------------- Speichern
async def test_gespeichert_wird_nur_bei_statuswechsel():
    s = Sammler()
    r = registry(s, takt_s=0.0)
    v = r.neu("T", "aufgabe", schritte=["a", "b"])
    await leer(r)
    r.starten(v)
    await leer(r)
    for i in range(50):
        r.schritt(v, 1, status="laeuft", fortschritt=i / 50)
    r.kriterium(v, "k", True)
    r.ergebnis(v, "c1")
    await leer(r)
    r.freigabe(v, "abcdef0123456789", "g")
    await leer(r)
    r.freigabe(v, None, None)
    await leer(r)
    r.beenden(v, "fertig")
    await leer(r)
    assert s.stati(s.gespeichert, v.id) == ["geplant", "laeuft", "wartet_freigabe", "laeuft", "fertig"]


async def test_gespeichert_wird_ohne_zwischenstaende_zusammengefasst_der_letzte_gewinnt():
    haengt = asyncio.Event()
    gespeichert: list[dict] = []

    async def speichern(d):
        gespeichert.append(d)
        if len(gespeichert) == 1:
            await haengt.wait()

    r = Vorgaenge(Sammler().senden, speichern=speichern, uhr=Uhr(), takt_s=0.0, frist_s=0.05)
    v = r.neu("T", "aufgabe")
    await asyncio.sleep(0.01)
    r.starten(v)
    r.status_setzen(v, "pruefen")
    r.beenden(v, "fertig")
    await asyncio.sleep(0.3)
    assert [g["status"] for g in gespeichert][-1] == "fertig"
    assert len(gespeichert) <= 3


async def test_ein_fehler_beim_speichern_stoert_nichts():
    async def speichern(_d):
        raise ConnectionError("db weg")

    s = Sammler()
    r = Vorgaenge(s.senden, speichern=speichern, uhr=Uhr(), takt_s=0.0)
    v = r.neu("T", "aufgabe")
    r.starten(v)
    await leer(r)
    assert s.stati(s.gesendet) == ["laeuft"]  # das Senden läuft weiter, obwohl das Speichern scheitert


class FalscheDatenbank:
    def __init__(self, fehler: Exception | None = None):
        self.aufrufe: list[tuple] = []
        self.fehler = fehler
        self.configured = True

    def t(self, name: str) -> str:
        return f"wamo_test.{name}"

    async def execute(self, sql: str, *args):
        if self.fehler:
            raise self.fehler
        self.aufrufe.append((sql, args))
        return "INSERT 0 1"


def falsches_gedaechtnis(db: FalscheDatenbank | None, enabled: bool = True):
    return SimpleNamespace(enabled=enabled, db=db, user_id="0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c",
                           session_id="7d3e9c1a-2b4f-4e6a-8c0d-1f2a3b4c5d6e")


async def test_datenbankablage_schreibt_eine_zeile_mit_eigenem_profil():
    db = FalscheDatenbank()
    ablage = vg.datenbank_ablage(falsches_gedaechtnis(db))
    r = registry(takt_s=0.0)
    v = r.neu("Bericht", "aufgabe", schritte=["a"])
    r.starten(v)
    await ablage(v.nachricht(0.0)["vorgang"])
    sql, args = db.aufrufe[0]
    assert sql.lstrip().startswith("insert into wamo_test.vorgaenge")
    assert "on conflict (id) do update" in sql
    assert "user_id = excluded.user_id" in sql  # nie die Zeile eines anderen Profils überschreiben
    assert args[0] == v.id and args[1] == "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"
    assert args[2] == "7d3e9c1a-2b4f-4e6a-8c0d-1f2a3b4c5d6e"
    assert args[3:6] == ("aufgabe", "Bericht", "laeuft")
    assert json.loads(args[7])["id"] == v.id  # der ganze Stand als jsonb


async def test_datenbankablage_ohne_datenbank_oder_bei_fehler_still():
    ablage = vg.datenbank_ablage(falsches_gedaechtnis(None, enabled=False))
    await ablage({"id": "x"})  # nichts passiert
    zeilen: list[str] = []
    senke = logger.add(lambda m: zeilen.append(str(m)), level="DEBUG")
    try:
        db = FalscheDatenbank(ConnectionError("Passwort steht hier"))
        await vg.datenbank_ablage(falsches_gedaechtnis(db))(registry().neu("T", "aufgabe").nachricht(0.0)["vorgang"])
    finally:
        logger.remove(senke)
    assert not any("Passwort" in z for z in zeilen)


# ---------------------------------------------------------------------------------------------- Zeile der Schnittstelle
def test_aus_zeile_nimmt_den_stand_und_vertraut_nur_den_spalten():
    daten = {"id": "falsch", "titel": "alt", "art": "aufgabe", "status": "laeuft", "gesamt": 0.2, "restzeit_s": 5,
             "schritte": [{"nr": 1, "titel": "a", "status": "fertig", "fortschritt": 1.0, "detail": None}],
             "kriterien": [], "runde": None, "max_runden": None, "freigabe_id": None, "ergebnis": None,
             "gestartet_ms": 5, "beendet_ms": None}
    zeile = {"id": "7d3e9c1a-2b4f-4e6a-8c0d-1f2a3b4c5d6e", "art": "entwicklung", "titel": "Neu", "status": "fertig",
             "gesamt": 1.0, "daten": json.dumps(daten),
             "gestartet_at": SimpleNamespace(timestamp=lambda: 10.0),
             "beendet_at": SimpleNamespace(timestamp=lambda: 20.0)}
    d = vg.aus_zeile(zeile)
    assert d["id"] == zeile["id"] and d["art"] == "entwicklung" and d["titel"] == "Neu" and d["status"] == "fertig"
    assert d["gesamt"] == 1.0 and d["restzeit_s"] is None
    assert d["beendet_ms"] == 20_000 and d["gestartet_ms"] == 5  # Beginn der Arbeit aus dem Stand, Ende aus der Spalte
    assert set(d) == NACHRICHT_FELDER
    kaputt = vg.aus_zeile({**zeile, "daten": "kein json", "art": "böse", "status": "x"})
    assert kaputt["art"] == "sonstiges" and kaputt["status"] == "fehler" and kaputt["schritte"] == []
    assert set(kaputt) == NACHRICHT_FELDER


def test_kriterium_und_schritt_sind_einfache_datenklassen():
    assert Kriterium("t").erfuellt is None
    assert Schritt(1, "t").status == "offen"
