"""Tests der Caddy-Sperre für den Passwortwechsel (infra/sokrates2/wamo-caddy/Caddyfile).

Befund des Token-Tests vom 12.11.2026: Ein Konto mit aal2-Token ändert sein Passwort mit PUT /auth/v1/user, auch mit der Einstellung
GOTRUE_SECURITY_UPDATE_PASSWORD_REQUIRE_REAUTHENTICATION, weil GoTrue die erneute Anmeldung nur bei Sitzungen über 24 Stunden verlangt.
Ein Laborbesucher könnte so den Platz für den Nächsten sperren. WAMO ändert Passwörter nie über diesen Weg (Konten legt nur der Admin an),
deshalb weist Caddy auf 55443 jedes PUT und PATCH auf einen Pfad .../auth/.../user ab. Der Envoy davor ist von außen nicht erreichbar
(Firewallkette WAMO-FW-HTTP: Loopback und Caddy-Netz).

Statische Prüfung ohne Caddy: Das Muster des Pfad-Matchers wird aus dem Caddyfile gelesen und mit Python-re ausprobiert (einfaches Muster,
in RE2 und re gleich). Die echte Probe ist der Token-Test über 55443 (labor_tokentest.py --basis https://192.168.178.136:55443).

Ausführen:  uv run python -m pytest -q test_caddy_passwortwechsel.py   (in voice-core/)
"""

import re
import unittest
from pathlib import Path

CADDYFILE = Path(__file__).resolve().parent.parent / "infra" / "sokrates2" / "wamo-caddy" / "Caddyfile"


def site_55443() -> str:
    """Der Block der Site auf 55443 (bis zur schließenden Klammer in Spalte 0)."""
    text = CADDYFILE.read_text(encoding="utf-8")
    m = re.search(r"^https://192\.168\.178\.136:55443[^\n]*\{\n(.*?)^\}", text, re.S | re.M)
    assert m, "Site auf 55443 nicht gefunden"
    return m.group(1)


def matcher_block(site: str) -> str:
    m = re.search(r"@passwortwechsel \{\n(.*?)\n\t\}", site, re.S)
    assert m, "Matcher @passwortwechsel fehlt"
    return m.group(1)


def muster() -> re.Pattern[str]:
    zeile = next(z for z in matcher_block(site_55443()).splitlines() if z.strip().startswith("path_regexp"))
    return re.compile(zeile.strip().split(None, 1)[1])


class CaddyPasswortwechselTest(unittest.TestCase):
    def test_methoden_put_und_patch(self):
        zeilen = [z.strip() for z in matcher_block(site_55443()).splitlines()]
        self.assertIn("method PUT PATCH", zeilen)

    def test_abgewiesen_wird_mit_403_und_ohne_weiterleitung(self):
        site = site_55443()
        m = re.search(r"handle @passwortwechsel \{\n(.*?)\n\t\}", site, re.S)
        self.assertIsNotNone(m, "handle @passwortwechsel fehlt")
        self.assertIn("respond", m.group(1))
        self.assertIn("403", m.group(1))
        self.assertNotIn("reverse_proxy", m.group(1))

    def test_die_weiterleitung_steht_im_uebrigen_handle(self):
        """Ohne eigenes handle wäre die Reihenfolge von respond und reverse_proxy Sache der Direktivenordnung."""
        site = site_55443()
        rest = re.sub(r"handle @passwortwechsel \{\n.*?\n\t\}\n", "", site, flags=re.S)
        m = re.search(r"\thandle \{\n(.*)\n\t\}", rest, re.S)
        self.assertIsNotNone(m, "übriges handle fehlt")
        self.assertIn("reverse_proxy host.docker.internal:55321", m.group(1))

    def test_gesperrte_pfade(self):
        p = muster()
        for pfad in ("/auth/v1/user", "/auth/v1/user/", "/AUTH/V1/USER", "/auth/v1/./user", "/auth/v1/admin/users/../../user",
                     "/auth/v1//user", "/x/auth/v1/user", "/auth/user"):
            with self.subTest(pfad=pfad):
                self.assertIsNotNone(p.search(pfad))

    def test_freie_pfade(self):
        p = muster()
        for pfad in ("/auth/v1/factors", "/auth/v1/admin/users", "/auth/v1/admin/users/5b1c0d3e-0000-4000-8000-000000000000",
                     "/auth/v1/token", "/auth/v1/userinfo", "/auth/v1/user_x", "/rest/v1/user", "/storage/v1/bucket", "/auth/v1/health"):
            with self.subTest(pfad=pfad):
                self.assertIsNone(p.search(pfad))

    def test_nur_die_site_auf_55443_ist_betroffen(self):
        text = CADDYFILE.read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"^\t@passwortwechsel \{$", text, re.M)), 1)  # genau ein Matcher, in einer Site


if __name__ == "__main__":
    unittest.main()
