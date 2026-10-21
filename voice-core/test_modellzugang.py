"""Tests des Modellzugangs je Profil (modellzugang.py): Zuordnung je Profil, nur Hausmodelle bei jeder
Unsicherheit, Hinterlegen mit Testanfrage, Begrenzung der Versuche, Verwaltung und dass kein Schlüssel in
Nachrichten an die Oberfläche oder in Logs landet. Datenbank und Anthropic sind durch Attrappen ersetzt."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import anthropic
import httpx
import pytest
from loguru import logger

import modellzugang as mz
from memory import DatabaseUnavailable
from schluessel import Tresor, kek_erzeugen

NIKO = "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"
ANDERE = "7d3e9c1a-2b4f-4e6a-8c0d-1f2a3b4c5d6e"
KERN_SCHLUESSEL = "sk-ant-api03-KERNkern-0123456789abcdefghij-K3rn"
EIGENER = "sk-ant-api03-EIGENeigen-0123456789abcdefghij-Ab12"


def _leer() -> dict:
    return {"nur_haus": False, "claude_schluessel": None, "claude_schluessel_ende": None, "claude_workspace": None,
            "claude_modell": None, "claude_geprueft_at": None, "stt": "soniox", "tts": "soniox"}


class FakeDb:
    """Tabelle model_access im Speicher; versteht genau die Anweisungen aus modellzugang.py."""

    configured = True

    def __init__(self, fehler: Exception | None = None):
        self.zeilen: dict[str, dict] = {}
        self.anweisungen: list[tuple[str, tuple]] = []
        self.fehler = fehler

    def t(self, name: str) -> str:
        return f"wamo_test.{name}"

    def zeile(self, uid: str, **werte) -> dict:
        self.zeilen[uid] = _leer() | werte
        return self.zeilen[uid]

    async def fetchrow(self, sql: str, *args):
        if self.fehler:
            raise self.fehler
        return self.zeilen.get(args[0])

    async def execute(self, sql: str, *args):
        if self.fehler:
            raise self.fehler
        self.anweisungen.append((sql, args))
        uid = args[0]
        if sql.startswith("insert") and "claude_schluessel," in sql:
            self.zeilen.setdefault(uid, _leer()).update(
                claude_schluessel=args[1], claude_schluessel_ende=args[2], claude_workspace=args[3],
                claude_geprueft_at=datetime(2026, 10, 21, 9, 30, tzinfo=UTC))
        elif sql.startswith("update"):
            self.zeilen.setdefault(uid, _leer()).update(
                claude_schluessel=None, claude_schluessel_ende=None, claude_workspace=None, claude_geprueft_at=None)
        return "OK"


@pytest.fixture
def tresor():
    return Tresor(base64.urlsafe_b64decode(kek_erzeugen()))


@pytest.fixture
def db():
    return FakeDb()


@pytest.fixture
def log():
    zeilen: list[str] = []
    sink = logger.add(lambda m: zeilen.append(str(m)), level="DEBUG")
    yield zeilen
    logger.remove(sink)


@pytest.fixture
def kern(monkeypatch):
    """Der Schlüssel des Kerns gehört Niko und ist ihm über die Zuordnung zugewiesen."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", KERN_SCHLUESSEL)
    monkeypatch.setenv(mz.ZUORDNUNG_ENV, NIKO.upper())  # Schreibweise egal, die UUID zählt


def _testen(grund: str | None = None, gesehen: list | None = None):
    async def pruefen(schluessel, workspace):
        if gesehen is not None:
            gesehen.append((schluessel, workspace))
        return grund
    return pruefen


def _ohne_schluessel(text: str) -> bool:
    return all(s not in text and s[:20] not in text for s in (EIGENER, KERN_SCHLUESSEL))


# --- Zuordnung je Profil ---------------------------------------------------------------------------


async def test_ohne_eintrag_nur_hausmodelle(db, tresor):
    z = await mz.zugang_laden(ANDERE, db=db, tresor=tresor)
    assert (z.art, z.quelle, z.claude_schluessel) == ("haus", "keiner", None)


async def test_kernschluessel_nur_fuer_das_zugeordnete_profil(db, tresor, kern):
    niko = await mz.zugang_laden(NIKO, db=db, tresor=tresor)
    assert (niko.art, niko.quelle, niko.claude_schluessel) == ("claude", "zuordnung", KERN_SCHLUESSEL)
    andere = await mz.zugang_laden(ANDERE, db=db, tresor=tresor)
    assert andere.art == "haus" and andere.claude_schluessel is None


