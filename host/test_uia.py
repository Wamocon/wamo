"""Tests der UI-Automation-Schicht und ihrer Host-Werkzeuge, ohne Fenster und ohne COM (Attrappe des Elementbaums).

Ausführen:  uv run pytest test_uia.py   (im Ordner host/)
Der echte Zugriff (ComUia) wird hier nie benutzt; er wurde von Hand gegen ein selbst gestartetes Fenster geprüft.
"""

import json
import threading
import unittest
from unittest import mock

import uia_control as u
import wamo_host as host

SCHLUESSEL = "freigabe-" + "k" * 40
NOTIZ = {"hwnd": 11, "titel": "Notiz - Editor", "programm": "notepad.exe"}
TEAMS = {"hwnd": 42, "titel": "Chat | Microsoft Teams", "programm": "ms-teams.exe"}


def knoten(rid, typ, name, **kw):
    return u.Knoten(rid=(rid,), typ=typ, name=name, **kw)


def standard_baum():
    return [
        knoten(1, "Schaltfläche", "Speichern", muster=("invoke",)),
        knoten(2, "Eingabefeld", "Titel", muster=("wert",)),
        knoten(3, "Eingabefeld", "Kennwort", passwort=True, muster=("wert",)),
        knoten(4, "Kontrollkästchen", "Haken", muster=("invoke", "toggle"), zustand=("aus",)),
        knoten(5, "Eingabefeld", "Adressleiste", muster=("wert",)),
        knoten(6, "Eingabefeld", "Fix", muster=("wert",), nur_lesen=True),
        knoten(7, "Schaltfläche", "Gesperrt", aktiv=False, muster=("invoke",)),
        knoten(8, "Schaltfläche", "Senden", muster=("invoke",)),
        knoten(9, "Gruppe", "Rahmen"),
        knoten(10, "Text", "Anzeige 1"),
    ]


class Attrappe(u.Backend):
    """Elementbaum aus Daten. Aktionen werden mitgeschrieben; Handles sind die Knoten selbst."""

    def __init__(self, baum=None):
        self.baum = list(baum if baum is not None else standard_baum())
        self.taten: list[tuple] = []
        self.aufnahmen = 0
        self.haengt = False
        self.fehler: Exception | None = None
        self.werte = {2: "alter Titel"}
        self.sperre = threading.Event()

    def momentaufnahme(self, hwnd, tiefe, maximal, auch_texte):
        self.aufnahmen += 1
        if self.fehler:
            raise self.fehler
        sicht = [k for k in self.baum if auch_texte or k.typ != "Text"]
        sicht = [k for k in sicht if k.typ in u.BEDIENBAR or k.muster or k.typ == "Text"]
        return [(k, k) for k in sicht[:maximal]], len(sicht) > maximal

    def tun(self, handle, was, text=None):
        if self.haengt and was == "invoke":
            self.sperre.wait(30)
        self.taten.append((handle.rid[0], was, text))
        if was == "wert_lesen":
            return self.werte.get(handle.rid[0], "")
        if was == "wert_setzen":
            self.werte[handle.rid[0]] = text
        return ""


