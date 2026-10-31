"""Tests für die Markierung des Lichtrahmens (wamo_host._markierung, windows_control.rechteck und window_rect).

Der Host liefert dem Kern nur ganze Zahlen mit Obergrenzen (Fensterrechteck, Klickpunkt, Monitor), nie Titel oder
Inhalte. Die Tests nutzen Attrappen: kein Fenster, keine Maus, keine Tastatur.
"""

import json
import unittest
from unittest import mock

import wamo_host as host
import windows_control as wc

MONITOR = {"x": 0, "y": 0, "b": 1920, "h": 1080}
FENSTER = {"x": 100, "y": 50, "b": 800, "h": 600}
NOTIZ = {"hwnd": 77, "titel": "Notizen - geheimer Kundenname", "programm": "notepad.exe"}


def _attrappen(fenster=FENSTER, monitor=MONITOR):
    return (mock.patch.object(host.wc, "window_rect", return_value=dict(fenster) if fenster else None),
            mock.patch.object(host.wc, "monitor_rect", return_value=dict(monitor) if monitor else None))


def _nur_zahlen(wert) -> bool:
    if isinstance(wert, dict):
        return all(isinstance(k, str) and _nur_zahlen(v) for k, v in wert.items())
    return isinstance(wert, int) and not isinstance(wert, bool)


class ZahlenPruefungTests(unittest.TestCase):
    def test_rechteck_nimmt_ganze_zahlen_in_den_grenzen(self):
        self.assertEqual(wc.rechteck(0, 0, 1, 1), {"x": 0, "y": 0, "b": 1, "h": 1})
        self.assertEqual(wc.rechteck(-1920, -200, 1920, 1080), {"x": -1920, "y": -200, "b": 1920, "h": 1080})
        self.assertIsNotNone(wc.rechteck(wc.MARK_POS_MAX, wc.MARK_POS_MIN, wc.MARK_GROESSE_MAX, wc.MARK_GROESSE_MAX))

    def test_rechteck_lehnt_alles_andere_ab(self):
        schlecht = [
            (0.5, 0, 10, 10), (0, 0, 10.0, 10), ("1", 0, 10, 10), (None, 0, 10, 10), (True, 0, 10, 10),
            (0, 0, 0, 10), (0, 0, 10, 0), (0, 0, -5, 10), (0, 0, 10, -5),
            (wc.MARK_POS_MAX + 1, 0, 10, 10), (0, wc.MARK_POS_MIN - 1, 10, 10),
            (0, 0, wc.MARK_GROESSE_MAX + 1, 10), (0, 0, 10, wc.MARK_GROESSE_MAX + 1),
            (float("nan"), 0, 10, 10), (10**12, 0, 10, 10),
        ]
        for args in schlecht:
            with self.subTest(args=args):
                self.assertIsNone(wc.rechteck(*args))

    def test_punkt(self):
        self.assertEqual(wc.punkt(5, -7), {"x": 5, "y": -7})
        for args in [(1.0, 2), ("1", 2), (None, 2), (True, 2), (wc.MARK_POS_MAX + 1, 0), (0, wc.MARK_POS_MIN - 1)]:
            with self.subTest(args=args):
                self.assertIsNone(wc.punkt(*args))

    def test_schnitt(self):
        self.assertEqual(wc.schnitt({"x": 0, "y": 0, "b": 100, "h": 100}, {"x": 50, "y": 60, "b": 100, "h": 100}),
                         {"x": 50, "y": 60, "b": 50, "h": 40})
        self.assertIsNone(wc.schnitt({"x": 0, "y": 0, "b": 10, "h": 10}, {"x": 10, "y": 0, "b": 10, "h": 10}))
        self.assertIsNone(wc.schnitt({"x": 0, "y": 0, "b": 10, "h": 10}, {"x": 500, "y": 500, "b": 10, "h": 10}))


class WindowRectTests(unittest.TestCase):
    def _gui(self, sichtbar=True, minimiert=False, existiert=True, rect=(10, 20, 410, 320)):
        gui = mock.MagicMock()
        gui.IsWindow.return_value = existiert
        gui.IsWindowVisible.return_value = sichtbar
        gui.IsIconic.return_value = minimiert
        gui.GetWindowRect.return_value = rect
        return gui

    def _rect(self, gui, rahmen=None):
        with mock.patch.object(wc, "_win32", return_value=(gui, None, None)), \
             mock.patch.object(wc, "_dpi_aware"), mock.patch.object(wc, "_sichtbarer_rahmen", return_value=rahmen):
            return wc.window_rect(42)

    def test_sichtbares_fenster(self):
        self.assertEqual(self._rect(self._gui()), {"x": 10, "y": 20, "b": 400, "h": 300})

    def test_sichtbarer_rahmen_hat_vorrang_vor_dem_unsichtbaren_rand(self):
        self.assertEqual(self._rect(self._gui(), rahmen=(17, 20, 403, 313)), {"x": 17, "y": 20, "b": 386, "h": 293})

    def test_minimiert_unsichtbar_oder_weg_ergibt_nichts(self):
        self.assertIsNone(self._rect(self._gui(minimiert=True)))
        self.assertIsNone(self._rect(self._gui(sichtbar=False)))
        self.assertIsNone(self._rect(self._gui(existiert=False)))

    def test_kaputte_werte_ergeben_nichts(self):
        self.assertIsNone(self._rect(self._gui(rect=(10, 10, 10, 10))))  # Größe 0
        self.assertIsNone(self._rect(self._gui(rect=(0, 0, 10**9, 10))))  # über der Obergrenze
        gui = self._gui()
        gui.GetWindowRect.side_effect = OSError("weg")
        self.assertIsNone(self._rect(gui))

    def test_ohne_fenster(self):
        self.assertIsNone(wc.window_rect(0))
        self.assertIsNone(wc.window_rect(None))