async def test_zuordnung_ohne_kernschluessel_oder_ohne_profil(monkeypatch, db, tresor):
    monkeypatch.setenv(mz.ZUORDNUNG_ENV, NIKO)
    monkeypatch.setenv("ANTHROPIC_API_KEY", " ")
    assert (await mz.zugang_laden(NIKO, db=db, tresor=tresor)).art == "haus"
    monkeypatch.setenv("ANTHROPIC_API_KEY", KERN_SCHLUESSEL)
    monkeypatch.setenv(mz.ZUORDNUNG_ENV, "alle")  # keine UUID: niemand
    assert (await mz.zugang_laden(NIKO, db=db, tresor=tresor)).art == "haus"


async def test_eigener_schluessel_aus_der_datenbank(db, tresor, kern):
    db.zeile(ANDERE, claude_schluessel=tresor.verschluesseln(ANDERE, EIGENER), claude_schluessel_ende="Ab12",
             claude_workspace="wrkspc_abcdef12", claude_modell="claude-sonnet-5-5", stt="haus")
    z = await mz.zugang_laden(ANDERE, db=db, tresor=tresor)
    assert (z.art, z.quelle, z.claude_schluessel) == ("claude", "profil", EIGENER)
    assert (z.claude_workspace, z.claude_modell, z.stt, z.tts) == ("wrkspc_abcdef12", "claude-sonnet-5-5", "haus",
                                                                    "soniox")
    assert EIGENER not in repr(z)


async def test_eigener_schluessel_hat_vorrang_vor_der_zuordnung(db, tresor, kern):
    db.zeile(NIKO, claude_schluessel=tresor.verschluesseln(NIKO, EIGENER))
    z = await mz.zugang_laden(NIKO, db=db, tresor=tresor)
    assert (z.quelle, z.claude_schluessel) == ("profil", EIGENER)


async def test_festgelegt_auf_hausmodelle_schlaegt_alles(db, tresor, kern):
    db.zeile(NIKO, nur_haus=True, stt="haus", tts="haus")
    z = await mz.zugang_laden(NIKO, db=db, tresor=tresor)
    assert (z.art, z.quelle, z.stt, z.tts) == ("haus", "festgelegt", "haus", "haus")


async def test_chiffrat_eines_anderen_profils_wird_nicht_genutzt(db, tresor, log):
    """Kopiert jemand das Chiffrat in eine fremde Zeile, entsteht kein Claude-Zugang (AAD = Profil-ID)."""
    db.zeile(ANDERE, claude_schluessel=tresor.verschluesseln(NIKO, EIGENER))
    z = await mz.zugang_laden(ANDERE, db=db, tresor=tresor)
    assert (z.art, z.quelle) == ("haus", "fehler")
    assert all(_ohne_schluessel(zeile) for zeile in log)


async def test_ohne_hauptschluessel_nur_hausmodelle(monkeypatch, db, tresor, kern):
    db.zeile(NIKO, claude_schluessel=tresor.verschluesseln(NIKO, EIGENER))
    monkeypatch.delenv("WAMO_SCHLUESSEL_KEK", raising=False)
    z = await mz.zugang_laden(NIKO, db=db)
    assert (z.art, z.quelle, z.hinweis) == ("haus", "fehler", "Hauptschlüssel fehlt")
    monkeypatch.setenv("WAMO_SCHLUESSEL_KEK", kek_erzeugen())  # anderer Hauptschlüssel
    assert (await mz.zugang_laden(NIKO, db=db)).hinweis == "Schlüssel nicht lesbar"


async def test_datenbankfehler_nur_hausmodelle(tresor, kern):
    z = await mz.zugang_laden(NIKO, db=FakeDb(fehler=DatabaseUnavailable("weg")), tresor=tresor)
    assert (z.art, z.quelle) == ("haus", "fehler")


async def test_ungueltige_profil_id(db, tresor):
    assert (await mz.zugang_laden("betreiber", db=db, tresor=tresor)).quelle == "fehler"


# --- Hinterlegen, Löschen, Stand --------------------------------------------------------------


async def test_hinterlegen_verschluesselt_und_ohne_schluessel_im_log(db, tresor, log):
    gesehen = []
    stand = await mz.hinterlegen(ANDERE, f"  {EIGENER} ", "wrkspc_abcdef12", db=db, tresor=tresor,
                                 testen=_testen(None, gesehen))
    assert gesehen == [(EIGENER, "wrkspc_abcdef12")]
    gespeichert = db.zeilen[ANDERE]["claude_schluessel"]
    assert isinstance(gespeichert, bytes) and EIGENER.encode() not in gespeichert
    assert tresor.entschluesseln(ANDERE, gespeichert) == EIGENER
    assert (stand.art, stand.quelle, stand.hinterlegt, stand.ende) == ("claude", "profil", True, "Ab12")
    assert stand.geprueft == "2026-10-21T09:30+00:00"
    assert all(EIGENER not in str(a) for _sql, args in db.anweisungen for a in args)
    assert any("endet auf …Ab12" in z for z in log) and all(_ohne_schluessel(z) for z in log)


