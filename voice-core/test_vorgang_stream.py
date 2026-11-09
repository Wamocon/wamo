"""Tests der Auswertung des Streams von Claude Code für Vorgänge (vorgang_stream.py): Aufgabenliste aus TaskCreate und
TaskUpdate (Karte nach Task-ID), TodoWrite (ersetzt alles), Rückfall auf die Schritte des Auftrags, Freigaben, Kriterien,
Ende und der Test-Treiber, der einen aufgezeichneten Stream aus einer Textdatei einspielt. Kein echtes Claude."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import vorgang_stream as vs
from vorgaenge import Schritt, Vorgaenge

STREAM_DATEI = Path(__file__).resolve().parent / "testdaten" / "vorgang_stream.jsonl"


def assistent(*bloecke: dict, eltern: str | None = None) -> dict:
    return {"type": "assistant", "parent_tool_use_id": eltern, "message": {"content": list(bloecke)}}


def aufruf(name: str, eingabe: dict, kennung: str = "toolu_1") -> dict:
    return {"type": "tool_use", "id": kennung, "name": name, "input": eingabe}


def ergebnis(kennung: str, text: str, fehler: bool = False) -> dict:
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": kennung, "content": text,
                                                     "is_error": fehler}]}}


def status_liste(liste: vs.TaskListe) -> list[tuple[str, str]]:
    return [(s.titel, s.status) for s in liste.schritte()]


# ---------------------------------------------------------------------------------------------- Aufgabenliste
def test_task_create_legt_schritte_in_der_reihenfolge_an():
    t = vs.TaskListe()
    assert t.leer()
    assert t.ereignis(assistent(aufruf("TaskCreate", {"subject": "Tests schreiben", "description": "x"}, "a")))
    assert t.ereignis(assistent(aufruf("TaskCreate", {"subject": "Umsetzen"}, "b")))
    assert status_liste(t) == [("Tests schreiben", "offen"), ("Umsetzen", "offen")]
    assert not t.leer()


def test_task_update_fuehrt_die_karte_nach_task_id_und_setzt_den_status():
    t = vs.TaskListe()
    t.ereignis(assistent(aufruf("TaskCreate", {"subject": "Eins"}, "a")))
    t.ereignis(assistent(aufruf("TaskCreate", {"subject": "Zwei"}, "b")))
    t.ereignis(assistent(aufruf("TaskUpdate", {"taskId": "2", "status": "in_progress", "activeForm": "Arbeitet an Zwei"},
                                "c")))
    assert status_liste(t) == [("Eins", "offen"), ("Zwei", "laeuft")]
    assert t.schritte()[1].detail == "Arbeitet an Zwei"
    t.ereignis(assistent(aufruf("TaskUpdate", {"taskId": 2, "status": "completed"}, "d")))  # Zahl statt Text
    t.ereignis(assistent(aufruf("TaskUpdate", {"taskId": "1", "status": "in_progress"}, "e")))
    assert status_liste(t) == [("Eins", "laeuft"), ("Zwei", "fertig")]
    assert t.schritte()[1].fortschritt == 1.0 and t.schritte()[1].detail is None  # Hinweis nur beim laufenden Schritt
    t.ereignis(assistent(aufruf("TaskUpdate", {"taskId": "1", "status": "pending"}, "f")))
    assert t.schritte()[0].status == "offen"


def test_die_task_id_aus_dem_ergebnis_gilt_statt_der_geschaetzten():
    t = vs.TaskListe()
    t.ereignis(assistent(aufruf("TaskCreate", {"subject": "Erste"}, "a")))
    t.ereignis(ergebnis("a", "Task #7 created successfully: Erste"))  # das Werkzeug vergab die 7
    t.ereignis(assistent(aufruf("TaskCreate", {"subject": "Zweite"}, "b")))
    t.ereignis(ergebnis("b", "Task #8 created successfully: Zweite"))
    t.ereignis(assistent(aufruf("TaskUpdate", {"taskId": "7", "status": "completed"}, "c")))
    t.ereignis(assistent(aufruf("TaskUpdate", {"taskId": "8", "status": "in_progress"}, "d")))
    assert status_liste(t) == [("Erste", "fertig"), ("Zweite", "laeuft")]


def test_unbekannte_task_id_wird_ignoriert_gestrichene_aufgaben_fallen_weg():
    t = vs.TaskListe()
    t.ereignis(assistent(aufruf("TaskCreate", {"subject": "A"}, "a")))
    assert not t.ereignis(assistent(aufruf("TaskUpdate", {"taskId": "99", "status": "completed"}, "b")))
    assert status_liste(t) == [("A", "offen")]
    t.ereignis(assistent(aufruf("TaskUpdate", {"taskId": "1", "status": "deleted"}, "c")))
    assert t.leer()
    t.ereignis(assistent(aufruf("TaskCreate", {"subject": "B"}, "d")))
    t.ereignis(assistent(aufruf("TaskUpdate", {"taskId": "2", "subject": "B umbenannt"}, "e")))  # IDs werden nie wiederverwendet
    assert [s.titel for s in t.schritte()] == ["B umbenannt"]


def test_fehlgeschlagenes_task_create_nimmt_den_schritt_wieder_weg():
    t = vs.TaskListe()
    t.ereignis(assistent(aufruf("TaskCreate", {"subject": "Geht nicht"}, "a")))
    t.ereignis(ergebnis("a", "Fehler: nicht erlaubt", fehler=True))
    assert t.leer()


def test_todowrite_ersetzt_die_ganze_liste_jedes_mal():
    t = vs.TaskListe()
    t.ereignis(assistent(aufruf("TodoWrite", {"todos": [
        {"content": "Planen", "status": "completed", "activeForm": "Plant"},
        {"content": "Bauen", "status": "in_progress", "activeForm": "Baut"},
        {"content": "Prüfen", "status": "pending", "activeForm": "Prüft"}]}, "a")))
    assert status_liste(t) == [("Planen", "fertig"), ("Bauen", "laeuft"), ("Prüfen", "offen")]
    assert t.schritte()[1].detail == "Baut"
    t.ereignis(assistent(aufruf("TodoWrite", {"todos": [{"content": "Nur noch Prüfen", "status": "in_progress"}]}, "b")))
    assert status_liste(t) == [("Nur noch Prüfen", "laeuft")]


def test_ereignisse_von_unteragenten_veraendern_die_liste_nicht():
    t = vs.TaskListe()
    t.ereignis(assistent(aufruf("TaskCreate", {"subject": "Haupt"}, "a")))
    assert not t.ereignis(assistent(aufruf("TodoWrite", {"todos": [{"content": "Fremd", "status": "pending"}]}, "b"),
                                    eltern="toolu_agent"))
    assert [s.titel for s in t.schritte()] == ["Haupt"]


@pytest.mark.parametrize("ev", [
    None, 5, "text", [], {}, {"type": "assistant"}, {"type": "assistant", "message": "x"},
    {"type": "assistant", "message": {"content": "text"}}, {"type": "assistant", "message": {"content": [5, None]}},
    assistent(aufruf("TaskCreate", {})), assistent(aufruf("TaskCreate", "kein dict")),
    assistent(aufruf("TaskUpdate", {"taskId": None, "status": "completed"})),
    assistent(aufruf("TaskUpdate", {"taskId": {"a": 1}, "status": ["x"]})),
    assistent(aufruf("TodoWrite", {"todos": "nein"})), assistent(aufruf("TodoWrite", {"todos": [5, None, {"x": 1}]})),
    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": 5, "content": {"a": 1}}]}},
    {"type": "user", "message": {"content": "text"}},
])
def test_kaputte_ereignisse_werfen_nie(ev):
    t = vs.TaskListe()
    t.ereignis(ev)
    assert t.schritte() == []


def test_texte_ohne_steuerzeichen_und_schluessel_und_begrenzte_menge():
    t = vs.TaskListe()
    geheim = "sk-ant-api03-" + "B" * 40
    t.ereignis(assistent(aufruf("TaskCreate", {"subject": "Titel\x00\n" + geheim + "x" * 400}, "a")))
    titel = t.schritte()[0].titel
    assert "\x00" not in titel and "\n" not in titel and geheim not in titel
    for i in range(vs.MAX_AUFGABEN + 50):
        t.ereignis(assistent(aufruf("TaskCreate", {"subject": f"s{i}"}, f"k{i}")))
    assert len(t.schritte()) <= vs.MAX_AUFGABEN


def test_zuruecksetzen_leert_alles():
    t = vs.TaskListe()
    t.ereignis(assistent(aufruf("TaskCreate", {"subject": "A"}, "a")))
    t.zuruecksetzen()
    assert t.leer()
    t.ereignis(assistent(aufruf("TaskCreate", {"subject": "Neu"}, "b")))
    t.ereignis(assistent(aufruf("TaskUpdate", {"taskId": "1", "status": "completed"}, "c")))
    assert status_liste(t) == [("Neu", "fertig")]  # die Nummerierung beginnt wieder bei 1


# ---------------------------------------------------------------------------------------------- Entwicklungsauftrag
class Sammler:
    def __init__(self):
        self.gesendet: list[dict] = []
        self.gespeichert: list[dict] = []

    async def senden(self, n: dict) -> None:
        self.gesendet.append(n)

    async def speichern(self, d: dict) -> None:
        self.gespeichert.append(d)


def umgebung(takt_s: float = 0.0):
    s = Sammler()
    reg = Vorgaenge(s.senden, speichern=s.speichern, takt_s=takt_s)
    f = vs.EntwicklungsVorgang(reg, "Umsetzung: rezepte-app", "abc123")
    return s, reg, f


def test_vorgang_ist_geplant_mit_canvas_und_den_drei_kriterien():
    _, _, f = umgebung()
    v = f.vorgang
    assert v.status == "geplant" and v.art == "entwicklung" and v.titel == "Umsetzung: rezepte-app"
    assert v.canvas_id == "dev-abc123"  # das Canvas des Auftrags wächst während des Laufs (dev_agent._canvas_item)
    assert [k.text for k in v.kriterien] == [vs.KRIT_SELBSTTEST, vs.KRIT_LAUF, vs.KRIT_FREIGABEN]
    assert all(k.erfuellt is None for k in v.kriterien)
    assert v.offenes_ende is True  # bis eine Aufgabenliste da ist


def test_aufgabenliste_ersetzt_den_rueckfall_und_gibt_eine_feste_schrittzahl():
    _, reg, f = umgebung()
    f.starten(fortsetzen=False)
    assert f.vorgang.status == "laeuft"
    f.ereignis({"type": "system", "subtype": "init"}, ["Start in rezepte-app", "Befehl: `ls`"])
    assert f.vorgang.offenes_ende is True and len(f.vorgang.arbeit) == 2  # Rückfall: die Schritte des Auftrags
    f.ereignis(assistent(aufruf("TaskCreate", {"subject": "Tests"}, "a")), ["x"])
    f.ereignis(assistent(aufruf("TaskCreate", {"subject": "Code"}, "b")), ["x"])
    f.ereignis(assistent(aufruf("TaskUpdate", {"taskId": "1", "status": "completed"}, "c")), ["x"])
    v = f.vorgang
    assert v.offenes_ende is False
    assert [(s.titel, s.status) for s in v.arbeit] == [("Tests", "fertig"), ("Code", "offen")]
    assert v.gesamt() == 0.5


def test_rueckfall_zeigt_die_schritte_des_auftrags_und_taeuscht_keine_restzeit_vor():
    _, _, f = umgebung()
    f.starten(fortsetzen=False)
    schritte = [f"Befehl: `cmd {i}`" for i in range(12)]
    f.ereignis({"type": "assistant", "message": {"content": []}}, schritte)
    v = f.vorgang
    assert [s.titel for s in v.arbeit] == schritte
    assert [s.status for s in v.arbeit[:-1]] == ["fertig"] * 11 and v.arbeit[-1].status == "laeuft"
    assert v.gesamt() <= 0.9 and v.restzeit_s(10_000.0) is None


def test_rueckfall_nimmt_hoechstens_die_letzten_schritte():
    _, _, f = umgebung()
    f.starten(fortsetzen=False)
    f.ereignis({"type": "assistant", "message": {"content": []}}, [f"s{i}" for i in range(100)])
    assert len(f.vorgang.arbeit) == vs.MAX_SCHRITTE_RUECKFALL and f.vorgang.arbeit[-1].titel == "s99"


def test_selbsttest_und_wamo_ereignisse_setzen_das_kriterium():
    _, _, f = umgebung()
    f.starten(fortsetzen=False)
    f.ereignis({"type": "wamo", "ereignis": "selbsttest_ok"}, [])
    assert f.vorgang.kriterien[0].erfuellt is True
    _, _, g = umgebung()
    g.ereignis({"type": "wamo", "ereignis": "selbsttest_fehlgeschlagen"}, [])
    assert g.vorgang.kriterien[0].erfuellt is False


def test_lauf_ohne_freigabe_endet_fertig_mit_erfuellten_kriterien():
    _, _, f = umgebung()
    f.starten(fortsetzen=False)
    f.ereignis(assistent(aufruf("TaskCreate", {"subject": "A"}, "a")), [])
    f.lauf_endet_vorlaeufig()
    assert f.vorgang.status == "pruefen"
    f.lauf_endet("fertig", {})
    v = f.vorgang
    assert v.status == "fertig" and v.beendet_ms is not None
    assert [(k.text, k.erfuellt) for k in v.kriterien][1:] == [(vs.KRIT_LAUF, True), (vs.KRIT_FREIGABEN, True)]
    assert v.arbeit[0].status == "uebersprungen" or v.arbeit[0].status == "fertig"
    assert v.gesamt() == 1.0


@pytest.mark.parametrize("text,endstatus", [
    ("fertig", "fertig"), ("fertig, Freigaben nicht abrufbar", "fertig"),
    ("abgebrochen (Zeitlimit)", "abgebrochen"), ("abgebrochen", "abgebrochen"),
    ("beendet (Code 1)", "fehler"), ("fehlgeschlagen", "fehler"), ("nicht gestartet (Auftrag abgewiesen)", "fehler"),
    ("nicht gestartet (kein Claude-Zugang: Max-Token)", "fehler"), ("läuft", "fehler"),
])
def test_endstatus_aus_dem_status_des_auftrags(text, endstatus):
    assert vs.endstatus(text) == endstatus
    _, _, f = umgebung()
    f.starten(fortsetzen=False)
    f.lauf_endet(text, {})
    assert f.vorgang.status == endstatus
    assert f.vorgang.kriterien[1].erfuellt is (endstatus == "fertig")


def anfrage(grund: str, kennung: str) -> dict:
    return {"id": kennung, "grund": grund, "tool": "Bash", "input": {"command": "git push"}}


def test_offene_freigaben_setzen_wartet_und_tragen_die_kennung_der_karte():
    _, _, f = umgebung()
    f.starten(fortsetzen=False)
    f.ereignis(assistent(aufruf("TaskCreate", {"subject": "Bauen"}, "a")), [])
    f.lauf_endet("fertig", {"f1": anfrage("git push", "f1"), "f2": anfrage("npm publish", "f2")})
    v = f.vorgang
    assert v.status == "wartet_freigabe" and v.beendet_ms is None
    assert v.freigabe_id == "f1"  # die Karte der Oberfläche ist die vorhandene
    assert [(s.titel, s.status) for s in v.freigaben] == [("Freigabe: git push", "wartet_freigabe"),
                                                         ("Freigabe: npm publish", "wartet_freigabe")]
    assert v.kriterien[2].erfuellt is False and "2" in (v.kriterien[2].hinweis or "")
    assert v.restzeit_s(1e9) is None


def test_freigabe_erteilt_setzt_den_lauf_fort_abgelehnt_beendet_ohne_weiteren_lauf():
    _, _, f = umgebung()
    f.starten(fortsetzen=False)
    f.lauf_endet("fertig", {"f1": anfrage("git push", "f1"), "f2": anfrage("npm publish", "f2")})
    f.freigabe_entschieden(anfrage("git push", "f1"), True, {"f2": anfrage("npm publish", "f2")})
    v = f.vorgang
    assert v.status == "wartet_freigabe" and v.freigabe_id == "f2"
    assert [(s.titel, s.status) for s in v.freigaben] == [("Freigabe: git push", "fertig"),
                                                         ("Freigabe: npm publish", "wartet_freigabe")]
    f.freigabe_entschieden(anfrage("npm publish", "f2"), True, {})
    assert f.vorgang.status == "laeuft" and f.vorgang.freigabe_id is None  # der Lauf geht weiter

    _, _, g = umgebung()
    g.starten(fortsetzen=False)
    g.lauf_endet("fertig", {"f1": anfrage("git push", "f1")})
    g.freigabe_entschieden(anfrage("git push", "f1"), False, {}, lauf_folgt=False)
    assert g.vorgang.status == "abgebrochen"
    assert g.vorgang.freigaben[0].status == "uebersprungen"
    assert g.vorgang.kriterien[2].erfuellt is False


def test_neuer_lauf_ohne_fortsetzen_beginnt_die_aufgabenliste_neu():
    _, _, f = umgebung()
    f.starten(fortsetzen=False)
    f.ereignis(assistent(aufruf("TaskCreate", {"subject": "Alt"}, "a")), [])
    f.lauf_endet("wartet auf Freigabe (mehr Zugänge)", {"z": anfrage("Zugänge", "z")})
    f.freigabe_entschieden(anfrage("Zugänge", "z"), True, {})
    f.starten(fortsetzen=False)  # der Auftrag startet neu (dev_agent: Freigabe für mehr Zugänge)
    assert f.vorgang.arbeit == [] and f.vorgang.status == "laeuft"
    f.starten(fortsetzen=True)  # eine Fortsetzung behält die Liste
    f.ereignis(assistent(aufruf("TaskCreate", {"subject": "Neu"}, "b")), [])
    f.starten(fortsetzen=True)
    assert [s.titel for s in f.vorgang.arbeit] == ["Neu"]


def test_ereignis_wirft_nie_auch_bei_unsinn():
    _, _, f = umgebung()
    f.starten(fortsetzen=False)
    f.ereignis(None, None)  # type: ignore[arg-type]
    f.ereignis({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": 5, "input": 7}]}}, [5, None])  # type: ignore[list-item]
    assert f.vorgang.status == "laeuft"


# ---------------------------------------------------------------------------------------------- Test-Treiber
async def test_treiber_spielt_den_aufgezeichneten_stream_aus_der_textdatei_ein():
    assert STREAM_DATEI.is_file()
    nachrichten = await vs.einspielen(STREAM_DATEI)
    assert nachrichten and all(m["type"] == "vorgang" for m in nachrichten)
    letzte = nachrichten[-1]["vorgang"]
    assert letzte["status"] == "fertig" and letzte["gesamt"] == 1.0 and letzte["art"] == "entwicklung"
    assert [s["status"] for s in letzte["schritte"]] == ["fertig"] * len(letzte["schritte"])
    assert [s["titel"] for s in letzte["schritte"]][:2] == ["Tests schreiben", "Funktion umsetzen"]
    stati = [m["vorgang"]["status"] for m in nachrichten]
    assert stati[0] == "geplant" and "laeuft" in stati and "pruefen" in stati
    # der Fortschritt steigt nie zurück
    gesamt = [m["vorgang"]["gesamt"] for m in nachrichten if m["vorgang"]["status"] in ("laeuft", "pruefen")]
    assert gesamt == sorted(gesamt) and 0.0 < gesamt[-1] <= 1.0
    # Kriterien: Selbsttest, Lauf, Freigaben
    assert [k["erfuellt"] for k in letzte["kriterien"]] == [True, True, True]


async def test_treiber_mit_freigabe_endet_auf_wartet_freigabe(tmp_path):
    zeilen = STREAM_DATEI.read_text(encoding="utf-8").splitlines()
    datei = tmp_path / "stream.jsonl"
    datei.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    nachrichten = await vs.einspielen(datei, offene_freigaben={"f1": anfrage("git push", "f1")})
    letzte = nachrichten[-1]["vorgang"]
    assert letzte["status"] == "wartet_freigabe" and letzte["freigabe_id"] == "f1"


async def test_treiber_ueberspringt_kaputte_zeilen(tmp_path):
    datei = tmp_path / "s.jsonl"
    datei.write_text('kein json\n[1]\n{"type":"assistant","message":{"content":[]}}\n\n', encoding="utf-8")
    nachrichten = await vs.einspielen(datei)
    assert nachrichten[-1]["vorgang"]["status"] == "fertig"


def test_treiber_hat_eine_kommandozeile(tmp_path, capsys):
    datei = tmp_path / "s.jsonl"
    datei.write_text(STREAM_DATEI.read_text(encoding="utf-8"), encoding="utf-8")
    assert vs.main([str(datei)]) == 0
    zeilen = [json.loads(z) for z in capsys.readouterr().out.splitlines() if z.strip()]
    assert zeilen and zeilen[-1]["vorgang"]["status"] == "fertig"
    assert vs.main([str(tmp_path / "gibt-es-nicht.jsonl")]) == 2
    assert vs.main([]) == 2


def test_schritt_klasse_passt_zur_registry():
    assert Schritt(1, "x").status == "offen"
    assert asyncio  # Modul wird für die Tests oben gebraucht
