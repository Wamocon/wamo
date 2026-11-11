"""Tests für die Büro-Schranken der Entwicklungsaufträge (entwicklung_betrieb.py und die Einhängepunkte in dev_agent.py).

Gilt nur im Büro (WAMO_BETRIEB=buero, Kern auf Sokrates-2). Außerhalb des Büros bleibt alles wie bisher; dafür stehen
die bestehenden Tests (test_dev_agent_sokrates2.py, test_smoke_bot.py, test_boss_bot.py). Alle Tests laufen ohne Netz.

Ausführen:  uv run python -m pytest -q test_entwicklung_betrieb.py   (im Ordner voice-core/)
"""

import asyncio
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import dev_agent
import entwicklung_betrieb as eb
from test_dev_agent_sokrates2 import stream as sk_stream

NIKO = "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"
ANDERE = "7d3e9c1a-2b4f-4e6a-8c0d-1f2a3b4c5d6e"
BUERO_ENV = {
    "WAMO_BETRIEB": "buero",
    "WAMO_ENTWICKLUNG_BUERO": "an",
    "WAMO_ENTWICKLUNG_ORT": "sokrates2",
    "WAMO_ENTWICKLUNG_PROFILE": NIKO,
    "WAMO_ENTWICKLUNG_KOSTEN_AUFTRAG_USD": "3",
    "WAMO_ENTWICKLUNG_KOSTEN_TAG_USD": "10",
}


def buero(**extra):
    """Umgebung eines Büro-Kerns mit eingeschalteten Schranken; extra überschreibt einzelne Werte."""
    return mock.patch.dict(os.environ, {**BUERO_ENV, **extra})


class SchrankenVersionTests(unittest.TestCase):
    def test_genau_eine_zeile_im_muster_des_server_skripts(self):
        quelle = (Path(__file__).parent / "entwicklung_betrieb.py").read_text(encoding="utf-8")
        treffer = [z for z in quelle.splitlines() if re.fullmatch(r"SCHRANKEN_VERSION = [1-9][0-9]*", z)]
        self.assertEqual(treffer, ["SCHRANKEN_VERSION = 1"])
        self.assertEqual(eb.SCHRANKEN_VERSION, 1)


class KonftestTests(unittest.TestCase):
    def test_conftest_neutralisiert_alle_neuen_variablen(self):
        """Eine Variable aus der Umgebung des Entwicklers oder einer .env darf nie in einen Test hineinwirken."""
        self.assertEqual(len(eb.ALLE_VARIABLEN), 5)
        for name in eb.ALLE_VARIABLEN:
            with self.subTest(name=name):
                self.assertNotIn(name, os.environ)


class BueroProblemTests(unittest.TestCase):
    def test_gueltige_einstellung_hat_kein_problem(self):
        with buero():
            self.assertIsNone(eb.buero_problem())

    def test_ort_lokal_im_buero_ergibt_einen_grund(self):
        with buero(WAMO_ENTWICKLUNG_ORT="lokal"):
            self.assertIn("lokal", eb.buero_problem())
        with buero(WAMO_ENTWICKLUNG_ORT="pc"):
            self.assertIn("ungültig", eb.buero_problem())

    def test_ohne_ort_gilt_sokrates2(self):
        with buero(WAMO_ENTWICKLUNG_ORT=""):
            self.assertIsNone(eb.buero_problem())

    def test_profile_fehlen_oder_sind_ungueltig(self):
        for roh in ("", "   ", "kein-uuid", f"{NIKO},kaputt", f"{NIKO},,{ANDERE}", f"{NIKO};{ANDERE}"):
            with self.subTest(roh=roh), buero(WAMO_ENTWICKLUNG_PROFILE=roh):
                self.assertIn("WAMO_ENTWICKLUNG_PROFILE", eb.buero_problem())
                self.assertIsNone(eb.profile())

    def test_mehrere_profile_werden_normalisiert(self):
        with buero(WAMO_ENTWICKLUNG_PROFILE=f" {NIKO.upper()} , {ANDERE} "):
            self.assertIsNone(eb.buero_problem())
            self.assertEqual(eb.profile(), (NIKO, ANDERE))

    def test_ungueltige_kostengrenzen_schalten_ab(self):
        for name in ("WAMO_ENTWICKLUNG_KOSTEN_AUFTRAG_USD", "WAMO_ENTWICKLUNG_KOSTEN_TAG_USD"):
            for roh in ("", "abc", "-1", "0", "0.0", "nan", "inf", "1e3", "3,5", "3 USD", "1000000"):
                with self.subTest(name=name, roh=roh), buero(**{name: roh}):
                    self.assertIn(name, eb.buero_problem())

    def test_gueltige_kostengrenzen(self):
        for roh, wert in (("3", 3.0), ("0.5", 0.5), ("10.00", 10.0), (" 2.25 ", 2.25)):
            with self.subTest(roh=roh), buero(WAMO_ENTWICKLUNG_KOSTEN_AUFTRAG_USD=roh, WAMO_ENTWICKLUNG_KOSTEN_TAG_USD="50"):
                self.assertIsNone(eb.buero_problem())
                self.assertEqual(eb.kosten_auftrag(), wert)
        with buero():
            self.assertEqual(eb.kosten_tag(), 10.0)

    def test_ungueltiger_schalterwert_schaltet_ab(self):
        with buero(WAMO_ENTWICKLUNG_BUERO="vielleicht"):
            self.assertIn("WAMO_ENTWICKLUNG_BUERO", eb.buero_problem())

    def test_ssh_pruefung_wird_durchgereicht(self):
        with buero():
            self.assertEqual(eb.buero_problem(lambda: "SSH-Schlüssel für Aufträge nicht lesbar"),
                             "SSH-Schlüssel für Aufträge nicht lesbar")
            self.assertIsNone(eb.buero_problem(lambda: None))

    def test_wirft_nie_und_nennt_keine_werte(self):
        with buero():
            text = eb.buero_problem(lambda: 1 / 0)
            self.assertIn("ZeroDivisionError", text)
        with buero(WAMO_ENTWICKLUNG_PROFILE="geheimer-wert-123"):
            self.assertNotIn("geheimer-wert-123", eb.buero_problem())
        with buero(WAMO_ENTWICKLUNG_KOSTEN_TAG_USD="geheim4711"):
            self.assertNotIn("geheim4711", eb.buero_problem())


