"""Der Boss meldet seine Aufgaben als Vorgänge (boss.py mit vorgaenge.py): Schritte, Runde bei Rückfall auf ein anderes
Modell, Prüfkriterien, Ergebnis auf dem Canvas, Fehler und Abbruch. Ohne Registry arbeitet der Boss wie bisher
(test_boss.py). Kein Test spricht mit einem Gateway oder Anthropic."""

from __future__ import annotations

import asyncio

import boss
from llm_anbieter import LLMFehler
from test_boss import Umgebung, _boss_ohne_netz, cz, hz  # noqa: F401 (Fixtures der Boss-Tests)
from vorgaenge import Vorgaenge


class Aufnahme:
    def __init__(self):
        self.gesendet: list[dict] = []

    async def senden(self, n: dict) -> None:
        self.gesendet.append(n)

    def stati(self) -> list[str]:
        return [m["vorgang"]["status"] for m in self.gesendet]


def mit_vorgaengen(u: Umgebung) -> tuple[Aufnahme, Vorgaenge]:
    """Hängt eine Registry an den Boss der Umgebung; das Canvas bekommt eine Kennung wie in bot.py (canvas_legen)."""
    aufnahme = Aufnahme()
    reg = Vorgaenge(aufnahme.senden, takt_s=0.0)
    u.boss._vorgaenge = reg
    alt = u.boss._canvas

    async def canvas(item):
        await alt(item)
        item["id"] = "11111111-2222-4333-8444-555555555555"

    u.boss._canvas = canvas
    return aufnahme, reg


def vorgang_von(reg: Vorgaenge):
    (v,) = reg.alle()
    return v


async def test_aufgabe_wird_ein_vorgang_mit_schritten_kriterien_und_ergebnis(cz, monkeypatch):
    u = Umgebung(cz, monkeypatch, ["```python\nprint(1)\n```\nFertig."])
    aufnahme, reg = mit_vorgaengen(u)
    await u.aufruf("aufgabe_verteilen", art="code", auftrag="Schreibe ein Programm, das 1 ausgibt")
    v = vorgang_von(reg)
    assert v.art == "aufgabe" and v.status == "laeuft" and "Schreibe ein Programm" in v.titel
    assert [s.titel for s in v.arbeit] == ["Spezialisten zuteilen", "Antwort erstellen", "Ergebnis auf das Canvas legen"]
    await u.fertig()
    await reg.warten()
    assert v.status == "fertig" and v.gesamt() == 1.0 and v.beendet_ms is not None
    assert [s.status for s in v.arbeit] == ["fertig", "fertig", "fertig"]
    assert [(k.text, k.erfuellt) for k in v.kriterien] == [("Antwort liegt vor", True), ("Antwort vollständig", True),
                                                          ("Codeblock enthalten", True)]
    assert v.canvas_id == "11111111-2222-4333-8444-555555555555"
    assert aufnahme.stati()[-1] == "fertig"
    assert "pruefen" in aufnahme.stati() or "fertig" in aufnahme.stati()


async def test_analyse_hat_keinen_codeblock_als_kriterium(cz, monkeypatch):
    u = Umgebung(cz, monkeypatch, ["Die Auswertung ist kurz."])
    _, reg = mit_vorgaengen(u)
    await u.aufruf("aufgabe_verteilen", art="analyse", auftrag="Werte die Zahlen aus")
    await u.fertig()
    assert [k.text for k in vorgang_von(reg).kriterien] == ["Antwort liegt vor", "Antwort vollständig"]


async def test_code_ohne_codeblock_ist_ein_offenes_kriterium_aber_kein_fehler(cz, monkeypatch):
    u = Umgebung(cz, monkeypatch, ["Hier steht nur Text."])
    _, reg = mit_vorgaengen(u)
    await u.aufruf("aufgabe_verteilen", art="code", auftrag="Schreibe etwas")
    await u.fertig()
    v = vorgang_von(reg)
    assert v.status == "fertig" and v.kriterien[2].erfuellt is False


async def test_gekuerztes_ergebnis_ist_nicht_vollstaendig(cz, monkeypatch):
    u = Umgebung(cz, monkeypatch, ["x" * (boss.MAX_ERGEBNIS_ZEICHEN + 10)])
    _, reg = mit_vorgaengen(u)
    await u.aufruf("aufgabe_verteilen", art="analyse", auftrag="Lang")
    await u.fertig()
    assert vorgang_von(reg).kriterien[1].erfuellt is False