class MarkierungTests(unittest.TestCase):
    def test_enthaelt_nur_ganze_zahlen_in_festen_schluesseln(self):
        a, b = _attrappen()
        with a, b:
            m = host._markierung(77)
        self.assertEqual(set(m), {"markierung"})
        self.assertEqual(set(m["markierung"]), {"fenster", "monitor"})
        self.assertTrue(_nur_zahlen(m["markierung"]))
        self.assertEqual(m["markierung"]["fenster"], FENSTER)

    def test_klick_hat_punkt_nur_im_monitor(self):
        a, b = _attrappen()
        with a, b:
            innen = host._markierung(77, (500, 400))["markierung"]
        self.assertEqual(innen["punkt"], {"x": 500, "y": 400})
        with mock.patch.object(host.wc, "window_rect", return_value={"x": 0, "y": 0, "b": 300, "h": 300}), \
             mock.patch.object(host.wc, "monitor_rect", return_value={"x": 0, "y": 0, "b": 300, "h": 300}):
            # Klick außerhalb des Monitors: kein Punkt, das Fenster bleibt auf den Monitor zugeschnitten
            aussen = host._markierung(77, (500, 400))["markierung"]
        self.assertNotIn("punkt", aussen)
        self.assertEqual(aussen["fenster"], {"x": 0, "y": 0, "b": 300, "h": 300})

    def test_fenster_wird_auf_den_monitor_zugeschnitten(self):
        gross = {"x": -50, "y": -50, "b": 3000, "h": 2000}
        a, b = _attrappen(fenster=gross)
        with a, b:
            m = host._markierung(77)["markierung"]
        self.assertEqual(m["fenster"], MONITOR)

    def test_keine_markierung_ohne_fenster_oder_monitor(self):
        for fenster, monitor in [(None, MONITOR), (FENSTER, None)]:
            a, b = _attrappen(fenster=fenster, monitor=monitor)
            with a, b:
                self.assertEqual(host._markierung(77), {})
        self.assertEqual(host._markierung(None), {})
        self.assertEqual(host._markierung(0), {})

    def test_jede_stoerung_ergibt_nichts(self):
        with mock.patch.object(host.wc, "window_rect", side_effect=RuntimeError("kaputt")):
            self.assertEqual(host._markierung(77), {})
        with mock.patch.object(host.wc, "window_rect", return_value=dict(FENSTER)), \
             mock.patch.object(host.wc, "monitor_rect", side_effect=OSError("kaputt")):
            self.assertEqual(host._markierung(77), {})

    def test_nie_titel_programm_oder_text_im_ergebnis(self):
        a, b = _attrappen()
        with a, b:
            m = host._markierung(77, (500, 400))
        text = json.dumps(m)
        for verboten in ("titel", "programm", "notepad", "Kunde", "text"):
            self.assertNotIn(verboten, text)


