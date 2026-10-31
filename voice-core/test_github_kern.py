"""Kern-Seite der GitHub-Werkzeuge des Hosts: Freigabekarte, Werkzeugliste, Zeitgrenze im Tunnel, Sperr-Erkennung."""

from __future__ import annotations

import json

import activity
import pc_host
import pc_tunnel
from pc_freigaben import AKTION_TEXT

AID = "0123456789abcdef"
ANFRAGE = json.dumps({
    "status": "needs_approval", "approval_id": AID, "aktion": "github", "grund": "GitHub-Repository anlegen",
    "detail": "Repository: Wamocon/radar\nSichtbarkeit: privat", "ziel": "GitHub, Organisation Wamocon", "gueltig_s": 300,
    "summary": "Das legt ein neues Repository bei GitHub an.",
})


def test_anlegen_wird_zur_freigabekarte_mit_name_organisation_und_sichtbarkeit():
    a = activity.freigabe_anfrage("github_repo_anlegen", {"name": "anderer-name-vom-modell"}, ANFRAGE, "call-1")
    assert a is not None and a["aktion"] == "github"
    assert "Wamocon/radar" in a["detail"] and "privat" in a["detail"]  # was der Host ausführt, nicht was das Modell sagt
    assert activity._status(ANFRAGE) == "wartet"
    assert "github" in AKTION_TEXT


def test_nur_das_anlegen_liefert_eine_karte():
    assert activity.freigabe_anfrage("git_klonen", {}, ANFRAGE) is None
    assert activity.freigabe_anfrage("websuche", {}, ANFRAGE) is None


def test_werkzeuge_sind_fuers_modell_sichtbar_und_die_zeitleiste_zeigt_das_ziel():
    assert {"git_klonen", "github_repo_anlegen"} <= set(pc_host.LLM_TOOLS)
    assert activity._detail("git_klonen", {"repo": "Wamocon/radar"}) == "Wamocon/radar"
    assert activity._detail("github_repo_anlegen", {"name": "radar", "beschreibung": "geheim"}) == "radar"


def test_klonen_hat_im_tunnel_mehr_zeit_als_der_standard():
    assert pc_tunnel.LANGE_WERKZEUGE["git_klonen"] > pc_tunnel.CALL_TIMEOUT_SECS
    assert pc_tunnel.LANGE_WERKZEUGE["git_klonen"] >= 90 + 10 + 5  # Klon, Beenden des Prozessbaums, letztes Lesen


def test_erfolg_beim_klonen_beweist_entsperrt():
    ok = json.dumps({"status": "success", "summary": "geklont"})
    assert pc_host.sperre_aus_ergebnis("git_klonen", ok) is False
    assert pc_host.sperre_aus_ergebnis("git_klonen", json.dumps({"status": "locked"})) is True
    assert pc_host.sperre_aus_ergebnis("github_repo_anlegen", ANFRAGE) is None
