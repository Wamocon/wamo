"""webseite_oeffnen: im aktiven Tab laden statt in einem neuen (nur Attrappen, nie ein echter Browser).

Ausfuehren:  uv run python -m pytest -q test_tab.py   (im Ordner host/)
"""

import json
import unittest
from unittest import mock

import uia_control as u
import wamo_host as host

URL = "https://www.amazon.de/s?k=elektrofahrrad"
CHROME = {"hwnd": 7, "titel": "Amazon.de - Google Chrome", "programm": "chrome.exe"}
CHROME2 = {"hwnd": 8, "titel": "Zeitung - Google Chrome", "programm": "chrome.exe"}
EDGE = {"hwnd": 9, "titel": "Nachrichten - Microsoft Edge", "programm": "msedge.exe"}
WAMO_UI = {"hwnd": 5, "titel": "WAMO - Google Chrome", "programm": "chrome.exe"}
ELECTRON = {"hwnd": 4, "titel": "WAMO", "programm": "electron.exe"}
NOTIZ = {"hwnd": 11, "titel": "Notiz - Editor", "programm": "notepad.exe"}
LOGIN = {"hwnd": 6, "titel": "Anmelden - Google Konten - Google Chrome", "programm": "chrome.exe"}
BANK = {"hwnd": 12, "titel": "Zahlungsmethode hinzufügen - Google Chrome", "programm": "chrome.exe"}


class AdressBackend(u.Backend):
    """Attrappe der Adressleiste: merkt sich die Aufrufe, antwortet nach den Vorgaben des Tests."""

    def __init__(self, ereignisse):
        self.ereignisse = ereignisse
        self.aufrufe: list[tuple[int, str]] = []
        self.antwort = None  # None: genau die Adresse und Fokus; sonst Wert, Dict oder Ausnahme
        self.fehler: Exception | None = None
        self.gesetzt = ""
        self.lesen_antwort = None  # None: wie gesetzt und mit Fokus; sonst Dict oder Ausnahme (Kontrolle vor Enter)
        self.lesungen = 0

    def adresse_setzen(self, hwnd, url, vor_aktion=None):
        self.aufrufe.append((hwnd, url))
        self.ereignisse.append("adresse")
        if self.fehler:
            raise self.fehler
        if vor_aktion is not None:
            vor_aktion()
        self.gesetzt = url
        if self.antwort is None:
            return {"wert": url, "fokus": True}
        return self.antwort

    def adresse_lesen(self, hwnd):
        self.lesungen += 1
        self.ereignisse.append("pruefen")
        if isinstance(self.lesen_antwort, Exception):
            raise self.lesen_antwort
        return self.lesen_antwort if self.lesen_antwort is not None else {"wert": self.gesetzt, "fokus": True}