async def test_unklarer_auftrag_hat_den_schritt_einordnen_und_den_titel_aus_dem_auftrag(hz, monkeypatch):
    u = Umgebung(hz, monkeypatch, ['{"art": "analyse"}', "Die Auswertung"])
    _, reg = mit_vorgaengen(u)
    await u.aufruf("aufgabe_verteilen", art="unklar", auftrag="Vergleiche die beiden Angebote")
    v = vorgang_von(reg)
    assert [s.titel for s in v.arbeit][0] == "Auftrag einordnen" and len(v.arbeit) == 4
    await u.fertig()
    assert v.status == "fertig" and v.arbeit[0].status == "fertig" and "Vergleiche" in v.titel


async def test_rueckfall_auf_das_naechste_modell_zeigt_die_runde(hz, monkeypatch):
    u = Umgebung(hz, monkeypatch, [LLMFehler("Zeitüberschreitung", verbindung=True, zeitlimit=True), "Antwort"])
    aufnahme, reg = mit_vorgaengen(u)
    await u.aufruf("aufgabe_verteilen", art="code", auftrag="Aufgabe")
    await u.fertig()
    await reg.warten()
    v = vorgang_von(reg)
    assert v.status == "fertig" and (v.runde, v.max_runden) == (2, boss.MAX_VERSUCHE)


async def test_misserfolg_endet_als_fehler_ohne_interne_meldung(cz, monkeypatch):
    u = Umgebung(cz, monkeypatch, [LLMFehler("INTERN: Schlüssel sk-ant-geheim abgelaufen", verbindung=True)])
    aufnahme, reg = mit_vorgaengen(u)
    await u.aufruf("aufgabe_verteilen", art="code", auftrag="Aufgabe")
    await u.fertig()
    await reg.warten()
    v = vorgang_von(reg)
    assert v.status == "fehler" and v.kriterien[0].erfuellt is False
    text = repr(v.nachricht(0.0))
    assert "INTERN" not in text and "geheim" not in text  # die Oberfläche bekommt nur einen festen Hinweis
    assert any(s.status == "fehler" for s in v.arbeit)
    assert aufnahme.stati()[-1] == "fehler"


async def test_vorgang_ist_beendet_bevor_das_gespraech_die_meldung_bekommt(cz, monkeypatch):
    u = Umgebung(cz, monkeypatch, ["Antwort"])
    _, reg = mit_vorgaengen(u)
    stand: list[str] = []
    alt = u.boss._melden

    async def melden(text):
        stand.append(vorgang_von(reg).status)
        await alt(text)

    u.boss._melden = melden
    await u.aufruf("aufgabe_verteilen", art="analyse", auftrag="Aufgabe")
    await u.fertig()
    assert stand == ["fertig"]


async def test_schliessen_bricht_den_vorgang_ab(cz, monkeypatch):
    u = Umgebung(cz, monkeypatch, ["x"])
    _, reg = mit_vorgaengen(u)
    haengt = asyncio.Event()

    async def modell_anfrage(*a, **kw):
        await haengt.wait()

    monkeypatch.setattr(boss, "modell_anfrage", modell_anfrage)
    await u.aufruf("aufgabe_verteilen", art="code", auftrag="Aufgabe")
    await asyncio.sleep(0.01)
    assert vorgang_von(reg).status == "laeuft"
    await u.boss.schliessen()
    v = vorgang_von(reg)
    assert v.status == "abgebrochen" and v.beendet_ms is not None


async def test_ohne_registry_aendert_sich_nichts(cz, monkeypatch):
    u = Umgebung(cz, monkeypatch, ["Antwort"])
    assert u.boss._vorgaenge is None
    r = await u.aufruf("aufgabe_verteilen", art="analyse", auftrag="Aufgabe")
    await u.fertig()
    assert r["angenommen"] is True and u.karten[-1]["status"] == "fertig"


async def test_ein_fehler_in_der_registry_stoert_die_aufgabe_nicht(cz, monkeypatch):
    u = Umgebung(cz, monkeypatch, ["Antwort"])

    class Kaputt:
        def __getattr__(self, name):
            def aufruf(*a, **kw):
                raise RuntimeError("Registry kaputt")
            return aufruf

    u.boss._vorgaenge = Kaputt()
    r = await u.aufruf("aufgabe_verteilen", art="analyse", auftrag="Aufgabe")
    await u.fertig()
    assert r["angenommen"] is True
    assert (await u.aufruf("aufgabe_ergebnis", id=r["id"]))["status"] == "fertig"
