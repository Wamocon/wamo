"""Tests für stimme_einlernen.py: Aufnahme, Streuungsprüfung, Speichern, Abbruch, Sicherungen beim Neu-Einlernen.

Ausführen:  python -m pytest -q test_stimme_einlernen.py   (im Ordner voice-core/)

Das Modell ist hier ein Ersatz: es liefert je Stück einen festen Abdruck (eigene Stimme oder Zufall).
"""

import asyncio
import unittest
from datetime import datetime, timezone
from unittest import mock

import numpy as np

import stimme_einlernen as se
from stimme_einlernen import ANLEITUNG, FERTIG_TEXT, ZIEL_SECS, Einlernen
from stimme_gate import EIGEN, StimmGate
from stimme_modell import DIM

RATE = 16000
EIGENER = np.eye(DIM, dtype=np.float32)[0]
EINWILLIGUNG_AM = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)


class Uhr:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class Rechner:
    """Ersatz für den Embedder: immer derselbe Abdruck, oder bei zufall=True ein anderer je Aufruf (unruhige Aufnahme)."""

    def __init__(self, zufall: bool = False):
        self.zufall = zufall
        self.aufrufe = 0
        self._rng = np.random.default_rng(5)

    def embed(self, pcm: bytes, rate: int):
        self.aufrufe += 1
        return self._rng.normal(size=DIM).astype(np.float32) if self.zufall else EIGENER


class Modell:
    def __init__(self, rechner=None, bereit: bool = True):
        self.rechner = rechner if rechner is not None else Rechner()
        self.bereit = bereit
        self.gescheitert = rechner is None and not bereit

    async def get(self):
        return self.rechner if self.bereit else None


def audio(wert: int, secs: float = 0.02) -> bytes:
    return np.full(int(RATE * secs), wert, dtype=np.int16).tobytes()


class EinlernenTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.uhr = Uhr()
        self.gate = StimmGate(modus="streng", uhr=self.uhr)
        self.gate.setze_abdruck(None)  # das erste Mal
        self.gesendet: list[dict] = []
        self.sagen = mock.AsyncMock()
        self.speichern = mock.AsyncMock(return_value=True)
        self.modell = Modell()
        self.e = self.neu(self.modell)

    def neu(self, modell) -> Einlernen:
        async def senden(daten):
            self.gesendet.append(daten)

        return Einlernen(self.gate, modell, senden=senden, sagen=self.sagen, speichern=self.speichern, uhr=self.uhr)

    def meldungen(self, art="stimmabdruck"):
        return [m for m in self.gesendet if m.get("type") == art]

    def letzter_status(self) -> str:
        return self.meldungen()[-1]["status"]

    async def aufnehmen(self, secs: float, wert: int = 8000):
        for _ in range(int(secs / 0.02)):
            self.uhr.t += 0.02
            await self.e.sammeln(audio(wert), RATE)

    async def anleitung_vorbei(self):
        self.e.bot_spricht(True)
        self.e.bot_spricht(False)

    async def test_einlernen_nach_ihrer_anleitung(self):
        await self.e.starten("stimme-1", EINWILLIGUNG_AM)
        self.sagen.assert_awaited_once_with(ANLEITUNG)
        self.assertEqual(self.meldungen()[0], {"type": "stimmabdruck", "status": "start", "sekunden": 0, "ziel": ZIEL_SECS})
        # Während WAMO die Anleitung spricht: Stille für die Spracherkennung, keine Aufnahme
        self.e.bot_spricht(True)
        self.assertTrue(await self.e.sammeln(audio(8000), RATE))
        self.e.bot_spricht(False)
        await self.aufnehmen(ZIEL_SECS + 0.1)
        await self.e.warten()
        self.speichern.assert_awaited_once()
        abdruck, version, am = self.speichern.await_args.args
        self.assertEqual(version, "stimme-1")
        self.assertEqual(am, EINWILLIGUNG_AM)
        self.assertAlmostEqual(float(np.dot(abdruck, EIGENER)), 1.0, places=4)
        self.assertEqual([m["status"] for m in self.meldungen()][-2:], ["rechnet", "fertig"])
        self.assertEqual(self.letzter_status(), "fertig")
        self.assertEqual(self.gate.status, "bereit")
        self.assertTrue(self.gate.hat_abdruck)
        self.assertTrue(self.gate.stimme_vertraut())  # die Person hat gerade selbst gesprochen
        self.assertEqual(self.meldungen("stimme")[-1], {"type": "stimme", "status": "bereit", "modus": "streng"})
        self.sagen.assert_awaited_with(FERTIG_TEXT)
        self.assertFalse(await self.e.sammeln(audio(8000), RATE))  # danach hört die Spracherkennung wieder mit
        self.assertFalse(self.e.beschaeftigt)

    async def test_fortschritt_wird_in_halben_sekunden_gemeldet(self):
        await self.e.starten("stimme-1", EINWILLIGUNG_AM)
        await self.anleitung_vorbei()
        await self.aufnehmen(3.0)
        aufnahme = [m for m in self.meldungen() if m["status"] == "aufnahme"]
        self.assertTrue(4 <= len(aufnahme) <= 7, len(aufnahme))
        self.assertTrue(all(m["ziel"] == ZIEL_SECS for m in aufnahme))
        self.assertEqual([m["sekunden"] for m in aufnahme], sorted(m["sekunden"] for m in aufnahme))

    async def test_nur_sprache_zaehlt_stille_nicht(self):
        await self.e.starten("stimme-1", EINWILLIGUNG_AM)
        await self.anleitung_vorbei()
        await self.aufnehmen(10.0, wert=0)
        await self.aufnehmen(ZIEL_SECS / 2)
        await self.e.warten()
        self.speichern.assert_not_awaited()  # erst die Hälfte der Sprache

    async def test_ohne_anleitung_beginnt_die_aufnahme_nach_der_wartezeit(self):
        await self.e.starten("stimme-1", EINWILLIGUNG_AM)
        await self.aufnehmen(se.WARTE_SECS - 1)
        self.assertEqual(self.meldungen()[-1]["status"], "start")  # noch nichts aufgenommen
        await self.aufnehmen(2.0)
        self.assertGreater(self.meldungen()[-1].get("sekunden", 0), 0)

    async def test_neu_einlernen_streng_nur_wenn_die_person_gerade_erkannt_ist(self):
        self.gate.setze_abdruck(EIGENER)
        self.gate.modell_ok = True
        await self.e.starten("stimme-1", EINWILLIGUNG_AM)  # Abdruck da, aber noch nichts gehört
        self.assertEqual(self.letzter_status(), "fehler")
        self.assertIn("text", self.meldungen()[-1])
        self.assertFalse(self.e.aktiv)
        self.assertFalse(await self.e.sammeln(audio(8000), RATE))  # keine Aufnahme gestartet
        self.gate.eintragen(EIGEN)
        await self.e.starten("stimme-1", EINWILLIGUNG_AM)
        self.assertEqual(self.letzter_status(), "start")
        self.assertTrue(self.e.aktiv)

    async def test_neu_einlernen_geht_auch_mit_einem_anderen_nachweis(self):
        """Die Sitzung kann statt der erkannten Stimme eine frische Anmeldung mit zweitem Faktor gelten lassen."""
        self.gate.setze_abdruck(EIGENER)
        self.gate.modell_ok = True
        e = Einlernen(self.gate, self.modell, senden=self.e._senden, sagen=self.sagen, speichern=self.speichern, uhr=self.uhr,
                      nachweis=lambda: True)
        await e.starten("stimme-1", EINWILLIGUNG_AM)
        self.assertEqual(self.letzter_status(), "start")

    async def test_neu_einlernen_nach_dem_loeschen_braucht_denselben_nachweis(self):
        self.gate.setze_abdruck(EIGENER)
        self.gate.modell_ok = True
        self.gate.eintragen(EIGEN)
        self.gate.setze_abdruck(None, geloescht=True)  # gelöscht: die erkannte Stimme gilt danach nicht mehr
        await self.e.starten("stimme-1", EINWILLIGUNG_AM)
        self.assertEqual(self.letzter_status(), "fehler")
        self.assertIn("anmelden", self.meldungen()[-1]["text"])
        self.assertFalse(self.e.aktiv)

    async def test_neu_einlernen_braucht_den_nachweis_in_jedem_modus_ausser_aus(self):
        self.gate.setze_abdruck(EIGENER)
        self.gate.modell_ok = True
        self.gate.modus = "beobachten"
        await self.e.starten("stimme-1", EINWILLIGUNG_AM)
        self.assertEqual(self.letzter_status(), "fehler")

    async def test_beim_ersten_einlernen_ist_keine_erkennung_noetig(self):
        await self.e.starten("stimme-1", EINWILLIGUNG_AM)
        self.assertEqual(self.letzter_status(), "start")

    async def test_zweimal_starten_startet_nur_einmal(self):
        await self.e.starten("stimme-1", EINWILLIGUNG_AM)
        await self.e.starten("stimme-1", EINWILLIGUNG_AM)
        self.assertEqual(len([m for m in self.meldungen() if m["status"] == "start"]), 1)
        self.sagen.assert_awaited_once()

    async def test_abbrechen_waehrend_der_aufnahme(self):
        await self.e.starten("stimme-1", EINWILLIGUNG_AM)
        await self.e.abbrechen()
        self.assertEqual(self.letzter_status(), "abgebrochen")
        self.assertFalse(await self.e.sammeln(audio(8000), RATE))

    async def test_abbrechen_waehrend_er_rechnet_speichert_nichts(self):
        await self.e.starten("stimme-1", EINWILLIGUNG_AM)
        await self.anleitung_vorbei()
        await self.aufnehmen(ZIEL_SECS + 0.1)
        await self.e.abbrechen()
        await self.e.warten()
        self.speichern.assert_not_awaited()
        self.assertEqual(self.letzter_status(), "abgebrochen")
        self.assertFalse(self.gate.hat_abdruck)

    async def test_abtastrate_nur_von_8000_bis_48000(self):
        for rate in (7999, 48001, 0, 1, 1000000):
            self.gesendet.clear()
            e = self.e = self.neu(self.modell)
            await e.starten("stimme-1", EINWILLIGUNG_AM)
            await self.anleitung_vorbei()
            self.assertTrue(await e.sammeln(audio(8000), rate), rate)  # der Ton wird trotzdem durch Stille ersetzt
            self.assertEqual(self.letzter_status(), "fehler", rate)
            self.assertFalse(e.aktiv, rate)
        for rate in (8000, 48000):
            e = self.e = self.neu(self.modell)
            await e.starten("stimme-1", EINWILLIGUNG_AM)
            await self.anleitung_vorbei()
            await e.sammeln(audio(8000), rate)
            self.assertTrue(e.aktiv, rate)
            await e.abbrechen()

    async def test_der_puffer_hat_eine_obergrenze_in_bytes(self):
        await self.e.starten("stimme-1", EINWILLIGUNG_AM)
        await self.anleitung_vorbei()
        with mock.patch.object(se, "MAX_BYTES", 10000):
            for _ in range(20):  # 20 mal 640 Bytes Stille: mehr als 10000, aber weit unter MAX_SECS
                self.uhr.t += 0.02
                await self.e.sammeln(audio(0), RATE)
        self.assertEqual(self.letzter_status(), "fehler")
        self.assertFalse(self.e.aktiv)
        self.speichern.assert_not_awaited()

    async def test_abbrechen_waehrend_des_speicherns_meldet_nicht_fertig(self):
        freigabe = asyncio.Event()
        speichert = asyncio.Event()

        async def langsam(abdruck, version, am):
            speichert.set()
            await freigabe.wait()
            return True

        self.speichern.side_effect = langsam
        await self.e.starten("stimme-1", EINWILLIGUNG_AM)
        await self.anleitung_vorbei()
        await self.aufnehmen(ZIEL_SECS + 0.1)
        await asyncio.wait_for(speichert.wait(), 5)
        abbruch = asyncio.create_task(self.e.abbrechen())
        await asyncio.sleep(0.01)
        freigabe.set()
        await abbruch
        await self.e.warten()
        self.assertNotIn("fertig", [m["status"] for m in self.meldungen()])
        self.assertNotIn(mock.call(FERTIG_TEXT), self.sagen.await_args_list)
        self.assertTrue(self.gate.hat_abdruck)  # die Datenbank hat ihn: das Gate gibt den Stand wieder, das Löschen kommt danach von außen
        self.assertFalse(self.e.beschaeftigt)

    async def test_abbrechen_vor_dem_speichern_speichert_nicht_und_meldet_nicht_fertig(self):
        loslassen = asyncio.Event()
        rechner = Rechner()
        embed = rechner.embed

        def gebremst(pcm, rate):
            import time

            time.sleep(0.2)
            return embed(pcm, rate)

        rechner.embed = gebremst
        e = self.e = self.neu(Modell(rechner))
        await e.starten("stimme-1", EINWILLIGUNG_AM)
        await self.anleitung_vorbei()
        await self.aufnehmen(ZIEL_SECS + 0.1)
        await asyncio.sleep(0.02)  # es rechnet noch
        await e.abbrechen()
        self.speichern.assert_not_awaited()
        self.assertNotIn("fertig", [m["status"] for m in self.meldungen()])
        self.assertFalse(self.gate.hat_abdruck)

    async def test_abbrechen_ohne_einlernen_tut_nichts(self):
        await self.e.abbrechen()
        self.assertEqual(self.gesendet, [])

    async def test_bricht_ab_wenn_die_person_nichts_sagt(self):
        await self.e.starten("stimme-1", EINWILLIGUNG_AM)
        await self.anleitung_vorbei()
        for _ in range(int(se.MAX_SECS / 0.5) + 2):
            self.uhr.t += 0.5
            await self.e.sammeln(audio(0), RATE)
        self.assertEqual(self.letzter_status(), "fehler")
        self.speichern.assert_not_awaited()
        self.assertFalse(self.e.aktiv)

    async def test_endet_auch_wenn_das_ende_der_anleitung_nie_gemeldet_wird(self):
        await self.e.starten("stimme-1", EINWILLIGUNG_AM)
        self.e.bot_spricht(True)  # BotStoppedSpeaking kommt nie
        for _ in range(int(se.MAX_SECS / 0.5) + 2):
            self.uhr.t += 0.5
            await self.e.sammeln(audio(8000), RATE)
        self.assertEqual(self.letzter_status(), "fehler")
        self.assertFalse(await self.e.sammeln(audio(8000), RATE))  # WAMO hört wieder zu

    async def test_zu_unruhige_aufnahme_wird_nicht_gespeichert(self):
        e = self.e = self.neu(Modell(Rechner(zufall=True)))
        await e.starten("stimme-1", EINWILLIGUNG_AM)
        await self.anleitung_vorbei()
        await self.aufnehmen(ZIEL_SECS + 0.1)
        await e.warten()
        self.speichern.assert_not_awaited()
        self.assertEqual(self.letzter_status(), "fehler")
        self.assertIn("unruhig", self.meldungen()[-1]["text"])
        self.assertFalse(self.gate.hat_abdruck)

    async def test_zu_wenig_gesprochen_wird_nicht_gespeichert(self):
        await self.e.starten("stimme-1", EINWILLIGUNG_AM)
        await self.anleitung_vorbei()
        with mock.patch.object(se, "sprachbloecke", return_value=audio(8000, 5.0)):  # nach dem Schneiden bleiben nur 5 s
            await self.aufnehmen(ZIEL_SECS + 0.1)
            await self.e.warten()
        self.speichern.assert_not_awaited()
        self.assertEqual(self.letzter_status(), "fehler")
        self.assertIn("zu wenig", self.meldungen()[-1]["text"])

    async def test_fehlendes_modell_beendet_das_einlernen_vor_der_aufnahme(self):
        e = self.e = self.neu(Modell(bereit=False))
        await e.starten("stimme-1", EINWILLIGUNG_AM)
        self.assertEqual(self.letzter_status(), "fehler")
        self.assertFalse(e.aktiv)
        self.assertFalse(await e.sammeln(audio(8000), RATE))
        self.sagen.assert_awaited_once()  # nur die Fehlermeldung, keine Anleitung

    async def test_beim_laden_des_modells_wird_der_zustand_gemeldet(self):
        e = self.e = self.neu(Modell(bereit=False))
        await e.starten("stimme-1", EINWILLIGUNG_AM)
        self.assertEqual(self.meldungen("stimme")[0]["status"], "laedt")

    async def test_speichern_schlaegt_fehl_der_abdruck_gilt_nicht(self):
        self.speichern.return_value = False
        await self.e.starten("stimme-1", EINWILLIGUNG_AM)
        await self.anleitung_vorbei()
        await self.aufnehmen(ZIEL_SECS + 0.1)
        await self.e.warten()
        self.assertEqual(self.letzter_status(), "fehler")
        self.assertFalse(self.gate.hat_abdruck)
        self.assertEqual(self.gate.status, "fehlt")

    async def test_ausnahme_beim_rechnen_ist_ein_fehler_und_wirft_nicht(self):
        rechner = Rechner()
        rechner.embed = mock.Mock(side_effect=RuntimeError("kaputt"))
        e = self.e = self.neu(Modell(rechner))
        await e.starten("stimme-1", EINWILLIGUNG_AM)
        await self.anleitung_vorbei()
        await self.aufnehmen(ZIEL_SECS + 0.1)
        await e.warten()
        self.assertEqual(self.letzter_status(), "fehler")
        self.assertFalse(e.beschaeftigt)
        self.assertFalse(await e.sammeln(audio(8000), RATE))

    async def test_abbrechen_beim_laden_des_modells_startet_keine_aufnahme(self):
        freigabe = asyncio.Event()

        class Langsam(Modell):
            async def get(self_):
                await freigabe.wait()
                return self_.rechner

        e = self.e = self.neu(Langsam())
        start = asyncio.create_task(e.starten("stimme-1", EINWILLIGUNG_AM))
        await asyncio.sleep(0.01)
        await e.abbrechen()
        freigabe.set()
        await start
        self.assertFalse(e.aktiv)
        self.assertNotIn("start", [m["status"] for m in self.meldungen()])
        self.sagen.assert_not_awaited()

    async def test_aufraeumen_beendet_ein_laufendes_rechnen_ohne_zu_speichern(self):
        """Endet die Sitzung, während der Abdruck berechnet wird, darf danach nichts mehr in die Datenbank gehen."""
        await self.e.starten("stimme-1", EINWILLIGUNG_AM)
        await self.anleitung_vorbei()
        await self.aufnehmen(ZIEL_SECS + 0.1)
        self.assertTrue(self.e.beschaeftigt)
        self.e.aufraeumen()
        await asyncio.sleep(0.05)
        self.speichern.assert_not_awaited()
        self.assertFalse(self.e.beschaeftigt)
        self.assertFalse(await self.e.sammeln(audio(8000), RATE))

    async def test_die_aufnahme_wird_nirgends_gespeichert(self):
        """Nur der Abdruck geht an speichern: drei Argumente, kein Ton."""
        await self.e.starten("stimme-1", EINWILLIGUNG_AM)
        await self.anleitung_vorbei()
        await self.aufnehmen(ZIEL_SECS + 0.1)
        await self.e.warten()
        abdruck, version, am = self.speichern.await_args.args
        self.assertEqual(abdruck.shape, (DIM,))
        self.assertEqual(self.speichern.await_args.kwargs, {})


if __name__ == "__main__":
    unittest.main()