@pytest.mark.parametrize("schluessel,workspace,grund", [
    ("sk-proj-0123456789abcdefghijklmn", None, "beginnt mit sk-ant-"),
    ("", None, "beginnt mit sk-ant-"),
    (EIGENER, "org_123", "wrkspc_"),
])
async def test_hinterlegen_format(db, tresor, schluessel, workspace, grund):
    gesehen = []
    with pytest.raises(mz.ZugangFehler, match=grund):
        await mz.hinterlegen(ANDERE, schluessel, workspace, db=db, tresor=tresor, testen=_testen(None, gesehen))
    assert gesehen == [] and db.anweisungen == []


async def test_hinterlegen_testanfrage_abgelehnt(db, tresor):
    with pytest.raises(mz.ZugangFehler, match="Anthropic lehnt den Schlüssel ab"):
        await mz.hinterlegen(ANDERE, EIGENER, db=db, tresor=tresor, testen=_testen("Anthropic lehnt den Schlüssel ab"))
    assert db.anweisungen == []


async def test_hinterlegen_bei_festgelegtem_profil(db, tresor):
    db.zeile(ANDERE, nur_haus=True)
    with pytest.raises(mz.ZugangFehler, match="nur Hausmodelle"):
        await mz.hinterlegen(ANDERE, EIGENER, db=db, tresor=tresor, testen=_testen())


async def test_hinterlegen_ohne_hauptschluessel(monkeypatch, db):
    monkeypatch.delenv("WAMO_SCHLUESSEL_KEK", raising=False)
    with pytest.raises(mz.ZugangFehler, match="WAMO_SCHLUESSEL_KEK"):
        await mz.hinterlegen(ANDERE, EIGENER, db=db, testen=_testen())


async def test_versuche_sind_begrenzt(db, tresor):
    gesehen = []
    for _ in range(mz.VERSUCHE_JE_FENSTER):
        with pytest.raises(mz.ZugangFehler, match="lehnt"):
            await mz.hinterlegen(ANDERE, EIGENER, db=db, tresor=tresor, testen=_testen("Anthropic lehnt ab", gesehen))
    with pytest.raises(mz.ZugangFehler, match="Zu viele Versuche"):
        await mz.hinterlegen(ANDERE, EIGENER, db=db, tresor=tresor, testen=_testen(None, gesehen))
    assert len(gesehen) == mz.VERSUCHE_JE_FENSTER  # der sechste Versuch erreicht Anthropic nicht
    # ein anderes Profil ist nicht betroffen, und nach dem Zeitfenster geht es wieder
    await mz.hinterlegen(NIKO, EIGENER, db=db, tresor=tresor, testen=_testen())
    assert mz._versuch_erlaubt(ANDERE, jetzt=10**9)


async def test_loeschen(db, tresor):
    await mz.hinterlegen(ANDERE, EIGENER, db=db, tresor=tresor, testen=_testen())
    stand = await mz.loeschen(ANDERE, db=db)
    assert db.zeilen[ANDERE]["claude_schluessel"] is None
    assert (stand.art, stand.hinterlegt, stand.ende) == ("haus", False, None)


async def test_stand_bei_zuordnung(db, kern):
    stand = await mz.stand_laden(NIKO, db=db)
    assert (stand.art, stand.quelle, stand.hinterlegt, stand.modell) == ("claude", "zuordnung", False,
                                                                         "Claude Haiku 5.5")
    assert (await mz.stand_laden(ANDERE, db=db)).modell == "Hausmodelle"


# --- Nachrichten der Oberfläche ---------------------------------------------------------------


async def _nachricht(data, *, db, tresor, testen=None):
    gesendet: list[dict] = []

    async def senden(n):
        gesendet.append(n)

    await mz.modellzugang_nachricht(ANDERE, data, senden, db=db, tresor=tresor, testen=testen or _testen())
    assert len(gesendet) == 1
    assert _ohne_schluessel(json.dumps(gesendet[0], ensure_ascii=False))
    return gesendet[0]


async def test_nachricht_hinterlegen_ohne_schluessel_in_der_antwort(db, tresor, log):
    n = await _nachricht({"aktion": "hinterlegen", "schluessel": EIGENER}, db=db, tresor=tresor)
    assert (n["type"], n["aktion"], n["hinterlegt"], n["ende"], n["art"]) == ("modellzugang", "hinterlegen", True,
                                                                              "Ab12", "claude")
    assert n["wirksam"] == "ab der nächsten Sitzung" and "fehler" not in n
    assert all(_ohne_schluessel(z) for z in log)


