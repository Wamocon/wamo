"""Tests des Kontingents (kontingent.py): Parser für rate_limit_event von Claude Code mit fester Allowlist,
Prüfung der Ereignisse des Dispatchers, Zusammenführen, Servernachricht und Speicher je Profil (Datenbank als Attrappe)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import kontingent as kt

JETZT = datetime(2026, 10, 22, 12, 0, 0, tzinfo=UTC)
EPOCHE = int(datetime(2026, 10, 22, 15, 0, 0, tzinfo=UTC).timestamp())  # 15:00 UTC, in drei Stunden
WOCHE = int(datetime(2026, 10, 26, 9, 0, 0, tzinfo=UTC).timestamp())

# Form wie von Claude Code 2.1.303 mit einer API-Attrappe und Kopfzeilen anthropic-ratelimit-unified-* ausgegeben
ECHT = {"type": "rate_limit_event", "uuid": "177cdf81-b4a5-4364-b22e-a6cc40163370",
        "session_id": "574c6fb1-9bd9-4ba8-81c6-91220c8aaf45",
        "rate_limit_info": {"isUsingOverage": False, "rateLimitType": "five_hour", "resetsAt": EPOCHE,
                            "status": "allowed",
                            "unifiedWindows": {"five_hour": {"resetsAt": EPOCHE, "utilization": 0.42},
                                               "seven_day": {"resetsAt": WOCHE, "utilization": 0.13},
                                               "seven_day_overage_included": {"resetsAt": WOCHE + 86400,
                                                                              "utilization": 0.07}}}}


def test_echtes_ereignis_wird_zu_drei_limits():
    ev = kt.ereignis_aus_zeile(ECHT)
    assert ev["type"] == "wamo" and ev["ereignis"] == "kontingent" and ev["quelle"] == "rate_limit_event"
    assert ev["limits"] == [
        {"art": "sitzung", "prozent": 42.0, "endet": "2026-10-22T15:00:00Z", "status": "ok"},
        {"art": "woche", "prozent": 13.0, "endet": "2026-10-26T09:00:00Z"},
        {"art": "fable", "prozent": 7.0, "endet": "2026-10-27T09:00:00Z"},
    ]


def test_status_des_begrenzenden_fensters_und_erreicht_ab_100_prozent():
    info = {"status": "allowed_warning", "rateLimitType": "seven_day", "resetsAt": WOCHE, "utilization": 0.91}
    assert kt.aus_rate_limit_info(info) == [
        {"art": "woche", "prozent": 91.0, "endet": "2026-10-26T09:00:00Z", "status": "warnung"}]
    voll = {"status": "rejected", "rateLimitType": "five_hour", "resetsAt": EPOCHE,
            "unifiedWindows": {"five_hour": {"utilization": 1.0, "resetsAt": EPOCHE},
                               "seven_day": {"utilization": 1.2, "resetsAt": WOCHE}}}
    out = {e["art"]: e for e in kt.aus_rate_limit_info(voll)}
    assert out["sitzung"]["status"] == "erreicht" and out["woche"]["status"] == "erreicht"
    assert out["woche"]["prozent"] == 120.0  # Nutzung über der Grenze kommt vor


def test_unbekanntes_und_fehlerhaftes_wird_ignoriert():
    assert kt.ereignis_aus_zeile({"type": "assistant"}) is None
    assert kt.ereignis_aus_zeile("kein Objekt") is None
    assert kt.ereignis_aus_zeile({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}}) is None
    boese = {"status": "allowed", "rateLimitType": "overage; drop table", "extra": "x" * 10_000,
             "unifiedWindows": {"five_hour": {"utilization": "viel", "resetsAt": "morgen"},
                                "seven_day": {"utilization": float("nan"), "resetsAt": 1},
                                "unbekannt": {"utilization": 0.5, "resetsAt": EPOCHE},
                                "seven_day_overage_included": {"utilization": True, "resetsAt": True}}}
    assert kt.aus_rate_limit_info(boese) == []
    assert kt.aus_rate_limit_info({"unifiedWindows": {"five_hour": {"utilization": -0.2, "resetsAt": EPOCHE}}}) \
        == [{"art": "sitzung", "endet": "2026-10-22T15:00:00Z"}]
    assert kt.aus_rate_limit_info({"unifiedWindows": {"five_hour": {"utilization": 99, "resetsAt": EPOCHE}}}) \
        == [{"art": "sitzung", "endet": "2026-10-22T15:00:00Z"}]  # Wertebereich: höchstens 1000 Prozent
    assert kt.aus_rate_limit_info(None) == [] and kt.aus_rate_limit_info([]) == []


def test_absurde_zahlen_und_zeiten_werfen_nie():
    gross = 10**400  # float(gross) wirft OverflowError
    info = {"status": "allowed", "rateLimitType": "five_hour", "resetsAt": gross, "utilization": gross,
            "unifiedWindows": {"five_hour": {"utilization": gross, "resetsAt": gross},
                               "seven_day": {"utilization": -gross, "resetsAt": gross},
                               "seven_day_overage_included": {"utilization": 1e308 * 10, "resetsAt": -1e308 * 10}}}
    kt.aus_rate_limit_info(info)  # darf nicht werfen
    assert [e for e in kt.aus_rate_limit_info(info) if "prozent" in e or "endet" in e] == []
    assert kt.limits_pruefen([{"art": "woche", "prozent": gross}]) == []
    ereignis = kt.ereignis_aus_zeile({"type": "rate_limit_event", "rate_limit_info": info})
    assert ereignis["limits"] == [{"art": "sitzung", "status": "ok"}]  # nur der Status des Fensters, keine Zahlen
    for schlecht in ("٢٠٩٩-01-01T00:00:00Z", "9999-99-99T99:99:99Z", "2096-02-30T00:00:00Z", "2096-10-22 15:00:00Z"):
        assert not kt.zeit_gueltig(schlecht)
        assert kt.limits_pruefen([{"art": "woche", "endet": schlecht}]) == []
    assert kt.zeit_gueltig("2096-10-22T15:00:00Z")


def test_neues_fenster_uebernimmt_weder_prozent_noch_status():
    alt = [{"art": "sitzung", "prozent": 100.0, "endet": "2096-10-22T15:00:00Z", "status": "erreicht"}]
    neu = [{"art": "sitzung", "prozent": 3.0, "endet": "2096-10-22T20:00:00Z"}]
    assert kt.zusammenfuehren(alt, neu, JETZT) == neu
    # ein abgelaufenes Fenster trägt nichts in das nächste hinein
    spaeter = datetime(2096, 10, 22, 16, 0, tzinfo=UTC)
    assert kt.zusammenfuehren(alt, [{"art": "sitzung", "endet": "2096-10-22T20:00:00Z"}], spaeter)         == [{"art": "sitzung", "endet": "2096-10-22T20:00:00Z"}]
    # dasselbe Fenster: Werte der Beobachtungen werden ergänzt
    gleich = kt.zusammenfuehren([{"art": "woche", "prozent": 5.0, "endet": "2096-10-26T09:00:00Z"}],
                                [{"art": "woche", "status": "warnung", "endet": "2096-10-26T09:00:00Z"}], JETZT)
    assert gleich == [{"art": "woche", "prozent": 5.0, "endet": "2096-10-26T09:00:00Z", "status": "warnung"}]
    # eine neue Beobachtung, die schon abgelaufen ist, zählt nicht
    assert kt.zusammenfuehren([], [{"art": "woche", "prozent": 5.0, "endet": "2020-01-01T00:00:00Z"}], JETZT) == []


def test_modell_wochenlimits_opus_und_sonnet():
    for typ, art in (("seven_day_opus", "opus"), ("seven_day_sonnet", "sonnet")):
        out = kt.aus_rate_limit_info({"status": "allowed_warning", "rateLimitType": typ, "resetsAt": WOCHE,
                                      "utilization": 0.8})
        assert out == [{"art": art, "prozent": 80.0, "endet": "2026-10-26T09:00:00Z", "status": "warnung"}]


def test_ausgabe_traegt_nur_allowlist_felder():
    ev = kt.ereignis_aus_zeile(ECHT)
    text = json.dumps(ev)
    for fremd in ("uuid", "session_id", "isUsingOverage", "rate_limit_info", "unifiedWindows", "177cdf81"):
        assert fremd not in text
    for limit in ev["limits"]:
        assert set(limit) <= set(kt.SCHLUESSEL)


def test_ereignis_des_dispatchers_wird_noch_einmal_geprueft():
    gut = kt.ereignis_aus_zeile(ECHT)
    assert kt.pruefen_ereignis(gut) == gut["limits"]
    schlecht = {"ereignis": "kontingent", "limits": [
        {"art": "sitzung", "prozent": 5, "endet": "2026-10-22T15:00:00Z", "zusatz": "x"},          # Zusatzfeld
        {"art": "unbekannt", "prozent": 5},                                                         # Art
        {"art": "woche", "prozent": -1}, {"art": "woche", "prozent": 5000},                         # Bereich
        {"art": "woche", "endet": "morgen"}, {"art": "woche", "status": "kaputt"},                  # Form
        {"art": "opus", "prozent": "5"}, "text", 5, None,
        {"art": "fable", "prozent": 7, "endet": "2026-10-27T09:00:00Z", "status": "ok"},
        {"art": "fable", "prozent": 99},                                                            # doppelt
    ]}
    assert kt.pruefen_ereignis(schlecht) == [{"art": "fable", "prozent": 7.0, "endet": "2026-10-27T09:00:00Z",
                                              "status": "ok"}]
    assert kt.pruefen_ereignis({"ereignis": "ende", "limits": gut["limits"]}) == []
    assert kt.pruefen_ereignis({"ereignis": "kontingent", "limits": "alles"}) == []
    assert kt.pruefen_ereignis("x") == []


def test_zusammenfuehren_neueres_gewinnt_und_abgelaufenes_faellt_weg():
    alt = kt.ereignis_aus_zeile(ECHT)["limits"]
    neu = [{"art": "sitzung", "prozent": 55.0, "endet": "2026-10-22T15:00:00Z", "status": "warnung"}]
    stand = {e["art"]: e for e in kt.zusammenfuehren(alt, neu, JETZT)}
    assert stand["sitzung"]["prozent"] == 55.0 and stand["sitzung"]["status"] == "warnung"
    assert stand["woche"]["prozent"] == 13.0  # bleibt aus der älteren Beobachtung
    spaeter = datetime(2026, 10, 22, 16, 0, tzinfo=UTC)  # das 5-Stunden-Fenster ist vorbei
    assert [e["art"] for e in kt.zusammenfuehren(alt, [], spaeter)] == ["woche", "fable"]
    # neues Fenster (anderes Ende) ohne Prozentwert: der alte Wert gilt nicht mehr
    nur_status = [{"art": "sitzung", "endet": "2026-10-22T20:00:00Z", "status": "ok"}]
    assert "prozent" not in {e["art"]: e for e in kt.zusammenfuehren(alt, nur_status, JETZT)}["sitzung"]


def test_nachricht_ohne_daten_ist_unbekannt():
    leer = kt.nachricht([], None, JETZT)
    assert leer == {"type": "kontingent", "zustand": "unbekannt", "stand": None, "limits": []}
    assert kt.nachricht("kaputt", "2026-10-22T11:00:00Z", JETZT)["zustand"] == "unbekannt"
    voll = kt.nachricht(kt.ereignis_aus_zeile(ECHT)["limits"], "2026-10-22T11:59:00Z", JETZT)
    assert voll["zustand"] == "bekannt" and voll["stand"] == "2026-10-22T11:59:00Z" and len(voll["limits"]) == 3
    assert kt.nachricht(voll["limits"], "gestern", JETZT)["stand"] is None
    vorbei = kt.nachricht(voll["limits"], "2026-10-22T11:59:00Z", datetime(2026, 11, 30, tzinfo=UTC))
    assert vorbei["zustand"] == "unbekannt" and vorbei["limits"] == []


class FakeDb:
    configured = True

    def __init__(self):
        self.zeilen: dict[str, dict] = {}
        self.anweisungen: list[tuple[str, tuple]] = []

    def t(self, name: str) -> str:
        return f"wamo_test.{name}"

    async def fetchrow(self, sql: str, *args):
        return self.zeilen.get(args[0])

    async def execute(self, sql: str, *args):
        self.anweisungen.append((sql, args))
        self.zeilen[args[0]] = {"kontingent": args[1], "kontingent_stand": datetime(2026, 10, 22, 11, 59, tzinfo=UTC)}


async def test_speichern_und_laden_je_profil():
    db, uid = FakeDb(), "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"
    assert await kt.laden(db, uid) == ([], None)
    limits = kt.ereignis_aus_zeile(ECHT)["limits"]
    await kt.speichern(db, uid, limits + [{"art": "geheim", "prozent": 1}])  # Fremdes wird nicht gespeichert
    sql, args = db.anweisungen[0]
    assert "insert into wamo_test.model_access (user_id, kontingent, kontingent_stand)" in sql
    assert "on conflict (user_id) do update set kontingent = excluded.kontingent" in sql
    assert "claude_schluessel" not in sql and "nur_haus" not in sql  # fasst nur die eigenen Spalten an
    assert json.loads(args[1]) == limits
    assert await kt.laden(db, uid) == (limits, "2026-10-22T11:59:00Z")
    db.zeilen[uid]["kontingent"] = "[kein json"
    assert await kt.laden(db, uid) == ([], "2026-10-22T11:59:00Z")
    db.zeilen[uid]["kontingent"] = [{"art": "x", "prozent": 3}]
    assert (await kt.laden(db, uid))[0] == []
