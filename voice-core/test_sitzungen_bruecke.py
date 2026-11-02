"""Tests der Ablöse-Brücke zwischen zwei Kern-Instanzen (sitzungen_bruecke.py und der Anbindung in sitzungen.py, Schritt 2.3).

Zwei Registries (Slot a und b) teilen sich einen Wegwerf-Ordner und laufen im selben Prozess. So lässt sich zeigen, dass eine neue Sitzung die
laufende desselben Profils auf der anderen Instanz ablöst (in beide Richtungen), dass die PC-Sperre vorher frei wird, dass fremde Profile in Ruhe
bleiben, dass verwaiste Marken nichts auslösen und dass ohne Slot alles wie bisher läuft.
"""

from __future__ import annotations

import asyncio
import os
import time
from unittest import mock

import pytest

from sitzungen import Sitzung, Sitzungen
from sitzungen_bruecke import MARKE_MAX_ALTER_S, Bruecke

NIKO = "11111111-1111-4111-8111-111111111111"
EVA = "22222222-2222-4222-8222-222222222222"


def verbindung():
    v = mock.Mock()
    v.disconnect = mock.AsyncMock()
    return v


def instanz(ordner, slot: str, **kw) -> Sitzungen:
    return Sitzungen(einzeln=True, meldung_vorlauf_secs=0, trennen_timeout_secs=0.2, bruecke=Bruecke(str(ordner), slot),
                     bruecke_warte_secs=kw.pop("warte", 2.0), bruecke_takt_secs=kw.pop("takt", 0.02), **kw)


def namen(ordner) -> list[str]:
    return sorted(os.listdir(ordner))


class TestBruecke:
    def test_ohne_slot_oder_mit_falschem_slot_ist_sie_aus(self, tmp_path, monkeypatch):
        monkeypatch.delenv("WAMO_SLOT", raising=False)
        assert not Bruecke.aus_umgebung().aktiv
        for slot in ("c", "", "ab", "../a"):
            monkeypatch.setenv("WAMO_SLOT", slot)
            assert not Bruecke.aus_umgebung().aktiv, slot
        monkeypatch.setenv("WAMO_SLOT", "b")
        monkeypatch.setenv("WAMO_SITZUNGEN_DIR", str(tmp_path / "s"))
        b = Bruecke.aus_umgebung()
        assert (b.aktiv, b.slot, b.andere) == (True, "b", "a")
        assert (tmp_path / "s").is_dir()

    def test_unbeschreibbarer_ordner_schaltet_die_bruecke_aus_statt_zu_stoeren(self, tmp_path):
        datei = tmp_path / "keinordner"
        datei.write_text("x")
        b = Bruecke(str(datei / "unter"), "a")
        assert not b.aktiv
        assert b.eintragen(NIKO) is False
        assert b.marken_fuer_mich() == []
        assert b.zaehle() == 0

    def test_eintragen_austragen_und_zaehlen(self, tmp_path):
        a, b = Bruecke(str(tmp_path), "a"), Bruecke(str(tmp_path), "b")
        assert a.eintragen(NIKO) and a.eintragen(EVA) and b.eintragen(NIKO)
        assert namen(tmp_path) == [f"{NIKO}.a", f"{NIKO}.b", f"{EVA}.a"]
        assert (a.zaehle(), b.zaehle(), a.zaehle("b")) == (2, 1, 1)
        assert a.andere_hat(NIKO) and not a.andere_hat(EVA)
        a.austragen(NIKO)
        assert not b.andere_hat(NIKO) and b.zaehle("a") == 1
        a.austragen(NIKO)   # zweimal ist kein Fehler

    def test_nur_uuid_namen_zaehlen(self, tmp_path):
        a = Bruecke(str(tmp_path), "a")
        for boese in ("../x", "x/y", "", "NIKO", "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA", NIKO + "/..", "a" * 300, "..", NIKO[:-1]):
            assert a.eintragen(boese) is False, boese
            assert a.marke_setzen(boese) is False, boese
            assert a.andere_hat(boese) is False, boese
        assert namen(tmp_path) == []
        (tmp_path / "fremd.txt").write_text("x")
        (tmp_path / f"{NIKO}.c").write_text("x")
        assert a.zaehle() == 0 and a.marken_fuer_mich() == []

    def test_marke_geht_nur_an_die_andere_instanz(self, tmp_path):
        a, b = Bruecke(str(tmp_path), "a"), Bruecke(str(tmp_path), "b")
        assert b.marke_setzen(NIKO)   # b bittet a
        assert namen(tmp_path) == [f"{NIKO}.abloesen.a"]
        assert a.marken_fuer_mich() == [NIKO]
        assert b.marken_fuer_mich() == []   # b löst nie sich selbst ab
        a.marke_loeschen(NIKO, fuer="a")
        assert a.marken_fuer_mich() == []

    def test_verwaiste_marke_wird_nicht_ausgefuehrt_sondern_geloescht(self, tmp_path):
        a, b = Bruecke(str(tmp_path), "a"), Bruecke(str(tmp_path), "b")
        b.marke_setzen(NIKO)
        alt = time.time() - MARKE_MAX_ALTER_S - 5
        os.utime(tmp_path / f"{NIKO}.abloesen.a", (alt, alt))
        assert a.marken_fuer_mich() == []
        assert namen(tmp_path) == []

    def test_aufraeumen_loescht_nur_die_eigenen(self, tmp_path):
        a, b = Bruecke(str(tmp_path), "a"), Bruecke(str(tmp_path), "b")
        a.eintragen(NIKO)
        b.eintragen(EVA)
        b.marke_setzen(NIKO)   # Marke an a
        a.marke_setzen(EVA)    # Marke an b
        assert a.aufraeumen() == 2
        assert namen(tmp_path) == [f"{EVA}.abloesen.b", f"{EVA}.b"]

    def test_dateien_sind_leer_und_nur_fuer_den_besitzer(self, tmp_path):
        a = Bruecke(str(tmp_path), "a")
        a.eintragen(NIKO)
        st = os.stat(tmp_path / f"{NIKO}.a")
        assert st.st_size == 0
        if os.name == "posix":
            assert st.st_mode & 0o077 == 0


