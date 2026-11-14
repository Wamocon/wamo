"""Tests für stimme_sitzung.py: Laden, Client-Nachrichten (start, abbrechen, loeschen), Einwilligung, Labor aus.

Ausführen:  python -m pytest -q test_stimme_sitzung.py   (im Ordner voice-core/)

Datenbank und Modell sind Ersatz (FakeDb aus test_stimme_speicher.py, Rechner wie in test_stimme_einlernen.py).
"""

import asyncio
import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import asyncpg
import numpy as np

import labor
import memory as m
import stimme_sitzung as ss
from auth_gate import Identity
from stimme_einlernen import ZIEL_SECS
from stimme_einwilligung import EINWILLIGUNG_TEXT, EINWILLIGUNG_VERSION
from stimme_gate import EIGEN, StimmGate
from stimme_modell import DIM, als_vektor
from test_stimme_speicher import FakeDb

USER_A = "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"
USER_B = "5b3f1c2d-8e7a-4b6c-9d0e-1f2a3b4c5d6e"
EIGENER = np.eye(DIM, dtype=np.float32)[0]
RATE = 16000


class Rechner:
    def embed(self, pcm, rate):
        return EIGENER


class Modell:
    def __init__(self, bereit: bool = True):
        self.bereit = bereit
        self.gescheitert = not bereit
        self.aufrufe = 0

    async def get(self):
        self.aufrufe += 1
        return Rechner() if self.bereit else None


class Uhr:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def profil(user: str = USER_A, rolle: str = "mitglied") -> Identity:
    return Identity(user, "Erika", rolle, "du")


def neu(db, *, modus="streng", user=USER_A, modell=None, identity=None):
    uhr = Uhr()
    gesendet: list[dict] = []

    async def senden(daten):
        gesendet.append(daten)

    sagen = mock.AsyncMock()
    gate = StimmGate(modus=modus, uhr=uhr)
    s = ss.StimmSitzung.fuer(identity or profil(user), m.MemoryStore(user, db), senden=senden, sagen=sagen, modell=modell or Modell(),
                             gate=gate, uhr=uhr)
    s.pruefer.push_frame = mock.AsyncMock(side_effect=lambda f, *a: gesendet.append(getattr(f, "data", None)))
    return s, gesendet, uhr, sagen


def arten(gesendet, art):
    return [x for x in gesendet if x and x.get("type") == art]


class LaborTests(unittest.TestCase):
    def test_laborprofile_bekommen_nie_eine_stimmsitzung(self):
        for art in ("platz", "schaufenster"):
            identity = Identity(USER_A, "Gast", "mitglied", "du", labor_art=art, labor_platz=3 if art == "platz" else None, labor_aktiv=True)
            s = ss.StimmSitzung.fuer(identity, m.MemoryStore(USER_A, FakeDb()), senden=mock.AsyncMock(), sagen=mock.AsyncMock())
            self.assertIsNone(s, art)

    def test_die_nachricht_stimmabdruck_wird_fuer_laborprofile_abgewiesen(self):
        """Beide Schichten der Laborsperre: vor dem RTVI-Verarbeiter und im Handler. Nur die Freigabe mit job labor kommt durch."""
        for art in ("platz", "schaufenster"):
            for aktion in ({"aktion": "start", "einwilligung": "stimme-1"}, {"aktion": "loeschen"}, {"aktion": "abbrechen"}):
                nachricht = {"label": labor.RTVI_LABEL, "type": "client-message", "id": "1", "data": {"t": "stimmabdruck", "d": aktion}}
                self.assertFalse(labor.rtvi_nachricht_erlaubt(art, nachricht), (art, aktion))
                self.assertFalse(labor.client_nachricht_erlaubt(art, "stimmabdruck", aktion), (art, aktion))

    def test_laborprofil_hat_keine_stimmpruefung_auch_nicht_mit_schalter(self):
        with mock.patch.dict(os.environ, {"WAMO_STIMME_PRUEFUNG": "streng"}):
            identity = Identity(USER_A, "Gast", "mitglied", "du", labor_art="platz", labor_platz=3, labor_aktiv=True)
            self.assertTrue(labor.ist_labor(identity))
            self.assertIsNone(ss.StimmSitzung.fuer(identity, m.MemoryStore(USER_A, FakeDb()), senden=mock.AsyncMock(), sagen=mock.AsyncMock()))

    def test_status_aus_fuer_die_oberflaeche_im_labor(self):
        self.assertEqual(ss.nachricht_aus(), {"type": "stimme", "status": "aus", "modus": "aus"})