class SchalterTests(unittest.TestCase):
    def test_im_buero_zaehlt_nur_der_buero_schalter(self):
        with buero():
            self.assertTrue(dev_agent.entwicklung_enabled())
        with buero(WAMO_ENTWICKLUNG_BUERO="aus", WAMO_ENTWICKLUNG="an"):
            self.assertFalse(dev_agent.entwicklung_enabled())  # WAMO_ENTWICKLUNG=an allein wirkt im Büro nicht
        with buero(WAMO_ENTWICKLUNG_BUERO="", WAMO_ENTWICKLUNG="an"):
            self.assertFalse(dev_agent.entwicklung_enabled())
        with buero(WAMO_ENTWICKLUNG_BUERO="vielleicht", WAMO_ENTWICKLUNG="an"):
            self.assertFalse(dev_agent.entwicklung_enabled())

    def test_buero_schalter_mit_aus_in_den_units_wird_ignoriert(self):
        with buero(WAMO_ENTWICKLUNG="aus"):  # so stehen die Units auf Sokrates-2
            self.assertTrue(dev_agent.entwicklung_enabled())

    def test_ausserhalb_des_buero_bleibt_es_beim_alten_schalter(self):
        with mock.patch.dict(os.environ, {"WAMO_BETRIEB": "", "WAMO_ENTWICKLUNG": "an", "WAMO_ENTWICKLUNG_BUERO": "aus"}):
            self.assertTrue(dev_agent.entwicklung_enabled())
        with mock.patch.dict(os.environ, {"WAMO_BETRIEB": "", "WAMO_ENTWICKLUNG": "", "WAMO_ENTWICKLUNG_BUERO": "an"}):
            self.assertFalse(dev_agent.entwicklung_enabled())

    def test_entwicklung_problem_im_buero_nutzt_die_schranken(self):
        with buero(WAMO_ENTWICKLUNG_ORT="lokal"):
            self.assertIn("lokal", dev_agent.entwicklung_problem())
        with buero(WAMO_ENTWICKLUNG_PROFILE=""):
            self.assertIn("WAMO_ENTWICKLUNG_PROFILE", dev_agent.entwicklung_problem())
        with buero(), mock.patch.object(dev_agent, "ssh_problem", return_value="ssh kaputt"):
            self.assertEqual(dev_agent.entwicklung_problem(), "ssh kaputt")
        with buero(), mock.patch.object(dev_agent, "ssh_problem", return_value=None):
            self.assertIsNone(dev_agent.entwicklung_problem())

    def test_unlesbarer_schluessel_oder_known_hosts_ergibt_ein_problem_ohne_pfad(self):
        with tempfile.TemporaryDirectory() as tmp:
            key, hosts = Path(tmp, "id_ed25519_wamo_jobs"), Path(tmp, "known_hosts_wamo_jobs")
            key.write_text("x", encoding="utf-8")
            hosts.write_text("x", encoding="utf-8")
            env = {"WAMO_ENTWICKLUNG_SSH_SCHLUESSEL": str(key), "WAMO_ENTWICKLUNG_SSH_KNOWN_HOSTS": str(hosts),
                   "WAMO_ENTWICKLUNG_SSH_ZIEL": "wamo@192.168.178.136"}
            with buero(**env), mock.patch.object(dev_agent.shutil, "which", return_value="ssh"):
                self.assertIsNone(dev_agent.entwicklung_problem())
                with mock.patch.object(dev_agent, "_lesbar", side_effect=lambda p: p != key):
                    problem = dev_agent.entwicklung_problem()
                    self.assertIn("Schlüssel", problem)
                    self.assertIn("lesbar", problem)
                    self.assertNotIn(tmp, problem)
                with mock.patch.object(dev_agent, "_lesbar", side_effect=lambda p: p != hosts):
                    self.assertIn("Hostschlüssel", dev_agent.entwicklung_problem())

    def test_echt_unlesbar_ist_eine_datei_die_sich_nicht_oeffnen_laesst(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(dev_agent._lesbar(_datei(tmp)))
            with mock.patch("builtins.open", side_effect=PermissionError):
                self.assertFalse(dev_agent._lesbar(Path(tmp, "x")))


def _datei(ordner: str) -> Path:
    pfad = Path(ordner, "lesbar")
    pfad.write_text("x", encoding="utf-8")
    return pfad


class ZulassungTests(unittest.TestCase):
    def test_zulassung_ist_unveraenderlich_und_hat_eine_kurzkennung(self):
        z = eb.BueroZulassung(NIKO.upper())
        self.assertEqual(z.user_id, NIKO)
        self.assertRegex(z.kennung, r"^[0-9a-f]{8}$")
        self.assertEqual(z.kennung, NIKO.replace("-", "")[:8])
        with self.assertRaises(Exception):
            z.user_id = ANDERE  # type: ignore[misc]
        self.assertNotIn(NIKO, repr(z))  # nie die volle Kennung im Log

    def test_zulassung_verlangt_eine_gueltige_user_id(self):
        for schlecht in ("", "x", None, 5, "0f6c2b1e"):
            with self.subTest(schlecht=schlecht), self.assertRaises(ValueError):
                eb.BueroZulassung(schlecht)  # type: ignore[arg-type]

    def test_admin_im_profil_bekommt_eine_zulassung(self):
        with buero():
            z = eb.zulassung_erteilen(NIKO, True)
            self.assertIsInstance(z, eb.BueroZulassung)
            self.assertEqual(z.user_id, NIKO)

    def test_admin_ausserhalb_des_profils_und_mitglieder_bekommen_keine(self):
        with buero():
            self.assertIsNone(eb.zulassung_erteilen(ANDERE, True))
            self.assertIsNone(eb.zulassung_erteilen(NIKO, False))  # auch nicht, wenn das Profil in der Liste steht
            self.assertIsNone(eb.zulassung_erteilen("kein-uuid", True))
            self.assertIsNone(eb.zulassung_erteilen(None, True))
        with buero(WAMO_ENTWICKLUNG_PROFILE=f"{NIKO},{ANDERE}"):
            self.assertIsNotNone(eb.zulassung_erteilen(ANDERE, True))
        with buero(WAMO_ENTWICKLUNG_PROFILE=""):
            self.assertIsNone(eb.zulassung_erteilen(NIKO, True))  # ohne Bindung gibt es keine Zulassung (fail closed)
        with buero(WAMO_ENTWICKLUNG_PROFILE=f"{NIKO},kaputt"):
            self.assertIsNone(eb.zulassung_erteilen(NIKO, True))

    def test_zulassung_gueltig_prueft_person_und_bindung_erneut(self):
        with buero():
            z = eb.BueroZulassung(NIKO)
            self.assertTrue(eb.zulassung_gueltig(z, NIKO))
            self.assertFalse(eb.zulassung_gueltig(z, ANDERE))
            self.assertFalse(eb.zulassung_gueltig(None, NIKO))
            self.assertFalse(eb.zulassung_gueltig("zulassung", NIKO))  # type: ignore[arg-type]
            self.assertFalse(eb.zulassung_gueltig(eb.BueroZulassung(ANDERE), ANDERE))  # nicht (mehr) in der Liste
        with buero(WAMO_ENTWICKLUNG_PROFILE=""):
            self.assertFalse(eb.zulassung_gueltig(eb.BueroZulassung(NIKO), NIKO))


class SitzungGrundTests(unittest.TestCase):
    """Die Gründe, die Log und Statusleiste nennen (grund_vorab, grund_quelle, anzeige_grund)."""

    def test_vorab_grund_je_fall(self):
        with buero():
            self.assertIsNone(eb.grund_vorab(NIKO, True))
            self.assertIn("Admin", eb.grund_vorab(NIKO, False))
            self.assertIn("nicht freigegeben", eb.grund_vorab(ANDERE, True))
            self.assertEqual(eb.grund_vorab(NIKO, True, problem="ssh kaputt"), "ssh kaputt")
        with buero(WAMO_ENTWICKLUNG_BUERO="aus"):
            self.assertIn("aus", eb.grund_vorab(NIKO, True))
        with buero(WAMO_ENTWICKLUNG_BUERO="vielleicht"):
            self.assertIn("ungültig", eb.grund_vorab(NIKO, True))

    def test_uebergang_und_keine_zaehlen_im_buero_gleich(self):
        self.assertIsNone(eb.grund_quelle("token"))
        self.assertIsNone(eb.grund_quelle("api"))
        for quelle in ("keine", "uebergang", "unbekannt", ""):
            with self.subTest(quelle=quelle):
                self.assertTrue(eb.grund_quelle(quelle).startswith("kein eigener Zugang"))

    def test_anzeige_nur_fuer_admins_und_hoechstens_120_zeichen(self):
        self.assertIsNone(eb.anzeige_grund(False, "irgendein Grund"))
        self.assertIsNone(eb.anzeige_grund(True, None))
        self.assertIsNone(eb.anzeige_grund(True, ""))
        lang = eb.anzeige_grund(True, "x" * 500)
        self.assertEqual(len(lang), 120)
        self.assertEqual(eb.anzeige_grund(True, "zwei\n Zeilen"), "zwei Zeilen")

    def test_gruende_nennen_nie_pfade_oder_werte(self):
        with buero():
            alle = [eb.grund_vorab(ANDERE, True), eb.grund_vorab(NIKO, False), eb.grund_quelle("keine"),
                    eb.grund_quelle("uebergang")]
        for text in alle:
            self.assertNotIn(NIKO, text)
            self.assertNotIn(ANDERE, text)
            self.assertNotRegex(text, r"[/\\]")


class ZugangImBueroTests(unittest.IsolatedAsyncioTestCase):
    def test_serverzugang_im_buero_nie(self):
        kern = {"WAMO_CLAUDE_SCHLUESSEL_PROFIL": NIKO, "WAMO_ENTWICKLUNG_SERVERZUGANG_PROFIL": ""}
        with mock.patch.dict(os.environ, {**kern, "WAMO_BETRIEB": ""}):
            self.assertTrue(dev_agent.serverzugang_erlaubt(NIKO))  # außerhalb des Büros wie bisher
        with buero(**kern):
            self.assertFalse(dev_agent.serverzugang_erlaubt(NIKO))
        with buero(WAMO_ENTWICKLUNG_SERVERZUGANG_PROFIL=NIKO, WAMO_CLAUDE_SCHLUESSEL_PROFIL=NIKO):
            self.assertFalse(dev_agent.serverzugang_erlaubt(NIKO))

    async def test_im_buero_kein_rueckfall_auf_den_schluessel_des_kerns(self):
        class Memory:
            user_id = NIKO

        agent = dev_agent.DevAgent(memory=Memory(), auftraggeber="Niko")
        kern = {"WAMO_CLAUDE_SCHLUESSEL_PROFIL": NIKO, "WAMO_ENTWICKLUNG_SERVERZUGANG_PROFIL": ""}
        with mock.patch.dict(os.environ, {**kern, "WAMO_BETRIEB": ""}), \
             mock.patch.object(dev_agent, "claude_code_zugang_fuer", return_value=None):
            self.assertEqual(await agent._remote_zugang(), ({"zugang": "server", "profil": NIKO.replace("-", "")[:8]}, None))
        with buero(**kern), mock.patch.object(dev_agent, "claude_code_zugang_fuer", return_value=None):
            self.assertIsNone(await agent._remote_zugang())  # der Auftrag startet nicht
        with buero(**kern), mock.patch.object(dev_agent, "claude_code_zugang_fuer",
                                              return_value={"typ": "api", "wert": "sk-ant-api03-" + "x" * 30}):
            kopf, zeile = await agent._remote_zugang()
            self.assertEqual(kopf["zugang"], "folgt")  # der eigene Zugang geht wie bisher
            self.assertIsNotNone(zeile)

    def test_befehlskriterien_im_buero_nur_mit_eigenem_schalter(self):
        agent = dev_agent.DevAgent(memory=None, auftraggeber="Niko")
        with mock.patch.dict(os.environ, {"WAMO_BETRIEB": "", "WAMO_ABNAHME_BEFEHLE": "ja"}):
            self.assertTrue(agent._remote_befehle())  # außerhalb des Büros unverändert
        with buero(WAMO_ABNAHME_BEFEHLE="ja"):
            self.assertFalse(agent._remote_befehle())
            self.assertFalse(agent._befehle_angeboten())
            self.assertIn("Schalter aus", agent._befehle_hinweis())
        with buero(WAMO_ABNAHME_BEFEHLE="ja", WAMO_ENTWICKLUNG_BEFEHLE_BUERO="an"):
            self.assertTrue(agent._remote_befehle())
        with buero(WAMO_ABNAHME_BEFEHLE="", WAMO_ENTWICKLUNG_BEFEHLE_BUERO="an"):
            self.assertFalse(agent._remote_befehle())  # ohne den Schalter in core.env ebenso nicht
        with buero(WAMO_ABNAHME_BEFEHLE="ja", WAMO_ENTWICKLUNG_BEFEHLE_BUERO="vielleicht"):
            self.assertFalse(agent._remote_befehle())


# ---------------------------------------------------------------- Harness für den DevAgent im Büro
class AuditMemory:
    """MemoryStore-Attrappe mit der Tabelle actions im Speicher (Automat wie der Trigger actions_guard)."""

    AUTOMAT = {"approved": "pending", "rejected": "pending", "blocked": "pending", "running": "approved",
               "done": "running", "failed": "running"}

    def __init__(self, user_id=NIKO):
        self.user_id = user_id
        self.zeilen: dict[str, dict] = {}
        self.ablauf: list[tuple] = []  # (aktion, id, status) in der Reihenfolge der Aufrufe
        self.erreichbar = True
        self.vortag_kosten = 0.0  # Kosten anderer Aufträge desselben Tages
        self.veraltet_aufrufe: list[dict] = []

    async def log_action(self, tool, params, reason, risk):
        if not self.erreichbar:
            return None
        aktion_id = f"a{len(self.zeilen) + 1}"
        self.zeilen[aktion_id] = {"tool": tool, "params": params, "reason": reason, "risk": risk, "status": "pending",
                                  "result": None}
        self.ablauf.append(("log", aktion_id, "pending"))
        return aktion_id

    async def update_action(self, action_id, status):
        zeile = self.zeilen.get(action_id)
        if not self.erreichbar or zeile is None or zeile["status"] != self.AUTOMAT.get(status):
            return False
        zeile["status"] = status
        self.ablauf.append(("update", action_id, status))
        return True

    async def aktion_abschliessen(self, action_id, status, result):
        zeile = self.zeilen.get(action_id)
        if not self.erreichbar or status not in ("done", "failed") or zeile is None or zeile["status"] != "running":
            return False
        zeile["status"], zeile["result"] = status, result
        self.ablauf.append(("abschluss", action_id, status))
        return True

    async def tageskosten(self, user_id=None, laufend_usd=0.0):
        """Wie die Abfrage in MemoryStore.tageskosten: Es zählen nur Zeilen im Status running, done und failed. Zeilen ohne verwertbare
        Kosten (kein result, NaN, keine Zahl) zählen mit laufend_usd (der Auftragsgrenze), jede Zeile mindestens 0 und höchstens
        KOSTEN_JE_ZEILE_MAX, eine laufende mindestens laufend_usd. Zeilen in pending, approved und rejected ohne result zählen nie
        (sonst zehrten sie den Tag auf, ohne dass ein Lauf bestand)."""
        if not self.erreichbar:
            return None
        summe = self.vortag_kosten
        for z in self.zeilen.values():
            if z["tool"] != "entwicklungsauftrag" or z["status"] not in ("running", "done", "failed"):
                continue
            try:
                kosten = float((z["result"] or {}).get("kosten_usd"))
            except (TypeError, ValueError):
                kosten = laufend_usd
            if kosten != kosten:  # NaN zählt nicht als Zahl
                kosten = laufend_usd
            kosten = min(1000.0, max(0.0, kosten))
            summe += max(kosten, laufend_usd) if z["status"] == "running" else kosten
        return summe

    async def veraltete_schliessen(self, user_id=None, nach_stunden=2, kosten_usd=0.0):
        self.veraltet_aufrufe.append({"nach_stunden": nach_stunden, "kosten_usd": kosten_usd})
        if not self.erreichbar:
            return None
        n = 0
        for z in self.zeilen.values():
            if z["tool"] == "entwicklungsauftrag" and z["status"] == "running" and z.get("alt"):
                z["status"], z["result"] = "failed", {"grund": "abgelaufen", "kosten_usd": kosten_usd}
                n += 1
        return n

    def stati(self):
        return [z["status"] for z in self.zeilen.values()]


class BueroFlow(unittest.IsolatedAsyncioTestCase):
    """DevAgent im Büro: ssh ist gemockt (Attrappen aus test_dev_agent_sokrates2), die Umgebung ist ein Büro-Kern."""

    zugang = {"typ": "api", "wert": "sk-ant-api03-" + "GEHEIMbuero" * 4}

    async def asyncSetUp(self):
        import test_dev_agent_sokrates2 as sk

        self.sk = sk
        self.tmp = tempfile.TemporaryDirectory()
        key, hosts = Path(self.tmp.name, "id_ed25519_wamo_jobs"), Path(self.tmp.name, "known_hosts_wamo_jobs")
        key.write_text("x", encoding="utf-8")
        hosts.write_text("x", encoding="utf-8")
        self.env = buero(WAMO_ENTWICKLUNG_SSH_SCHLUESSEL=str(key), WAMO_ENTWICKLUNG_SSH_KNOWN_HOSTS=str(hosts),
                         WAMO_ENTWICKLUNG_SSH_ZIEL="wamo@192.168.178.136", WAMO_CLAUDE_SCHLUESSEL_PROFIL="",
                         WAMO_ENTWICKLUNG_SERVERZUGANG_PROFIL="")
        self.env.start()
        self.patches = [mock.patch.object(dev_agent.shutil, "which", return_value="ssh"),
                        mock.patch.object(dev_agent, "claude_code_zugang_fuer", side_effect=lambda uid: dict(self.zugang))]
        self.calls: list[list[str]] = []
        self.start_procs: list = []
        self.approve_rc = 0
        self.pending_lines: list[str] = []

        async def fake_exec(*argv, **kwargs):
            self.calls.append(list(argv))
            befehl = argv[-1].split(" ")[0]
            if befehl == "start":
                return self.start_procs.pop(0)
            if befehl == "approve":
                return sk.FakeProc([{"ok": True}], returncode=self.approve_rc)
            if befehl == "pending":
                return sk.FakeProc(self.pending_lines, returncode=0)
            return sk.FakeProc([{"ok": True}])

        async def fake_kill(proc):
            if isinstance(proc, sk.FakeProc) and proc.returncode is None:
                proc.beenden()

        self.patches += [mock.patch.object(dev_agent.asyncio, "create_subprocess_exec", side_effect=fake_exec),
                         mock.patch.object(dev_agent, "_kill_tree", side_effect=fake_kill)]
        for p in self.patches:
            p.start()
        self.memory = AuditMemory()
        self.agent = dev_agent.DevAgent(memory=self.memory, auftraggeber="Niko", zulassung=eb.BueroZulassung(NIKO))
        self.task = sk.FakeTask()
        self.agent.attach(self.task)

    async def asyncTearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.env.stop()
        self.tmp.cleanup()

    def remote(self, befehl):
        return [c[-1] for c in self.calls if c[-1].split(" ")[0] == befehl]

    async def warte_auf(self, bedingung, was: str, frist_s: float = 5.0):
        """Wartet, bis die Bedingung gilt. Läuft die Frist ab, scheitert der Test mit einer Meldung (nie still weiter)."""
        ende = asyncio.get_running_loop().time() + frist_s
        while not bedingung():
            if asyncio.get_running_loop().time() > ende:
                self.fail(f"Zeitüberschreitung nach {frist_s} s: {was}")
            await asyncio.sleep(0.01)

    def neuer_job(self, agent=None, **extra):
        """Ein vorbereiteter Auftrag direkt im Agenten (ohne _vorbereiten)."""
        job = dev_agent.Job(id="ab12cd34ef56ab12", auftrag="Prüfe das Repo", projekt=dev_agent.PurePosixPath(
            "/srv/wamo/projekte/kern-testrepo"), art=extra.pop("art", "analyse"), repo=extra.pop("repo", "acme/testrepo"))
        (agent or self.agent)._jobs[job.id] = job
        return job

    async def vorbereiten(self, agent=None, **argumente):
        params = type("P", (), {})()
        params.arguments = {"auftrag": "Prüfe das Repo auf Sicherheitslücken", "projekt": "ignoriert", "art": "analyse",
                            "repo": "acme/testrepo", **argumente}
        ergebnis: dict = {}

        async def cb(r):
            ergebnis.update(r)
        params.result_callback = cb
        await (agent or self.agent)._vorbereiten(params)
        return ergebnis

    def ohne_zulassung(self):
        """Ein Agent, der außerhalb des Büros ohne Zulassung gebaut wurde und jetzt im Büro Befehle bekommt."""
        with mock.patch.dict(os.environ, {"WAMO_BETRIEB": ""}):
            agent = dev_agent.DevAgent(memory=self.memory, auftraggeber="Niko")
        agent.attach(self.task)
        return agent


class ZulassungImDevAgentTests(BueroFlow):
    def test_konstruktor_im_buero_ohne_zulassung_wirft(self):
        with self.assertRaises(eb.ZulassungFehlt):
            dev_agent.DevAgent(memory=None, auftraggeber="Niko")
        with self.assertRaises(eb.ZulassungFehlt):
            dev_agent.DevAgent(memory=None, auftraggeber="Niko", zulassung=None)
        with self.assertRaises(eb.ZulassungFehlt):
            dev_agent.DevAgent(memory=None, auftraggeber="Niko", zulassung="ja")  # type: ignore[arg-type]

    def test_konstruktor_im_buero_mit_zulassung_eines_anderen_profils_wirft(self):
        with self.assertRaises(eb.ZulassungFehlt):
            dev_agent.DevAgent(memory=AuditMemory(user_id=ANDERE), auftraggeber="Niko", zulassung=eb.BueroZulassung(NIKO))

    def test_konstruktor_im_buero_mit_zulassung(self):
        agent = dev_agent.DevAgent(memory=AuditMemory(), auftraggeber="Niko", zulassung=eb.BueroZulassung(NIKO))
        self.assertEqual(agent._zulassung.user_id, NIKO)

    def test_ausserhalb_des_buero_bleibt_die_signatur_wirksam_ohne_den_wert(self):
        with mock.patch.dict(os.environ, {"WAMO_BETRIEB": ""}):
            dev_agent.DevAgent(memory=None, auftraggeber="Erika")
            dev_agent.DevAgent(memory=None, auftraggeber="Erika", ort="sokrates2", zulassung=None)
            dev_agent.DevAgent(memory=None, auftraggeber="Erika", zulassung=eb.BueroZulassung(NIKO))

    async def test_vorbereiten_ohne_zulassung_legt_keine_karte(self):
        agent = self.ohne_zulassung()
        ergebnis = await self.vorbereiten(agent)
        self.assertFalse(ergebnis["ok"])
        self.assertIn("nicht freigegeben", ergebnis["fehler"])
        self.assertEqual(agent._jobs, {})
        self.assertEqual([m for m in self.task.messages() if m.get("type") == "auftrag"], [])

    async def test_auftrag_start_ohne_zulassung_startet_keinen_ssh_prozess(self):
        agent = self.ohne_zulassung()
        job = self.neuer_job(agent)
        await agent.on_client_message("auftrag_start", {"job": job.id, "entscheidung": "ja"})
        self.assertEqual(self.calls, [])
        self.assertFalse(job.started)
        self.assertIsNone(agent._running)

    async def test_freigabe_ohne_zulassung_ruft_nie_approve(self):
        agent = self.ohne_zulassung()
        job = self.neuer_job(agent)
        job.pending["f" * 16] = {"id": "f" * 16, "grund": "git push", "tool": "Bash", "input": {"command": "git push"},
                                 "action_id": "a1"}
        await agent.on_client_message("freigabe", {"job": job.id, "id": "f" * 16, "entscheidung": "ja"})
        self.assertEqual(self.calls, [])
        self.assertEqual(self.memory.ablauf, [])  # auch keine Buchung in actions
        self.assertIn("f" * 16, job.pending)  # unverändert: nichts wurde verbraucht

    async def test_versuche_ohne_zulassung_stehen_im_log_mit_kuerzel_ohne_werte(self):
        from loguru import logger

        meldungen: list[str] = []
        sink = logger.add(lambda m: meldungen.append(str(m)), level="WARNING")
        try:
            agent = self.ohne_zulassung()
            job = self.neuer_job(agent)
            await agent.on_client_message("auftrag_start", {"job": job.id, "entscheidung": "ja"})
            await self.vorbereiten(agent)
        finally:
            logger.remove(sink)
        log = "\n".join(meldungen)
        self.assertEqual(log.count("ohne Zulassung abgewiesen"), 2)
        self.assertIn(NIKO.replace("-", "")[:8], log)
        self.assertNotIn(NIKO, log)
        self.assertNotIn("GEHEIMbuero", log)

    async def test_zulassung_einer_anderen_person_zaehlt_nicht(self):
        agent = self.ohne_zulassung()
        agent._zulassung = eb.BueroZulassung(ANDERE)  # nicht das Profil dieser Sitzung (und nicht in der Liste)
        job = self.neuer_job(agent)
        await agent.on_client_message("auftrag_start", {"job": job.id, "entscheidung": "ja"})
        self.assertEqual(self.calls, [])

    async def test_mit_zulassung_geht_der_auftrag_los(self):
        self.start_procs.append(self.sk.FakeProc(self.sk.stream()))
        job = self.neuer_job()
        await self.agent.on_client_message("auftrag_start", {"job": job.id, "entscheidung": "ja"})
        await self.agent._running
        self.assertEqual(len(self.remote("start")), 1)


# ---------------------------------------------------------------- R3: Repo-Pflicht, Projektname, Quittung, Grund-Codes
# Dieselben Zeilen testet der Dispatcher (Schnittstelle I5)
PROJEKTNAMEN = (
    ("Acme/Test-Repo", None, "kern-test-repo"),
    ("acme/my_repo.js", None, "kern-my-repo-js"),
    ("acme/a__b", None, "kern-a-b"),
    ("acme/-x-", None, "kern-x"),
    (None, "Marktstudie", "kern-marktstudie"),
    (None, "Märkte", "kern-mrkte"),  # Umlaute und ß werden wie jedes Zeichen außerhalb a-z0-9- verworfen, nicht umgeschrieben
)
GRUND_CODES = ("notaus", "konfig_ungueltig", "kanal_zu", "abgelaufen", "befehl", "fremd", "art", "repo", "repo_fehlt",
               "dienst", "projekt", "zugang", "fork", "herkunft_unklar", "grenze_start", "grenze_laufzeit",
               "grenze_folgen", "abstand", "platz")


class ProjektnameTests(unittest.TestCase):
    def test_wertetabelle_aus_der_schnittstelle(self):
        for repo, wort, erwartet in PROJEKTNAMEN:
            with self.subTest(repo=repo, wort=wort):
                self.assertEqual(eb.kern_projektname(repo, wort), erwartet)

    def test_das_repo_wamo_wird_abgelehnt_in_jeder_schreibweise(self):
        for repo in ("acme/wamo", "acme/WAMO", "Acme/Wamo", "acme/wamo_", "acme/-wamo-"):
            with self.subTest(repo=repo), self.assertRaises(eb.ProjektnameFehler) as ctx:
                eb.kern_projektname(repo, None)
            self.assertEqual(ctx.exception.grund, "repo")

    def test_zu_lange_namen_werden_abgelehnt(self):
        self.assertEqual(eb.kern_projektname("acme/" + "a" * 45), "kern-" + "a" * 45)  # genau 50 Zeichen
        for name in ("a" * 46, "a" * 80):
            with self.subTest(laenge=len(name)), self.assertRaises(eb.ProjektnameFehler) as ctx:
                eb.kern_projektname(f"acme/{name}", None)
            self.assertEqual(ctx.exception.grund, "projekt")

    def test_nichts_brauchbares_wird_abgelehnt(self):
        for repo, wort in ((None, None), (None, ""), (None, "!!!"), ("acme/---", None), ("acme/", None), ("kein-repo", None)):
            with self.subTest(repo=repo, wort=wort), self.assertRaises(eb.ProjektnameFehler):
                eb.kern_projektname(repo, wort)

    def test_projektwort_des_modells_wird_bereinigt(self):
        """Regel aus der Schnittstelle I5: klein, "." und "_" zu "-", jedes andere Zeichen außerhalb a-z0-9- fällt weg (auch
        Leerraum, Umlaute und ß), mehrere "-" zu einem, "-" am Rand weg. Der Dispatcher erzwingt dieselbe Regel."""
        for wort, erwartet in (("Rezepte-App", "kern-rezepte-app"), ("Rezepte App", "kern-rezepteapp"),
                               ("Prüfung der Käse", "kern-prfungderkse"), ("Märkte", "kern-mrkte"), ("Größe", "kern-gre"),
                               ("Öl und Fuß", "kern-lundfu"), ("a..b__c", "kern-a-b-c"), ("  Markt/Studie! ", "kern-marktstudie")):
            with self.subTest(wort=wort):
                self.assertEqual(eb.kern_projektname(None, wort), erwartet)

    def test_ein_wort_nur_aus_umlauten_ergibt_keinen_namen(self):
        for wort in ("äöü", "ß", "Ä Ö Ü"):
            with self.subTest(wort=wort), self.assertRaises(eb.ProjektnameFehler) as ctx:
                eb.kern_projektname(None, wort)
            self.assertEqual(ctx.exception.grund, "projekt")

    def test_das_repo_hat_vorrang_vor_dem_wort(self):
        self.assertEqual(eb.kern_projektname("acme/testrepo", "ganz-anderes-projekt"), "kern-testrepo")

    def test_selbstschutz_bleibt(self):
        import dev_guard

        self.assertEqual(dev_guard.SELF_REPO, "wamo")
        self.assertEqual(dev_agent.SELF_REPO, "wamo")
        with self.assertRaises(ValueError):
            dev_agent.repo_pruefen("acme/wamo")


class GrundSaetzeTests(unittest.TestCase):
    def test_jeder_code_hat_einen_satz_und_nur_diese(self):
        self.assertEqual(tuple(eb.GRUND_SAETZE), GRUND_CODES)

    def test_saetze_sind_kurz_sprechbar_und_ohne_pfade_oder_werte(self):
        for code, satz in eb.GRUND_SAETZE.items():
            with self.subTest(code=code):
                self.assertIsInstance(satz, str)
                self.assertTrue(satz.endswith("."))
                self.assertLessEqual(len(satz), 110)
                self.assertNotRegex(satz, r"[/\\_=:*#`]")
                self.assertNotRegex(satz, r"[–—]| - ")
                self.assertNotIn("  ", satz)

    def test_kanal_gesperrt_ist_ein_remote_ereignis_mit_dem_satz(self):
        self.assertTrue(dev_agent.REMOTE_EREIGNIS_STATUS["kanal_gesperrt"].startswith("nicht gestartet"))
        for code, satz in eb.GRUND_SAETZE.items():
            status = dev_agent.remote_status(2, {"kanal_gesperrt": {"grund": code}})
            with self.subTest(code=code):
                self.assertEqual(status, f"nicht gestartet ({satz.rstrip('.')})")
        self.assertEqual(dev_agent.remote_status(2, {"kanal_gesperrt": {"grund": "unbekannt"}}),
                         "nicht gestartet (Der Kanal hat den Auftrag abgewiesen)")
        self.assertEqual(dev_agent.remote_status(2, {"kanal_gesperrt": {}}), "nicht gestartet (Der Kanal hat den Auftrag abgewiesen)")

    def test_ssh_fehler_werden_aus_stderr_eingeordnet_ohne_pfade(self):
        faelle = (
            ("Host key verification failed.", "Hostschlüssel"),
            ("@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@", "Hostschlüssel"),
            ("No ED25519 host key is known for 192.168.178.136 and you have requested strict checking.", "Hostschlüssel"),
            ("wamo@192.168.178.136: Permission denied (publickey).", "abgelehnt"),
            ("ssh: connect to host 192.168.178.136 port 22: Connection timed out", "nicht erreichbar"),
            ("ssh: connect to host 192.168.178.136 port 22: Connection refused", "nicht erreichbar"),
            ("ssh: connect to host 192.168.178.136 port 22: No route to host", "nicht erreichbar"),
            ("kex_exchange_identification: Connection closed by remote host", "nicht erreichbar"),
            ('Load key "/var/lib/wamo-core/.ssh/id_ed25519_wamo_jobs": Permission denied', "nicht lesen"),
            ("irgendwas Unbekanntes", "fehlgeschlagen"),
            ("", "fehlgeschlagen"),
        )
        for stderr, stichwort in faelle:
            satz = eb.ssh_fehler_satz(stderr)
            with self.subTest(stderr=stderr):
                self.assertIn(stichwort, satz)
                self.assertNotRegex(satz, r"[/\\]|192\.168|id_ed25519")

    def test_ablehngrund_je_ereignis_und_code(self):
        g = eb.ablehn_grund
        self.assertEqual(g(2, {"kanal_gesperrt": {"grund": "kanal_zu"}}), eb.GRUND_SAETZE["kanal_zu"])
        self.assertIn("läuft schon", g(4, {"belegt": {}}))
        self.assertIn("Claude-Zugang", g(5, {"keine_zugangsdaten": {}}))
        self.assertIn("Claude-Zugang", g(2, {"zugang_ungueltig": {}}))
        self.assertIn("Selbsttest", g(3, {"selbsttest_fehlgeschlagen": {}}))
        self.assertIn("GitHub", g(8, {"start_abgelehnt": {}}))
        self.assertIn("Hostschlüssel", g(255, {}, "Host key verification failed."))
        self.assertIn("abgewiesen", g(2, {}))
        self.assertIn("nicht gestartet", g(None, {}))


class RepoPflichtTests(BueroFlow):
    async def test_github_art_ohne_repo_wird_im_buero_abgelehnt(self):
        ergebnis = await self.vorbereiten(repo=None, art="analyse", auftrag="Prüfe die Struktur")
        self.assertFalse(ergebnis["ok"])
        self.assertIn("Repo", ergebnis["fehler"])
        self.assertIn("owner/name", ergebnis["fehler"])
        self.assertEqual(self.agent._jobs, {})
        for art in ("umsetzung", "review", "sicherheit", "test"):  # lesend und schreibend
            with self.subTest(art=art):
                self.assertFalse((await self.vorbereiten(repo=None, art=art, auftrag="Mach etwas"))["ok"])

    async def test_recherche_ohne_repo_ist_erlaubt_und_bekommt_ein_kern_projekt(self):
        ergebnis = await self.vorbereiten(repo=None, art="recherche", projekt="Marktstudie", auftrag="Recherchiere den Markt")
        self.assertTrue(ergebnis["ok"])
        karte = next(m for m in self.task.messages() if m.get("type") == "auftrag")
        self.assertEqual(karte["projekt"], "kern-marktstudie")
        self.assertNotIn("repo", karte)

    async def test_projekt_wird_aus_dem_repo_abgeleitet_das_modellwort_zaehlt_nicht(self):
        ergebnis = await self.vorbereiten(repo="Acme/Test-Repo", projekt="irgendwas anderes")
        self.assertTrue(ergebnis["ok"])
        karte = next(m for m in self.task.messages() if m.get("type") == "auftrag")
        self.assertEqual((karte["projekt"], karte["repo"]), ("kern-test-repo", "Acme/Test-Repo"))
        job = self.agent._jobs[ergebnis["auftrag_id"]]
        self.assertEqual(str(job.projekt), "/srv/wamo/projekte/kern-test-repo")

    async def test_repo_aus_der_adresse_im_auftragstext_genuegt(self):
        ergebnis = await self.vorbereiten(repo=None, auftrag="Prüfe https://github.com/acme/testrepo auf Fehler")
        self.assertTrue(ergebnis["ok"])
        self.assertEqual(self.agent._jobs[ergebnis["auftrag_id"]].repo, "acme/testrepo")

    async def test_das_repo_wamo_und_zu_lange_namen_gehen_nicht(self):
        for repo in ("acme/wamo", "acme/WAMO", "https://github.com/acme/wamo.git", "acme/" + "x" * 46):
            with self.subTest(repo=repo):
                self.assertFalse((await self.vorbereiten(repo=repo))["ok"])
        self.assertEqual(self.agent._jobs, {})

    async def test_die_werkzeugbeschreibung_nennt_die_pflicht_nur_im_buero(self):
        def repo_text(agent):
            vorbereiten = next(s for s in agent.schemas() if s.name == "entwicklung_vorbereiten")
            return vorbereiten.properties["repo"]["description"]

        self.assertIn("Pflicht", repo_text(self.agent))
        with mock.patch.dict(os.environ, {"WAMO_BETRIEB": ""}):
            self.assertNotIn("Pflicht", repo_text(dev_agent.DevAgent(memory=None, auftraggeber="Erika")))


class QuittungTests(BueroFlow):
    """Jeder Klick auf Starten bekommt eine auftrag_quittung: ok, oder nicht ok mit einem Satz als Grund."""

    def quittungen(self):
        return [m for m in self.task.messages() if m.get("type") == "auftrag_quittung"]

    async def klick(self, job):
        await self.agent.on_client_message("auftrag_start", {"job": job.id, "entscheidung": "ja"})
        if self.agent._running is not None:
            await self.agent._running

    async def test_erfolg_ok_true_genau_einmal(self):
        self.start_procs.append(self.sk.FakeProc(self.sk.stream()))
        job = self.neuer_job()
        await self.klick(job)
        self.assertEqual(self.quittungen(), [{"type": "auftrag_quittung", "job": job.id, "ok": True}])
        self.assertTrue(job.started)

    async def test_kanal_gesperrt_jeder_code_ok_false_mit_dem_satz_und_die_karte_bleibt(self):
        for code in GRUND_CODES:
            with self.subTest(code=code):
                self.task.frames.clear()
                self.start_procs.append(self.sk.FakeProc([{"type": "wamo", "ereignis": "kanal_gesperrt", "grund": code}],
                                                         returncode=2))
                job = self.neuer_job()
                await self.klick(job)
                self.assertEqual(self.quittungen(),
                                 [{"type": "auftrag_quittung", "job": job.id, "ok": False, "grund": eb.GRUND_SAETZE[code]}])
                self.assertIn(job.id, self.agent._jobs)  # die Karte bleibt bestehen
                self.assertFalse(job.started)  # ein zweiter Klick ist möglich
                self.assertEqual(job.status, f"nicht gestartet ({eb.GRUND_SAETZE[code].rstrip('.')})")
        self.assertEqual(self.remote("cancel"), [])  # Exit 2: nichts lief, kein Abbruch nötig

    async def test_nach_der_ablehnung_geht_ein_zweiter_klick(self):
        self.start_procs += [self.sk.FakeProc([{"type": "wamo", "ereignis": "kanal_gesperrt", "grund": "kanal_zu"}], returncode=2),
                             self.sk.FakeProc(self.sk.stream())]
        job = self.neuer_job()
        await self.klick(job)
        await self.klick(job)
        self.assertEqual([q["ok"] for q in self.quittungen()], [False, True])

    async def test_belegt_im_dispatcher(self):
        self.start_procs.append(self.sk.FakeProc([{"type": "wamo", "ereignis": "belegt", "grund": "Es läuft schon ein Auftrag"}],
                                                 returncode=4))
        job = self.neuer_job()
        await self.klick(job)
        (q,) = self.quittungen()
        self.assertFalse(q["ok"])
        self.assertIn("läuft schon", q["grund"])

    async def test_belegt_im_kern_ein_auftrag_laeuft_noch(self):
        job = self.neuer_job()
        self.agent._running = asyncio.ensure_future(asyncio.sleep(30))
        try:
            await self.agent.on_client_message("auftrag_start", {"job": job.id, "entscheidung": "ja"})
        finally:
            self.agent._running.cancel()
        (q,) = self.quittungen()
        self.assertFalse(q["ok"])
        self.assertIn("läuft schon", q["grund"])
        self.assertEqual(self.calls, [])
        self.assertFalse(job.started)

    async def test_problem_der_einstellung_ok_false_ohne_pfad(self):
        job = self.neuer_job()
        with mock.patch.dict(os.environ, {"WAMO_ENTWICKLUNG_KOSTEN_TAG_USD": "kaputt"}):
            await self.agent.on_client_message("auftrag_start", {"job": job.id, "entscheidung": "ja"})
        (q,) = self.quittungen()
        self.assertFalse(q["ok"])
        self.assertNotIn("KOSTEN", q["grund"])  # die Einzelheiten stehen im Log und in der Statusleiste, nicht in der Quittung
        self.assertEqual(self.calls, [])
        self.assertFalse(job.started)

    async def test_ssh_ausfall_255_wird_klassifiziert(self):
        faelle = (("Host key verification failed.", "Hostschlüssel"), ("wamo@x: Permission denied (publickey).", "abgelehnt"),
                  ("ssh: connect to host x port 22: Connection timed out", "nicht erreichbar"))
        for stderr, stichwort in faelle:
            with self.subTest(stderr=stderr):
                self.task.frames.clear()
                proc = self.sk.FakeProc([], returncode=255)
                proc.stderr = asyncio.StreamReader()
                proc.stderr.feed_data(stderr.encode() + b"\n")
                proc.stderr.feed_eof()
                self.start_procs.append(proc)
                job = self.neuer_job()
                await self.klick(job)
                (q,) = self.quittungen()
                self.assertFalse(q["ok"])
                self.assertIn(stichwort, q["grund"])
                self.assertNotRegex(q["grund"], r"[/\\]")

    async def test_unbekannter_auftrag_und_verwerfen(self):
        await self.agent.on_client_message("auftrag_start", {"job": "gibtesnicht", "entscheidung": "ja"})
        (q,) = self.quittungen()
        self.assertEqual((q["job"], q["ok"]), ("gibtesnicht", False))
        self.assertIn("nicht mehr", q["grund"])
        self.task.frames.clear()
        job = self.neuer_job()
        await self.agent.on_client_message("auftrag_start", {"job": job.id, "entscheidung": "nein"})
        self.assertEqual(self.quittungen(), [])  # Verwerfen ist kein Start
        self.assertNotIn(job.id, self.agent._jobs)

    async def test_ohne_zulassung_ok_false(self):
        agent = self.ohne_zulassung()
        job = self.neuer_job(agent)
        await agent.on_client_message("auftrag_start", {"job": job.id, "entscheidung": "ja"})
        (q,) = self.quittungen()
        self.assertEqual((q["ok"], q["grund"]), (False, eb.SATZ_KEINE_ZULASSUNG))

    async def test_zugang_erweitert_gilt_als_angenommen(self):
        eingabe = {"projekt": "kern-testrepo", "neu": ["GH_TOKEN"], "bisher": []}
        fp = self.sk.dev_guard.fingerprint("WAMO-Zugang", eingabe)
        self.start_procs.append(self.sk.FakeProc([{"type": "wamo", "ereignis": "zugang_erweitert", "id": fp,
                                                   "tool": "WAMO-Zugang", "input": eingabe, "grund": "x"}], returncode=7))
        job = self.neuer_job()
        await self.klick(job)
        self.assertEqual([q["ok"] for q in self.quittungen()], [True])  # die Freigabekarte ist der nächste Schritt

    async def test_die_quittung_traegt_nie_einen_zugangswert(self):
        self.start_procs.append(self.sk.FakeProc([{"type": "wamo", "ereignis": "kanal_gesperrt", "grund": "zugang"}], returncode=2))
        job = self.neuer_job()
        await self.klick(job)
        self.assertNotIn("GEHEIMbuero", repr(self.task.messages()) + repr(self.task.llm_texts()))


# ---------------------------------------------------------------- R4: Audit und Kostenbremse über actions
def stream_mit_kosten(kosten: float, tool: bool = False):
    s = [dict(z) if isinstance(z, dict) else z for z in sk_stream(tool=tool)]
    for z in s:
        if isinstance(z, dict) and z.get("type") == "result":
            z["total_cost_usd"] = kosten
    return s


class AuditTests(BueroFlow):
    async def klick(self, job):
        await self.agent.on_client_message("auftrag_start", {"job": job.id, "entscheidung": "ja"})
        if self.agent._running is not None:
            await self.agent._running

    def quittungen(self):
        return [m for m in self.task.messages() if m.get("type") == "auftrag_quittung"]

    async def test_folge_pending_approved_running_done_mit_result(self):
        self.start_procs.append(self.sk.FakeProc(stream_mit_kosten(0.12)))
        job = self.neuer_job(art="analyse")
        await self.klick(job)
        self.assertEqual(self.memory.ablauf, [("log", "a1", "pending"), ("update", "a1", "approved"),
                                              ("update", "a1", "running"), ("abschluss", "a1", "done")])
        zeile = self.memory.zeilen["a1"]
        self.assertEqual((zeile["tool"], zeile["risk"]), ("entwicklungsauftrag", "read"))  # GitHub-Stufe lesen
        self.assertEqual(zeile["params"], {"job": job.id, "art": "analyse", "projekt": "kern-testrepo",
                                           "repo": "acme/testrepo", "profil": NIKO.replace("-", "")[:8]})
        r = zeile["result"]
        self.assertEqual((r["job"], r["art"], r["projekt"], r["repo"], r["status"], r["runden"]),
                         (job.id, "analyse", "kern-testrepo", "acme/testrepo", "fertig", 1))
        self.assertAlmostEqual(r["kosten_usd"], 0.12)
        self.assertIsInstance(r["dauer_s"], int)

    async def test_schreibende_art_hat_risiko_change(self):
        self.start_procs.append(self.sk.FakeProc(stream_mit_kosten(0.0)))
        await self.klick(self.neuer_job(art="umsetzung"))
        self.assertEqual(self.memory.zeilen["a1"]["risk"], "change")

    async def test_ohne_github_stufe_risiko_read(self):
        self.start_procs.append(self.sk.FakeProc(stream_mit_kosten(0.0)))
        await self.klick(self.neuer_job(art="recherche", repo=None))
        self.assertEqual(self.memory.zeilen["a1"]["risk"], "read")
        self.assertIsNone(self.memory.zeilen["a1"]["params"]["repo"])

    async def test_abgewiesener_start_geht_ueber_running_nach_failed_mit_grund(self):
        self.start_procs.append(self.sk.FakeProc([{"type": "wamo", "ereignis": "kanal_gesperrt", "grund": "kanal_zu"}],
                                                 returncode=2))
        job = self.neuer_job()
        await self.klick(job)
        self.assertEqual([a[2] for a in self.memory.ablauf], ["pending", "approved", "running", "failed"])
        r = self.memory.zeilen["a1"]["result"]
        self.assertEqual(r["status"], "nicht gestartet (Der Kanal ist geschlossen, es startet kein Auftrag)")
        self.assertEqual(r["grund"], "kanal_zu")
        self.assertEqual(r["kosten_usd"], 0)
        self.assertEqual([q["ok"] for q in self.quittungen()], [False])

    async def test_doppelklick_startet_nur_einmal_und_bucht_eine_zeile(self):
        self.start_procs.append(self.sk.FakeProc(stream_mit_kosten(0.1)))
        job = self.neuer_job()
        nachricht = {"job": job.id, "entscheidung": "ja"}
        await asyncio.gather(self.agent.on_client_message("auftrag_start", nachricht),
                             self.agent.on_client_message("auftrag_start", nachricht))
        await self.agent._running
        self.assertEqual(len(self.remote("start")), 1)
        self.assertEqual(self.memory.stati(), ["done"])

    async def test_unbekannter_grundcode_kommt_nie_als_freier_text_ins_result(self):
        self.start_procs.append(self.sk.FakeProc([{"type": "wamo", "ereignis": "kanal_gesperrt",
                                                   "grund": "sk-ant-api03-" + "GEHEIMbuero" * 4}], returncode=2))
        await self.klick(self.neuer_job())
        self.assertEqual(self.memory.zeilen["a1"]["result"]["grund"], "unbekannt")
        self.assertNotIn("GEHEIMbuero", repr(self.memory.zeilen) + repr(self.task.messages()))

    async def test_zweiter_klick_nach_ablehnung_legt_eine_neue_zeile_an(self):
        self.start_procs += [self.sk.FakeProc([{"type": "wamo", "ereignis": "kanal_gesperrt", "grund": "kanal_zu"}], returncode=2),
                             self.sk.FakeProc(stream_mit_kosten(0.1))]
        job = self.neuer_job()
        await self.klick(job)
        await self.klick(job)
        self.assertEqual(self.memory.stati(), ["failed", "done"])

    async def test_tagesgrenze_verhindert_den_start_mit_satz(self):
        self.memory.vortag_kosten = 10.0  # Grenze je Tag und Profil: 10 USD
        job = self.neuer_job()
        await self.klick(job)
        (q,) = self.quittungen()
        self.assertFalse(q["ok"])
        self.assertIn("heute", q["grund"])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.memory.zeilen, {})  # keine Zeile, kein Lauf
        self.assertFalse(job.started)
        self.memory.vortag_kosten = 9.99
        self.start_procs.append(self.sk.FakeProc(stream_mit_kosten(0.1)))
        self.task.frames.clear()
        await self.klick(job)
        self.assertEqual([q["ok"] for q in self.quittungen()], [True])

    async def test_offene_zeilen_ohne_result_zehren_den_tag_nicht_auf(self):
        """Eine Zeile, die in beginnen auf pending, approved oder rejected stehen blieb (ein Statuswechsel scheiterte), hatte nie einen
        Lauf und hat kein result. Sie zählt nicht mit der Auftragsgrenze, sonst sperrten vier misslungene Klicks den ganzen Tag."""
        for i, status in enumerate(("pending", "approved", "rejected") * 2):
            self.memory.zeilen[f"x{i}"] = {"tool": "entwicklungsauftrag", "status": status, "params": {}, "reason": "", "risk": "read",
                                           "result": None}
        self.assertEqual(await self.memory.tageskosten(laufend_usd=3.0), 0.0)  # sechs Zeilen zu 3 USD wären 18 USD, die Tagesgrenze ist 10
        self.start_procs.append(self.sk.FakeProc(stream_mit_kosten(0.1)))
        await self.klick(self.neuer_job())
        self.assertEqual([q["ok"] for q in self.quittungen()], [True])

    async def test_scheitert_running_bleibt_die_zeile_ohne_kosten_und_der_zweite_klick_geht(self):
        echt = self.memory.update_action

        async def running_scheitert(action_id, status):
            return False if status == "running" else await echt(action_id, status)

        self.memory.update_action = running_scheitert
        job = self.neuer_job()
        await self.klick(job)
        self.assertEqual(self.memory.stati(), ["approved"])  # niemand schließt sie: sie hat kein result
        self.assertEqual(await self.memory.tageskosten(laufend_usd=3.0), 0.0)  # und sie kostet den Tag nichts
        self.memory.update_action = echt
        self.start_procs.append(self.sk.FakeProc(stream_mit_kosten(0.1)))
        self.task.frames.clear()
        await self.klick(job)
        self.assertEqual([q["ok"] for q in self.quittungen()], [True])

    async def test_laufende_zeile_zaehlt_mindestens_mit_der_auftragsgrenze(self):
        self.memory.zeilen["lauf"] = {"tool": "entwicklungsauftrag", "status": "running", "params": {}, "reason": "", "risk": "read",
                                      "result": None}
        self.assertEqual(await self.memory.tageskosten(laufend_usd=3.0), 3.0)
        self.assertEqual(await self.memory.tageskosten(), 0.0)

    async def test_auftragsgrenze_verhindert_den_naechsten_lauf(self):
        fp = self.sk.dev_guard.fingerprint("Bash", self.sk.PUSH)
        self.pending_lines = [json.dumps({"id": fp, "grund": "git push", "tool": "Bash", "input": self.sk.PUSH})]
        self.start_procs.append(self.sk.FakeProc(stream_mit_kosten(3.5, tool=True)))  # Grenze je Auftrag: 3 USD
        job = self.neuer_job()
        await self.klick(job)
        self.assertEqual(self.memory.zeilen["a1"]["status"], "running")  # mit offener Freigabe läuft der Auftrag weiter
        await self.agent.on_client_message("freigabe", {"job": job.id, "id": fp, "entscheidung": "ja"})
        await self.agent._running
        self.assertEqual(len(self.remote("start")), 1)  # kein weiterer Lauf
        self.assertIn("Kostengrenze", job.status)
        self.assertEqual(self.memory.zeilen["a1"]["status"], "failed")
        self.assertAlmostEqual(self.memory.zeilen["a1"]["result"]["kosten_usd"], 3.5)
        self.assertTrue(any("Kostengrenze" in t for t in self.task.llm_texts()))

    async def test_kosten_unter_der_grenze_erlauben_den_naechsten_lauf(self):
        fp = self.sk.dev_guard.fingerprint("Bash", self.sk.PUSH)
        self.pending_lines = [json.dumps({"id": fp, "grund": "git push", "tool": "Bash", "input": self.sk.PUSH})]
        self.start_procs += [self.sk.FakeProc(stream_mit_kosten(2.9, tool=True)), self.sk.FakeProc(stream_mit_kosten(0.05))]
        job = self.neuer_job()
        await self.klick(job)
        self.pending_lines = []
        await self.agent.on_client_message("freigabe", {"job": job.id, "id": fp, "entscheidung": "ja"})
        await self.agent._running
        self.assertEqual(len(self.remote("start")), 2)
        self.assertEqual(self.memory.zeilen["a1"]["status"], "done")
        self.assertAlmostEqual(self.memory.zeilen["a1"]["result"]["kosten_usd"], 2.95)

    async def test_veraltete_running_zeile_wird_geschlossen_und_mit_der_auftragsgrenze_gezaehlt(self):
        self.memory.zeilen["alt"] = {"tool": "entwicklungsauftrag", "status": "running", "alt": True, "params": {},
                                     "reason": "", "risk": "read", "result": None}
        self.memory.vortag_kosten = 7.5  # mit der geschlossenen Zeile (3 USD, die Auftragsgrenze) sind es 10,5 USD
        job = self.neuer_job()
        await self.agent.on_client_message("auftrag_start", {"job": job.id, "entscheidung": "ja"})
        self.assertEqual(self.memory.veraltet_aufrufe, [{"nach_stunden": 2, "kosten_usd": 3.0}])
        self.assertEqual(self.memory.zeilen["alt"]["status"], "failed")
        self.assertEqual(self.memory.zeilen["alt"]["result"]["grund"], "abgelaufen")
        (q,) = self.quittungen()
        self.assertFalse(q["ok"])
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.memory.zeilen), ["alt"])

    async def test_datenbank_nicht_erreichbar_heisst_kein_start_mit_quittung(self):
        self.memory.erreichbar = False
        job = self.neuer_job()
        await self.klick(job)
        (q,) = self.quittungen()
        self.assertFalse(q["ok"])
        self.assertIn("Datenbank", q["grund"])
        self.assertEqual(self.calls, [])
        self.assertFalse(job.started)

    async def test_datenbank_ohne_gedaechtnis_heisst_kein_start(self):
        class OhneDatenbank(AuditMemory):
            async def log_action(self, *a, **k):
                return None  # MemoryStore ohne Datenbank (enabled False) liefert None

        self.agent._memory = self.agent._buch._memory = OhneDatenbank()
        job = self.neuer_job()
        await self.klick(job)
        self.assertEqual([q["ok"] for q in self.quittungen()], [False])
        self.assertEqual(self.calls, [])

    async def test_kein_zugang_schliesst_die_zeile_als_failed(self):
        with mock.patch.object(dev_agent, "claude_code_zugang_fuer", return_value=None):
            job = self.neuer_job()
            await self.klick(job)
        self.assertEqual(self.memory.stati(), ["failed"])
        self.assertEqual(self.calls, [])
        self.assertEqual([q["ok"] for q in self.quittungen()], [False])

    async def test_auflegen_schliesst_eine_offene_zeile(self):
        proc = self.sk.FakeProc([{"type": "wamo", "ereignis": "selbsttest_ok"}, {"type": "wamo", "ereignis": "gestartet"}],
                                haengt=True)
        self.start_procs.append(proc)
        job = self.neuer_job()
        await self.agent.on_client_message("auftrag_start", {"job": job.id, "entscheidung": "ja"})
        await self.warte_auf(lambda: self.agent._proc is proc and job.quittiert, "der Lauf ist gestartet und quittiert")
        await self.agent.close()
        self.assertEqual(self.memory.stati(), ["failed"])
        self.assertIn("Sitzung", self.memory.zeilen["a1"]["result"]["status"])
        try:
            await asyncio.wait_for(self.agent._running, 5)
        except asyncio.CancelledError:
            pass

    async def test_logzeilen_fuer_start_und_ende_mit_kuerzel_ohne_zugangswert(self):
        from loguru import logger

        meldungen: list[str] = []
        sink = logger.add(lambda m: meldungen.append(str(m)), level="DEBUG")
        self.start_procs.append(self.sk.FakeProc(stream_mit_kosten(0.12)))
        try:
            await self.klick(self.neuer_job())
        finally:
            logger.remove(sink)
        log = "\n".join(meldungen)
        kuerzel = NIKO.replace("-", "")[:8]
        self.assertRegex(log, rf"Start.*Profil {kuerzel}|Profil {kuerzel}.*Start")
        self.assertRegex(log, rf"Ende.*Profil {kuerzel}|Profil {kuerzel}.*Ende")
        self.assertNotIn(NIKO, log)
        self.assertNotIn("GEHEIMbuero", log)

    async def test_ein_dummy_zugangswert_kommt_nirgends_vor(self):
        from loguru import logger

        meldungen: list[str] = []
        sink = logger.add(lambda m: meldungen.append(str(m)), level="DEBUG")
        self.start_procs.append(self.sk.FakeProc(stream_mit_kosten(0.12)))
        try:
            proc = self.start_procs[0]
            job = self.neuer_job()
            await self.klick(job)
        finally:
            logger.remove(sink)
        gesehen = repr(self.memory.zeilen) + repr(self.memory.ablauf) + "\n".join(meldungen)
        self.assertNotIn("GEHEIMbuero", gesehen)  # keine Zeile in actions, kein result, kein Log
        self.assertNotIn(self.zugang["wert"], gesehen)
        self.assertIn(self.zugang["wert"], proc.stdin.data.decode())  # er ging nur über stdin an den Dispatcher


