"""Tests der Sprachanbindung des Fernzugriffs auf die Sparks (persona.FERNZUGRIFF_PROMPT, tools.fernzugriff_oeffnen, bot-Verdrahtung).

Der Fernzugriff war vorher nur ein Knopf in den Einstellungen: WAMO kannte ihn nicht und konnte auf die Frage nach den Monitoren der Sparks
nur "kenne ich nicht" sagen. Jetzt kennt ihn die Persona (nur Admin bei eingeschaltetem Fernzugriff), und das Werkzeug öffnet nur den Dialog
in der Oberfläche: Die Sitzung startet der Nutzer dort selbst, mit einem echten Klick auf Verbinden. Seit KP-2 verlangt der Dialog den Code der
Authenticator-App nur noch bei einer älteren Anmeldung; das Codefeld war früher die Schranke gegen ein Öffnen durch das Modell (Befund M7),
jetzt ist es der Klick der Person.

Ausführen:  uv run python -m pytest -q test_fernzugriff_sprache.py   (in voice-core/)
"""

import asyncio
import re
import unittest
from pathlib import Path
from types import SimpleNamespace

import persona as P
import tools as T
from auth_gate import Identity

HIER = Path(__file__).resolve().parent
ADMIN = Identity("123e4567-e89b-12d3-a456-426614174000", "Niko", "admin", "du")
MITGLIED = Identity("223e4567-e89b-12d3-a456-426614174000", "Erika", "mitglied", "sie")


class FakeLLM:
    def __init__(self):
        self.funktionen: dict = {}
        self.frames: list = []

    def register_function(self, name, fn, **kw):
        self.funktionen[name] = fn

    async def push_frame(self, frame):
        self.frames.append(frame)


def schemas(identity, **kw):
    llm = FakeLLM()
    ts = T.register_tools(llm, memory=None, identity=identity, zugang=None, **kw)
    return llm, [s.name for s in ts.standard_tools]


class PersonaTests(unittest.TestCase):
    def test_standard_bleibt_unveraendert(self):
        self.assertEqual(P.build_system_prompt("Niko", "du"), P.build_system_prompt("Niko", "du", fernzugriff=False))
        self.assertNotIn("fernzugriff_oeffnen", P.build_system_prompt("Niko", "du"))

    def test_mit_fernzugriff_kennt_wamo_die_sparks_und_das_werkzeug(self):
        text = P.build_system_prompt("Niko", "du", fernzugriff=True)
        for muss in ("fernzugriff_oeffnen", "Sokrates 1", "Sokrates 2", "Caesar", "Authenticator-App", "nie selbst", "Live-Bild"):
            self.assertIn(muss, text)
        self.assertNotIn("{name}", text)

    def test_kein_code_bei_jedem_start_und_der_klick_gehoert_der_person(self):
        """Seit KP-2 verlangt der Dialog den Code nur bei einer älteren Anmeldung. WAMO sagt nicht mehr, dass jedes Mal ein Code
        nötig ist, und nennt den Klick auf Verbinden als den Schritt der Person (Befund M7)."""
        text = P.build_system_prompt("Niko", "du", fernzugriff=True)
        self.assertNotIn("frischen Code", text)
        self.assertNotIn("Code der Authenticator-App eingeben muss", text)
        self.assertIn("selbst auf Verbinden klickt", text)
        self.assertRegex(text, r"Nur bei einer älteren Anmeldung fragt der Dialog zusätzlich den Code der\s+Authenticator-App ab")

    def test_nur_der_baustein_kommt_dazu(self):
        ohne, mit = P.build_system_prompt("Niko", "du"), P.build_system_prompt("Niko", "du", fernzugriff=True)
        self.assertEqual(mit.replace(P._fill(P.FERNZUGRIFF_PROMPT, {"name": "Niko", "anrede": "du"}), ""), ohne)

    def test_text_ohne_markdown_und_gedankenstriche(self):
        baustein = P.FERNZUGRIFF_PROMPT
        for zeichen in (chr(0x2014), chr(0x2013), "**", "`", "#"):
            self.assertNotIn(zeichen, baustein)
        self.assertLess(len(baustein), 900)  # der Prompt ist im Sprachpfad: kurz halten