class TestZweiInstanzen:
    def test_neue_sitzung_loest_die_der_anderen_instanz_ab_in_beide_richtungen(self, tmp_path):
        async def lauf():
            a, b = instanz(tmp_path, "a"), instanz(tmp_path, "b")
            for neu_auf, alt_auf, alt_slot in ((b, a, "a"), (a, b, "b")):
                v_alt = verbindung()
                meldungen = []

                async def abloesen(grund, m=meldungen):
                    m.append(grund)

                alt = Sitzung(NIKO, verbindung=v_alt, abloesen=abloesen)
                await alt_auf.uebernehmen(alt)
                assert alt_auf.anzahl() == 1 and (tmp_path / f"{NIKO}.{alt_slot}").exists()
                neu = Sitzung(NIKO, verbindung=verbindung())
                t0 = time.monotonic()
                assert await neu_auf.uebernehmen(neu) is None   # die lokale Ablösung meldet nur Sitzungen derselben Instanz
                assert time.monotonic() - t0 < 1.5
                assert alt.abgeloest and v_alt.disconnect.await_count == 1 and len(meldungen) == 1
                assert alt_auf.anzahl() == 0
                assert not (tmp_path / f"{NIKO}.{alt_slot}").exists()
                assert neu_auf.aktiv(NIKO) is neu and not neu.abgeloest
                assert not [n for n in namen(tmp_path) if "abloesen" in n]   # die Marke ist weg
                neu_auf.freigeben(neu)
                assert namen(tmp_path) == []
        asyncio.run(lauf())

    def test_die_pc_sperre_ist_frei_bevor_die_neue_sitzung_weiterlaeuft(self, tmp_path):
        async def lauf():
            a, b = instanz(tmp_path, "a"), instanz(tmp_path, "b")
            alt = Sitzung(NIKO, verbindung=verbindung())
            frei = []
            alt.pc_lease_freigeben = lambda: frei.append(True)   # type: ignore[method-assign]
            await a.uebernehmen(alt)
            await b.uebernehmen(Sitzung(NIKO, verbindung=verbindung()))
            assert frei == [True] and alt.abgeloest
            assert alt.pc_lease_nehmen() is False and alt.pc_lease_haelt() is False   # abgelöst: nie wieder
        asyncio.run(lauf())

    def test_andere_profile_bleiben_unberuehrt(self, tmp_path):
        async def lauf():
            a, b = instanz(tmp_path, "a"), instanz(tmp_path, "b")
            eva = Sitzung(EVA, verbindung=verbindung())
            await a.uebernehmen(eva)
            await a.uebernehmen(Sitzung(NIKO, verbindung=verbindung()))
            await b.uebernehmen(Sitzung(NIKO, verbindung=verbindung()))
            await asyncio.sleep(0.1)
            assert not eva.abgeloest and a.aktiv(EVA) is eva and (tmp_path / f"{EVA}.a").exists()
        asyncio.run(lauf())

    def test_dauert_die_ablosung_zu_lange_laeuft_die_neue_trotzdem_an_und_die_marke_bleibt(self, tmp_path):
        async def lauf():
            a = instanz(tmp_path, "a", takt=30.0)   # a schaut praktisch nie nach
            b = instanz(tmp_path, "b", warte=0.3)
            alt = Sitzung(NIKO, verbindung=verbindung())
            await a.uebernehmen(alt)
            t0 = time.monotonic()
            neu = Sitzung(NIKO, verbindung=verbindung())
            await b.uebernehmen(neu)
            assert 0.25 < time.monotonic() - t0 < 1.5
            assert b.aktiv(NIKO) is neu and not alt.abgeloest
            assert (tmp_path / f"{NIKO}.abloesen.a").exists()   # a kann es noch nachholen
        asyncio.run(lauf())

    def test_verwaiste_marke_loest_keine_neue_sitzung_ab(self, tmp_path):
        async def lauf():
            a = instanz(tmp_path, "a")
            Bruecke(str(tmp_path), "b").marke_setzen(NIKO)
            alt = time.time() - MARKE_MAX_ALTER_S - 5
            os.utime(tmp_path / f"{NIKO}.abloesen.a", (alt, alt))
            neu = Sitzung(NIKO, verbindung=verbindung())
            await a.uebernehmen(neu)
            await asyncio.sleep(0.15)
            assert not neu.abgeloest and a.aktiv(NIKO) is neu
        asyncio.run(lauf())

    def test_start_raeumt_liegen_gebliebene_dateien_der_eigenen_instanz_auf(self, tmp_path):
        Bruecke(str(tmp_path), "a").eintragen(NIKO)   # nach einem Absturz von a
        Bruecke(str(tmp_path), "b").eintragen(EVA)
        instanz(tmp_path, "a")
        assert namen(tmp_path) == [f"{EVA}.b"]

    def test_eine_panne_bei_einem_profil_beendet_die_beobachtung_nicht(self, tmp_path):
        async def lauf():
            a = instanz(tmp_path, "a")
            b = Bruecke(str(tmp_path), "b")
            kaputt = Sitzung(NIKO, verbindung=verbindung(), abloesen=mock.AsyncMock(side_effect=RuntimeError("weg")))
            ok = Sitzung(EVA, verbindung=verbindung())
            await a.uebernehmen(kaputt)
            await a.uebernehmen(ok)
            b.marke_setzen(NIKO)
            b.marke_setzen(EVA)
            await asyncio.sleep(0.4)
            assert kaputt.abgeloest and ok.abgeloest   # beide abgelöst, obwohl die Meldung bei einem scheiterte
        asyncio.run(lauf())

    def test_ohne_slot_bleibt_alles_wie_bisher(self, tmp_path, monkeypatch):
        monkeypatch.delenv("WAMO_SLOT", raising=False)
        async def lauf():
            r = Sitzungen(einzeln=True, meldung_vorlauf_secs=0, trennen_timeout_secs=0.2)
            assert not r._bruecke.aktiv
            alt = Sitzung(NIKO, verbindung=verbindung())
            assert await r.uebernehmen(alt) is None
            neu = Sitzung(NIKO, verbindung=verbindung())
            assert await r.uebernehmen(neu) is alt and alt.abgeloest
            assert r._beobachter is None   # kein Beobachter ohne Brücke
            r.freigeben(neu)
        asyncio.run(lauf())

    def test_schalter_einzeln_aus_schaltet_auch_die_bruecke_aus(self, tmp_path):
        async def lauf():
            r = Sitzungen(einzeln=False, bruecke=Bruecke(str(tmp_path), "a"))
            assert await r.uebernehmen(Sitzung(NIKO)) is None
            assert namen(tmp_path) == []
        asyncio.run(lauf())