class TabTest(unittest.TestCase):
    """Gemeinsame Attrappen: Fensterliste, Vordergrund, Fokus, Tastendruck, Start, Sperre, Audit."""

    def setUp(self):
        host.PENDING.clear()
        host._recent_actions.clear()
        host._rate_zeiten.clear()
        self.ereignisse: list[str] = []
        self.audit: list[dict] = []
        self.fenster = [dict(CHROME)]
        self.vorne = None  # None: nach focus liegt das angeforderte Fenster vorne
        self.fokus_ok = True
        self.gesperrt = [False]
        self.b = AdressBackend(self.ereignisse)
        self.fokussiert: list[int] = []
        self.press = mock.Mock(side_effect=lambda codes: self.ereignisse.append(("enter", list(codes))))
        self.launch = mock.Mock()
        self.aktuell = {"hwnd": 0}

        def focus(hwnd):
            self.fokussiert.append(hwnd)
            self.ereignisse.append("fokus")
            if self.fokus_ok:
                self.aktuell = next(w for w in self.fenster if w["hwnd"] == hwnd)
            return self.fokus_ok

        def foreground():
            if self.vorne is not None:
                return dict(self.vorne)
            return dict(self.aktuell)

        def lock():
            return self.gesperrt[-1] if len(self.gesperrt) == 1 else self.gesperrt.pop(0)

        for p in (mock.patch.object(host, "UIA", u.UiaSchicht(self.b)),
                  mock.patch.object(host, "_audit", side_effect=self.audit.append),
                  mock.patch.object(host.wc, "list_windows", side_effect=lambda: [dict(w) for w in self.fenster]),
                  mock.patch.object(host.wc, "focus", side_effect=focus),
                  mock.patch.object(host.wc, "foreground", side_effect=foreground),
                  mock.patch.object(host.wc, "press", self.press),
                  mock.patch.object(host.wc, "is_locked", side_effect=lock),
                  mock.patch.object(host.wc, "window_rect", return_value={"x": 100, "y": 100, "b": 800, "h": 600}),
                  mock.patch.object(host.wc, "_monitorliste", return_value=[(0, 0, 2560, 1440), (2560, 0, 2560, 1440)]),
                  mock.patch.object(host, "resolve_app", side_effect=lambda n: f"{n}.exe"),
                  mock.patch.object(host, "_launch", self.launch)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(host._recent_actions.clear)
        self.addCleanup(host._rate_zeiten.clear)
        self.addCleanup(host.PENDING.clear)

    def eintraege(self):
        return [e for e in self.audit if e.get("tool") == "webseite_oeffnen"]


class AktiverTabTests(TabTest):
    def test_laedt_im_aktiven_tab_ohne_neuen_start(self):
        r = host.webseite_oeffnen(URL)
        self.assertEqual((r["status"], r["tab"], r["weg"], r["browser"]), ("success", "aktuell", "uia", "chrome"))
        self.assertIn("im aktiven Tab geladen", r["summary"])
        self.assertIn("Chrome", r["summary"])
        self.assertIn("Monitor 1", r["summary"])
        self.assertIn("Amazon.de", r["fenster"])
        self.launch.assert_not_called()
        self.assertEqual(self.b.aufrufe, [(7, URL)])
        self.assertEqual(self.ereignisse, ["fokus", "adresse", "pruefen", ("enter", [0x0D])])  # vorne, Adresse, Kontrolle, Enter

    def test_ergebnis_traegt_die_markierung_des_fensters(self):
        self.assertIn("markierung", host.webseite_oeffnen(URL))

    def test_zweite_suche_im_selben_tab(self):
        host.webseite_oeffnen("https://www.amazon.de")
        host.webseite_oeffnen(URL)
        self.launch.assert_not_called()
        self.assertEqual([a[1] for a in self.b.aufrufe], ["https://www.amazon.de", URL])

    def test_mehrere_fenster_nimmt_das_oberste_und_nennt_den_titel(self):
        self.fenster = [dict(CHROME2), dict(CHROME)]  # Reihenfolge = Stapel, oben zuerst
        r = host.webseite_oeffnen(URL)
        self.assertEqual(self.fokussiert, [8])
        self.assertEqual(self.b.aufrufe[0][0], 8)
        self.assertIn("Zeitung", r["fenster"])
        self.assertIn("Zeitung", r["summary"])

    def test_edge_wenn_edge_gewuenscht(self):
        self.fenster = [dict(CHROME), dict(EDGE)]
        r = host.webseite_oeffnen(URL, "edge")
        self.assertEqual((r["tab"], r["browser"]), ("aktuell", "edge"))
        self.assertEqual(self.b.aufrufe[0][0], 9)
        self.assertIn("Edge", r["summary"])

    def test_offenes_fenster_genuegt_auch_ohne_auffindbare_programmdatei(self):
        """Edge liegt hier unter EdgeCore: der Weg über die Adressleiste braucht die Programmdatei nicht."""
        self.fenster = [dict(EDGE)]
        with mock.patch.object(host, "resolve_app", side_effect=ValueError("edge ist auf diesem PC nicht installiert")):
            self.assertEqual(host.webseite_oeffnen(URL, "edge")["tab"], "aktuell")
            host._recent_actions.clear()
            self.b.fehler = u.UiaFehler("x")  # Rückfall ohne startbaren Browser: klare Ablehnung statt Absturz
            r = host.webseite_oeffnen(URL, "edge")
        self.assertEqual(r["status"], "rejected")
        self.assertIn("nicht installiert", r["summary"])

    def test_unbekannter_browser_wird_chrome(self):
        r = host.webseite_oeffnen(URL, "opera")
        self.assertEqual(r["browser"], "chrome")

    def test_neuer_tab_als_text_false_ist_kein_neuer_tab(self):
        for wert in ("false", "nein", "0", "", None, 0):
            with self.subTest(wert=wert):
                host._recent_actions.clear()
                self.assertEqual(host.webseite_oeffnen(URL, "chrome", wert)["tab"], "aktuell")
        self.launch.assert_not_called()

    def test_audit_ohne_inhalte_ausser_der_adresse(self):
        host.webseite_oeffnen(URL)
        e = self.eintraege()[-1]
        self.assertEqual((e["risk"], e["tab"], e["weg"], e["browser"], e["url"]), ("change", "aktuell", "uia", "chrome", URL))
        roh = json.dumps(self.audit, ensure_ascii=False)
        for inhalt in ("Amazon.de - Google", "Zeitung", "Nachrichten"):  # nie ein Fenster- oder Seitentitel
            self.assertNotIn(inhalt, roh)

    def test_doppelter_aufruf_wird_abgefangen_und_nennt_tab_und_weg(self):
        host.webseite_oeffnen(URL)
        r = host.webseite_oeffnen(URL)
        self.assertEqual(r["status"], "duplicate")
        dup = [e for e in self.audit if e.get("risk") == "duplicate"][-1]
        self.assertEqual((dup["tab"], dup["weg"]), ("aktuell", "uia"))
        self.assertEqual(len(self.b.aufrufe), 1)


class NeuerTabTests(TabTest):
    def test_neuer_tab_behaelt_das_alte_verhalten(self):
        r = host.webseite_oeffnen(URL, "chrome", neuer_tab=True)
        self.launch.assert_called_once_with("chrome.exe", [URL])
        self.assertEqual((r["status"], r["tab"], r["weg"]), ("success", "neu", "start"))
        self.assertIn("im neuen Tab geöffnet", r["summary"])
        self.assertIn("gewünscht", r["summary"])
        self.assertEqual(self.b.aufrufe, [])
        self.assertEqual(self.fokussiert, [])
        self.press.assert_not_called()
        e = self.eintraege()[-1]
        self.assertEqual((e["tab"], e["weg"]), ("neu", "start"))

    def test_neuer_tab_als_text_true(self):
        for wert in ("true", "ja", "1"):
            with self.subTest(wert=wert):
                host._recent_actions.clear()
                self.assertEqual(host.webseite_oeffnen(URL, "chrome", wert)["tab"], "neu")

    def test_dup_unterscheidet_aktuell_und_neu(self):
        host.webseite_oeffnen(URL)
        self.assertEqual(host.webseite_oeffnen(URL, "chrome", neuer_tab=True)["status"], "success")


class RueckfallTests(TabTest):
    def _neu(self, r, grund, code=None):
        self.assertEqual((r["status"], r["tab"], r["weg"]), ("success", "neu", "start"), r)
        self.assertIn("im neuen Tab geöffnet, ", r["summary"].replace(" (Chrome)", ""))
        self.assertIn(grund, r["summary"])
        if code:
            self.assertEqual(r["rueckfall"], code)
        self.launch.assert_called_once_with("chrome.exe", [URL])
        self.press.assert_not_called()

    def test_kein_browserfenster(self):
        self.fenster = [dict(NOTIZ)]
        r = host.webseite_oeffnen(URL)
        self._neu(r, "weil kein passendes Chrome-Fenster offen war")
        self.assertEqual(self.b.aufrufe, [])

    def test_nur_ein_anderer_browser_wird_nie_benutzt(self):
        self.fenster = [dict(EDGE)]
        r = host.webseite_oeffnen(URL, "chrome")
        self._neu(r, "weil kein passendes Chrome-Fenster offen war")
        self.assertEqual((self.b.aufrufe, self.fokussiert), ([], []))
        host.PENDING.clear()
        host._recent_actions.clear()
        self.launch.reset_mock()
        self.fenster = [dict(CHROME)]
        r = host.webseite_oeffnen(URL, "edge")  # umgekehrt genauso
        self.assertEqual((r["tab"], self.fokussiert), ("neu", []))
        self.launch.assert_called_once_with("edge.exe", [URL])

    def test_eigene_oberflaeche_wird_nie_benutzt(self):
        self.fenster = [dict(WAMO_UI), dict(ELECTRON)]
        r = host.webseite_oeffnen(URL)
        self._neu(r, "kein passendes Chrome-Fenster")
        self.assertEqual((self.b.aufrufe, self.fokussiert), ([], []))

    def test_eigene_oberflaeche_vorne_dann_das_naechste_browserfenster(self):
        self.fenster = [dict(WAMO_UI), dict(CHROME)]
        r = host.webseite_oeffnen(URL)
        self.assertEqual(r["tab"], "aktuell")
        self.assertEqual(self.fokussiert, [7])

    def test_anmelde_und_zahlungsseiten_werden_nicht_ueberschrieben(self):
        for fenster in (LOGIN, BANK):
            with self.subTest(titel=fenster["titel"]):
                self.launch.reset_mock()
                host._recent_actions.clear()
                self.fenster = [dict(fenster)]
                r = host.webseite_oeffnen(URL)
                self.assertEqual(r["tab"], "neu")
                self.assertEqual((self.b.aufrufe, self.fokussiert), ([], []))

    def test_fokus_verweigert(self):
        self.fokus_ok = False
        r = host.webseite_oeffnen(URL)
        self._neu(r, "Windows das Chrome-Fenster nicht nach vorne gelassen hat", "fokus")
        self.assertEqual(self.b.aufrufe, [])  # ohne Fokus wird die Adressleiste nicht angefasst

    def test_fokus_gemeldet_aber_anderes_fenster_vorne(self):
        self.vorne = {"hwnd": 99, "titel": "Notiz", "programm": "notepad.exe"}
        r = host.webseite_oeffnen(URL)
        self._neu(r, "nicht nach vorne gelassen", "fokus")
        self.assertEqual(self.b.aufrufe, [])

    def test_adressleiste_nicht_gefunden(self):
        self.b.fehler = u.UiaFehler("Die Adressleiste des Browsers ist per UI Automation nicht erreichbar.")
        r = host.webseite_oeffnen(URL)
        self._neu(r, "die Adressleiste von Chrome nicht per UI Automation zu bedienen war", "adressleiste")

    def test_adressleiste_ohne_fokus(self):
        self.b.antwort = {"wert": URL, "fokus": False}
        r = host.webseite_oeffnen(URL)
        self._neu(r, "Adressleiste", "adressleiste")

    def test_adressleiste_zeigt_etwas_anderes(self):
        self.b.antwort = {"wert": URL + "/ergaenzt", "fokus": True}
        r = host.webseite_oeffnen(URL)
        self._neu(r, "nicht genau die geprüfte Adresse", "adresse_abweichend")

    def test_fenster_wechselt_vor_dem_enter(self):
        orig = self.b.adresse_setzen

        def setzen(hwnd, url, vor_aktion=None):
            self.vorne = dict(WAMO_UI)  # nach dem Setzen liegt die eigene Oberfläche vorne
            return orig(hwnd, url, vor_aktion)

        self.b.adresse_setzen = setzen
        r = host.webseite_oeffnen(URL)
        self._neu(r, "das Fenster vor dem Abschicken gewechselt hat", "ziel_gewechselt")

    def test_anderes_fenster_vorne_vor_dem_enter(self):
        orig = self.b.adresse_setzen

        def setzen(hwnd, url, vor_aktion=None):
            self.vorne = dict(CHROME2)
            return orig(hwnd, url, vor_aktion)

        self.b.adresse_setzen = setzen
        self._neu(host.webseite_oeffnen(URL), "gewechselt", "ziel_gewechselt")

    def test_titel_wird_zur_anmeldeseite_vor_dem_enter(self):
        orig = self.b.adresse_setzen

        def setzen(hwnd, url, vor_aktion=None):
            self.vorne = dict(CHROME, titel="Anmelden - Google Chrome")
            return orig(hwnd, url, vor_aktion)

        self.b.adresse_setzen = setzen
        self._neu(host.webseite_oeffnen(URL), "gewechselt", "ziel_gewechselt")

    def test_rueckfall_steht_im_audit_ohne_titel(self):
        self.b.fehler = u.UiaFehler("x")
        host.webseite_oeffnen(URL)
        e = self.eintraege()
        self.assertEqual([(x["tab"], x["weg"]) for x in e], [("aktuell", "uia"), ("neu", "start")])
        self.assertEqual(e[-1]["rueckfall"], "adressleiste")
        self.assertNotIn("Amazon", json.dumps(self.audit))

    def test_timeout_der_adressleiste_ist_ein_rueckfall(self):
        with mock.patch.object(u, "ADRESSE_SECS", 0.05):
            self.b.adresse_setzen = lambda h, url: __import__("time").sleep(0.4)
            r = host.webseite_oeffnen(URL)
        self.assertEqual((r["tab"], r["rueckfall"]), ("neu", "adressleiste"))
        self.press.assert_not_called()


class ReviewTests(TabTest):
    """Funde aus dem Review: Kontrolle direkt vor Enter, Abbruch des Fadens, Mail/Messenger, offene Freigabe, Rate."""

    def test_kontrolle_vor_enter_faengt_fokus_in_der_seite_ab(self):
        self.b.lesen_antwort = {"wert": URL, "fokus": False}  # die Person hat in die Seite geklickt
        r = host.webseite_oeffnen(URL)
        self.assertEqual((r["tab"], r["rueckfall"]), ("neu", "adressleiste"))
        self.press.assert_not_called()

    def test_kontrolle_vor_enter_faengt_nachtraegliche_ergaenzung_ab(self):
        self.b.lesen_antwort = {"wert": URL + "x", "fokus": True}
        r = host.webseite_oeffnen(URL)
        self.assertEqual((r["tab"], r["rueckfall"]), ("neu", "adresse_abweichend"))
        self.press.assert_not_called()

    def test_kontrolle_nicht_lesbar_ist_rueckfall(self):
        self.b.lesen_antwort = u.UiaFehler("x")
        self.assertEqual(host.webseite_oeffnen(URL)["rueckfall"], "adressleiste")
        self.press.assert_not_called()

    def test_abgebrochener_faden_greift_spaeter_nicht_mehr_ein(self):
        import threading
        import time
        eingriffe = []
        freigabe = threading.Event()

        def langsam(hwnd, url, vor_aktion=None):
            freigabe.wait(2)  # haengt, bis der Host aufgegeben hat
            try:
                vor_aktion()
            except u.UiaFehler:
                return {"wert": "", "fokus": False}
            eingriffe.append(url)
            return {"wert": url, "fokus": True}

        self.b.adresse_setzen = langsam
        with mock.patch.object(u, "ADRESSE_SECS", 0.05):
            r = host.webseite_oeffnen(URL)
        freigabe.set()
        time.sleep(0.2)
        self.assertEqual(r["rueckfall"], "adressleiste")
        self.assertEqual(eingriffe, [])  # SetFocus und SetValue wurden nicht mehr ausgefuehrt

    def test_mail_und_messenger_seiten_werden_nicht_ueberschrieben(self):
        for titel in ("Posteingang - max@firma.de - Gmail - Google Chrome", "WhatsApp Web - Google Chrome",
                      "Microsoft Teams - Google Chrome"):
            with self.subTest(titel=titel):
                host._recent_actions.clear()
                self.launch.reset_mock()
                self.fenster = [dict(CHROME, titel=titel)]
                self.assertEqual(host.webseite_oeffnen(URL)["tab"], "neu")
                self.assertEqual(self.b.aufrufe, [])

    def test_offene_freigabe_haelt_den_aktiven_tab_an_aber_nicht_den_neuen(self):
        host._neue_freigabe("powershell", command="x")
        self.assertEqual(host.webseite_oeffnen(URL)["status"], "wartet")
        self.assertEqual((self.fokussiert, self.b.aufrufe), ([], []))
        self.assertEqual(host.webseite_oeffnen(URL, "chrome", neuer_tab=True)["tab"], "neu")

    def test_rate_grenze_nur_fuer_den_aktiven_tab(self):
        with mock.patch.object(host, "ADRESSE_RATE_PRO_MINUTE", 2):
            host._rate_zeiten.pop("webseite_oeffnen", None)
            for i in range(2):
                self.assertEqual(host.webseite_oeffnen(f"https://www.amazon.de/s?k={i}")["status"], "success")
            self.assertEqual(host.webseite_oeffnen("https://www.amazon.de/s?k=x")["status"], "rejected")
        host._rate_zeiten.pop("webseite_oeffnen", None)

    def test_enter_scheitert_mit_rueckfall_statt_rohem_fehler(self):
        self.press.side_effect = OSError("SendInput")
        self.assertEqual(host.webseite_oeffnen(URL)["tab"], "neu")

    def test_gesperrt_nach_fehlgeschlagener_adressleiste_startet_nichts(self):
        self.b.fehler = u.UiaFehler("x")
        self.gesperrt = [False, False, True]  # vorab frei, danach vor dem Rueckfall gesperrt
        r = host.webseite_oeffnen(URL)
        self.assertEqual(r["status"], "locked")
        self.launch.assert_not_called()
        self.assertTrue(any(e.get("risk") == "locked" for e in self.eintraege()))

    def test_sperre_vor_enter_steht_im_audit(self):
        self.gesperrt = [False, False, True]
        self.assertEqual(host.webseite_oeffnen(URL)["status"], "locked")
        self.assertTrue(any(e.get("risk") == "locked" for e in self.eintraege()))


class SperreTests(TabTest):
    def test_gesperrter_bildschirm_vorab(self):
        self.gesperrt = [True]
        r = host.webseite_oeffnen(URL)
        self.assertEqual(r["status"], "locked")
        self.assertEqual((self.launch.call_count, self.fokussiert, self.b.aufrufe), (0, [], []))
        self.press.assert_not_called()

    def test_sperre_zwischen_adresse_und_enter_sendet_nichts_und_startet_nichts(self):
        self.gesperrt = [False, False, True]  # Vorab (zweimal) frei, vor dem Enter gesperrt
        r = host.webseite_oeffnen(URL)
        self.assertEqual(r["status"], "locked")
        self.press.assert_not_called()
        self.launch.assert_not_called()
        self.assertEqual(len(self.b.aufrufe), 1)

    def test_nicht_pruefbare_sperre_gilt_als_gesperrt(self):
        with mock.patch.object(host.wc, "is_locked", side_effect=OSError):
            self.assertEqual(host.webseite_oeffnen(URL)["status"], "locked")
        self.press.assert_not_called()


class AdressenTests(TabTest):
    def test_nicht_erlaubte_adressen_beruehren_nichts(self):
        for url in ("file:///C:/Windows/System32/cmd.exe", "javascript:alert(1)", "http://localhost:3000",
                    "http://192.168.0.1/", "https://user:pw@example.com/", 'https://example.com/" & calc', "ftp://x.de",
                    "https://example.com/a b", "", "https://" + "a" * 3000 + ".de"):
            with self.subTest(url=url[:40]):
                r = host.webseite_oeffnen(url)
                self.assertEqual(r["status"], "rejected")
        self.assertEqual((self.launch.call_count, self.fokussiert, self.b.aufrufe), (0, [], []))
        self.press.assert_not_called()

    def test_lange_adresse_braucht_freigabe_und_laedt_danach_im_aktiven_tab(self):
        url = "https://www.example.com/s?q=" + "a" * 250
        r = host.webseite_oeffnen(url)
        self.assertEqual((r["status"], r["aktion"], r["detail"]), ("needs_approval", "webseite", url))
        self.assertEqual((self.launch.call_count, self.fokussiert, self.b.aufrufe), (0, [], []))
        freigabe = [e for e in self.audit if e.get("risk") == "critical"][-1]
        self.assertEqual(freigabe["tab"], "aktuell")
        with mock.patch.object(host, "FREIGABE_SCHLUESSEL", "k" * 43):
            ergebnis = host.entscheiden(r["approval_id"], True, "k" * 43)
        self.assertEqual((ergebnis["status"], ergebnis["tab"]), ("success", "aktuell"), ergebnis)
        self.assertEqual(self.b.aufrufe, [(7, url)])
        self.launch.assert_not_called()
        self.assertIn(("enter", [0x0D]), self.ereignisse)
        e = [x for x in self.eintraege() if x.get("freigabe")][-1]
        self.assertEqual((e["tab"], e["weg"]), ("aktuell", "uia"))

    def test_lange_adresse_mit_neuer_tab_startet_nach_freigabe_einen_neuen(self):
        url = "https://www.example.com/s?q=" + "a" * 250
        r = host.webseite_oeffnen(url, "chrome", neuer_tab=True)
        self.assertEqual(r["status"], "needs_approval")
        with mock.patch.object(host, "FREIGABE_SCHLUESSEL", "k" * 43):
            ergebnis = host.entscheiden(r["approval_id"], True, "k" * 43)
        self.assertEqual((ergebnis["status"], ergebnis["tab"]), ("success", "neu"))
        self.launch.assert_called_once_with("chrome.exe", [url])
        self.assertEqual(self.b.aufrufe, [])

    def test_abgelehnte_freigabe_aendert_nichts(self):
        url = "https://www.example.com/s?q=" + "a" * 250
        r = host.webseite_oeffnen(url)
        with mock.patch.object(host, "FREIGABE_SCHLUESSEL", "k" * 43):
            self.assertEqual(host.entscheiden(r["approval_id"], False, "k" * 43)["status"], "rejected")
        self.assertEqual((self.launch.call_count, self.fokussiert, self.b.aufrufe), (0, [], []))


class SchichtTests(unittest.TestCase):
    """UiaSchicht.adresse_setzen: eigene Schutzprüfungen vor dem Backend."""

    def setUp(self):
        self.b = AdressBackend([])
        self.s = u.UiaSchicht(self.b)

    def test_nur_http_und_https_ohne_leer_und_steuerzeichen(self):
        for url in ("javascript:alert(1)", "file:///C:/x", "chrome://settings", "https://a.de/ b", 'https://a.de/"', "",
                    "https://a.de/\n", None, 42, "https://" + "a" * 3000):
            with self.subTest(url=str(url)[:30]), self.assertRaises(u.UiaFehler):
                self.s.adresse_setzen(1, url)
        self.assertEqual(self.b.aufrufe, [])

    def test_gueltige_adresse_geht_ans_backend(self):
        self.assertEqual(self.s.adresse_setzen(5, "https://www.amazon.de/s?k=a+b&x=1")["ausgefuehrt"], True)
        self.assertEqual(self.b.aufrufe, [(5, "https://www.amazon.de/s?k=a+b&x=1")])

    def test_abweichender_wert_und_fehlender_fokus(self):
        self.b.antwort = {"wert": "https://a.de/x", "fokus": True}
        with self.assertRaises(u.AdresseAbweichend):
            self.s.adresse_setzen(1, "https://a.de/")
        self.b.antwort = {"wert": "https://a.de/", "fokus": False}
        with self.assertRaises(u.UiaFehler) as ctx:
            self.s.adresse_setzen(1, "https://a.de/")
        self.assertNotIsInstance(ctx.exception, u.AdresseAbweichend)

    def test_backend_ausnahme_wird_zum_uia_fehler(self):
        self.b.fehler = RuntimeError("boom")
        with self.assertRaises(u.UiaFehler):
            self.s.adresse_setzen(1, "https://a.de/")

    def test_element_text_setzen_bleibt_fuer_adressleisten_gesperrt(self):
        k = u.Knoten(rid=(1,), typ="Eingabefeld", name="Adress- und Suchleiste", muster=("wert",))
        with self.assertRaises(u.UiaFehler):
            u.pruefe_beschreibbar(k)


if __name__ == "__main__":
    unittest.main()
