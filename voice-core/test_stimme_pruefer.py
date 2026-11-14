"""Tests für stimme_pruefer.py: Prüfen je Äußerung aus dem Ringpuffer, Urteil abwarten, Ton ersetzen beim Einlernen.

Ausführen:  python -m pytest -q test_stimme_pruefer.py   (im Ordner voice-core/)

Das Modell ist hier ein Ersatz: positiver Ton = eigene Stimme, negativer = fremde Stimme.
"""

import asyncio
import time
import unittest
from unittest import mock

import numpy as np

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InputAudioRawFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.tests.utils import run_test

import stimme_pruefer
from stimme_gate import EIGEN, FREMD, UNKLAR, StimmGate
from stimme_modell import DIM
from stimme_pruefer import MAX_PRUEF_SECS, URTEIL_MAX_ALTER_SECS, Stimmpruefer

RATE = 16000
EIGENER = np.eye(DIM, dtype=np.float32)[0]
FREMDER = np.eye(DIM, dtype=np.float32)[1]


class Uhr:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


HALB = np.array([0.4, np.sqrt(1 - 0.16)] + [0.0] * (DIM - 2), dtype=np.float32)  # Kosinus 0,4 zum eigenen Abdruck


class Rechner:
    def __init__(self):
        self.aufrufe = 0
        self.dauer = 0.0
        self.festes_ergebnis = None  # ersetzt die Unterscheidung nach dem Vorzeichen des Tons

    def embed(self, pcm: bytes, rate: int):
        self.aufrufe += 1
        if self.dauer:
            time.sleep(self.dauer)
        if self.festes_ergebnis is not None:
            return self.festes_ergebnis
        return EIGENER if np.frombuffer(pcm, dtype=np.int16).astype(np.int64).sum() > 0 else FREMDER


class Modell:
    bereit = True
    gescheitert = False

    def __init__(self):
        self.rechner = Rechner()

    async def get(self):
        return self.rechner


def audio(wert: int, secs: float = 0.02) -> InputAudioRawFrame:
    return InputAudioRawFrame(audio=np.full(int(RATE * secs), wert, dtype=np.int16).tobytes(), sample_rate=RATE, num_channels=1)


def bereites_gate(modus="streng", uhr=None) -> StimmGate:
    gate = StimmGate(modus=modus, uhr=uhr or Uhr())
    gate.setze_abdruck(EIGENER)
    gate.modell_ok = True
    return gate