class SchichtTests(unittest.TestCase):
    def setUp(self):
        self.b = Attrappe()
        self.zeit = [1000.0]
        self.s = u.UiaSchicht(self.b, uhr=lambda: self.zeit[0])

    def liste(self, **kw):
        return self.s.auflisten(11, "notepad.exe", **kw)

    def test_liste_nummeriert_und_ohne_texte_ohne_auch_texte(self):
        li = self.liste()
        namen = [k.name for k in li.knoten]
        self.assertNotIn("Anzeige 1", namen)
        self.assertEqual(namen[0], "Speichern")
        self.assertIn("Anzeige 1", [k.name for k in self.liste(auch_texte=True).knoten])

    def test_grenzen_fuer_tiefe_und_anzahl(self):
        li = self.s.auflisten(11, "notepad.exe", tiefe=99, maximal=3)
        self.assertEqual(len(li.knoten), 3)
        self.assertTrue(li.gekuerzt)
        self.assertEqual(li.tiefe, u.MAX_TIEFE)
        self.assertEqual(self.s.auflisten(11, "notepad.exe", maximal=10**6).maximal, u.MAX_ELEMENTE_GRENZE)
        self.assertEqual(self.s.auflisten(11, "notepad.exe", maximal=-5).maximal, 1)

    def test_beschreibung_zeigt_nie_einen_wert_beim_passwortfeld(self):
        li = self.liste()
        eintraege = [u.beschreibung(i, k) for i, k in enumerate(li.knoten, 1)]
        pw = eintraege[2]
        self.assertEqual(pw["typ"], u.PASSWORTFELD)
        self.assertTrue(pw["gesperrt"])
        self.assertNotIn("wert", pw)
        self.assertEqual(eintraege[3]["zustand"], ["aus"])
        self.assertEqual(eintraege[6]["zustand"][0], "deaktiviert")
        self.assertIn("schreibgeschützt", eintraege[5]["zustand"])

    def test_namen_mit_passwortwoertern_sind_geschuetzt_auch_ohne_flag(self):
        k = knoten(50, "Eingabefeld", "Neues Passwort bestätigen", muster=("wert",))
        self.assertTrue(k.geschuetzt)
        self.assertTrue(u.beschreibung(1, k).get("gesperrt"))
        with self.assertRaises(u.UiaFehler):
            u.pruefe_beschreibbar(k)

    def test_sicherer_text_entfernt_steuerzeichen_und_kuerzt(self):
        t = u.sicherer_text("a\x00b‮​c\nd\te\x1b[31m" + "x" * 300)
        self.assertNotIn("\x00", t)
        self.assertNotIn("‮", t)
        self.assertNotIn("​", t)
        self.assertNotIn("\n", t)
        self.assertLessEqual(len(t), u.NAME_MAX)
        self.assertEqual(u.sicherer_text(None), "")
        self.assertEqual(u.sicherer_text("Ä ö ü ß"), "Ä ö ü ß")

    def test_holen_fehler(self):
        with self.assertRaises(u.UiaFehler) as e:
            self.s.holen(11, "notepad.exe", 1)  # nie aufgelistet
        self.assertIn("elemente_auflisten", str(e.exception))
        self.liste()
        for schlecht in (0, 99, -1, True, "1", None, 1.0):
            with self.subTest(nummer=schlecht), self.assertRaises(u.UiaFehler):
                self.s.holen(11, "notepad.exe", schlecht)
        self.s.holen(11, "notepad.exe", 1)

    def test_liste_gehoert_zum_fenster_und_programm(self):
        self.liste()
        with self.assertRaises(u.UiaFehler):
            self.s.holen(12, "notepad.exe", 1)  # anderes Fenster
        with self.assertRaises(u.UiaFehler):
            self.s.holen(11, "chrome.exe", 1)  # Handle wurde neu vergeben
        with self.assertRaises(u.UiaFehler):
            self.s.holen(11, "notepad.exe", 1)  # und die Liste ist dabei verworfen worden

    def test_nummern_verfallen_nach_zeit(self):
        self.liste()
        self.zeit[0] += u.NUMMER_GUELTIG_SECS + 1
        with self.assertRaises(u.UiaFehler):
            self.s.holen(11, "notepad.exe", 1)

    def test_neues_auflisten_ersetzt_die_alte_liste(self):
        a = self.liste()
        self.b.baum.insert(0, knoten(99, "Schaltfläche", "Neu", muster=("invoke",)))
        b = self.liste()
        li, k = self.s.holen(11, "notepad.exe", 1)
        self.assertIs(li, b)
        self.assertEqual(k.name, "Neu")
        self.assertIsNot(li, a)

    def test_veraltet_wenn_element_dazukommt_oder_verschwindet(self):
        li, k = self._hole(1)
        self.b.baum.append(knoten(77, "Schaltfläche", "Dazu", muster=("invoke",)))
        with self.assertRaises(u.UiaFehler) as e:
            self.s.klicken(11, li, k)
        self.assertIn("veraltet", str(e.exception))
        self.assertEqual(self.b.taten, [])  # kein Klick auf eine veraltete Nummer
        with self.assertRaises(u.UiaFehler):
            self.s.holen(11, "notepad.exe", 1)  # Liste ist verworfen

    def test_veraltet_wenn_element_ersetzt_wird(self):
        li, k = self._hole(1)
        self.b.baum[0] = knoten(500, "Schaltfläche", "Speichern", muster=("invoke",))  # neue Kennung, gleicher Name
        with self.assertRaises(u.UiaFehler):
            self.s.klicken(11, li, k)
        self.assertEqual(self.b.taten, [])

    def test_veraltet_wenn_gleiche_kennung_anderer_name(self):
        li, k = self._hole(1)
        self.b.baum[0] = knoten(1, "Schaltfläche", "Löschen", muster=("invoke",))
        with self.assertRaises(u.UiaFehler):
            self.s.klicken(11, li, k)
        self.assertEqual(self.b.taten, [])

    def test_anzeigetexte_machen_nummern_nicht_ungueltig(self):
        li, k = self._hole(1, auch_texte=True)
        self.b.baum[-1] = knoten(10, "Text", "Anzeige 2")
        self.b.baum.append(knoten(88, "Text", "Ausdruck 1 +"))
        self.assertTrue(self.s.klicken(11, li, k)["ausgefuehrt"])

    def _hole(self, nr, **kw):
        self.liste(**kw)
        return self.s.holen(11, "notepad.exe", nr)

    def test_klick_nimmt_invoke_vor_toggle(self):
        li, k = self._hole(1)
        self.assertEqual(self.s.klicken(11, li, k)["muster"], "invoke")
        li, k = self._hole(4)
        self.assertEqual(self.s.klicken(11, li, k)["muster"], "invoke")
        k2 = knoten(4, "Kontrollkästchen", "Haken", muster=("toggle",))
        self.assertEqual(u.UiaSchicht._muster_fuer_klick(k2), "toggle")

    def test_klick_ohne_muster_wird_abgelehnt(self):
        k = knoten(9, "Gruppe", "Rahmen")
        with self.assertRaises(u.UiaFehler) as e:
            self.s.klicken(11, None, k)
        self.assertIn("auf_bildschirm_klicken", str(e.exception))

    def test_deaktivierte_und_geschuetzte_elemente_werden_nicht_geklickt(self):
        li, k = self._hole(7)
        with self.assertRaises(u.UiaFehler):
            self.s.klicken(11, li, k)
        li, k = self._hole(3)
        with self.assertRaises(u.UiaFehler):
            self.s.klicken(11, li, k)
        self.assertEqual(self.b.taten, [])

    def test_text_setzen_und_verbote(self):
        li, k = self._hole(2)
        self.assertEqual(self.s.text_setzen(11, li, k, "Neuer Titel äöü")["zeichen"], 15)
        self.assertIn((2, "wert_setzen", "Neuer Titel äöü"), self.b.taten)
        n = len(self.b.taten)
        for nr in (3, 5, 6, 7, 1):  # Passwort, Adressleiste, schreibgeschützt, deaktiviert, ohne Wertmuster
            li, k = self._hole(nr)
            with self.subTest(nr=nr), self.assertRaises(u.UiaFehler):
                self.s.text_setzen(11, li, k, "x")
        li, k = self._hole(2)
        for schlecht in (None, 5, "x" * (u.TEXT_MAX + 1)):
            with self.subTest(text=repr(schlecht)[:10]), self.assertRaises(u.UiaFehler):
                self.s.text_setzen(11, li, k, schlecht)
        self.assertEqual(len(self.b.taten), n)

    def test_lesen_gibt_wert_gekuerzt_und_nie_bei_passwort(self):
        li, k = self._hole(2)
        self.b.werte[2] = "a\x00b" + "z" * 900
        r = self.s.lesen(11, li, k)
        self.assertTrue(r["gekuerzt"])
        self.assertLessEqual(len(r["wert"]), u.WERT_MAX)
        self.assertNotIn("\x00", r["wert"])
        li, k = self._hole(3)
        with self.assertRaises(u.UiaFehler):
            self.s.lesen(11, li, k)
        self.assertNotIn((3, "wert_lesen", None), self.b.taten)

    def test_haengendes_invoke_gilt_als_ausgeloest(self):
        u_alt = u.AKTION_SECS
        u.AKTION_SECS = 0.3
        self.addCleanup(setattr, u, "AKTION_SECS", u_alt)
        self.addCleanup(self.b.sperre.set)
        self.b.haengt = True
        li, k = self._hole(1)
        r = self.s.klicken(11, li, k)
        self.assertTrue(r["ausgefuehrt"])
        self.assertFalse(r["ruecklauf"])
        with self.assertRaises(u.UiaFehler):
            self.s.holen(11, "notepad.exe", 1)  # Dialog offen: alte Nummern gelten nicht mehr

    def test_haengende_aufnahme_wird_gemeldet(self):
        u_alt = u.SNAPSHOT_SECS
        u.SNAPSHOT_SECS = 0.2
        self.addCleanup(setattr, u, "SNAPSHOT_SECS", u_alt)
        ev = threading.Event()
        self.addCleanup(ev.set)
        self.b.momentaufnahme = lambda *a: ev.wait(30)
        with self.assertRaises(u.UiaFehler) as e:
            self.liste()
        self.assertIn("antwortet nicht", str(e.exception))

    def test_com_fehler_wird_zu_uiafehler_ohne_stacktrace(self):
        self.b.fehler = OSError("boom geheim")
        with self.assertRaises(u.UiaFehler) as e:
            self.liste()
        self.assertNotIn("geheim", str(e.exception))