class VorbereitenTests(unittest.IsolatedAsyncioTestCase):
    async def test_mit_abdruck_wird_modell_geladen_und_status_bereit(self):
        db = FakeDb({USER_A: als_vektor(EIGENER)})
        modell = Modell()
        s, gesendet, _, _ = neu(db, modell=modell)
        await s.vorbereiten()
        self.assertEqual(s.gate.status, "bereit")
        self.assertEqual(arten(gesendet, "stimme")[-1], {"type": "stimme", "status": "bereit", "modus": "streng"})
        self.assertEqual(modell.aufrufe, 1)

    async def test_ohne_abdruck_status_fehlt_und_das_modell_wird_nicht_geladen(self):
        modell = Modell()
        s, gesendet, _, _ = neu(FakeDb(), modell=modell)
        await s.vorbereiten()
        self.assertEqual(s.gate.status, "fehlt")
        self.assertEqual(modell.aufrufe, 0)
        self.assertEqual(arten(gesendet, "stimme")[-1]["status"], "fehlt")

    async def test_ohne_abdruck_wird_nichts_gesperrt(self):
        s, _, _, _ = neu(FakeDb())
        await s.vorbereiten()
        self.assertIsNone(await s.gate.pruefen("programm_oeffnen"))

    async def test_modell_fehlt_bei_vorhandenem_abdruck_ist_kaputt_und_sperrt_streng(self):
        s, gesendet, _, _ = neu(FakeDb({USER_A: als_vektor(EIGENER)}), modell=Modell(bereit=False))
        await s.vorbereiten()
        self.assertEqual(s.gate.status, "kaputt")
        self.assertIsNotNone(await s.gate.pruefen("programm_oeffnen"))

    async def test_datenbankfehler_wird_wiederholt_dann_kaputt(self):
        db = FakeDb(fehler=asyncpg.PostgresError("kaputt"))
        s, gesendet, _, _ = neu(db)
        with mock.patch.object(ss.asyncio, "sleep", mock.AsyncMock()):
            await s.vorbereiten()
        self.assertEqual(len(db.calls), ss.LADE_VERSUCHE)
        self.assertEqual(s.gate.status, "kaputt")
        self.assertEqual(arten(gesendet, "stimme")[-1]["status"], "kaputt")

    async def test_zweiter_versuch_gelingt(self):
        db = FakeDb({USER_A: als_vektor(EIGENER)})
        echtes = db.fetchval
        zaehler = {"n": 0}

        async def mal_kaputt(sql, *args):
            zaehler["n"] += 1
            if zaehler["n"] == 1:
                raise asyncpg.PostgresError("kurz weg")
            return await echtes(sql, *args)

        db.fetchval = mal_kaputt
        s, _, _, _ = neu(db)
        with mock.patch.object(ss.asyncio, "sleep", mock.AsyncMock()):
            await s.vorbereiten()
        self.assertEqual(s.gate.status, "bereit")

    async def test_modus_aus_fragt_die_datenbank_nicht(self):
        db = FakeDb({USER_A: als_vektor(EIGENER)})
        s, gesendet, _, _ = neu(db, modus="aus")
        await s.vorbereiten()
        self.assertEqual(db.calls, [])
        self.assertEqual(arten(gesendet, "stimme")[-1], {"type": "stimme", "status": "aus", "modus": "aus"})

    async def test_ohne_datenbank_ist_die_stimmpruefung_aus(self):
        s, gesendet, _, _ = neu(None)
        await s.vorbereiten()
        self.assertEqual(s.gate.status, "aus")
        self.assertIsNone(await s.gate.pruefen("programm_oeffnen"))

    async def test_unerwartete_ausnahme_ist_kaputt_und_wirft_nicht(self):
        db = FakeDb()
        db.fetchval = mock.AsyncMock(side_effect=RuntimeError("unerwartet"))
        s, _, _, _ = neu(db)
        with mock.patch.object(ss.asyncio, "sleep", mock.AsyncMock()):
            await s.vorbereiten()
        self.assertEqual(s.gate.status, "kaputt")

    async def test_zwei_profile_laden_je_ihren_abdruck_und_teilen_nur_das_modell(self):
        andere = np.eye(DIM, dtype=np.float32)[9]
        db = FakeDb({USER_A: als_vektor(EIGENER), USER_B: als_vektor(andere)})
        modell = Modell()
        a, _, _, _ = neu(db, user=USER_A, modell=modell)
        b, _, _, _ = neu(db, user=USER_B, modell=modell)
        await asyncio.gather(a.vorbereiten(), b.vorbereiten())
        self.assertAlmostEqual(float(np.dot(a.gate.abdruck, EIGENER)), 1.0, places=4)
        self.assertAlmostEqual(float(np.dot(b.gate.abdruck, andere)), 1.0, places=4)
        self.assertIsNot(a.gate, b.gate)
        a.gate.eintragen(EIGEN)
        self.assertIsNone(a.gate.grund_der_stimme())
        self.assertEqual(b.gate.grund_der_stimme(), "Stimme noch nicht erkannt")