class PrueferBasis(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.uhr = Uhr()
        self.gate = bereites_gate(uhr=self.uhr)
        self.modell = Modell()
        self.speichern = mock.AsyncMock(return_value=True)
        self.sagen = mock.AsyncMock()
        self.v = Stimmpruefer(self.gate, self.modell, speichern=self.speichern, sagen=self.sagen, uhr=self.uhr)
        self.gesendet: list = []
        self.v.push_frame = mock.AsyncMock(side_effect=lambda f, *a: self.gesendet.append(getattr(f, "data", None)))

    async def sprechen(self, wert: int, secs: float, warten: bool = True):
        """VAD-Beginn, secs Sekunden Ton (20-ms-Pakete), VAD-Ende mit 0,2 s Stille. Danach das Urteil abwarten."""
        self.v.on_speech_start(self.uhr.t + 0.2, 0.2)
        for _ in range(int(secs / 0.02)):
            self.uhr.t += 0.02
            await self.v.on_audio(audio(wert))
        for _ in range(10):
            self.uhr.t += 0.02
            await self.v.on_audio(audio(0))
        self.v.on_speech_stop(self.uhr.t, 0.2)
        if warten:
            return await self.v.urteil_abwarten(5.0)

    def meldungen(self, art):
        return [m for m in self.gesendet if m and m.get("type") == art]


class PruefenTests(PrueferBasis):
    async def test_eigene_stimme_wird_erkannt_und_gemeldet(self):
        urteil = await self.sprechen(8000, 2.0)
        self.assertEqual(urteil.urteil, EIGEN)
        self.assertGreater(urteil.wert, 0.99)
        self.assertGreaterEqual(urteil.secs, 1.9)
        self.assertIsNone(self.gate.grund_der_stimme())
        meldung = self.meldungen("stimme")[-1]
        self.assertEqual((meldung["ergebnis"], meldung["modus"]), (EIGEN, "streng"))
        self.assertEqual(meldung["wert"], round(float(urteil.wert), 2))
        self.assertNotIn("ignoriert", meldung)

    async def test_fremde_stimme_sperrt(self):
        await self.sprechen(8000, 2.0)
        self.v.on_turn_end()  # der Zug der Person ist vorbei, jetzt spricht jemand anderes
        urteil = await self.sprechen(-8000, 2.0)
        self.assertEqual(urteil.urteil, FREMD)
        self.assertEqual(self.gate.grund_der_stimme(), "fremde Stimme")
        self.assertEqual(self.meldungen("stimme")[-1]["ergebnis"], FREMD)

    async def test_fremd_waehrend_wamo_spricht_ist_unklar_und_sperrt(self):
        await self.sprechen(8000, 2.0)
        self.v.on_bot_speaking(True)
        urteil = await self.sprechen(-8000, 2.0)
        self.assertEqual(urteil.urteil, UNKLAR)
        self.assertTrue(urteil.ueberlappung)
        self.assertEqual(self.gate.grund_der_stimme(), "Stimme nicht sicher erkannt")
        self.assertEqual(self.meldungen("stimme")[-1]["ergebnis"], UNKLAR)

    async def test_kurzes_ja_nach_der_person_bleibt_erlaubt(self):
        await self.sprechen(8000, 2.0)
        self.v.on_turn_end()
        await self.sprechen(8000, 0.5)
        self.assertIsNone(self.gate.grund_der_stimme())
        self.assertEqual(self.meldungen("stimme")[-1]["ergebnis"], UNKLAR)  # die Oberfläche zeigt "unklar"

    async def test_kurze_aeusserung_die_nicht_nach_der_person_klingt_entzieht_das_vertrauen(self):
        await self.sprechen(8000, 2.0)
        self.v.on_turn_end()
        self.modell.rechner.festes_ergebnis = HALB
        urteil = await self.sprechen(8000, 0.5)
        self.assertEqual(urteil.urteil, UNKLAR)
        self.assertEqual(self.gate.grund_der_stimme(), "Stimme nicht sicher erkannt")

    async def test_satzende_beginnt_einen_eigenen_zug(self):
        """Ein fremder Einwurf nach einem fertigen Satz erbt nicht das Urteil des Satzes davor."""
        await self.sprechen(8000, 2.0)
        self.v.satz_ende()  # der Satz der Person ist zu Ende (endgültiges Transkript mit Satzzeichen), der Zug aber noch offen
        urteil = await self.sprechen(-8000, 0.5)
        self.assertEqual(urteil.urteil, FREMD)
        self.assertEqual(self.gate.grund_der_stimme(), "fremde Stimme")

    async def test_ohne_satzende_gehoert_der_kurze_einwurf_zum_selben_zug(self):
        """Ein unfertiger Satz der Person bleibt ein Zug: die Pause mitten im Satz trennt nicht."""
        await self.sprechen(8000, 2.0)
        urteil = await self.sprechen(-8000, 0.5)
        self.assertEqual(urteil.urteil, EIGEN)

    async def test_nach_rede_von_wamo_beginnt_ein_eigener_zug(self):
        self.v.on_bot_speaking(True)
        await self.sprechen(8000, 2.0)  # die Person fällt WAMO ins Wort
        self.v.on_bot_speaking(False)
        urteil = await self.sprechen(-8000, 0.5)
        self.assertEqual(urteil.urteil, FREMD)

    async def test_lange_pause_trennt_die_aeusserungen(self):
        await self.sprechen(8000, 2.0)
        self.uhr.t += stimme_pruefer.ZUG_PAUSE_SECS + 1  # kein Zugende gemeldet, aber lange still
        await self.sprechen(-8000, 2.0)
        self.assertEqual(self.gate.grund_der_stimme(), "fremde Stimme")

    async def test_aeusserung_bleibt_hoechstens_8_sekunden(self):
        for _ in range(6):
            await self.sprechen(8000, 2.0)
        self.assertLessEqual(len(self.v._zug), int(MAX_PRUEF_SECS * RATE) * 2)

    async def test_fehlgeschlagene_pruefung_entzieht_das_vertrauen(self):
        await self.sprechen(8000, 2.0)
        self.v.on_turn_end()
        self.modell.rechner.embed = mock.Mock(side_effect=RuntimeError("kaputt"))
        urteil = await self.sprechen(-8000, 2.0)
        self.assertEqual(urteil.urteil, UNKLAR)
        self.assertEqual(self.gate.grund_der_stimme(), "Stimme nicht sicher erkannt")
        self.assertNotIn("wert", self.meldungen("stimme")[-1])

    async def test_nan_aus_dem_modell_gilt_als_unklar(self):
        await self.sprechen(8000, 2.0)
        self.v.on_turn_end()
        self.modell.rechner.festes_ergebnis = np.full(DIM, np.nan, dtype=np.float32)
        urteil = await self.sprechen(8000, 2.0)
        self.assertEqual(urteil.urteil, UNKLAR)
        self.assertIsNone(urteil.wert)
        self.assertEqual(self.gate.grund_der_stimme(), "Stimme nicht sicher erkannt")
        self.assertNotIn("wert", self.meldungen("stimme")[-1])

    async def test_ohne_abdruck_oder_im_modus_aus_wird_nicht_gerechnet(self):
        self.gate.setze_abdruck(None)
        self.assertIsNone(await self.sprechen(8000, 2.0))
        self.gate.setze_abdruck(EIGENER)
        self.gate.modus = "aus"
        self.assertIsNone(await self.sprechen(8000, 2.0))
        self.assertEqual(self.modell.rechner.aufrufe, 0)

    async def test_beobachten_misst_und_meldet_sperrt_aber_nicht(self):
        self.gate.modus = "beobachten"
        await self.sprechen(8000, 2.0)
        self.v.on_turn_end()
        await self.sprechen(-8000, 2.0)
        self.assertEqual(self.meldungen("stimme")[-1], {"type": "stimme", "ergebnis": FREMD,
                                                        "wert": self.meldungen("stimme")[-1]["wert"], "modus": "beobachten"})
        self.assertIsNone(await self.gate.pruefen("programm_oeffnen"))

    async def test_abdruck_waehrend_des_rechnens_geloescht_traegt_nichts_ein(self):
        self.modell.rechner.dauer = 0.15
        self.v.on_speech_start(self.uhr.t + 0.2, 0.2)
        for _ in range(100):
            self.uhr.t += 0.02
            await self.v.on_audio(audio(8000))
        self.v.on_speech_stop(self.uhr.t, 0.2)
        await asyncio.sleep(0.03)  # die Prüfung rechnet gerade
        self.gate.setze_abdruck(None)  # die Person löscht ihren Abdruck
        await self.v.urteil_abwarten(5.0)
        self.assertEqual(self.gate.status, "fehlt")
        self.assertEqual(self.meldungen("stimme"), [])

    async def test_mehrere_profile_haben_je_ihr_urteil(self):
        """Zwei Sitzungen im selben Prozess teilen nur den Rechner, nie den Prüfzustand."""
        zweites_gate = bereites_gate(uhr=self.uhr)
        zweiter = Stimmpruefer(zweites_gate, self.modell, speichern=self.speichern, sagen=self.sagen, uhr=self.uhr)
        zweiter.push_frame = mock.AsyncMock()
        await self.sprechen(8000, 2.0)
        self.assertIsNone(self.gate.grund_der_stimme())
        self.assertEqual(zweites_gate.grund_der_stimme(), "Stimme noch nicht erkannt")
        self.assertIsNone(await zweiter.urteil_abwarten(0.05))


class UrteilAbwartenTests(PrueferBasis):
    async def test_ohne_pruefung_gibt_es_kein_urteil(self):
        self.assertIsNone(await self.v.urteil_abwarten(0.05))

    async def test_wartet_auf_die_laufende_pruefung(self):
        self.modell.rechner.dauer = 0.1
        urteil = await self.sprechen(8000, 2.0, warten=False)
        self.assertIsNone(urteil)
        ergebnis = await self.v.urteil_abwarten(2.0)
        self.assertEqual(ergebnis.urteil, EIGEN)

    async def test_zeitueberschreitung_gilt_als_unklar_also_kein_urteil(self):
        self.modell.rechner.dauer = 0.5
        await self.sprechen(8000, 2.0, warten=False)
        self.assertIsNone(await self.v.urteil_abwarten(0.05))
        await self.v.urteil_abwarten(5.0)  # aufräumen

    async def test_wartet_bis_eine_laufende_aeusserung_zu_ende_ist(self):
        self.v.on_speech_start(self.uhr.t + 0.2, 0.2)
        wartend = asyncio.create_task(self.v.urteil_abwarten(2.0))
        await asyncio.sleep(0.02)
        self.assertFalse(wartend.done())
        for _ in range(100):
            self.uhr.t += 0.02
            await self.v.on_audio(audio(8000))
        self.v.on_speech_stop(self.uhr.t, 0.2)
        urteil = await wartend
        self.assertEqual(urteil.urteil, EIGEN)

    async def test_ein_altes_urteil_traegt_kein_neues_transkript(self):
        await self.sprechen(-8000, 2.0)
        self.uhr.t += URTEIL_MAX_ALTER_SECS + 1
        self.assertIsNone(await self.v.urteil_abwarten(0.05))

    async def test_transkript_gehoert_zur_beendeten_aeusserung_nicht_zur_naechsten(self):
        """Kommt das Transkript einer beendeten Äußerung erst, während die nächste läuft, gilt das Urteil der beendeten. Es wartet nicht auf
        das Ende der nächsten und bekommt nicht deren Urteil."""
        self.modell.rechner.dauer = 0.1
        await self.sprechen(-8000, 2.0, warten=False)  # ein Gast, die Prüfung läuft
        self.v.on_speech_start(self.uhr.t + 0.2, 0.2)  # die Person spricht schon wieder
        nr = self.v.aeusserung_zuordnen()
        urteil = await self.v.urteil_abwarten(1.0, nr)
        self.assertIsNotNone(urteil)
        self.assertEqual((urteil.nr, urteil.urteil), (nr, FREMD))

    async def test_transkript_mitten_in_der_rede_gehoert_zur_laufenden_aeusserung(self):
        """Schließt die Erkennung mitten in der Rede ab, gehört das Transkript zur laufenden Äußerung: es wartet auf deren Urteil."""
        await self.sprechen(8000, 2.0)
        self.v.on_turn_end()
        self.v.on_speech_start(self.uhr.t + 0.2, 0.2)
        wartend = asyncio.create_task(self.v.urteil_abwarten(2.0, self.v.aeusserung_zuordnen()))
        for _ in range(100):
            self.uhr.t += 0.02
            await self.v.on_audio(audio(-8000))
        self.v.on_speech_stop(self.uhr.t, 0.2)
        self.assertEqual((await wartend).urteil, FREMD)

    async def test_ein_zweites_transkript_derselben_aeusserung_bekommt_dasselbe_urteil(self):
        a = await self.sprechen(-8000, 2.0)
        b = await self.v.urteil_abwarten(0.05, self.v.aeusserung_zuordnen())
        self.assertEqual(a, b)

    async def test_urteile_sind_durchnummeriert(self):
        a = await self.sprechen(8000, 2.0)
        self.v.on_turn_end()
        b = await self.sprechen(8000, 2.0)
        self.assertGreater(b.nr, a.nr)


class EinlernenImPrueferTests(PrueferBasis):
    async def test_waehrend_des_einlernens_hoert_die_spracherkennung_stille(self):
        self.gate.setze_abdruck(None)
        await self.v.einlernen_starten("stimme-1", mock.sentinel.am)
        self.assertTrue(self.v.einlernen_aktiv)
        self.sagen.assert_awaited_once()
        frame = audio(8000)
        await self.v.on_audio(frame)
        self.assertEqual(frame.audio, bytes(len(frame.audio)))
        await self.v.einlernen_abbrechen()
        frame = audio(8000)
        await self.v.on_audio(frame)
        self.assertNotEqual(frame.audio, bytes(len(frame.audio)))  # danach hört sie wieder mit

    async def test_waehrend_des_einlernens_wird_nicht_geprueft(self):
        self.gate.setze_abdruck(None)
        await self.v.einlernen_starten("stimme-1", mock.sentinel.am)
        self.v.on_speech_start(self.uhr.t + 0.2, 0.2)
        self.v.on_speech_stop(self.uhr.t + 1, 0.2)
        self.assertIsNone(await self.v.urteil_abwarten(0.05))
        self.assertEqual(self.modell.rechner.aufrufe, 0)

    async def test_cleanup_beendet_das_einlernen(self):
        self.gate.setze_abdruck(None)
        await self.v.einlernen_starten("stimme-1", mock.sentinel.am)
        self.assertTrue(self.v.einlernen_aktiv)
        await self.v.cleanup()
        self.assertFalse(self.v.einlernen_aktiv)

    async def test_cleanup_wartet_bis_laufende_pruefungen_beendet_sind(self):
        """Nach dem Aufräumen läuft keine Prüfung mehr weiter: abgebrochen und abgewartet, ohne Fehler nach außen."""
        self.modell.rechner.dauer = 0.3
        await self.sprechen(8000, 2.0, warten=False)
        laufend = list(self.v._tasks)
        self.assertTrue(laufend)
        await self.v.cleanup()
        self.assertTrue(all(t.done() for t in laufend))
        self.assertIsNone(await self.v.urteil_abwarten(0.05))  # wer danach fragt, bekommt kein Urteil

    async def test_status_melden(self):
        await self.v.status_melden()
        self.assertEqual(self.meldungen("stimme")[-1], {"type": "stimme", "status": "bereit", "modus": "streng"})


class FrameTests(PrueferBasis):
    async def test_frames_laufen_unveraendert_durch_und_erreichen_die_behandlung(self):
        # Ein echter push_frame: das Gespräch darf durch die Prüfung nie etwas verlieren
        v = Stimmpruefer(self.gate, self.modell, speichern=self.speichern, sagen=self.sagen, uhr=self.uhr)
        v.on_speech_start = mock.Mock()
        v.on_speech_stop = mock.Mock()
        v.on_turn_end = mock.Mock()
        v.on_bot_speaking = mock.Mock()
        frames = [
            VADUserStartedSpeakingFrame(start_secs=0.2, timestamp=5.0),
            audio(8000),
            VADUserStoppedSpeakingFrame(stop_secs=0.2, timestamp=6.0),
            UserStoppedSpeakingFrame(),
            BotStartedSpeakingFrame(),
            BotStoppedSpeakingFrame(),
        ]
        unten, oben = await run_test(v, frames_to_send=frames)
        v.on_speech_start.assert_called_once_with(5.0, 0.2)
        v.on_speech_stop.assert_called_once_with(6.0, 0.2)
        v.on_turn_end.assert_called_once()
        self.assertEqual([c.args[0] for c in v.on_bot_speaking.call_args_list], [True, False])
        self.assertEqual(sum(isinstance(f, InputAudioRawFrame) for f in unten), 1)


if __name__ == "__main__":
    unittest.main()