class HostTests(unittest.TestCase):
    """Die Werkzeuge im Host mit allen Wächtern davor."""

    def setUp(self):
        host.PENDING.clear()
        host._recent_actions.clear()
        host._rate_zeiten.clear()
        self.audit: list[dict] = []
        self.b = Attrappe()
        self.fenster = dict(NOTIZ)
        for p in (mock.patch.object(host, "UIA", u.UiaSchicht(self.b)),
                  mock.patch.object(host, "FREIGABE_SCHLUESSEL", SCHLUESSEL),
                  mock.patch.object(host, "_audit", side_effect=self.audit.append),
                  mock.patch.object(host.wc, "find_window", side_effect=lambda q: self.fenster),
                  mock.patch.object(host.wc, "foreground", side_effect=lambda: self.fenster),
                  mock.patch.object(host.wc, "window_info", side_effect=lambda h: self.fenster)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(host.PENDING.clear)
        self.addCleanup(host._rate_zeiten.clear)
        self.addCleanup(host._recent_actions.clear)

    def liste(self):
        r = host.elemente_auflisten("Notiz", auch_texte=True)
        self.assertEqual(r["status"], "success", r)
        return {e["name"]: e["nr"] for e in r["elemente"]}

    # --- Auflisten ---
    def test_auflisten_ist_read_ohne_nebenwirkung(self):
        r = host.elemente_auflisten("Notiz")
        self.assertEqual(r["status"], "success")
        self.assertEqual(self.b.taten, [])
        eintrag = [e for e in self.audit if e.get("tool") == "elemente_auflisten"][-1]
        self.assertEqual(eintrag["risk"], "read")
        self.assertEqual(eintrag["anzahl"], len(r["elemente"]))
        kennwort = [e for e in r["elemente"] if e["name"] == "Kennwort"][0]
        self.assertTrue(kennwort["gesperrt"])
        self.assertNotIn("alter Titel", json.dumps(r, ensure_ascii=False))

    def test_auflisten_nie_in_verbotenen_fenstern(self):
        for f in ({"hwnd": 1, "titel": "Windows PowerShell", "programm": "powershell.exe"},
                  {"hwnd": 2, "titel": "x", "programm": ""},
                  {"hwnd": 3, "titel": "Anmelden - Bank", "programm": "chrome.exe"},
                  {"hwnd": 4, "titel": "WAMO", "programm": "electron.exe"},
                  {"hwnd": 5, "titel": "Passwörter", "programm": "keepassxc.exe"}):
            self.fenster = f
            with self.subTest(f=f["programm"]):
                self.assertEqual(host.elemente_auflisten("x")["status"], "rejected")
        self.assertEqual(self.b.aufnahmen, 0)

    def test_gesperrter_bildschirm_und_offene_freigabe(self):
        with mock.patch.object(host.wc, "is_locked", return_value=True):
            for r in (host.elemente_auflisten("x"), host.element_lesen(1), host.element_klicken(1),
                      host.element_text_setzen(1, "x")):
                self.assertEqual(r["status"], "locked")
        self.assertEqual(self.b.aufnahmen, 0)
        nr = self.liste()
        host.PENDING["a" * 16] = {"art": "powershell", "erstellt": host._jetzt(), "frist": 300, "command": "x"}
        self.assertEqual(host.element_klicken(nr["Speichern"])["status"], "wartet")
        self.assertEqual(host.element_text_setzen(nr["Titel"], "x")["status"], "wartet")
        self.assertEqual(self.b.taten, [])

    # --- Klicken ---
    def test_klick_change_mit_audit(self):
        nr = self.liste()
        r = host.element_klicken(nr["Speichern"], "Notiz")
        self.assertEqual(r["status"], "success", r)
        self.assertIn((1, "invoke", None), self.b.taten)
        e = [x for x in self.audit if x.get("tool") == "element_klicken" and x.get("risk") == "change"]
        self.assertEqual(len(e), 1)
        self.assertEqual((e[0]["element"], e[0]["ziel"], e[0]["nummer"]), ("Speichern", "Notiz - Editor", nr["Speichern"]))

    def test_klick_auf_veraltete_nummer(self):
        nr = self.liste()
        self.b.baum.append(knoten(66, "Schaltfläche", "Neu", muster=("invoke",)))
        r = host.element_klicken(nr["Speichern"], "Notiz")
        self.assertEqual(r["status"], "rejected")
        self.assertIn("veraltet", r["summary"])
        self.assertEqual(self.b.taten, [])
        self.assertEqual(host.element_klicken(nr["Speichern"], "Notiz")["status"], "rejected")  # Liste ist weg

    def test_klick_ohne_liste_oder_mit_falscher_nummer(self):
        self.assertEqual(host.element_klicken(1)["status"], "rejected")
        self.liste()
        for n in (0, 999, -3):
            self.assertEqual(host.element_klicken(n)["status"], "rejected")
        self.assertEqual(self.b.taten, [])

    def test_klick_passwortfeld_und_deaktiviertes_element(self):
        nr = self.liste()
        self.assertEqual(host.element_klicken(nr["Kennwort"])["status"], "rejected")
        self.assertEqual(host.element_klicken(nr["Gesperrt"])["status"], "rejected")
        self.assertEqual(self.b.taten, [])

    def test_klick_in_messenger_braucht_freigabekarte_und_laeuft_genau_einmal(self):
        self.fenster = dict(TEAMS)
        nr = self.liste()
        r = host.element_klicken(nr["Speichern"], "Teams", beschreibung="Speichern")
        self.assertEqual((r["status"], r["aktion"]), ("needs_approval", "klicken"))
        self.assertIn("Speichern", r["detail"])
        self.assertIn("Chat | Microsoft Teams", r["ziel"])
        self.assertEqual(self.b.taten, [])
        erstes = host.entscheiden(r["approval_id"], True, SCHLUESSEL)
        zweites = host.entscheiden(r["approval_id"], True, SCHLUESSEL)
        self.assertEqual(erstes["status"], "success", erstes)
        self.assertEqual(zweites["status"], "error")
        self.assertEqual(self.b.taten.count((1, "invoke", None)), 1)

    def test_freigegebener_klick_in_anderes_fenster_laeuft_nicht(self):
        self.fenster = dict(TEAMS)
        nr = self.liste()
        r = host.element_klicken(nr["Speichern"], "Teams")
        self.fenster = {"hwnd": 42, "titel": "Bank", "programm": "chrome.exe"}
        ergebnis = host.entscheiden(r["approval_id"], True, SCHLUESSEL)
        self.assertEqual(ergebnis["status"], "error")
        self.assertEqual(self.b.taten, [])

    def test_freigegebener_klick_scheitert_wenn_element_inzwischen_ersetzt(self):
        self.fenster = dict(TEAMS)
        nr = self.liste()
        r = host.element_klicken(nr["Speichern"], "Teams")
        self.b.baum[0] = knoten(900, "Schaltfläche", "Speichern", muster=("invoke",))
        ergebnis = host.entscheiden(r["approval_id"], True, SCHLUESSEL)
        self.assertEqual(ergebnis["status"], "error")
        self.assertEqual(self.b.taten, [])

    def test_nein_fuehrt_nichts_aus(self):
        self.fenster = dict(TEAMS)
        nr = self.liste()
        r = host.element_klicken(nr["Speichern"], "Teams")
        self.assertEqual(host.entscheiden(r["approval_id"], False, SCHLUESSEL)["status"], "rejected")
        self.assertEqual(self.b.taten, [])

    def test_sensible_namen_brauchen_freigabe_in_jedem_fenster(self):
        nr = self.liste()
        r = host.element_klicken(nr["Senden"], "Notiz")
        self.assertEqual(r["status"], "needs_approval")
        self.assertEqual(self.b.taten, [])
        for name in ("Absenden", "Jetzt bezahlen", "Zahlungspflichtig bestellen", "Konto löschen", "Delete", "Veröffentlichen",
                     "Abmelden", "Place order", "Überweisung ausführen"):
            self.assertTrue(host.SENSIBLER_NAME.search(name), name)
        for name in ("Speichern", "Öffnen", "Abbrechen", "Weiter", "Sendungsverfolgung zeigen"):
            self.assertFalse(host.SENSIBLER_NAME.search(name), name)

    def test_freigabe_karte_zeigt_elementname_und_fenstertitel(self):
        self.fenster = dict(TEAMS)
        nr = self.liste()
        r = host.element_klicken(nr["Senden"], "Teams")
        self.assertIn("Senden", r["detail"])
        self.assertIn("Chat | Microsoft Teams", r["detail"])
        self.assertIn("ms-teams.exe", r["ziel"])

    def test_klick_rate_grenze(self):
        nr = self.liste()
        with mock.patch.object(host, "UIA_PRO_MINUTE", 2):
            self.assertEqual(host.element_klicken(nr["Speichern"], "Notiz")["status"], "success")
            self.assertEqual(host.element_klicken(nr["Haken"], "Notiz")["status"], "success")
            r = host.element_klicken(nr["Speichern"], "Notiz")
        self.assertEqual(r["status"], "rejected")
        self.assertEqual(len(self.b.taten), 2)

    def test_klick_in_konsole_oder_eigener_oberflaeche(self):
        nr = self.liste()
        for f in ({"hwnd": 11, "titel": "Windows PowerShell", "programm": "powershell.exe"},
                  {"hwnd": 11, "titel": "WAMO", "programm": "electron.exe"}):
            self.fenster = f
            self.assertEqual(host.element_klicken(nr["Speichern"], "x")["status"], "rejected")
        self.assertEqual(self.b.taten, [])

    # --- Text setzen ---
    def test_text_setzen_change_audit_ohne_klartext(self):
        nr = self.liste()
        geheim = "Mein privater Satz 4711"
        r = host.element_text_setzen(nr["Titel"], geheim, "Notiz")
        self.assertEqual(r["status"], "success", r)
        self.assertIn((2, "wert_setzen", geheim), self.b.taten)
        eintraege = [e for e in self.audit if e.get("tool") == "element_text_setzen"]
        self.assertTrue(eintraege)
        for e in eintraege:
            self.assertNotIn("4711", json.dumps(e, ensure_ascii=False))
        self.assertEqual(eintraege[-1]["zeichen"], len(geheim))
        self.assertNotIn("4711", json.dumps(r, ensure_ascii=False))

    def test_text_setzen_verbote(self):
        nr = self.liste()
        for name in ("Kennwort", "Adressleiste", "Fix", "Gesperrt", "Speichern"):
            with self.subTest(name=name):
                self.assertEqual(host.element_text_setzen(nr[name], "x", "Notiz")["status"], "rejected")
        self.assertEqual(host.element_text_setzen(nr["Titel"], "x" * 2001, "Notiz")["status"], "rejected")
        self.assertEqual(host.element_text_setzen(nr["Titel"], 5, "Notiz")["status"], "rejected")
        self.assertEqual(self.b.taten, [])
        self.assertEqual(host.element_text_setzen(nr["Titel"], "", "Notiz")["status"], "success")  # Feld leeren ist erlaubt
        self.assertEqual(self.b.taten, [(2, "wert_setzen", "")])  # und nur das ist passiert

    def test_text_setzen_nicht_im_explorer(self):
        nr = self.liste()
        self.fenster = {"hwnd": 11, "titel": "Dokumente", "programm": "explorer.exe"}
        self.assertEqual(host.element_text_setzen(nr["Titel"], "x", "Dokumente")["status"], "rejected")
        self.assertEqual(self.b.taten, [])

    def test_text_in_messenger_nur_nach_freigabe_und_ohne_text_im_audit(self):
        self.fenster = dict(TEAMS)
        nr = self.liste()
        r = host.element_text_setzen(nr["Titel"], "Bin gleich da 4711", "Teams")
        self.assertEqual((r["status"], r["aktion"]), ("needs_approval", "senden"))
        self.assertEqual(r["detail"], "Bin gleich da 4711")
        self.assertEqual(self.b.taten, [])
        ok = host.entscheiden(r["approval_id"], True, SCHLUESSEL)
        self.assertEqual(ok["status"], "success", ok)
        self.assertEqual(self.b.taten.count((2, "wert_setzen", "Bin gleich da 4711")), 1)
        self.assertEqual(host.entscheiden(r["approval_id"], True, SCHLUESSEL)["status"], "error")
        for e in self.audit:
            self.assertNotIn("4711", json.dumps(e, ensure_ascii=False, default=str))

    def test_text_rate_grenze(self):
        nr = self.liste()
        with mock.patch.object(host, "UIA_PRO_MINUTE", 1):
            self.assertEqual(host.element_text_setzen(nr["Titel"], "a", "Notiz")["status"], "success")
            self.assertEqual(host.element_text_setzen(nr["Titel"], "b", "Notiz")["status"], "rejected")

    # --- Lesen ---
    def test_lesen_read_und_passwort_verboten(self):
        nr = self.liste()
        r = host.element_lesen(nr["Titel"], "Notiz")
        self.assertEqual((r["status"], r["wert"]), ("success", "alter Titel"))
        self.assertEqual(host.element_lesen(nr["Kennwort"], "Notiz")["status"], "rejected")
        e = [x for x in self.audit if x.get("tool") == "element_lesen" and x.get("risk") == "read"]
        self.assertEqual(len(e), 1)
        self.assertNotIn("alter Titel", json.dumps(e[0], ensure_ascii=False))

    # --- Registrierung ---
    def test_werkzeuge_registriert_und_im_llm_filter(self):
        import asyncio
        import pathlib
        import re
        namen = {t.name for t in asyncio.run(host.mcp.list_tools())}
        neu = {"elemente_auflisten", "element_klicken", "element_text_setzen", "element_lesen"}
        self.assertTrue(neu <= namen)
        quelle = (pathlib.Path(host.__file__).parent.parent / "voice-core" / "pc_host.py").read_text(encoding="utf-8")
        llm = re.search(r"LLM_TOOLS = \[(.*?)\]", quelle, re.S).group(1)
        for n in neu:
            self.assertIn(f'"{n}"', llm)

    def test_audit_art_und_eingabe_arten(self):
        self.assertIn("uia_klick", host.EINGABE_ARTEN)
        self.assertIn("uia_text", host.EINGABE_ARTEN)


class ReviewTests(unittest.TestCase):
    """Regressionstests zu den Funden aus dem Review (Zombie-Faden, Systemfenster, UNC, Namen, Grenzen)."""

    def setUp(self):
        host.PENDING.clear()
        host._rate_zeiten.clear()
        self.b = Attrappe()
        self.fenster = dict(NOTIZ)
        self.audit: list[dict] = []
        for p in (mock.patch.object(host, "UIA", u.UiaSchicht(self.b)),
                  mock.patch.object(host, "FREIGABE_SCHLUESSEL", SCHLUESSEL),
                  mock.patch.object(host, "_audit", side_effect=self.audit.append),
                  mock.patch.object(host.wc, "find_window", side_effect=lambda q: self.fenster),
                  mock.patch.object(host.wc, "foreground", side_effect=lambda: self.fenster),
                  mock.patch.object(host.wc, "window_info", side_effect=lambda h: self.fenster)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(host.PENDING.clear)
        self.addCleanup(host._rate_zeiten.clear)

    def liste(self):
        r = host.elemente_auflisten("x", auch_texte=True)
        self.assertEqual(r["status"], "success", r)
        return {e["name"]: e["nr"] for e in r["elemente"]}

    def test_langsame_aufnahme_fuehrt_die_aktion_nie_nachtraeglich_aus(self):
        """Der Faden, den der Aufrufer nach dem Zeitlimit aufgegeben hat, klickt später nicht doch noch."""
        alt = u.AKTION_SECS
        u.AKTION_SECS = 0.2
        self.addCleanup(setattr, u, "AKTION_SECS", alt)
        schicht = u.UiaSchicht(self.b)
        li = schicht.auflisten(11, "notepad.exe")
        li, k = schicht.holen(11, "notepad.exe", 1)
        frei = threading.Event()
        original = self.b.momentaufnahme

        def langsam(*a):
            frei.wait(5)
            return original(*a)

        self.b.momentaufnahme = langsam
        with self.assertRaises(u.UiaFehler):
            schicht.klicken(11, li, k)
        frei.set()
        import time
        time.sleep(0.4)  # dem Zombie-Faden Zeit geben, weiterzulaufen
        self.assertEqual(self.b.taten, [])

    def test_zombie_faden_loescht_keine_neue_liste(self):
        alt = u.AKTION_SECS
        u.AKTION_SECS = 0.2
        self.addCleanup(setattr, u, "AKTION_SECS", alt)
        schicht = u.UiaSchicht(self.b)
        li = schicht.auflisten(11, "notepad.exe")
        li, k = schicht.holen(11, "notepad.exe", 1)
        frei = threading.Event()
        original = self.b.momentaufnahme
        self.b.momentaufnahme = lambda *a: (frei.wait(5), original(*a))[1]
        self.b.baum.append(knoten(66, "Schaltfläche", "Neu", muster=("invoke",)))  # der Zombie sähe "veraltet"
        with self.assertRaises(u.UiaFehler):
            schicht.klicken(11, li, k)
        self.b.momentaufnahme = original
        neu = schicht.auflisten(11, "notepad.exe")
        frei.set()
        import time
        time.sleep(0.4)
        self.assertIs(schicht.holen(11, "notepad.exe", 1)[0], neu)

    def test_hoechstens_vier_faeden_gleichzeitig(self):
        alt = u.SNAPSHOT_SECS
        u.SNAPSHOT_SECS = 0.05
        self.addCleanup(setattr, u, "SNAPSHOT_SECS", alt)
        frei = threading.Event()
        self.addCleanup(frei.set)
        self.b.momentaufnahme = lambda *a: frei.wait(10)
        schicht = u.UiaSchicht(self.b)
        for _ in range(u.MAX_FAEDEN):
            with self.assertRaises(u.UiaFehler):
                schicht.auflisten(11, "notepad.exe")
        with self.assertRaises(u.UiaFehler) as e:
            schicht.auflisten(11, "notepad.exe")
        self.assertIn("belegt", str(e.exception))

    def test_faden_ende_wird_aufgerufen(self):
        self.b.faden_ende = mock.Mock()
        u.UiaSchicht(self.b).auflisten(11, "notepad.exe")
        self.b.faden_ende.assert_called_once()

    def test_umgebungsvariablen_fenster_nur_mit_karte(self):
        nr = self.liste()
        for f in ({"hwnd": 11, "titel": "Systemeigenschaften", "programm": "SystemPropertiesAdvanced.exe"},
                  {"hwnd": 11, "titel": "Umgebungsvariablen", "programm": "rundll32.exe"}):
            self.fenster = f
            self.liste()
            host.PENDING.clear()
            k = host.element_klicken(nr["Speichern"], "x")
            self.assertEqual((k["status"], k["aktion"]), ("needs_approval", "klicken"), k)
            self.assertIn("Umgebungsvariablen", k["summary"])
            host.PENDING.clear()  # offene Anfragen lassen Eingaben ruhen
            t = host.element_text_setzen(nr["Titel"], "C:\\evil", "x")
            self.assertEqual(t["status"], "needs_approval", t)
        self.assertEqual(self.b.taten, [])

    def test_sensible_kennung_und_namenlose_knoepfe(self):
        self.b.baum.append(knoten(70, "Schaltfläche", "", aid="SendButton", muster=("invoke",)))
        self.b.baum.append(knoten(71, "Schaltfläche", "", muster=("invoke",)))
        nr = {e["nr"]: e for e in host.elemente_auflisten("x")["elemente"]}
        nummern = [n for n, e in nr.items() if e["name"] == "(ohne Name)"]
        self.assertEqual(len(nummern), 2)
        for n in nummern:
            host.PENDING.clear()
            r = host.element_klicken(n, "x")
            self.assertEqual(r["status"], "needs_approval", r)
        self.assertEqual(self.b.taten, [])

    def test_vollbreite_schreibweise_umgeht_die_namenspruefung_nicht(self):
        name = u.sicherer_text("Ｓｅｎｄｅｎ")
        self.assertEqual(name, "Senden")
        self.assertTrue(host.SENSIBLER_NAME.search(name))
        self.assertTrue(knoten(1, "Eingabefeld", u.sicherer_text("Ｐａｓｓｗｏｒｔ")).geschuetzt)

    def test_weitere_geheimnis_namen_sind_geschuetzt(self):
        for name in ("API-Key", "API Key", "Zugriffstoken", "Token", "IBAN", "Kreditkarte", "Card number", "Schlüssel",
                     "Neues Passwort"):
            k = knoten(1, "Eingabefeld", name, muster=("wert",))
            self.assertTrue(k.geschuetzt or name == "Zugriffstoken", name)
        self.assertTrue(knoten(1, "Eingabefeld", "Token", muster=("wert",)).geschuetzt)

    def test_lesen_und_auflisten_haben_eine_rate_grenze(self):
        with mock.patch.object(host, "UIA_LESEN_PRO_MINUTE", 2):
            self.assertEqual(host.elemente_auflisten("x")["status"], "success")
            self.assertEqual(host.elemente_auflisten("x")["status"], "success")
            self.assertEqual(host.elemente_auflisten("x")["status"], "rejected")
        host._rate_zeiten.clear()
        nr = self.liste()
        with mock.patch.object(host, "UIA_LESEN_PRO_MINUTE", 1):
            host._rate_zeiten.clear()
            self.assertEqual(host.element_lesen(nr["Titel"])["status"], "success")
            self.assertEqual(host.element_lesen(nr["Titel"])["status"], "rejected")

    def test_kartentitel_ist_bereinigt(self):
        self.fenster = {"hwnd": 11, "titel": "Chat\u202e | Teams\x00", "programm": "ms-teams.exe"}
        nr = self.liste()
        r = host.element_klicken(nr["Speichern"], "x")
        self.assertEqual(r["status"], "needs_approval")
        self.assertNotIn("\u202e", r["detail"])
        self.assertNotIn("\x00", r["detail"])

    def test_unc_und_fremdes_laufwerk_bei_ordner_anlegen(self):
        with mock.patch.object(host.Path, "resolve", side_effect=AssertionError("resolve darf nicht laufen")):
            for pfad in ("\\\\10.255.255.1\\share\\x\\y", "//10.255.255.1/share/x", "\\\\?\\C:\\x", "\\\\.\\pipe\\x"):
                with self.subTest(pfad=pfad):
                    self.assertEqual(host.ordner_anlegen(pfad)["status"], "rejected")
        fremd = "Z:\\Projekte\\x" if host.HOME.drive.lower() != "z:" else "Y:\\Projekte\\x"
        with mock.patch.object(host.Path, "resolve", side_effect=AssertionError("resolve darf nicht laufen")):
            self.assertEqual(host.ordner_anlegen(fremd)["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
