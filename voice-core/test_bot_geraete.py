"""Bürogeräte in der Sitzung (bot.py): Die Werkzeuge aus geraete_werkzeuge.py hängen in jeder Büro-Sitzung, nie im Labor.

Bis 12.11.2026 waren sie gebaut, aber nirgends eingehängt: WAMO sagte "ich habe keine Verbindung zu Fernsehern". Diese Prüfungen lesen
bot.py statisch (die Sitzung braucht Netz und Modelle) und halten die Verdrahtung fest: Aufbau, Bedingung, Klick auf die Karte, Aufräumen.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import geraete_werkzeuge as gw

BOT = Path(__file__).with_name("bot.py")
QUELLE = BOT.read_text(encoding="utf-8")
BAUM = ast.parse(QUELLE)


def _aufrufe(name: str) -> list[ast.Call]:
    return [k for k in ast.walk(BAUM) if isinstance(k, ast.Call) and isinstance(k.func, ast.Name) and k.func.id == name]


def test_bot_importiert_die_werkzeuge_und_den_job_namen():
    importe = {a.name for k in ast.walk(BAUM) if isinstance(k, ast.ImportFrom) and k.module == "geraete_werkzeuge" for a in k.names}
    assert {"Buerogeraete", "JOB"} <= importe


def test_die_werkzeuge_werden_gebaut_und_geladen():
    assert len(_aufrufe("Buerogeraete")) == 1
    assert "await buerogeraete.schemas()" in QUELLE
    assert "buerogeraete.anbinden(senden=send, melden=melde_llm)" in QUELLE


def test_nur_im_buero_und_nie_im_labor():
    zeile = next(z for z in QUELLE.splitlines() if "buerogeraete = Buerogeraete(" in z)
    index = QUELLE.splitlines().index(zeile)
    bedingung = QUELLE.splitlines()[index - 1].strip()
    assert bedingung == "if im_buero and not labor_sitzung:"


def test_fremdinhalt_und_taint_kommen_aus_dem_zug():
    assert "fremdinhalt=turn.taint" in QUELLE
    assert "ist_fremd=lambda: turn.tainted" in QUELLE


def test_die_endpruefung_der_laborsitzung_steht_nach_dem_letzten_block_der_werkzeuge_einhaengt():
    """Die Prüfung gilt für das Endergebnis der Werkzeugliste. Stand sie vor dem Bürogeräte-Block (Befund vom 12.11.2026), schützte nur noch
    dessen Bedingung "not labor_sitzung". Jetzt scheitert die Sitzung auch dann, wenn dort oder davor später jemand ein Werkzeug einhängt."""
    pruefung = QUELLE.index("labor_sitzung.werkzeuge_pruefen(")
    assert QUELLE.count("labor_sitzung.werkzeuge_pruefen(") == 1
    letzte_ergaenzung = max(m.start() for m in re.finditer(r"standard_tools \+= ", QUELLE))
    assert letzte_ergaenzung < pruefung
    assert QUELLE.index("buerogeraete = Buerogeraete(") < pruefung
    kopf = QUELLE.splitlines()[QUELLE[:pruefung].count("\n") - 3 : QUELLE[:pruefung].count("\n")]
    assert any(z.strip() == "if labor_sitzung:" for z in kopf)  # die Prüfung läuft nur in einer Laborsitzung


def test_klick_auf_die_karte_geht_an_entscheiden_und_nur_mit_werkzeugen():
    assert 'data.get("job") == GERAETE_JOB' in QUELLE
    assert "await buerogeraete.entscheiden(data)" in QUELLE
    zweig = QUELLE.index('data.get("job") == GERAETE_JOB')
    assert "buerogeraete is None" in QUELLE[zweig : zweig + 400]


def test_der_klick_zweig_steht_vor_dem_auffangzweig_der_entwicklung():
    assert QUELLE.index('data.get("job") == GERAETE_JOB') < QUELLE.index("await dev.on_client_message(msg.type, data)")


def test_offene_karten_werden_beim_ende_abgelehnt_und_halten_den_ja_waechter_zurueck():
    assert "await asyncio.wait_for(buerogeraete.schliessen(), CLEANUP_DB_TIMEOUT_SECS)" in QUELLE
    assert "buerogeraete is not None and buerogeraete._offen" in QUELLE


def test_die_variable_ist_vor_dem_ja_waechter_zustand_gebunden():
    """freigabe_offen liest buerogeraete; ein Aufruf vor der Bindung wäre ein NameError, den except AttributeError nicht fängt."""
    assert QUELLE.count("buerogeraete: Buerogeraete | None = None") == 1
    assert QUELLE.index("buerogeraete: Buerogeraete | None = None") < QUELLE.index("def freigabe_offen")


def test_job_name_stimmt_mit_der_oberflaeche_ueberein():
    ui = Path(__file__).resolve().parent.parent / "ui" / "src" / "lib" / "laborKarte.ts"
    assert gw.JOB == "geraet"
    assert 'GERAET_JOB = "geraet"' in ui.read_text(encoding="utf-8")
