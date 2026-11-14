"""Tests für fuellwoerter.py: "Moment." statt Stille, wenn ein Werkzeug läuft, und der optionale Rückmeldelaut "Mhm".

Ausführen:  uv run python -m pytest -q test_fuellwoerter.py   (im Ordner voice-core/)
"""

import asyncio
import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    LLMFullResponseStartFrame,
    LLMMarkerFrame,
    LLMTextFrame,
    OutputAudioRawFrame,
    StartFrame,
    UserStartedSpeakingFrame,
    VADUserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from fuellwoerter import (
    HAPPEN_BYTES,
    LANG_SAETZE,
    MHM_SAETZE,
    WERKZEUG_SAETZE,
    FuellwortBank,
    FuellwortSpieler,
    ablage,
    auf_happen,
    bank_fuer,
    stille_abschneiden,
)


def ton(ms_stille_davor=200, ms_laut=300, ms_stille_danach=200, rate=24000):
    stille = lambda ms: np.zeros(rate * ms // 1000, dtype=np.int16)  # noqa: E731
    laut = np.full(rate * ms_laut // 1000, 8000, dtype=np.int16)
    return np.concatenate([stille(ms_stille_davor), laut, stille(ms_stille_danach)]).tobytes()


class AufnahmeTests(unittest.IsolatedAsyncioTestCase):
    def test_stille_vorn_und_hinten_weg(self):
        gekuerzt = stille_abschneiden(ton(), vorn_ms=30, hinten_ms=80)
        self.assertEqual(len(gekuerzt) // 2, 24000 * (30 + 300 + 80) // 1000)
        self.assertEqual(stille_abschneiden(bytes(4800)), b"")

    def test_ganze_happen(self):
        self.assertEqual(len(auf_happen(bytes(100))), HAPPEN_BYTES)
        self.assertEqual(len(auf_happen(bytes(HAPPEN_BYTES))), HAPPEN_BYTES)

    async def test_einmal_aufnehmen_dann_aus_der_ablage(self):
        aufrufe = []

        async def sprechen(text):
            aufrufe.append(text)
            if text == "Sekunde.":
                raise RuntimeError("Netz weg")
            return ton()

        with tempfile.TemporaryDirectory() as tmp:
            bank = FuellwortBank("Lena", {"speed": 1.1}, ordner=Path(tmp), saetze=WERKZEUG_SAETZE)
            await bank.vorbereiten(sprechen)
            self.assertTrue(bank.bereit)
            self.assertEqual(sorted(aufrufe), sorted(WERKZEUG_SAETZE))
            # Neuer Prozess: alles Gespeicherte kommt aus der Ablage, nur das Fehlende neu
            aufrufe.clear()
            wieder = FuellwortBank("Lena", {"speed": 1.1}, ordner=Path(tmp), saetze=WERKZEUG_SAETZE)
            await wieder.vorbereiten(sprechen)
            self.assertEqual(aufrufe, ["Sekunde."])
            # Andere Stimme: eigene Aufnahmen
            andere = FuellwortBank("Jonas", ordner=Path(tmp), saetze=WERKZEUG_SAETZE)
            self.assertEqual(len(andere.aus_ablage_laden()), len(WERKZEUG_SAETZE))

    def test_nicht_zweimal_hintereinander_dasselbe(self):
        bank = FuellwortBank("Lena", saetze=WERKZEUG_SAETZE)
        bank._ton = {s: b"x" for s in WERKZEUG_SAETZE}
        wahl = [bank.waehlen(WERKZEUG_SAETZE)[0] for _ in range(20)]
        self.assertTrue(all(a != b for a, b in zip(wahl, wahl[1:])))

    def test_eine_bank_je_stimme_und_einstellung(self):
        # Der Kern bedient mehrere Sitzungen: gleiche Stimme und Einstellung teilen sich eine Bank, andere bekommen eine eigene
        a = bank_fuer("Lena", {"speed": 1.1})
        self.assertIs(bank_fuer("Lena", {"speed": 1.1}), a)
        self.assertIsNot(bank_fuer("Lena", {"speed": 1.2}), a)
        self.assertIsNot(bank_fuer("Jonas", {"speed": 1.1}), a)

    def test_ablage_liegt_im_cache_ordner_des_dienstes(self):
        with mock.patch.dict("os.environ", {"XDG_CACHE_HOME": "/var/cache/wamo-core"}):
            self.assertEqual(ablage(), Path("/var/cache/wamo-core") / "wamo" / "fuellwoerter")
        with mock.patch.dict("os.environ", {"XDG_CACHE_HOME": ""}):
            self.assertEqual(ablage().name, "fuellwoerter")
            self.assertEqual(ablage().parent.name, ".cache")


def bereite_bank():
    bank = FuellwortBank("Lena")
    bank._ton = {s: bytes(HAPPEN_BYTES) for s in WERKZEUG_SAETZE + LANG_SAETZE + MHM_SAETZE}
    return bank


def werkzeug_start(i="1"):
    return FunctionCallInProgressFrame(function_name="programm_oeffnen", tool_call_id=i, arguments={})


def werkzeug_ende(i="1"):
    return FunctionCallResultFrame(function_name="programm_oeffnen", tool_call_id=i, arguments={}, result="ok")


class SpielerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        p = mock.patch.object(FrameProcessor, "process_frame", new=mock.AsyncMock())
        p.start()
        self.addCleanup(p.stop)
        self.spieler = self.bauen()

    def bauen(self, **kwargs):
        optionen = dict(nach_secs=0.02, lang_secs=0.1, zufall=lambda: 0.0)
        spieler = FuellwortSpieler(bereite_bank(), **{**optionen, **kwargs})
        spieler.gespielt = []

        async def push(frame, direction=FrameDirection.DOWNSTREAM):
            if isinstance(frame, OutputAudioRawFrame):
                spieler.gespielt.append(frame)

        async def abbrechen(task):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        spieler.push_frame = push
        spieler.create_task = lambda coro, name=None: asyncio.get_running_loop().create_task(coro)
        spieler.cancel_task = abbrechen
        return spieler

    async def senden(self, *frames, spieler=None):
        for frame in frames:
            richtung = FrameDirection.UPSTREAM if isinstance(frame, BotStartedSpeakingFrame) else FrameDirection.DOWNSTREAM
            await (spieler or self.spieler).process_frame(frame, richtung)

    async def test_werkzeug_ohne_ansage_bekommt_ein_moment(self):
        await self.senden(UserStartedSpeakingFrame(), LLMFullResponseStartFrame(), werkzeug_start())
        await asyncio.sleep(0.06)
        self.assertEqual(len(self.spieler.gespielt), 1)
        await self.senden(werkzeug_ende())

    async def test_hat_wamo_schon_etwas_gesagt_kein_fuellwort(self):
        await self.senden(UserStartedSpeakingFrame(), LLMFullResponseStartFrame(), LLMTextFrame("Klar, mach ich."), werkzeug_start())
        await asyncio.sleep(0.06)
        self.assertEqual(self.spieler.gespielt, [])
        await self.senden(werkzeug_ende())

    async def test_spricht_wamo_gerade_kein_fuellwort(self):
        await self.senden(UserStartedSpeakingFrame(), werkzeug_start(), BotStartedSpeakingFrame())
        await asyncio.sleep(0.06)
        self.assertEqual(self.spieler.gespielt, [])
        await self.senden(werkzeug_ende())

    async def test_neuer_satz_der_person_bricht_das_warten_ab(self):
        await self.senden(UserStartedSpeakingFrame(), werkzeug_start(), UserStartedSpeakingFrame())
        await asyncio.sleep(0.06)
        self.assertEqual(self.spieler.gespielt, [])

    async def test_langes_werkzeug_bekommt_einmal_dauert_noch(self):
        await self.senden(UserStartedSpeakingFrame(), werkzeug_start())
        await asyncio.sleep(0.7)
        self.assertEqual(len(self.spieler.gespielt), 2)  # "Moment." und einmal "Dauert noch ..."
        await self.senden(werkzeug_ende())

    async def test_zweites_werkzeug_im_selben_zug_bekommt_einen_neuen_beobachter(self):
        """Das Ergebnis des letzten offenen Werkzeugs setzt _werkzeug_seit zurück: Sonst blieb ein zweites Werkzeug im selben Zug
        (ohne neuen Satz der Person) ohne Beobachter und damit ohne "Dauert noch"."""
        await self.senden(UserStartedSpeakingFrame(), werkzeug_start("1"))
        await asyncio.sleep(0.08)
        self.assertEqual(len(self.spieler.gespielt), 1)  # "Moment."
        await self.senden(werkzeug_ende("1"))
        self.assertIsNone(self.spieler._werkzeug_seit, "kein Werkzeug mehr offen")
        for _ in range(200):  # der erste Beobachter endet beim nächsten Blick auf die offenen Werkzeuge
            if self.spieler._waechter is None:
                break
            await asyncio.sleep(0.01)
        self.assertIsNone(self.spieler._waechter)
        await self.senden(werkzeug_start("2"))  # derselbe Zug: kein Satz der Person dazwischen
        self.assertIsNotNone(self.spieler._waechter, "neuer Beobachter für das zweite Werkzeug")
        for _ in range(200):
            if len(self.spieler.gespielt) >= 2:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(len(self.spieler.gespielt), 2, "das zweite Werkzeug dauert: einmal Dauert noch")
        await self.senden(werkzeug_ende("2"))

    async def test_folgt_das_zweite_werkzeug_sofort_bleibt_es_bei_einem_beobachter(self):
        """Startet das zweite Werkzeug, bevor der erste Beobachter das Ende des ersten bemerkt hat, übernimmt er das zweite: kein
        zweiter Beobachter, also auch kein doppeltes Füllwort."""
        await self.senden(UserStartedSpeakingFrame(), werkzeug_start("1"))
        await asyncio.sleep(0.05)
        erster = self.spieler._waechter
        self.assertIsNotNone(erster)
        await self.senden(werkzeug_ende("1"), werkzeug_start("2"))
        self.assertIs(self.spieler._waechter, erster, "der laufende Beobachter sieht das zweite Werkzeug selbst")
        for _ in range(200):
            if len(self.spieler.gespielt) >= 2:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.3)
        self.assertEqual(len(self.spieler.gespielt), 2, "einmal Moment, einmal Dauert noch")
        await self.senden(werkzeug_ende("2"))

    async def test_solange_ein_werkzeug_offen_ist_bleibt_der_beginn_stehen(self):
        await self.senden(UserStartedSpeakingFrame(), werkzeug_start("1"))
        seit = self.spieler._werkzeug_seit
        self.assertIsNotNone(seit)
        await self.senden(werkzeug_start("2"), werkzeug_ende("1"))
        self.assertEqual(self.spieler._werkzeug_seit, seit, "Werkzeug 2 läuft noch: keine neue Zählung, kein zweiter Beobachter")
        await self.senden(werkzeug_ende("2"))
        self.assertIsNone(self.spieler._werkzeug_seit)

    async def test_ohne_schalter_kein_werkzeug_fuellwort(self):
        spieler = self.bauen(werkzeug=False)
        await self.senden(UserStartedSpeakingFrame(), werkzeug_start(), spieler=spieler)
        await asyncio.sleep(0.06)
        self.assertEqual(spieler.gespielt, [])

    async def test_aufnahmen_entstehen_erst_beim_start_der_pipeline(self):
        # Der Aufbau einer Sitzung (und damit jeder Test, der nur aufbaut) greift nicht ins Netz: erst der Start holt die Aufnahmen.
        aufrufe = []

        async def sprechen(text):
            aufrufe.append(text)
            return ton()

        with tempfile.TemporaryDirectory() as tmp:
            bank = FuellwortBank("Lena", ordner=Path(tmp), saetze=WERKZEUG_SAETZE)
            spieler = self.bauen(sprechen=sprechen)
            spieler._bank = bank
            await asyncio.sleep(0.02)
            self.assertEqual(aufrufe, [])
            await self.senden(StartFrame(), spieler=spieler)
            for _ in range(50):
                if bank.bereit and len(aufrufe) == len(WERKZEUG_SAETZE):
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(sorted(aufrufe), sorted(WERKZEUG_SAETZE))
            # ein zweiter Start (zweite Sitzung mit derselben Bank) holt nichts doppelt
            await self.senden(StartFrame(), spieler=spieler)
            await asyncio.sleep(0.02)
            self.assertEqual(len(aufrufe), len(WERKZEUG_SAETZE))

    async def test_mhm_nur_wenn_eingeschaltet_selten_und_nie_ins_wort(self):
        await self.senden(LLMMarkerFrame("◐"))
        self.assertEqual(self.spieler.gespielt, [])  # Standard: aus
        spieler = self.bauen(mhm=True, mhm_ruhe_secs=10)
        await self.senden(LLMMarkerFrame("◐"), LLMMarkerFrame("◐"), spieler=spieler)
        self.assertEqual(len(spieler.gespielt), 1)  # Ruhezeit
        redet = self.bauen(mhm=True)
        await self.senden(VADUserStartedSpeakingFrame(), LLMMarkerFrame("◐"), spieler=redet)
        self.assertEqual(redet.gespielt, [])  # die Person spricht schon weiter


if __name__ == "__main__":
    unittest.main()
