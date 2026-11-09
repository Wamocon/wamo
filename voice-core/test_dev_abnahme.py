"""Die Abnahmeschleife des Entwicklungsauftrags (dev_agent.py mit abnahme.py): arbeiten, prüfen, bei Nichterfüllung die nächste Runde
mit genau den offenen Punkten, bis alles erfüllt ist oder Runden oder Zeit erschöpft sind.

Kein echtes Claude und kein Netz: SSH und der Claude-Prozess sind wie in test_dev_agent_sokrates2.py gemockt (Stream als Zeilen), das
beurteilende Modell und der Befehlsausführer sind Attrappen, die Uhr der Schleife ist eine Zählung.

Ausführen:  .venv\\Scripts\\python.exe -m pytest -q -p no:cacheprovider test_dev_abnahme.py   (im Ordner voice-core/)
"""

from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest import mock

import abnahme
import dev_agent
import dev_guard
import test_dev_agent_sokrates2 as sokrates2  # das Modul, nicht dessen Testklasse: sie würde hier noch einmal laufen
from test_dev_agent_sokrates2 import PUSH, SID, FakeProc
from vorgang_stream import KRIT_FREIGABEN, KRIT_LAUF, KRIT_SELBSTTEST, KRIT_ZIEL
from vorgaenge import Vorgaenge

README = "Die README erklärt die Installation"
BEISPIEL = "Ein Beispielaufruf steht in der README"
ORDNER = "/srv/wamo/projekte/rezepte-app"


def lauf(bericht: str = "README ergänzt.", dateien: tuple[str, ...] = (), ende: str = "fertig") -> list[dict]:
    zeilen: list[dict] = [{"type": "wamo", "ereignis": "selbsttest_ok"},
                          {"type": "system", "subtype": "init", "session_id": SID}]
    for d in dateien:
        zeilen.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Write", "input": {"file_path": f"{ORDNER}/{d}"}}]}})
    zeilen += [{"type": "result", "result": bericht, "total_cost_usd": 0.05, "session_id": SID},
               {"type": "wamo", "ereignis": "ende", "ergebnis": ende, "code": 0}]
    return zeilen


def urteil(erfuellt: bool, hinweis: str = "") -> str:
    return json.dumps({"erfuellt": erfuellt, "hinweis": hinweis})


class Urteile:
    """Attrappe für das beurteilende Modell: antwortet der Reihe nach, merkt sich jede Anfrage."""

    def __init__(self, *antworten: str | Exception):
        self.antworten, self.aufrufe, self.vor_antwort = list(antworten), [], None

    async def __call__(self, system: str, inhalt: str) -> str:
        self.aufrufe.append(inhalt)
        if self.vor_antwort:
            await self.vor_antwort()
        antwort = self.antworten.pop(0) if self.antworten else urteil(False, "keine Antwort vorbereitet")
        if isinstance(antwort, Exception):
            raise antwort
        return antwort


class Ausfuehrer:
    def __init__(self, *antworten):
        self.antworten, self.aufrufe = list(antworten), []

    async def __call__(self, argv, ordner):
        self.aufrufe.append(tuple(argv))
        return self.antworten.pop(0)


class Aufnahme:
    def __init__(self):
        self.gesendet: list[dict] = []

    async def senden(self, n: dict) -> None:
        self.gesendet.append(n)

    def stati(self) -> list[str]:
        return [m["vorgang"]["status"] for m in self.gesendet]


class SchleifeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.basis = sokrates2.RemoteFlowTests("test_job_runs_over_ssh_with_lifeline")
        await self.basis.asyncSetUp()
        self.aufnahme = Aufnahme()
        self.reg = Vorgaenge(self.aufnahme.senden, takt_s=0.0)
        self.urteile = Urteile()
        self.uhr = [0.0]
        self.basis.pending_line = ""
        self.neu_agent()

    async def asyncTearDown(self):
        await self.basis.asyncTearDown()

    def befehle_an(self, wert: str = "ja") -> None:
        """Schaltet Befehlskriterien zu (Standard aus, nur der Wert ja)."""
        patcher = mock.patch.dict(os.environ, {abnahme.BEFEHLE_ENV: wert})
        patcher.start()
        self.addCleanup(patcher.stop)

    def neu_agent(self, **kw):
        self.agent = dev_agent.DevAgent(memory=self.basis.memory, auftraggeber="Erika", vorgaenge=self.reg,
                                        beurteiler=kw.pop("beurteiler", self.urteile), **kw)
        self.agent._jetzt = lambda: self.uhr[0]
        self.agent.attach(self.basis.task)
        self.basis.agent = self.agent

    @property
    def vorgang(self):
        (v,) = self.reg.alle()
        return v

    def laeufe(self, *proc: FakeProc):
        self.basis.start_procs.extend(proc)
        self.procs = list(proc)

    def kopf(self, nr: int) -> dict:
        return json.loads(self.procs[nr].stdin.data.decode("utf-8").splitlines()[0])

    async def vorbereiten(self, **argumente):
        params = type("P", (), {})()
        params.arguments = {"auftrag": "Dokumentiere die Rezepte-App", "projekt": "rezepte-app", "art": "umsetzung",
                            **argumente}
        antwort: dict = {}

        async def rueckruf(r):
            antwort.update(r)

        params.result_callback = rueckruf
        await self.agent._vorbereiten(params)
        return antwort

    async def starten(self, **argumente):
        antwort = await self.vorbereiten(**argumente)
        self.assertTrue(antwort["ok"], antwort)
        await self.agent.on_client_message("auftrag_start", {"job": antwort["auftrag_id"], "entscheidung": "ja"})
        await self.agent._running
        await self.reg.warten()
        return self.agent._jobs[antwort["auftrag_id"]]

    # ---------------------------------------------------------------- ohne Ziel bleibt alles wie bisher
    async def test_ohne_abnahme_eine_runde_kein_pruefen(self):
        self.laeufe(FakeProc(lauf()))
        job = await self.starten(max_runden=5, zeitlimit_min=60)  # ohne abnahme ohne Wirkung
        self.assertEqual(job.status, "fertig")
        self.assertEqual((job.kriterien, job.max_runden, job.runde), ([], 1, 1))
        self.assertEqual(self.urteile.aufrufe, [])
        self.assertEqual(len(self.basis.remote("start")), 1)
        v = self.vorgang
        self.assertEqual((v.status, v.runde, v.max_runden), ("fertig", None, None))
        self.assertEqual([k.text for k in v.kriterien], [KRIT_SELBSTTEST, KRIT_LAUF, KRIT_FREIGABEN])
        kopf = self.kopf(0)
        self.assertNotIn("Prüfbares Ziel", kopf["auftrag"])

    # ---------------------------------------------------------------- Ziel in der ersten Runde erreicht
    async def test_ziel_in_runde_eins_erreicht(self):
        self.urteile.vor_antwort = self.reg.warten  # die Tafel bekommt den Stand "wird geprüft" zu sehen
        self.urteile.antworten = [urteil(True, "Abschnitt Installation vorhanden")]
        self.laeufe(FakeProc(lauf("README um Installation ergänzt.", ("README.md",))))
        job = await self.starten(abnahme=[README])
        self.assertEqual(job.status, "fertig")
        self.assertEqual(len(self.basis.remote("start")), 1)
        self.assertIn("Prüfbares Ziel", self.kopf(0)["auftrag"])  # das Ziel steht im ersten Prompt
        self.assertIn(README, self.kopf(0)["auftrag"])
        (anfrage,) = self.urteile.aufrufe
        self.assertIn("README um Installation ergänzt.", anfrage)  # der Abschlussbericht
        self.assertIn("README.md", anfrage)
        self.assertNotIn(ORDNER, anfrage)  # Dateien relativ zum Projekt
        v = self.vorgang
        self.assertEqual((v.status, v.runde, v.max_runden), ("fertig", 1, 3))
        self.assertEqual([k.text for k in v.kriterien], [KRIT_SELBSTTEST, KRIT_LAUF, KRIT_FREIGABEN, KRIT_ZIEL, README])
        self.assertEqual([k.erfuellt for k in v.kriterien], [True, True, True, True, True])
        self.assertEqual(v.kriterien[-1].hinweis, "Abschnitt Installation vorhanden")
        self.assertIn("pruefen", self.aufnahme.stati())
        texte = self.basis.task.llm_texts()
        self.assertIn("fertig", texte[-1])
        self.assertIn("1 von 1", texte[-1])

    # ---------------------------------------------------------------- Runde zwei nur mit den offenen Punkten
    async def test_zweite_runde_bekommt_nur_die_offenen_kriterien(self):
        self.urteile.vor_antwort = self.reg.warten
        self.urteile.antworten = [urteil(True, "ok"), urteil(False, "Beispielaufruf fehlt"),
                                  urteil(True, "ok"), urteil(True, "Beispiel vorhanden")]
        self.laeufe(FakeProc(lauf("Installation ergänzt.", ("README.md",))), FakeProc(lauf("Beispiel ergänzt.")))
        job = await self.starten(abnahme=[README, BEISPIEL])
        self.assertEqual((job.status, job.runde), ("fertig", 2))
        starts = self.basis.remote("start")
        self.assertEqual(len(starts), 2)
        self.assertTrue(starts[0].endswith("umsetzung"))
        self.assertTrue(starts[1].endswith(f"umsetzung {SID}"))  # Wiederaufnahme derselben Sitzung
        zweit = self.kopf(1)["auftrag"]
        self.assertIn(BEISPIEL, zweit)
        self.assertIn("Beispielaufruf fehlt", zweit)
        self.assertIn("Runde 2 von 3", zweit)
        self.assertNotIn(README, zweit)  # Erledigtes steht nicht mehr darin
        self.assertNotIn("Dokumentiere die Rezepte-App", zweit)  # die Sitzung kennt den Auftrag
        self.assertIn("Runde 2 von 3: offene Kriterien nachbessern", job.steps)
        v = self.vorgang
        self.assertEqual((v.status, v.runde, v.max_runden), ("fertig", 2, 3))
        runden = [m["vorgang"]["runde"] for m in self.aufnahme.gesendet]
        self.assertIn(1, runden)
        self.assertIn(2, runden)
        self.assertAlmostEqual(job.cost_usd, 0.10)  # jede Runde zählt in den Verbrauch
        self.assertEqual(len(self.urteile.aufrufe), 4)  # beide Kriterien in jeder Runde: ein Rückschritt fiele auf

    async def test_ohne_sitzungskennung_bekommt_die_naechste_runde_den_auftrag_mit(self):
        ohne = [{"type": "result", "result": "Bericht", "total_cost_usd": 0.01}, {"type": "wamo", "ereignis": "ende",
                                                                                   "ergebnis": "fertig", "code": 0}]
        self.urteile.antworten = [urteil(False, "fehlt"), urteil(True)]
        self.laeufe(FakeProc(ohne), FakeProc(lauf()))
        await self.starten(abnahme=[README])
        starts = self.basis.remote("start")
        self.assertTrue(starts[1].endswith("umsetzung"))  # keine Sitzung zum Fortsetzen
        self.assertIn("Dokumentiere die Rezepte-App", self.kopf(1)["auftrag"])

    async def test_der_bericht_der_vorrunde_wird_nicht_noch_einmal_beurteilt(self):
        ohne_bericht = [{"type": "system", "subtype": "init", "session_id": SID},
                        {"type": "wamo", "ereignis": "ende", "ergebnis": "fertig", "code": 0}]
        self.urteile.antworten = [urteil(False, "fehlt"), urteil(False, "fehlt")]
        self.laeufe(FakeProc(lauf("BERICHT-RUNDE-EINS", ("README.md",))), FakeProc(ohne_bericht))
        await self.starten(abnahme=[README], max_runden=2)
        erste, zweite = self.urteile.aufrufe
        self.assertIn("BERICHT-RUNDE-EINS", erste)
        self.assertNotIn("BERICHT-RUNDE-EINS", zweite)

    # ---------------------------------------------------------------- Ziel verfehlt: ehrlich melden
    async def test_ziel_nach_den_runden_nicht_erreicht_ist_ein_fehler_nie_fertig(self):
        self.urteile.antworten = [urteil(False, "kein Abschnitt")] * 2
        self.laeufe(FakeProc(lauf()), FakeProc(lauf()))
        job = await self.starten(abnahme=[README], max_runden=2)
        self.assertEqual(job.status, "Ziel nach 2 Runden nicht erreicht")
        self.assertEqual(len(self.basis.remote("start")), 2)
        v = self.vorgang
        self.assertEqual(v.status, "fehler")
        ziel = next(k for k in v.kriterien if k.text == KRIT_ZIEL)
        self.assertIs(ziel.erfuellt, False)
        self.assertIn("2 Runden", ziel.hinweis)
        offen = next(k for k in v.kriterien if k.text == README)
        self.assertEqual((offen.erfuellt, offen.hinweis), (False, "kein Abschnitt"))
        self.assertIs(next(k for k in v.kriterien if k.text == KRIT_LAUF).erfuellt, True)  # der Lauf selbst war in Ordnung
        canvas = [m for m in self.basis.task.messages() if m.get("type") == "canvas"][-1]["item"]["text"]
        self.assertIn("## Abnahme", canvas)
        self.assertIn(f"nicht erfüllt: {README} (Hinweis: kein Abschnitt)", canvas)
        self.assertIn("Runde:** 2 von 2", canvas)
        meldung = self.basis.task.llm_texts()[-1]
        self.assertIn("Ziel nach 2 Runden nicht erreicht", meldung)
        self.assertIn("0 von 1 Kriterien erfüllt", meldung)
        self.assertNotIn("kein Abschnitt", meldung)  # Hinweise aus Prüfungen gehen nie ans Sprachmodell
        self.assertNotIn(": fertig", meldung)

    async def test_eine_einzige_runde_wird_im_singular_gemeldet(self):
        self.urteile.antworten = [urteil(False, "fehlt")]
        self.laeufe(FakeProc(lauf()))
        job = await self.starten(abnahme=[README], max_runden=1)
        self.assertEqual(job.status, "Ziel nach 1 Runde nicht erreicht")
        self.assertEqual(self.vorgang.status, "fehler")

    async def test_zeitlimit_beendet_die_schleife_vor_der_naechsten_runde(self):
        async def zeit_vergeht():
            self.uhr[0] += 120.0

        self.urteile.vor_antwort = zeit_vergeht
        self.urteile.antworten = [urteil(False, "fehlt")]
        self.laeufe(FakeProc(lauf()), FakeProc(lauf()))
        job = await self.starten(abnahme=[README], zeitlimit_min=1)
        self.assertEqual(job.status, "Ziel nach 1 Runde nicht erreicht (Zeitlimit)")
        self.assertEqual(len(self.basis.remote("start")), 1)
        self.assertEqual(self.vorgang.status, "fehler")

    # ---------------------------------------------------------------- Freigaben bleiben unangetastet
    async def test_offene_freigabe_haelt_die_schleife_an_und_zaehlt_nicht_gegen_das_zeitlimit(self):
        fp = dev_guard.fingerprint("Bash", PUSH)
        self.basis.pending_line = json.dumps({"id": fp, "grund": "git push", "tool": "Bash", "input": PUSH})
        self.urteile.antworten = [urteil(True, "ok")]
        self.laeufe(FakeProc(lauf("Push wartet.")))
        job = await self.starten(abnahme=[README], zeitlimit_min=1)
        self.assertEqual(self.vorgang.status, "wartet_freigabe")
        self.assertEqual(self.urteile.aufrufe, [])  # solange eine Freigabe offen ist, wird nichts geprüft
        self.assertEqual(len(self.basis.remote("start")), 1)
        self.uhr[0] += 10_000.0  # lange Wartezeit auf den Klick
        self.basis.pending_line = ""
        self.basis.start_procs.append(FakeProc(lauf("Push erledigt.")))
        await self.agent.on_client_message("freigabe", {"job": job.id, "id": fp, "entscheidung": "ja"})
        await self.agent._running
        await self.reg.warten()
        self.assertEqual(job.status, "fertig")  # die Wartezeit zählte nicht gegen das Limit von einer Minute
        self.assertEqual(len(self.urteile.aufrufe), 1)
        self.assertEqual(job.runde, 1)
        self.assertLess(job.aktiv_s, 60.0)
        self.assertEqual(self.vorgang.status, "fertig")

    async def test_abgelehnte_freigabe_beendet_den_auftrag_ohne_weitere_runde(self):
        fp = dev_guard.fingerprint("Bash", PUSH)
        self.basis.pending_line = json.dumps({"id": fp, "grund": "git push", "tool": "Bash", "input": PUSH})
        self.laeufe(FakeProc(lauf()))
        job = await self.starten(abnahme=[README])
        await self.agent.on_client_message("freigabe", {"job": job.id, "id": fp, "entscheidung": "nein"})
        await self.reg.warten()
        self.assertEqual(self.vorgang.status, "abgebrochen")
        self.assertEqual(self.urteile.aufrufe, [])
        self.assertEqual(len(self.basis.remote("start")), 1)

    # ---------------------------------------------------------------- Fehler des Laufs werden nicht schöngeprüft
    async def test_fehlgeschlagener_lauf_wird_nicht_geprueft_und_nicht_wiederholt(self):
        self.laeufe(FakeProc(lauf(ende="fehler"), returncode=1))
        job = await self.starten(abnahme=[README])
        self.assertTrue(job.status.startswith("beendet"), job.status)
        self.assertEqual(self.urteile.aufrufe, [])
        self.assertEqual(len(self.basis.remote("start")), 1)
        v = self.vorgang
        self.assertEqual(v.status, "fehler")
        self.assertIs(next(k for k in v.kriterien if k.text == KRIT_ZIEL).erfuellt, False)

    async def test_fehler_des_beurteilenden_modells_heisst_nicht_erfuellt(self):
        self.urteile.antworten = [RuntimeError("Netz weg")]
        self.laeufe(FakeProc(lauf()))
        job = await self.starten(abnahme=[README], max_runden=1)
        self.assertEqual(job.status, "Ziel nach 1 Runde nicht erreicht")
        k = next(k for k in self.vorgang.kriterien if k.text == README)
        self.assertEqual(k.erfuellt, False)
        self.assertIn("nicht möglich", k.hinweis)

    # ---------------------------------------------------------------- Befehlskriterien
    async def test_befehlskriterien_laufen_ueber_den_ausfuehrer(self):
        self.befehle_an()
        ausfuehrer = Ausfuehrer((1, "...\n2 failed in 0.3s"), (0, "3 passed"))
        self.neu_agent(befehl_ausfuehrer=ausfuehrer)
        self.laeufe(FakeProc(lauf()), FakeProc(lauf()))
        job = await self.starten(abnahme=["befehl: pytest -q"])
        self.assertEqual((job.status, job.runde), ("fertig", 2))
        self.assertEqual(ausfuehrer.aufrufe, [("pytest", "-q"), ("pytest", "-q")])
        zweit = self.kopf(1)["auftrag"]
        self.assertIn("befehl: pytest -q", zweit)
        self.assertIn("Exit 1", zweit)
        self.assertIn("2 failed", zweit)
        self.assertEqual(self.urteile.aufrufe, [])  # Befehle brauchen kein Modell

    async def test_ein_befehl_der_den_waechter_nicht_passiert_wird_nie_ausgefuehrt(self):
        self.befehle_an()
        ausfuehrer = Ausfuehrer((0, ""))
        self.neu_agent(befehl_ausfuehrer=ausfuehrer)
        self.laeufe(FakeProc(lauf()))
        job = await self.starten(abnahme=["befehl: grep -q x .env"], max_runden=1)
        self.assertEqual(ausfuehrer.aufrufe, [])
        self.assertEqual(job.status, "Ziel nach 1 Runde nicht erreicht")

    # ---------------------------------------------------------------- Vorbereiten
    async def test_vorbereiten_begrenzt_runden_und_zeit_und_nennt_das_ziel(self):
        antwort = await self.vorbereiten(abnahme=[README], max_runden=9, zeitlimit_min=99)
        job = self.agent._jobs[antwort["auftrag_id"]]
        self.assertEqual((job.max_runden, job.zeitlimit_s), (5, 60 * 60))
        self.assertEqual(antwort["abnahme"], [README])
        self.assertEqual(antwort["max_runden"], 5)
        self.assertIn("Ziel", antwort["hinweis"])
        standard = self.agent._jobs[(await self.vorbereiten(abnahme=[README]))["auftrag_id"]]
        self.assertEqual((standard.max_runden, standard.zeitlimit_s), (3, 30 * 60))
        for roh, erwartet in (("2", 2), (0, 1), (-4, 1), ("viel", 3), (True, 3), (2.0, 2), (None, 3)):
            with self.subTest(roh=roh):
                j = self.agent._jobs[(await self.vorbereiten(abnahme=[README], max_runden=roh))["auftrag_id"]]
                self.assertEqual(j.max_runden, erwartet)

    async def test_vorbereiten_weist_schlechte_kriterien_mit_einem_satz_ab(self):
        for abnahme, wort in (([f"K{i}" for i in range(6)], "höchstens 5"), (["x" * 301], "300"),
                              (["befehl: pytest -q"], "Betriebsart"), (5, "Liste")):
            with self.subTest(wort=wort):
                antwort = await self.vorbereiten(abnahme=abnahme)
                self.assertFalse(antwort["ok"])
                self.assertIn(wort, antwort["fehler"])
        self.assertEqual(self.agent._jobs, {})  # nichts angelegt, keine Karte

    async def test_ohne_beurteiler_sind_aussagen_nicht_pruefbar(self):
        self.neu_agent(beurteiler=None)
        antwort = await self.vorbereiten(abnahme=[README])
        self.assertFalse(antwort["ok"])
        self.assertIn("beurteil", antwort["fehler"])

    async def test_befehl_ist_nur_mit_ausfuehrer_oder_betriebsart_lokal_erlaubt(self):
        self.befehle_an()
        self.neu_agent(befehl_ausfuehrer=Ausfuehrer())
        self.assertTrue((await self.vorbereiten(abnahme=["befehl: pytest -q"]))["ok"])
        self.neu_agent(ort="lokal")
        self.assertIs(self.agent._ausfuehrer.__name__, "befehl_ausfuehren")
        beschreibung = self.agent.schemas()[0].properties["abnahme"]["description"]
        self.assertIn("befehl:", beschreibung)

    async def test_befehlskriterien_sind_ohne_zuschaltung_aus_auch_mit_ausfuehrer_und_lokal(self):
        """WAMO_ABNAHME_BEFEHLE (Standard aus): ohne den Wert ja lehnt schon das Vorbereiten ein befehl: ab, mit klarer Meldung."""
        for kw in ({"befehl_ausfuehrer": Ausfuehrer()}, {"ort": "lokal"}):
            self.neu_agent(**kw)
            with self.subTest(kw=list(kw)):
                antwort = await self.vorbereiten(abnahme=["befehl: pytest -q"])
                self.assertFalse(antwort["ok"])
                self.assertIn("zugeschaltet", antwort["fehler"])
                self.assertIn("Aussage", antwort["fehler"])
                self.assertEqual(self.agent._jobs, {})  # keine Karte, nichts angelegt
                beschreibung = self.agent.schemas()[0].properties["abnahme"]["description"]
                self.assertNotIn("befehl:", beschreibung)  # das Modell bietet sie gar nicht erst an
                self.assertTrue((await self.vorbereiten(abnahme=[README]))["ok"])  # Aussagen bleiben möglich

    async def test_nur_der_wert_ja_schaltet_befehlskriterien_zu(self):
        for wert in ("an", "1", "true", "JA", "Ja", "on", "nein", "", "jaa", "j"):
            with self.subTest(wert=wert), mock.patch.dict(os.environ, {abnahme.BEFEHLE_ENV: wert}):
                self.neu_agent(befehl_ausfuehrer=Ausfuehrer())
                self.assertFalse((await self.vorbereiten(abnahme=["befehl: pytest -q"]))["ok"])
        with mock.patch.dict(os.environ, {abnahme.BEFEHLE_ENV: "ja"}):
            self.neu_agent(befehl_ausfuehrer=Ausfuehrer())
            self.assertTrue((await self.vorbereiten(abnahme=["befehl: pytest -q"]))["ok"])
        env = {k: v for k, v in os.environ.items() if k != abnahme.BEFEHLE_ENV}
        with mock.patch.dict(os.environ, env, clear=True):  # ganz ohne Variable
            self.neu_agent(befehl_ausfuehrer=Ausfuehrer())
            self.assertFalse((await self.vorbereiten(abnahme=["befehl: pytest -q"]))["ok"])

    async def test_zugeschaltete_befehle_prueft_weiter_die_allowlist(self):
        self.befehle_an()
        self.neu_agent(befehl_ausfuehrer=Ausfuehrer())
        for roh in ("befehl: pytest -p myplugin", "befehl: go test -exec ./x ./...", "befehl: node --test --import ./x.js"):
            with self.subTest(roh=roh):
                antwort = await self.vorbereiten(abnahme=[roh])
                self.assertFalse(antwort["ok"])
                self.assertIn("nicht erlaubt", antwort["fehler"])
        self.assertEqual(self.agent._jobs, {})

    async def test_schema_kennt_abnahme_runden_und_zeitlimit_ohne_neue_pflichtfelder(self):
        schema = self.agent.schemas()[0]
        self.assertEqual(schema.required, ["auftrag", "projekt", "art"])
        for name in ("abnahme", "max_runden", "zeitlimit_min"):
            self.assertIn(name, schema.properties)
        self.assertEqual(schema.properties["abnahme"]["type"], "array")
        self.assertEqual(schema.properties["abnahme"]["items"], {"type": "string"})
        text = schema.properties["abnahme"]["description"]
        self.assertIn("ohne zusätzliche Rückfrage", text)
        self.assertIn("1 bis 3", text)
        self.assertNotIn("befehl:", text)  # auf Sokrates-2 laufen Befehle nicht: das Modell soll sie nicht anbieten
        self.assertIn("Aussage", text)

    # ---------------------------------------------------------------- Startkarte zeigt die Kriterien
    def karte(self) -> dict:
        return [m for m in self.basis.task.messages() if m.get("type") == "auftrag"][-1]

    async def test_die_startkarte_zeigt_die_abnahmekriterien(self):
        self.befehle_an()
        self.neu_agent(befehl_ausfuehrer=Ausfuehrer())
        antwort = await self.vorbereiten(abnahme=[README, "befehl: pytest -q", BEISPIEL])
        self.assertTrue(antwort["ok"], antwort)
        self.assertEqual(self.karte()["abnahme"], [README, "befehl: pytest -q", BEISPIEL])
        self.assertEqual(self.karte()["job"], antwort["auftrag_id"])

    async def test_die_startkarte_hat_ohne_abnahme_kein_feld_abnahme(self):
        await self.vorbereiten()
        self.assertNotIn("abnahme", self.karte())

    async def test_die_karte_zeigt_nur_gesaeuberte_kriterien_hoechstens_fuenf_je_dreihundert_zeichen(self):
        geheim = "sk-ant-api03-" + "Z" * 40
        antwort = await self.vorbereiten(abnahme=[f"Token {geheim} ist nicht im Bericht\x07", "x" * 300, "Drittes\nmit Umbruch"])
        self.assertTrue(antwort["ok"], antwort)
        eintraege = self.karte()["abnahme"]
        self.assertEqual(len(eintraege), 3)
        self.assertTrue(all(len(e) <= 300 for e in eintraege))
        self.assertNotIn(geheim, json.dumps(eintraege))
        self.assertTrue(all("\x07" not in e and "\n" not in e for e in eintraege))
        fuenf = await self.vorbereiten(abnahme=[f"Kriterium {i}" for i in range(5)])
        self.assertTrue(fuenf["ok"], fuenf)
        self.assertEqual(len(self.karte()["abnahme"]), 5)

    # ---------------------------------------------------------------- Abbruch
    async def test_sitzungsende_beendet_die_schleife_sofort(self):
        beurteilt = asyncio.Event()

        async def haengt():
            beurteilt.set()
            await asyncio.sleep(60)

        self.urteile.vor_antwort = haengt
        self.laeufe(FakeProc(lauf()), FakeProc(lauf()))
        antwort = await self.vorbereiten(abnahme=[README])
        await self.agent.on_client_message("auftrag_start", {"job": antwort["auftrag_id"], "entscheidung": "ja"})
        await asyncio.wait_for(beurteilt.wait(), 10)
        await self.agent.close()
        await asyncio.sleep(0.05)
        self.assertTrue(self.agent._running.done())
        self.assertEqual(len(self.basis.remote("start")), 1)  # keine zweite Runde

    # ---------------------------------------------------------------- Anzeige
    async def test_nachrichten_enthalten_keine_pruefausgabe_mit_schluessel(self):
        self.befehle_an()
        geheim = "sk-ant-api03-" + "Z" * 40
        ausfuehrer = Ausfuehrer((1, f"Token {geheim} ungültig"))
        self.neu_agent(befehl_ausfuehrer=ausfuehrer)
        self.laeufe(FakeProc(lauf()))
        await self.starten(abnahme=["befehl: pytest -q"], max_runden=1)
        alles = json.dumps(self.aufnahme.gesendet) + json.dumps(self.basis.task.messages())
        self.assertNotIn(geheim, alles)


if __name__ == "__main__":
    unittest.main()