class WerkzeugTests(unittest.TestCase):
    def test_nur_admin_mit_schalter_bekommt_das_werkzeug(self):
        self.assertIn("fernzugriff_oeffnen", schemas(ADMIN, fernzugriff=True)[1])
        self.assertNotIn("fernzugriff_oeffnen", schemas(ADMIN, fernzugriff=False)[1])
        self.assertNotIn("fernzugriff_oeffnen", schemas(ADMIN)[1])
        llm, namen = schemas(MITGLIED, fernzugriff=True)  # selbst bei falschem Aufruf nie für Mitglieder
        self.assertNotIn("fernzugriff_oeffnen", namen)
        self.assertNotIn("fernzugriff_oeffnen", llm.funktionen)

    def test_andere_werkzeuge_bleiben(self):
        _, namen = schemas(ADMIN, fernzugriff=True)
        self.assertEqual(namen[:4], ["aktuelle_zeit", "merken", "websuche", "canvas_zeigen"])
        self.assertEqual(schemas(ADMIN)[1], namen[:4])

    def test_aufruf_schickt_nur_die_nachricht_an_die_oberflaeche(self):
        llm, _ = schemas(ADMIN, fernzugriff=True)
        ergebnisse = []

        async def rueck(r):
            ergebnisse.append(r)

        params = SimpleNamespace(llm=llm, arguments={"ziel": "sokrates2", "egal": "x"}, result_callback=rueck)
        asyncio.run(llm.funktionen["fernzugriff_oeffnen"](params))
        self.assertEqual([f.data for f in llm.frames], [{"type": "fernzugriff_oeffnen"}])  # ohne Ziel und ohne Nutzdaten
        self.assertEqual(ergebnisse[0]["geoeffnet"], True)
        self.assertIn("Verbinden", ergebnisse[0]["hinweis"])  # der Klick gehört der Person
        self.assertNotIn("Code", ergebnisse[0]["hinweis"])    # kein Hinweis auf ein Codefeld, das es meist nicht mehr gibt

    def test_aufruf_startet_nie_eine_sitzung_auch_wenn_alles_bereitsteht(self):
        """Befund M7 (Kern): Das Codefeld war die Schranke gegen ein Öffnen durch das Modell. Ohne Codefeld darf der Aufruf an
        keiner Stelle Datenbank, Gateway oder Zugangsdienst berühren: Alles, was eine Sitzung beginnen kann, wird zur Falle."""
        import rd_api

        beruehrt: list[str] = []

        def falle(name):
            async def f(*_a, **_k):
                beruehrt.append(name)
                raise AssertionError(f"{name} berührt")
            return f

        alt = {n: getattr(rd_api, n) for n in ("start", "gateway", "_sitzung_beginnen", "_am_gateway_anmelden", "shared_database")}
        rd_api.start = falle("start")
        rd_api.gateway = falle("gateway")
        rd_api._sitzung_beginnen = falle("_sitzung_beginnen")
        rd_api._am_gateway_anmelden = falle("_am_gateway_anmelden")
        rd_api.shared_database = lambda: beruehrt.append("shared_database")
        try:
            llm, _ = schemas(ADMIN, fernzugriff=True)

            async def rueck(_r):
                pass

            asyncio.run(llm.funktionen["fernzugriff_oeffnen"](SimpleNamespace(llm=llm, arguments={}, result_callback=rueck)))
        finally:
            for n, f in alt.items():
                setattr(rd_api, n, f)
        self.assertEqual(beruehrt, [])
        self.assertEqual([f.data for f in llm.frames], [{"type": "fernzugriff_oeffnen"}])

    def test_beschreibung_nennt_den_klick_und_keinen_code_bei_jedem_start(self):
        beschreibung = T.FERNZUGRIFF_SCHEMA.description
        self.assertIn("Startet nie selbst eine Sitzung", beschreibung)
        self.assertIn("klickt selbst auf Verbinden", beschreibung)
        self.assertNotIn("bestätigt mit dem", beschreibung)

    def test_tools_importiert_rd_api_nie(self):
        import ast

        baum = ast.parse((HIER / "tools.py").read_text(encoding="utf-8"))
        namen = {a.name.split(".")[0] for n in ast.walk(baum) if isinstance(n, ast.Import) for a in n.names}
        namen |= {n.module.split(".")[0] for n in ast.walk(baum) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertNotIn("rd_api", namen)
        self.assertNotIn("geraete_admin_api", namen)

    def test_schema_hat_keine_parameter(self):
        self.assertEqual(T.FERNZUGRIFF_SCHEMA.name, "fernzugriff_oeffnen")
        self.assertEqual(T.FERNZUGRIFF_SCHEMA.properties, {})
        self.assertEqual(T.FERNZUGRIFF_SCHEMA.required, [])


class VerdrahtungTests(unittest.TestCase):
    """bot.py ist zu schwer zum Starten: Die Verdrahtung wird am Quelltext geprüft."""

    @classmethod
    def setUpClass(cls):
        cls.bot = (HIER / "bot.py").read_text(encoding="utf-8")

    def test_schalter_nur_fuer_admin_und_nur_bei_aktivem_fernzugriff(self):
        self.assertRegex(self.bot, r"fernzugriff = is_admin and rd_aktiv\(\)")
        self.assertIn("from rd_api import aktiv as rd_aktiv", self.bot)

    def test_beide_stellen_bekommen_denselben_schalter(self):
        self.assertRegex(self.bot, r"build_system_prompt\([^)]*fernzugriff=fernzugriff")
        self.assertRegex(self.bot, r"register_tools\(llm, memory, identity, turn, zugang=zugang, fernzugriff=fernzugriff\)")

    def test_aktiv_liest_den_schalter_ohne_standard(self):
        import os
        import rd_api
        alt = os.environ.pop("WAMO_RD_AKTIV", None)
        try:
            self.assertFalse(rd_api.aktiv())
            os.environ["WAMO_RD_AKTIV"] = " Ja "
            self.assertTrue(rd_api.aktiv())
            os.environ["WAMO_RD_AKTIV"] = "nein"
            self.assertFalse(rd_api.aktiv())
        finally:
            os.environ.pop("WAMO_RD_AKTIV", None)
            if alt is not None:
                os.environ["WAMO_RD_AKTIV"] = alt

    def test_keine_sitzung_ueber_die_stimme(self):
        tools = (HIER / "tools.py").read_text(encoding="utf-8")
        handler = tools[tools.index("async def fernzugriff_oeffnen"):tools.index('llm.register_function("aktuelle_zeit"')]
        handler = "\n".join(z.split("#")[0] for z in handler.splitlines())  # nur der Code, nicht die Erklärung im Kommentar
        for verboten in ("rd_api", "start(", "sitzung", "guacamole", "totp", "subprocess"):
            self.assertNotIn(verboten.lower(), handler.lower(), verboten)
        self.assertEqual(len(re.findall(r"push_frame", handler)), 1)


if __name__ == "__main__":
    unittest.main()