class FreigabeBuchungTests(BueroFlow):
    async def test_approved_wird_erst_nach_code_0_von_approve_verbucht(self):
        fp = self.sk.dev_guard.fingerprint("Bash", self.sk.PUSH)
        self.pending_lines = [json.dumps({"id": fp, "grund": "git push", "tool": "Bash", "input": self.sk.PUSH})]
        self.start_procs.append(self.sk.FakeProc(self.sk.stream()))
        job = self.neuer_job()
        await self.agent.on_client_message("auftrag_start", {"job": job.id, "entscheidung": "ja"})
        await self.agent._running
        self.assertIn(fp, job.pending)
        aktion = job.pending[fp]["action_id"]
        self.assertEqual(self.memory.zeilen[aktion]["status"], "pending")
        self.approve_rc = 2
        await self.agent.on_client_message("freigabe", {"job": job.id, "id": fp, "entscheidung": "ja"})
        self.assertEqual(self.memory.zeilen[aktion]["status"], "pending")  # nichts verbucht
        self.assertIn(fp, job.pending)  # die Karte bleibt
        self.start_procs.append(self.sk.FakeProc(self.sk.stream(tool=False)))
        self.approve_rc = 0
        await self.agent.on_client_message("freigabe", {"job": job.id, "id": fp, "entscheidung": "ja"})
        await self.agent._running
        self.assertEqual(self.memory.zeilen[aktion]["status"], "approved")  # der zweite Klick wird verbucht


if __name__ == "__main__":
    unittest.main()