async def test_nachricht_fehler_ohne_schluessel(db, tresor, log):
    n = await _nachricht({"aktion": "hinterlegen", "schluessel": EIGENER + "!"}, db=db, tresor=tresor)
    assert n["fehler"].startswith("Das ist kein Anthropic-API-Schlüssel") and n["hinterlegt"] is False
    n = await _nachricht({"aktion": "hinterlegen", "schluessel": EIGENER}, db=db, tresor=tresor,
                         testen=_testen("Anthropic lehnt den Schlüssel ab"))
    assert n["fehler"] == "Anthropic lehnt den Schlüssel ab"
    assert all(_ohne_schluessel(z) for z in log)


async def test_nachricht_status_und_loeschen(db, tresor):
    assert (await _nachricht({}, db=db, tresor=tresor))["aktion"] == "status"
    assert (await _nachricht({"aktion": "unbekannt"}, db=db, tresor=tresor))["aktion"] == "status"
    await _nachricht({"aktion": "hinterlegen", "schluessel": EIGENER}, db=db, tresor=tresor)
    n = await _nachricht({"aktion": "loeschen"}, db=db, tresor=tresor)
    assert (n["aktion"], n["hinterlegt"]) == ("loeschen", False)


async def test_nachricht_datenbank_weg(tresor):
    n = await _nachricht({"aktion": "loeschen"}, db=FakeDb(fehler=DatabaseUnavailable("weg")), tresor=tresor)
    assert n == {"type": "modellzugang", "aktion": "loeschen", "fehler": "Datenbank gerade nicht erreichbar"}


# --- Testanfrage bei Anthropic ----------------------------------------------------------------


def _status(code):
    return httpx.Response(code, request=httpx.Request("POST", "https://api.anthropic.test.invalid/v1/messages"))


@pytest.mark.parametrize("fehler,grund", [
    (None, None),
    (anthropic.AuthenticationError("x", response=_status(401), body=None), "Anthropic lehnt den Schlüssel ab"),
    (anthropic.PermissionDeniedError("x", response=_status(403), body=None), "Workspace oder Berechtigung"),
    (anthropic.RateLimitError("x", response=_status(429), body=None), "HTTP 429"),
    (anthropic.APIConnectionError(request=httpx.Request("POST", "https://a.invalid")), "nicht erreichbar"),
])
async def test_schluessel_testen(monkeypatch, fehler, grund):
    aufrufe = []

    class Client:
        def __init__(self, **kw):
            aufrufe.append(("client", kw))
            self.messages = self

        async def create(self, **kw):
            aufrufe.append(("create", kw))
            if fehler:
                raise fehler

        async def close(self):
            aufrufe.append(("close", {}))

    monkeypatch.setattr(mz.anthropic, "AsyncAnthropic", Client)
    ergebnis = await mz.schluessel_testen(EIGENER, "wrkspc_abcdef12")
    assert ergebnis is None if grund is None else grund in ergebnis
    assert ergebnis is None or _ohne_schluessel(ergebnis)
    client, create, close = aufrufe
    assert client[1]["api_key"] == EIGENER and client[1]["max_retries"] == 0
    assert client[1]["default_headers"] == {"anthropic-workspace-id": "wrkspc_abcdef12"}
    assert create[1]["max_tokens"] == 1 and create[1]["model"] == "claude-haiku-5-5"
    assert close[0] == "close"


# --- Verwaltung ---------------------------------------------------------------------------------


async def test_admin_festlegen(db):
    await mz.admin_festlegen(ANDERE, nur_haus=True, tts="haus", db=db)
    sql, args = db.anweisungen[-1]
    assert sql.startswith("insert into wamo_test.model_access (user_id, nur_haus, tts) values ($1, $2, $3)")
    assert "nur_haus = excluded.nur_haus, tts = excluded.tts" in sql and "claude_schluessel" not in sql
    assert args == (ANDERE, True, "haus")


@pytest.mark.parametrize("kw,grund", [
    ({"modell": "gpt-5"}, "Claude-ID"),
    ({"modell": "claude-haiku; drop table x"}, "Claude-ID"),
    ({"stt": "whisper"}, "soniox oder haus"),
    ({}, "Nichts zu ändern"),
])
async def test_admin_festlegen_prueft(db, kw, grund):
    with pytest.raises(mz.ZugangFehler, match=grund):
        await mz.admin_festlegen(ANDERE, db=db, **kw)
    with pytest.raises(mz.ZugangFehler, match="UUID"):
        await mz.admin_festlegen("kein-profil", nur_haus=True, db=db)
    assert db.anweisungen == []
