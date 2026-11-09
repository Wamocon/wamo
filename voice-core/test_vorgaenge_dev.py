"""Der Entwicklungsauftrag meldet seine Arbeit als Vorgang (dev_agent.py mit vorgang_stream.py): Aufgabenliste aus dem Stream,
Rückfall auf die Schritte des Auftrags, Freigaben mit der Kennung der vorhandenen Karte, Fortsetzen und Neustart, Ablehnen,
Verwerfen, Fehlstarts. SSH ist wie in test_dev_agent_sokrates2.py gemockt; ein Stream läuft als Textdatei ein, kein echtes Claude."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import dev_agent
import dev_guard
from test_dev_agent_sokrates2 import KOLLEGIN, PUSH, FakeProc, RemoteFlowTests, stream
from vorgaenge import Vorgaenge

STREAM_DATEI = Path(__file__).resolve().parent / "testdaten" / "vorgang_stream.jsonl"


def stream_aus_datei() -> list[str]:
    return [z for z in STREAM_DATEI.read_text(encoding="utf-8").splitlines() if z.strip()]


class Aufnahme:
    def __init__(self):
        self.gesendet: list[dict] = []
        self.gespeichert: list[dict] = []

    async def senden(self, n: dict) -> None:
        self.gesendet.append(n)

    async def speichern(self, d: dict) -> None:
        self.gespeichert.append(d)

    def stati(self) -> list[str]:
        return [m["vorgang"]["status"] for m in self.gesendet]


class VorgangFlowTests(unittest.IsolatedAsyncioTestCase):
    """Nutzt die SSH-Attrappen von RemoteFlowTests (ohne deren Tests zu wiederholen)."""

    async def asyncSetUp(self):
        self.basis = RemoteFlowTests("test_job_runs_over_ssh_with_lifeline")
        await self.basis.asyncSetUp()
        self.aufnahme = Aufnahme()
        self.reg = Vorgaenge(self.aufnahme.senden, speichern=self.aufnahme.speichern, takt_s=0.0)
        self.agent = dev_agent.DevAgent(memory=self.basis.memory, auftraggeber="Erika", vorgaenge=self.reg)
        self.agent.attach(self.basis.task)
        self.basis.agent = self.agent
        self.basis.pending_line = ""  # ohne Anfragen aus der Sandbox, solange ein Test keine setzt

    async def asyncTearDown(self):
        await self.basis.asyncTearDown()

    @property
    def vorgang(self):
        (v,) = self.reg.alle()
        return v

    async def start(self, **extra):
        job = await self.basis.prepare_and_start(**extra)
        await self.reg.warten()
        return job

    async def test_karte_legt_einen_geplanten_vorgang_an_der_auf_das_canvas_des_auftrags_zeigt(self):
        params = type("P", (), {})()
        params.arguments = {"auftrag": "Baue eine Rezepte-App", "projekt": "rezepte-app", "art": "umsetzung"}
        antwort: dict = {}

        async def rueckruf(r):
            antwort.update(r)

        params.result_callback = rueckruf
        await self.agent._vorbereiten(params)
        await self.reg.warten()
        v = self.vorgang
        self.assertEqual((v.art, v.status, v.titel), ("entwicklung", "geplant", "Umsetzung: rezepte-app"))
        self.assertEqual(v.canvas_id, f"dev-{antwort['auftrag_id']}")
        self.assertEqual(self.aufnahme.stati(), ["geplant"])

    async def test_aufgabenliste_aus_dem_stream_wird_zu_schritten_und_der_lauf_endet_fertig(self):
        lines = stream_aus_datei()
        self.basis.start_procs.append(FakeProc(lines))
        job = await self.start()
        v = self.vorgang
        self.assertEqual(v.status, "fertig")
        self.assertEqual([s.titel for s in v.arbeit], ["Tests schreiben", "Funktion umsetzen", "Ergebnis prüfen"])
        self.assertEqual([s.status for s in v.arbeit], ["fertig"] * 3)
        self.assertEqual(v.gesamt(), 1.0)
        self.assertFalse(v.offenes_ende)
        self.assertEqual([k.erfuellt for k in v.kriterien], [True, True, True])
        self.assertIsNone(v.freigabe_id)
        letzte = self.aufnahme.gesendet[-1]["vorgang"]
        self.assertEqual(letzte["status"], "fertig")
        self.assertEqual(letzte["ergebnis"], {"canvas_id": f"dev-{job.id}"})
        # gespeichert wird bei jedem Statuswechsel, nie bei jedem Schritt
        gespeichert = [d["status"] for d in self.aufnahme.gespeichert]
        self.assertEqual(gespeichert[-1], "fertig")
        self.assertLessEqual(len(gespeichert), 6)

    async def test_ohne_aufgabenliste_zeigt_der_vorgang_die_schritte_des_auftrags_ohne_restzeit(self):
        self.basis.start_procs.append(FakeProc(stream()))
        job = await self.start()
        v = self.vorgang
        self.assertTrue(any(s.titel.startswith("Befehl: `git push") for s in v.arbeit) or v.status == "wartet_freigabe")
        self.assertTrue(v.offenes_ende)
        self.assertIsNone(v.restzeit_s(1e9))
        self.assertLessEqual(len(v.arbeit), 30)
        self.assertIn(job.id, v.canvas_id)

    async def test_offene_freigabe_setzt_wartet_und_traegt_die_kennung_der_vorhandenen_karte(self):
        self.basis.pending_line = json.dumps({"id": dev_guard.fingerprint("Bash", PUSH), "grund": "git push",
                                              "tool": "Bash", "input": PUSH})
        self.basis.start_procs.append(FakeProc(stream()))
        await self.start()
        v = self.vorgang
        karte = next(m for m in self.basis.task.messages() if m.get("type") == "freigaben")
        self.assertEqual(v.status, "wartet_freigabe")
        self.assertEqual(v.freigabe_id, karte["items"][0]["id"])  # dieselbe Kennung wie die Karte in Freigaben.tsx
        self.assertEqual([s.status for s in v.freigaben], ["wartet_freigabe"])
        self.assertIsNone(v.beendet_ms)
        self.assertEqual(self.aufnahme.stati()[-1], "wartet_freigabe")
        self.assertFalse(v.kriterien[2].erfuellt)

    async def test_freigabe_erteilt_setzt_den_lauf_fort_und_beendet_ihn_fertig(self):
        fp = dev_guard.fingerprint("Bash", PUSH)
        self.basis.pending_line = json.dumps({"id": fp, "grund": "git push", "tool": "Bash", "input": PUSH})
        self.basis.start_procs.append(FakeProc(stream()))
        job = await self.start()
        self.basis.pending_line = ""
        self.basis.start_procs.append(FakeProc(stream(tool=False)))
        await self.agent.on_client_message("freigabe", {"job": job.id, "id": fp, "entscheidung": "ja"})
        await self.reg.warten()
        self.assertIn(self.vorgang.status, ("laeuft", "pruefen", "fertig"))  # sofort: der Lauf geht weiter
        self.assertIsNone(self.vorgang.freigabe_id)
        await self.agent._running
        await self.reg.warten()
        v = self.vorgang
        self.assertEqual(v.status, "fertig")
        self.assertEqual([s.status for s in v.freigaben], ["fertig"])  # die Freigabe bleibt als erledigter Schritt stehen
        self.assertEqual(v.kriterien[2].erfuellt, True)

    async def test_freigabe_abgelehnt_beendet_den_vorgang_abgebrochen(self):
        fp = dev_guard.fingerprint("Bash", PUSH)
        self.basis.pending_line = json.dumps({"id": fp, "grund": "git push", "tool": "Bash", "input": PUSH})
        self.basis.start_procs.append(FakeProc(stream()))
        job = await self.start()
        await self.agent.on_client_message("freigabe", {"job": job.id, "id": fp, "entscheidung": "nein"})
        await self.reg.warten()
        v = self.vorgang
        self.assertEqual(v.status, "abgebrochen")
        self.assertEqual([s.status for s in v.freigaben], ["uebersprungen"])
        self.assertEqual(self.aufnahme.stati()[-1], "abgebrochen")

    async def test_freigabe_nicht_uebertragen_bleibt_wartend(self):
        fp = dev_guard.fingerprint("Bash", PUSH)
        self.basis.pending_line = json.dumps({"id": fp, "grund": "git push", "tool": "Bash", "input": PUSH})
        self.basis.start_procs.append(FakeProc(stream()))
        job = await self.start()
        self.basis.approve_rc = 1
        await self.agent.on_client_message("freigabe", {"job": job.id, "id": fp, "entscheidung": "ja"})
        await self.reg.warten()
        self.assertEqual(self.vorgang.status, "wartet_freigabe")
        self.assertEqual(self.vorgang.freigabe_id, fp)

    async def test_mehr_zugaenge_freigabe_startet_neu_und_beginnt_die_liste_von_vorn(self):
        eingabe = {"projekt": "rezepte-app", "neu": ["GH_TOKEN"], "bisher": ["ANTHROPIC_API_KEY"]}
        fp = dev_guard.fingerprint("WAMO-Zugang", eingabe)
        self.basis.pending_line = "{}"
        self.basis.start_procs += [
            FakeProc([{"type": "wamo", "ereignis": "selbsttest_ok"},
                      {"type": "wamo", "ereignis": "zugang_erweitert", "id": fp, "tool": "WAMO-Zugang", "input": eingabe,
                       "grund": "Mehr Zugänge als frühere Aufträge in diesem Projekt: GH_TOKEN"}], returncode=7),
            FakeProc(stream_aus_datei())]
        job = await self.start()
        self.assertEqual(self.vorgang.status, "wartet_freigabe")
        self.assertEqual(self.vorgang.freigabe_id, fp)
        await self.agent.on_client_message("freigabe", {"job": job.id, "id": fp, "entscheidung": "ja"})
        await self.agent._running
        await self.reg.warten()
        v = self.vorgang
        self.assertEqual(v.status, "fertig")
        self.assertEqual([s.titel for s in v.arbeit], ["Tests schreiben", "Funktion umsetzen", "Ergebnis prüfen"])

    async def test_fehlstarts_enden_als_fehler(self):
        self.basis.start_procs.append(FakeProc([{"type": "wamo", "ereignis": "selbsttest_fehlgeschlagen", "grund": "x"}],
                                               returncode=3))
        await self.start()
        v = self.vorgang
        self.assertEqual(v.status, "fehler")
        self.assertIs(v.kriterien[0].erfuellt, False)
        self.assertIs(v.kriterien[1].erfuellt, False)

    async def test_ohne_claude_zugang_endet_der_vorgang_als_fehler(self):
        self.basis.memory.user_id = KOLLEGIN
        await self.start()
        v = self.vorgang
        self.assertEqual(v.status, "fehler")
        self.assertIn("Zugang", v.kriterien[1].hinweis or "")

    async def test_verworfener_auftrag_ist_abgebrochen(self):
        params = type("P", (), {})()
        params.arguments = {"auftrag": "Baue etwas", "projekt": "rezepte-app", "art": "umsetzung"}
        antwort: dict = {}

        async def rueckruf(r):
            antwort.update(r)

        params.result_callback = rueckruf
        await self.agent._vorbereiten(params)
        await self.agent.on_client_message("auftrag_start", {"job": antwort["auftrag_id"], "entscheidung": "nein"})
        await self.reg.warten()
        self.assertEqual(self.vorgang.status, "abgebrochen")

    async def test_ein_kaputter_vorgang_stoert_den_auftrag_nicht(self):
        class Kaputt:
            def __getattr__(self, name):
                def aufruf(*a, **kw):
                    raise RuntimeError("Registry kaputt")
                return aufruf

        self.agent._vorgaenge = Kaputt()
        self.basis.start_procs.append(FakeProc(stream_aus_datei()))
        job = await self.basis.prepare_and_start()
        self.assertEqual(job.status, "fertig")

    async def test_ohne_registry_laeuft_alles_wie_bisher(self):
        agent = dev_agent.DevAgent(memory=self.basis.memory, auftraggeber="Erika")
        agent.attach(self.basis.task)
        self.basis.agent = agent
        self.basis.start_procs.append(FakeProc(stream_aus_datei()))
        job = await self.basis.prepare_and_start()
        self.assertEqual(job.status, "fertig")
        self.assertEqual(self.reg.alle(), [])

    async def test_nachrichten_enthalten_nie_den_text_von_claude_code_oder_befehle_mit_schluessel(self):
        geheim = "sk-ant-api03-" + "Z" * 40
        lines = stream_aus_datei()
        lines.insert(3, json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "toolu_x", "name": "TaskCreate", "input": {"subject": f"Token {geheim} prüfen"}}]}}))
        self.basis.start_procs.append(FakeProc(lines))
        await self.start()
        alles = json.dumps(self.aufnahme.gesendet) + json.dumps(self.aufnahme.gespeichert)
        self.assertNotIn(geheim, alles)
        self.assertNotIn("Die Funktion summe ist umgesetzt", alles)  # das Ergebnis steht nur auf dem Canvas


if __name__ == "__main__":
    unittest.main()