class NachladenTests(unittest.IsolatedAsyncioTestCase):
    """Scheiterte das Laden beim Start, blieb der Stand bis zum Sitzungsende unbekannt (kaputt), und der Modus streng sperrte Werkzeuge per
    Sprache, auch für ein Profil ohne Abdruck. laden_bis_bekannt liest den Stand deshalb mit wachsender Pause neu, bis er bekannt ist."""

    async def lauf(self, db, *, heil_nach: int, modell=None):
        s, gesendet, _, _ = neu(db, modell=modell)
        pausen: list[float] = []

        async def schlafen(secs):
            if secs >= ss.NACHLADEN_SECS[0]:  # die kurzen Pausen zwischen den drei Versuchen eines Ladens zählen nicht
                pausen.append(secs)
                if len(pausen) == heil_nach:
                    db.fehler = None  # die Datenbank ist wieder da

        with mock.patch.object(ss.asyncio, "sleep", schlafen):
            await asyncio.wait_for(s.laden_bis_bekannt(), 5.0)
        return s, gesendet, pausen

    async def test_ohne_abdruck_erholt_sich_der_stand_und_nichts_bleibt_gesperrt(self):
        s, gesendet, pausen = await self.lauf(FakeDb(fehler=asyncpg.PostgresError("weg")), heil_nach=3)
        self.assertEqual(s.gate.status, "fehlt")
        self.assertIsNone(await s.gate.pruefen("programm_oeffnen"))
        self.assertEqual([x["status"] for x in arten(gesendet, "stimme")][-2:], ["kaputt", "fehlt"])
        self.assertEqual(pausen, list(ss.NACHLADEN_SECS[:3]))
        self.assertEqual(ss.NACHLADEN_SECS[0], 30.0)
        self.assertTrue(all(a < b for a, b in zip(ss.NACHLADEN_SECS, ss.NACHLADEN_SECS[1:])))  # die Pause wächst

    async def test_mit_abdruck_wird_die_pruefung_bereit(self):
        s, _, _ = await self.lauf(FakeDb({USER_A: als_vektor(EIGENER)}, fehler=asyncpg.PostgresError("weg")), heil_nach=1)
        self.assertEqual(s.gate.status, "bereit")

    async def test_die_pause_bleibt_nach_der_letzten_stufe_gleich(self):
        n = len(ss.NACHLADEN_SECS) + 2
        _, _, pausen = await self.lauf(FakeDb(fehler=asyncpg.PostgresError("weg")), heil_nach=n)
        self.assertEqual(pausen[-3:], [ss.NACHLADEN_SECS[-1]] * 3)

    async def test_bekannter_stand_laedt_nicht_neu(self):
        db = FakeDb()
        s, _, pausen = await self.lauf(db, heil_nach=99)
        self.assertEqual((s.gate.status, pausen, len(db.calls)), ("fehlt", [], 1))

    async def test_geloescht_waehrend_der_pause_wird_nicht_ueberschrieben(self):
        db = FakeDb({USER_A: als_vektor(EIGENER)}, fehler=asyncpg.PostgresError("weg"))
        s, _, _, _ = neu(db)

        async def schlafen(secs):
            if secs >= ss.NACHLADEN_SECS[0]:
                s.gate.setze_abdruck(None, geloescht=True)  # die Person löscht ihren Abdruck (mit Nachweis)
                db.fehler = None

        with mock.patch.object(ss.asyncio, "sleep", schlafen):
            await asyncio.wait_for(s.laden_bis_bekannt(), 5.0)
        self.assertEqual(s.gate.status, "fehlt")
        self.assertIsNone(s.gate.abdruck)


class ClientBereitTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_und_einwilligung_gehen_an_die_oberflaeche(self):
        s, gesendet, _, _ = neu(FakeDb())
        await s.vorbereiten()
        gesendet.clear()
        await s.client_bereit()
        self.assertEqual(gesendet[0], {"type": "stimme", "status": "fehlt", "modus": "streng"})
        self.assertEqual(gesendet[1], {"type": "stimmabdruck_einwilligung", "version": "stimme-1", "text": EINWILLIGUNG_TEXT})

    async def test_im_modus_aus_keine_einwilligung(self):
        s, gesendet, _, _ = neu(FakeDb(), modus="aus")
        await s.client_bereit()
        self.assertEqual(arten(gesendet, "stimmabdruck_einwilligung"), [])
        self.assertEqual(arten(gesendet, "stimme")[-1]["status"], "aus")


class NachrichtTests(unittest.IsolatedAsyncioTestCase):
    async def bereit(self, db=None, **kw):
        s, gesendet, uhr, sagen = neu(db if db is not None else FakeDb(), **kw)
        await s.vorbereiten()
        gesendet.clear()
        return s, gesendet, uhr, sagen

    async def test_start_ohne_einwilligung_startet_nichts(self):
        s, gesendet, _, sagen = await self.bereit()
        for daten in ({"aktion": "start"}, {"aktion": "start", "einwilligung": ""}, {"aktion": "start", "einwilligung": "stimme-0"},
                      {"aktion": "start", "einwilligung": True}, {"aktion": "start", "einwilligung": ["stimme-1"]}):
            await s.nachricht(daten)
            self.assertFalse(s.pruefer.einlernen_aktiv, daten)
        self.assertEqual({x["status"] for x in arten(gesendet, "stimmabdruck")}, {"fehler"})
        self.assertIn("text", arten(gesendet, "stimmabdruck")[0])
        sagen.assert_not_awaited()

    async def test_start_mit_der_aktuellen_version_beginnt_das_einlernen(self):
        s, gesendet, uhr, sagen = await self.bereit()
        vorher = datetime.now(timezone.utc)
        await s.nachricht({"aktion": "start", "einwilligung": EINWILLIGUNG_VERSION})
        self.assertTrue(s.pruefer.einlernen_aktiv)
        self.assertEqual(arten(gesendet, "stimmabdruck")[0]["status"], "start")
        sagen.assert_awaited_once()
        await s.nachricht({"aktion": "abbrechen"})
        self.assertFalse(s.pruefer.einlernen_aktiv)
        self.assertEqual(arten(gesendet, "stimmabdruck")[-1]["status"], "abgebrochen")
        self.assertGreaterEqual(datetime.now(timezone.utc), vorher)

    async def test_start_im_modus_aus_wird_abgelehnt(self):
        s, gesendet, _, _ = await self.bereit(modus="aus")
        await s.nachricht({"aktion": "start", "einwilligung": EINWILLIGUNG_VERSION})
        self.assertFalse(s.pruefer.einlernen_aktiv)
        self.assertEqual(arten(gesendet, "stimmabdruck")[-1]["status"], "fehler")

    async def test_start_solange_der_stand_nicht_lesbar_ist_wird_abgelehnt(self):
        db = FakeDb(fehler=asyncpg.PostgresError("kaputt"))
        s, gesendet, _, _ = neu(db)
        with mock.patch.object(ss.asyncio, "sleep", mock.AsyncMock()):
            await s.vorbereiten()
        gesendet.clear()
        await s.nachricht({"aktion": "start", "einwilligung": EINWILLIGUNG_VERSION})
        self.assertFalse(s.pruefer.einlernen_aktiv)
        self.assertEqual(arten(gesendet, "stimmabdruck")[-1]["status"], "fehler")

    async def test_unbekannte_oder_kaputte_nachrichten_sind_ohne_wirkung(self):
        s, gesendet, _, _ = await self.bereit()
        for daten in ({"aktion": "fliegen"}, {}, {"aktion": None}, {"aktion": ["start"]}, "start", None, 5, [1]):
            await s.nachricht(daten)
        self.assertEqual(gesendet, [])
        self.assertFalse(s.pruefer.einlernen_aktiv)

    async def aufnehmen(self, s, uhr, secs=ZIEL_SECS + 0.1):
        s.pruefer.on_bot_speaking(True)
        s.pruefer.on_bot_speaking(False)
        from pipecat.frames.frames import InputAudioRawFrame

        for _ in range(int(secs / 0.02)):
            uhr.t += 0.02
            await s.pruefer.on_audio(InputAudioRawFrame(audio=np.full(320, 8000, dtype=np.int16).tobytes(), sample_rate=RATE, num_channels=1))

    async def test_ganzes_einlernen_speichert_mit_einwilligung_und_name(self):
        db = FakeDb()
        s, gesendet, uhr, _ = await self.bereit(db)
        vorher = datetime.now(timezone.utc)
        await s.nachricht({"aktion": "start", "einwilligung": EINWILLIGUNG_VERSION})
        await self.aufnehmen(s, uhr)
        await s.pruefer.einlernen_warten()
        nachher = datetime.now(timezone.utc)
        self.assertEqual(arten(gesendet, "stimmabdruck")[-1]["status"], "fertig")
        zeile = [c for c in db.calls if c[1].startswith("insert into")][0][2]
        self.assertEqual((zeile[0], zeile[1], zeile[3]), (USER_A, "Erika", "stimme-1"))
        self.assertTrue(vorher <= zeile[4] <= nachher)  # der Zeitpunkt der Einwilligung (beim Klick), mit Zeitzone
        self.assertIsNotNone(zeile[4].tzinfo)
        self.assertEqual(s.gate.status, "bereit")

    async def test_loeschen_entfernt_den_abdruck_und_meldet_es(self):
        db = FakeDb({USER_A: als_vektor(EIGENER), USER_B: als_vektor(EIGENER)})
        s, gesendet, _, _ = await self.bereit(db)
        self.assertEqual(s.gate.status, "bereit")
        s.gate.eintragen(EIGEN)
        await s.nachricht({"aktion": "loeschen"})
        self.assertNotIn(USER_A, db.zeilen)
        self.assertIn(USER_B, db.zeilen)  # nur das eigene Profil
        self.assertEqual(s.gate.status, "fehlt")
        self.assertFalse(s.gate.hat_abdruck)
        self.assertEqual(arten(gesendet, "stimmabdruck")[-1], {"type": "stimmabdruck", "status": "geloescht"})
        self.assertEqual(arten(gesendet, "stimme")[-1], {"type": "stimme", "status": "fehlt", "modus": "streng"})
        self.assertIsNone(await s.gate.pruefen("programm_oeffnen"))  # danach wird nichts mehr gesperrt

    async def test_loeschen_geht_auch_im_modus_aus_und_ohne_hindernis(self):
        db = FakeDb({USER_A: als_vektor(EIGENER)})
        s, gesendet, _, _ = await self.bereit(db, modus="aus")
        await s.nachricht({"aktion": "loeschen"})
        self.assertNotIn(USER_A, db.zeilen)
        self.assertEqual(arten(gesendet, "stimmabdruck")[-1]["status"], "geloescht")

    async def test_loeschen_ohne_abdruck_ist_in_ordnung(self):
        s, gesendet, _, _ = await self.bereit()
        await s.nachricht({"aktion": "loeschen"})
        self.assertEqual(arten(gesendet, "stimmabdruck")[-1]["status"], "geloescht")

    async def test_loeschen_scheitert_der_abdruck_bleibt_im_gate_und_es_wird_gemeldet(self):
        db = FakeDb({USER_A: als_vektor(EIGENER)})
        s, gesendet, _, _ = await self.bereit(db)
        db.fehler = asyncpg.PostgresError("kaputt")
        await s.nachricht({"aktion": "loeschen"})
        self.assertEqual(arten(gesendet, "stimmabdruck")[-1]["status"], "fehler")
        self.assertTrue(s.gate.hat_abdruck)  # nichts behaupten, was nicht stimmt

    async def test_loeschen_waehrend_des_rechnens_laesst_keinen_abdruck_zurueck(self):
        db = FakeDb()
        s, gesendet, uhr, _ = await self.bereit(db)
        await s.nachricht({"aktion": "start", "einwilligung": EINWILLIGUNG_VERSION})
        await self.aufnehmen(s, uhr)  # der Abdruck wird gerade berechnet
        await s.nachricht({"aktion": "loeschen"})
        await s.pruefer.einlernen_warten()
        self.assertEqual(db.zeilen, {})
        self.assertFalse(s.gate.hat_abdruck)
        self.assertEqual([c for c in db.calls if c[1].startswith("insert")], [])

    async def test_neu_einlernen_ohne_erkennung_wird_im_streng_modus_abgelehnt(self):
        db = FakeDb({USER_A: als_vektor(EIGENER)})
        s, gesendet, _, _ = await self.bereit(db)
        await s.nachricht({"aktion": "start", "einwilligung": EINWILLIGUNG_VERSION})
        self.assertFalse(s.pruefer.einlernen_aktiv)
        self.assertEqual(arten(gesendet, "stimmabdruck")[-1]["status"], "fehler")
        s.gate.eintragen(EIGEN)
        gesendet.clear()
        await s.nachricht({"aktion": "start", "einwilligung": EINWILLIGUNG_VERSION})
        self.assertTrue(s.pruefer.einlernen_aktiv)

    # ---------- Löschen und Ersetzen brauchen einen Nachweis (M3) ----------

    @staticmethod
    def angemeldet(vor_secs: float | None) -> Identity:
        return Identity(USER_A, "Erika", "mitglied", "du", anmeldung=None if vor_secs is None else time.time() - vor_secs)

    async def test_loeschen_ohne_nachweis_wird_abgelehnt_und_nennt_die_neue_anmeldung(self):
        db = FakeDb({USER_A: als_vektor(EIGENER)})
        s, gesendet, _, _ = await self.bereit(db, identity=self.angemeldet(None))
        await s.nachricht({"aktion": "loeschen"})
        letzte = arten(gesendet, "stimmabdruck")[-1]
        self.assertEqual(letzte["status"], "fehler")
        self.assertIn("anmelden", letzte["text"])
        self.assertIn(USER_A, db.zeilen)  # nichts gelöscht
        self.assertTrue(s.gate.hat_abdruck)
        self.assertEqual([x for x in arten(gesendet, "stimmabdruck") if x["status"] == "geloescht"], [])

    async def test_loeschen_mit_alter_anmeldung_ohne_stimme_wird_abgelehnt(self):
        db = FakeDb({USER_A: als_vektor(EIGENER)})
        s, gesendet, _, _ = await self.bereit(db, identity=self.angemeldet(ss.ANMELDUNG_FRISCH_SECS + 5))
        await s.nachricht({"aktion": "loeschen"})
        self.assertEqual(arten(gesendet, "stimmabdruck")[-1]["status"], "fehler")
        self.assertIn(USER_A, db.zeilen)

    async def test_loeschen_mit_frischer_anmeldung_geht_auch_ohne_erkannte_stimme(self):
        db = FakeDb({USER_A: als_vektor(EIGENER)})
        s, gesendet, _, _ = await self.bereit(db, identity=self.angemeldet(ss.ANMELDUNG_FRISCH_SECS - 60))
        await s.nachricht({"aktion": "loeschen"})
        self.assertNotIn(USER_A, db.zeilen)
        self.assertEqual(arten(gesendet, "stimmabdruck")[-1]["status"], "geloescht")

    async def test_loeschen_gilt_auch_im_modus_beobachten(self):
        db = FakeDb({USER_A: als_vektor(EIGENER)})
        s, gesendet, _, _ = await self.bereit(db, modus="beobachten", identity=self.angemeldet(None))
        await s.nachricht({"aktion": "loeschen"})
        self.assertEqual(arten(gesendet, "stimmabdruck")[-1]["status"], "fehler")
        self.assertIn(USER_A, db.zeilen)

    async def test_loeschen_bei_unlesbarem_stand_braucht_den_nachweis(self):
        db = FakeDb({USER_A: als_vektor(EIGENER)}, fehler=asyncpg.PostgresError("kaputt"))
        s, gesendet, _, _ = neu(db, identity=self.angemeldet(None))
        with mock.patch.object(ss.asyncio, "sleep", mock.AsyncMock()):
            await s.vorbereiten()
        db.fehler = None
        gesendet.clear()
        await s.nachricht({"aktion": "loeschen"})  # vielleicht gibt es einen Abdruck: nicht ohne Nachweis löschen
        self.assertEqual(arten(gesendet, "stimmabdruck")[-1]["status"], "fehler")
        self.assertIn(USER_A, db.zeilen)

    async def test_nach_dem_loeschen_braucht_das_neue_einlernen_denselben_nachweis(self):
        db = FakeDb({USER_A: als_vektor(EIGENER)})
        s, gesendet, _, _ = await self.bereit(db, identity=self.angemeldet(None))
        s.gate.eintragen(EIGEN)  # mit der erkannten Stimme gelöscht
        await s.nachricht({"aktion": "loeschen"})
        self.assertEqual(arten(gesendet, "stimmabdruck")[-1]["status"], "geloescht")
        gesendet.clear()
        await s.nachricht({"aktion": "start", "einwilligung": EINWILLIGUNG_VERSION})
        self.assertFalse(s.pruefer.einlernen_aktiv)
        self.assertEqual(arten(gesendet, "stimmabdruck")[-1]["status"], "fehler")
        self.assertIn("anmelden", arten(gesendet, "stimmabdruck")[-1]["text"])

    async def test_nach_dem_loeschen_geht_neues_einlernen_mit_frischer_anmeldung(self):
        db = FakeDb({USER_A: als_vektor(EIGENER)})
        s, gesendet, _, _ = await self.bereit(db, identity=self.angemeldet(30))
        await s.nachricht({"aktion": "loeschen"})
        gesendet.clear()
        await s.nachricht({"aktion": "start", "einwilligung": EINWILLIGUNG_VERSION})
        self.assertTrue(s.pruefer.einlernen_aktiv)

    async def test_ersetzen_mit_frischer_anmeldung_geht_auch_ohne_erkannte_stimme(self):
        db = FakeDb({USER_A: als_vektor(EIGENER)})
        s, gesendet, _, _ = await self.bereit(db, identity=self.angemeldet(30))
        await s.nachricht({"aktion": "start", "einwilligung": EINWILLIGUNG_VERSION})
        self.assertTrue(s.pruefer.einlernen_aktiv)

    async def test_erstes_einlernen_und_loeschen_ohne_abdruck_brauchen_keinen_nachweis(self):
        s, gesendet, _, _ = await self.bereit(identity=self.angemeldet(None))
        await s.nachricht({"aktion": "start", "einwilligung": EINWILLIGUNG_VERSION})
        self.assertTrue(s.pruefer.einlernen_aktiv)
        await s.nachricht({"aktion": "abbrechen"})
        await s.nachricht({"aktion": "loeschen"})
        self.assertEqual(arten(gesendet, "stimmabdruck")[-1]["status"], "geloescht")

    async def test_zug_verworfen_reicht_an_den_waechter_durch(self):
        s, _, _, _ = await self.bereit()
        self.assertFalse(s.zug_verworfen())
        s.waechter._verworfen = True
        self.assertTrue(s.zug_verworfen())
        self.assertFalse(s.zug_verworfen())


class ZeitTests(unittest.TestCase):
    def test_einwilligungszeit_ist_utc_mit_zone(self):
        jetzt = ss.jetzt_utc()
        self.assertIsNotNone(jetzt.tzinfo)
        self.assertLess(abs(jetzt - datetime.now(timezone.utc)), timedelta(seconds=2))


if __name__ == "__main__":
    unittest.main()