class WerkzeugeLiefernMarkierungTests(unittest.TestCase):
    """Nur gelungene Aktionen tragen eine Markierung; Sperre, offene Freigabe und Ablehnung nie."""

    def test_fenster_fokussieren(self):
        a, b = _attrappen()
        with a, b, mock.patch.object(host.wc, "find_window", return_value=dict(NOTIZ)), \
             mock.patch.object(host.wc, "focus", return_value=True):
            r = host.fenster_fokussieren("Notizen")
        self.assertEqual(r["status"], "success")
        self.assertEqual(r["markierung"]["fenster"], FENSTER)
        self.assertNotIn("geheimer Kundenname", json.dumps(r["markierung"]))

    def test_fokus_verweigert_hat_keine_markierung(self):
        a, b = _attrappen()
        with a, b, mock.patch.object(host.wc, "find_window", return_value=dict(NOTIZ)), \
             mock.patch.object(host.wc, "focus", return_value=False):
            r = host.fenster_fokussieren("Notizen")
        self.assertEqual(r["status"], "rejected")
        self.assertNotIn("markierung", r)

    def test_gesperrter_bildschirm_hat_keine_markierung(self):
        a, b = _attrappen()
        with a, b, mock.patch.object(host.wc, "is_locked", return_value=True), \
             mock.patch.object(host.wc, "find_window", return_value=dict(NOTIZ)), \
             mock.patch.object(host.wc, "focus", return_value=True):
            r = host.fenster_fokussieren("Notizen")
        self.assertEqual(r["status"], "locked")
        self.assertNotIn("markierung", r)

    def test_offene_freigabe_hat_keine_markierung(self):
        host.PENDING["0123456789abcdef"] = {"art": "powershell", "erstellt": host._jetzt(), "frist": 300, "command": "x"}
        a, b = _attrappen()
        with a, b, mock.patch.object(host.wc, "find_window", return_value=dict(NOTIZ)), \
             mock.patch.object(host.wc, "focus", return_value=True) as fokus:
            r = host.fenster_fokussieren("Notizen")
        self.assertEqual(r["status"], "wartet")
        self.assertNotIn("markierung", r)
        fokus.assert_not_called()

    def test_tippen_und_tasten(self):
        a, b = _attrappen()
        with a, b, mock.patch.object(host.wc, "foreground", return_value=dict(NOTIZ)), \
             mock.patch.object(host.wc, "type_text"), mock.patch.object(host.wc, "press"):
            tippen = host.tastatur_tippen("Hallo")
            tasten = host.tasten_druecken("ctrl+s")
        for r in (tippen, tasten):
            self.assertEqual(r["status"], "success")
            self.assertEqual(r["markierung"]["fenster"], FENSTER)
            self.assertNotIn("punkt", r["markierung"])

    def test_klick_traegt_den_punkt(self):
        a, b = _attrappen()
        with a, b, mock.patch.object(host.wc, "window_at", return_value=dict(NOTIZ)), \
             mock.patch.object(host.wc, "screen_bounds", return_value=(0, 0, 1920, 1080)), \
             mock.patch.object(host.wc, "click") as klick:
            r = host.maus_klicken(300, 200)
        self.assertEqual(r["status"], "success")
        klick.assert_called_once()
        self.assertEqual(r["markierung"]["punkt"], {"x": 300, "y": 200})
        self.assertTrue(_nur_zahlen(r["markierung"]))

    def test_klick_in_mail_fenster_wartet_auf_freigabe_ohne_markierung(self):
        teams = {"hwnd": 5, "titel": "Chat | Microsoft Teams", "programm": "ms-teams.exe"}
        a, b = _attrappen()
        with a, b, mock.patch.object(host.wc, "window_at", return_value=teams), \
             mock.patch.object(host.wc, "screen_bounds", return_value=(0, 0, 1920, 1080)), \
             mock.patch.object(host.wc, "click") as klick:
            r = host.maus_klicken(300, 200)
        self.assertEqual(r["status"], "needs_approval")
        self.assertNotIn("markierung", r)
        klick.assert_not_called()

    def test_freigegebener_klick_traegt_die_markierung(self):
        teams = {"hwnd": 5, "titel": "Chat | Microsoft Teams", "programm": "ms-teams.exe"}
        a, b = _attrappen()
        host.FREIGABE_SCHLUESSEL = "k" * 43
        try:
            with a, b, mock.patch.object(host.wc, "window_at", return_value=teams), \
                 mock.patch.object(host.wc, "screen_bounds", return_value=(0, 0, 1920, 1080)), \
                 mock.patch.object(host.wc, "click"):
                antrag = host.maus_klicken(300, 200)
                r = host.entscheiden(antrag["approval_id"], True, "k" * 43)
        finally:
            host.FREIGABE_SCHLUESSEL = ""
        self.assertEqual(r["status"], "success")
        self.assertEqual(r["markierung"]["punkt"], {"x": 300, "y": 200})

    def test_abgelehnte_freigabe_hat_keine_markierung(self):
        teams = {"hwnd": 5, "titel": "Chat | Microsoft Teams", "programm": "ms-teams.exe"}
        a, b = _attrappen()
        host.FREIGABE_SCHLUESSEL = "k" * 43
        try:
            with a, b, mock.patch.object(host.wc, "window_at", return_value=teams), \
                 mock.patch.object(host.wc, "screen_bounds", return_value=(0, 0, 1920, 1080)):
                antrag = host.maus_klicken(300, 200)
                r = host.entscheiden(antrag["approval_id"], False, "k" * 43)
        finally:
            host.FREIGABE_SCHLUESSEL = ""
        self.assertEqual(r["status"], "rejected")
        self.assertNotIn("markierung", r)

    def test_scrollen(self):
        gui = mock.MagicMock()
        gui.GetWindowRect.return_value = (0, 0, 400, 300)
        a, b = _attrappen()
        with a, b, mock.patch.object(host.wc, "_win32", return_value=(gui, None, None)), \
             mock.patch.object(host.wc, "window_at", return_value=dict(NOTIZ)), \
             mock.patch.object(host.wc, "screen_bounds", return_value=(0, 0, 1920, 1080)), \
             mock.patch.object(host.wc, "scroll"):
            r = host.maus_scrollen("runter", 3)
        self.assertEqual(r["status"], "success")
        self.assertEqual(r["markierung"]["fenster"], FENSTER)


if __name__ == "__main__":
    unittest.main()
