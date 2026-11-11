"""T10 (Testlabor): "nach dem Löschen ist nichts mehr da".

Drei Schichten, die ineinandergreifen:
  1. Vollständigkeit: Die Löschliste (labor_loeschliste.py) wird gegen ALLE Migrationen geprüft. Jede Tabelle mit einer Spalte, die auf
     profiles(user_id) oder auf auth.users(id) verweist, steht in LOESCHLISTE oder, begründet, in AUSNAHMEN; jeder Verweis in einer
     Listentabelle, der kein Besitz ist, steht in FREMDE_VERWEISE. Eine neue Tabelle mit Besitzerspalte macht diesen Test rot.
     Dass die SQL-Funktion in 0018 genau diese Liste löscht, prüft test_migration_0018.py.
  2. Verwaltung (labor_verwaltung.py): ruft nur die SQL-Funktionen auf, prüft ihr Ergebnis gegen die Liste und verlangt nach einer
     vollständigen Löschung null Zeilen. Getestet gegen eine Attrappe der Datenbank, die die Semantik der Funktionen aus der Liste
     nachbildet (Besitzer, Zeitfenster, Sitzungen) und die Zugangsprüfungen (kein Laborprofil, freigeschaltet, laufende Sitzung).
  3. Die echte Datenbank prüft supabase/tests/0018_labor_profile_test.sql, dort mit jeder Tabelle gefüllt und dynamisch aus dem Katalog.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest

import labor_loeschliste as ll
import labor_verwaltung as lv

MIGRATIONEN = Path(__file__).resolve().parent.parent / "supabase" / "migrations"
ALS_PROFIL = r"(?:__SCHEMA__\.profiles\(user_id\)|auth\.users\(id\))"


# ---------- 1. Vollständigkeit gegen alle Migrationen ----------

def _sql_ohne_kommentare(text: str) -> str:
    return "\n".join(z.split("--", 1)[0] for z in text.splitlines())


def _verweise_auf_profile() -> dict[str, set[str]]:
    """Tabelle -> Spalten, die auf profiles(user_id) oder auf auth.users(id) verweisen, aus allen Migrationen (ohne .down.sql)."""
    ergebnis: dict[str, set[str]] = {}
    for datei in sorted(MIGRATIONEN.glob("*.sql")):
        if datei.name.endswith(".down.sql"):
            continue
        text = _sql_ohne_kommentare(datei.read_text(encoding="utf-8"))
        for anweisung in text.split(";"):
            kopf = re.match(r"\s*(create table(?: if not exists)?|alter table)\s+__SCHEMA__\.(\w+)", anweisung, re.I)
            if not kopf:
                continue
            tabelle = kopf.group(2)
            if kopf.group(1).lower().startswith("create"):
                for zeile in anweisung.splitlines():
                    spalte = re.match(rf"\s*(\w+)\s+uuid\b[^,]*\breferences\s+{ALS_PROFIL}", zeile, re.I)
                    if spalte:
                        ergebnis.setdefault(tabelle, set()).add(spalte.group(1))
            for spalte in re.findall(rf"add column if not exists (\w+) uuid references {ALS_PROFIL}", anweisung, re.I):
                ergebnis.setdefault(tabelle, set()).add(spalte)
            for spalte in re.findall(rf"foreign key \((\w+)\) references {ALS_PROFIL}", anweisung, re.I):
                ergebnis.setdefault(tabelle, set()).add(spalte)
    return ergebnis


def _tabellen_mit_besitzernamen() -> dict[str, set[str]]:
    """Tabellen mit einer uuid-Spalte, die wie eine Besitzerspalte heißt, ob mit oder ohne Fremdschlüssel."""
    ergebnis: dict[str, set[str]] = {}
    for datei in sorted(MIGRATIONEN.glob("*.sql")):
        if datei.name.endswith(".down.sql"):
            continue
        text = _sql_ohne_kommentare(datei.read_text(encoding="utf-8"))
        for anweisung in text.split(";"):
            kopf = re.match(r"\s*create table(?: if not exists)?\s+__SCHEMA__\.(\w+)", anweisung, re.I)
            if kopf:
                for zeile in anweisung.splitlines():
                    spalte = re.match(r"\s*(\w+)\s+uuid\b", zeile)
                    if spalte and spalte.group(1) in ll.BESITZER_NAMEN:
                        ergebnis.setdefault(kopf.group(1), set()).add(spalte.group(1))
    return ergebnis


def test_der_parser_findet_die_bekannten_besitzerspalten():
    """Selbstprüfung des Tests: Wenn der Parser die bekannten Fälle nicht findet, taugt der Vollständigkeitstest nichts."""
    v = _verweise_auf_profile()
    assert {"user_id"} <= v["turns"] and {"user_id"} <= v["sessions"] and {"user_id"} <= v["vorgaenge"]
    assert {"besitzer", "empfaenger", "widerrufen_von"} <= v["teilungen"]  # create table mit mehreren Spalten
    assert {"user_id", "freigegeben_von", "geaendert_von"} <= v["devices"]  # nur per alter table add constraint foreign key
    assert "bearbeitet_von" in v["canvas_items"]  # nur per add column
    assert {"user_id"} <= v["model_access"] and {"profil_id"} <= v["rd_sitzungen"] and {"akteur"} <= v["release_ereignisse"]
    assert "reviewed_by" in v["memories"] and "user_id" in v["profiles"]
    assert len(v) >= 18


def test_jede_tabelle_mit_besitzerspalte_steht_in_der_liste_oder_der_ausnahmeliste():
    loesch = {o.tabelle: o for o in ll.LOESCHLISTE}
    fehlend: list[str] = []
    for tabelle, spalten in _verweise_auf_profile().items():
        if tabelle in ll.AUSNAHMEN:
            continue
        if tabelle not in loesch:
            fehlend.append(f"{tabelle} ({', '.join(sorted(spalten))}) steht weder in LOESCHLISTE noch in AUSNAHMEN")
            continue
        for spalte in sorted(spalten):
            if spalte not in loesch[tabelle].besitzer and (tabelle, spalte) not in ll.FREMDE_VERWEISE:
                fehlend.append(f"{tabelle}.{spalte} ist weder Besitzerspalte der Liste noch in FREMDE_VERWEISE")
    assert not fehlend, "\n".join(fehlend)


def test_die_liste_enthaelt_keine_tippfehler_und_keine_veralteten_eintraege():
    verweise = _verweise_auf_profile()
    for o in ll.LOESCHLISTE:
        assert o.tabelle in verweise, o.tabelle
        assert set(o.besitzer) <= verweise[o.tabelle], (o.tabelle, o.besitzer)
        assert o.besitzer and o.zeit and o.inhalt
    for tabelle in ll.AUSNAHMEN:
        assert tabelle in verweise, tabelle
        assert ll.AUSNAHMEN[tabelle].strip(), tabelle
    for (tabelle, spalte), grund in ll.FREMDE_VERWEISE.items():
        assert spalte in verweise.get(tabelle, set()), (tabelle, spalte)
        assert grund.strip()
        assert tabelle not in ll.AUSNAHMEN
        assert all(spalte not in o.besitzer for o in ll.LOESCHLISTE if o.tabelle == tabelle), (tabelle, spalte)
    assert not set(ll.AUSNAHMEN) & {o.tabelle for o in ll.LOESCHLISTE}
    assert len(set(ll.tabellen())) == len(ll.LOESCHLISTE)


def test_die_zeit_und_sitzungsspalten_gibt_es_in_den_migrationen():
    tabellen: dict[str, str] = {}
    for datei in sorted(MIGRATIONEN.glob("*.sql")):
        if datei.name.endswith(".down.sql"):
            continue
        text = _sql_ohne_kommentare(datei.read_text(encoding="utf-8"))
        for treffer in re.finditer(r"create table(?: if not exists)? __SCHEMA__\.(\w+) \((.*?)\n\);", text, re.S):
            tabellen[treffer.group(1)] = treffer.group(2)
    for o in ll.LOESCHLISTE:
        koerper = tabellen[o.tabelle]
        assert re.search(rf"^\s*{o.zeit}\s+(timestamptz|\S+)\s.*not null|^\s*{o.zeit}\s+timestamptz\b", koerper, re.M), (o.tabelle, o.zeit)
        if o.sitzung:
            assert re.search(rf"^\s*{o.sitzung}\s+uuid\b.*references __SCHEMA__\.sessions\(id\)", koerper, re.M), (o.tabelle, o.sitzung)


def test_neue_besitzernamen_ohne_fremdschluessel_fallen_auf():
    """Eine Spalte user_id, besitzer, empfaenger oder profil_id in einer Tabelle ohne Verweis auf ein Profil wäre ein Besitzer, den die
    Löschfunktion nicht kennt. Heute gibt es keine."""
    mit_verweis = _verweise_auf_profile()
    ohne = {t: sorted(s - mit_verweis.get(t, set())) for t, s in _tabellen_mit_besitzernamen().items() if s - mit_verweis.get(t, set())}
    assert not ohne, ohne


# ---------- 2. Verwaltung gegen die Attrappe ----------

A, B, C = "00000000-0000-4000-8000-0000000018a1", "00000000-0000-4000-8000-0000000018b1", "00000000-0000-4000-8000-0000000018c0"
JETZT = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)


class LaborDb:
    """Attrappe der Datenbank für labor_loeschen und labor_zaehlen. Sie bildet die Semantik aus der Löschliste nach: Besitzerspalten,
    Zeitfenster, Zeilen der Sitzungen im Fenster, Zugangsprüfungen. Die echte Funktion prüft supabase/tests/0018_labor_profile_test.sql."""

    configured = True
    schema = "wamo_test"

    def __init__(self):
        self.profile: dict[str, dict] = {}
        self.zeilen: dict[str, list[dict]] = {o.tabelle: [] for o in ll.LOESCHLISTE}
        self.aufrufe: list[tuple[str, tuple]] = []
        self.geschlossen = False
        self._n = 0

    def t(self, name: str) -> str:
        return f"{self.schema}.{name}"

    def profil(self, uid: str, labor_art: str | None = "platz", labor_aktiv: bool = False) -> None:
        self.profile[uid] = {"labor_art": labor_art, "labor_aktiv": labor_aktiv}

    def fuellen(self, uid: str, start: datetime, *, zeuge: str = "zeuge") -> None:
        """Eine Zeile je Tabelle der Liste, alle zur Sitzung dieses Durchlaufs gehörend."""
        self._n += 1
        sitzung = f"sitzung-{self._n}"
        for o in ll.LOESCHLISTE:
            zeile: dict = {"id": f"{o.tabelle}-{self._n}", o.zeit: start + timedelta(minutes=1)}
            for spalte in o.besitzer:
                zeile[spalte] = uid
            if o.tabelle == "teilungen":
                zeile["empfaenger"] = zeuge
            if o.tabelle == "teilung_events":
                zeile["empfaenger"] = zeuge
            if o.sitzung:
                zeile[o.sitzung] = sitzung
            if o.tabelle == "sessions":
                zeile.update(id=sitzung, started_at=start, ended_at=start + timedelta(minutes=10))
            self.zeilen[o.tabelle].append(zeile)

    def anzahl(self, uid: str) -> dict[str, int]:
        return {o.tabelle: sum(1 for z in self.zeilen[o.tabelle] if any(z.get(s) == uid for s in o.besitzer)) for o in ll.LOESCHLISTE}

    async def fetchval(self, sql: str, *args):
        self.aufrufe.append((sql, args))
        uid = args[0]
        if "labor_zaehlen" in sql:
            return self._json(self._zaehlen(uid))
        if "labor_loeschen" in sql:
            return self._json(self._loeschen(uid, args[1], args[2]))
        raise AssertionError(f"unerwartete Anweisung: {sql}")

    @staticmethod
    def _json(werte: dict) -> str:
        import json

        return json.dumps(werte)  # asyncpg liefert jsonb als Text

    async def close(self) -> None:
        self.geschlossen = True

    # --- die Semantik der beiden Funktionen ---
    def _laborprofil(self, uid: str) -> dict:
        p = self.profile.get(uid)
        if p is None or p["labor_art"] is None:
            raise asyncpg.InsufficientPrivilegeError("kein Laborprofil")
        return p

    def _zaehlen(self, uid: str) -> dict[str, int]:
        self._laborprofil(uid)
        return self.anzahl(uid)

    def _loeschen(self, uid: str, ab: datetime | None, bis: datetime | None) -> dict[str, int]:
        if ab is not None and bis is not None and ab > bis:
            raise asyncpg.InvalidParameterValueError("p_ab liegt nach p_bis")
        p = self._laborprofil(uid)
        if p["labor_aktiv"]:
            raise asyncpg.ObjectNotInPrerequisiteStateError("noch freigeschaltet")
        grenze = JETZT - timedelta(minutes=ll.SITZUNG_MAX_MINUTEN)
        for s in self.zeilen["sessions"]:
            if s["user_id"] == uid and s.get("ended_at") is None and s["started_at"] > grenze:
                raise asyncpg.ObjectNotInPrerequisiteStateError("laufende Sitzung")
        voll = ab is None and bis is None

        def im_fenster(wert: datetime) -> bool:
            return voll or ((ab is None or wert >= ab) and (bis is None or wert <= bis))

        sitzungen = {s["id"] for s in self.zeilen["sessions"] if s["user_id"] == uid and im_fenster(s["started_at"])}
        ergebnis: dict[str, int] = {}
        for o in ll.LOESCHLISTE:
            behalten, weg = [], 0
            for z in self.zeilen[o.tabelle]:
                gehoert = any(z.get(s) == uid for s in o.besitzer)
                if o.tabelle == "sessions":
                    treffer = gehoert and (voll or z["id"] in sitzungen)
                else:
                    treffer = gehoert and (im_fenster(z[o.zeit]) or (o.sitzung is not None and z.get(o.sitzung) in sitzungen))
                if treffer:
                    weg += 1
                else:
                    behalten.append(z)
            self.zeilen[o.tabelle] = behalten
            ergebnis[o.tabelle] = weg
        return ergebnis


@pytest.fixture
def db():
    d = LaborDb()
    d.profil(A)
    d.profil(B, "schaufenster")
    d.profil(C, None)  # ein gewöhnliches Mitglied
    d.fuellen(A, JETZT - timedelta(hours=3))
    d.fuellen(B, JETZT - timedelta(hours=5))
    d.fuellen(B, JETZT - timedelta(hours=1))
    d.fuellen(C, JETZT - timedelta(hours=2))
    return d


async def test_nach_dem_loeschen_ist_nichts_mehr_da_und_andere_bleiben(db):
    vor_b, vor_c = db.anzahl(B), db.anzahl(C)
    assert all(n >= 1 for n in db.anzahl(A).values())  # jede Tabelle der Liste war gefüllt
    ergebnis = await lv.loeschen(db, A)
    assert set(ergebnis.geloescht) == set(ll.tabellen()) and all(n >= 1 for n in ergebnis.geloescht.values())
    assert all(n == 0 for n in db.anzahl(A).values())
    assert db.anzahl(B) == vor_b and db.anzahl(C) == vor_c
    assert ergebnis.rest == {t: 0 for t in ll.tabellen()}


async def test_loeschen_prueft_selbst_dass_danach_nichts_mehr_da_ist(db):
    """Löscht die Funktion zu wenig (etwa eine neue Tabelle fehlt in der Funktion), schlägt die Verwaltung an, statt "fertig" zu melden."""
    original = db._loeschen

    def lueckenhaft(uid, ab, bis):
        ergebnis = original(uid, ab, bis)
        db.zeilen["turns"].append({"id": "t-neu", "user_id": uid, "created_at": JETZT, "session_id": "x"})  # blieb liegen
        return ergebnis

    db._loeschen = lueckenhaft
    with pytest.raises(lv.LaborFehler, match="noch Zeilen"):
        await lv.loeschen(db, A)


async def test_zeitfenster_loescht_nur_den_durchlauf(db):
    ergebnis = await lv.loeschen(db, B, ab=JETZT - timedelta(hours=6), bis=JETZT - timedelta(hours=4))
    assert ergebnis.rest is None  # bei einem Fenster bleibt der Rest berechtigt stehen
    assert all(n == 1 for n in db.anzahl(B).values())  # der zweite Durchlauf ist unberührt
    assert all(n == 1 for n in ergebnis.geloescht.values())


async def test_zeitfenster_nur_mit_ab_oder_nur_mit_bis(db):
    await lv.loeschen(db, B, ab=JETZT - timedelta(hours=2))  # alles ab vor zwei Stunden: der zweite Durchlauf
    assert all(n == 1 for n in db.anzahl(B).values())
    await lv.loeschen(db, B, bis=JETZT - timedelta(hours=2))  # alles bis vor zwei Stunden: der erste
    assert all(n == 0 for n in db.anzahl(B).values())


async def test_zeilen_der_sitzung_gehen_mit_auch_wenn_sie_ausserhalb_des_fensters_liegen(db):
    """Eine Sitzung im Fenster nimmt alle ihre Zeilen mit, auch spätere (zum Beispiel ein Canvas nach dem Ende)."""
    db.zeilen["canvas_items"].append({"id": "spaet", "user_id": B, "created_at": JETZT, "session_id": "sitzung-2"})
    await lv.loeschen(db, B, ab=JETZT - timedelta(hours=6), bis=JETZT - timedelta(hours=4))
    assert not [z for z in db.zeilen["canvas_items"] if z["id"] == "spaet"]


@pytest.mark.parametrize("uid", [C, "00000000-0000-4000-8000-0000000018ff"])
async def test_ein_profil_ohne_labor_art_wird_nie_angefasst(db, uid):
    vor = {t: list(z) for t, z in db.zeilen.items()}
    with pytest.raises(lv.LaborFehler, match="Laborprofil"):
        await lv.loeschen(db, uid)
    with pytest.raises(lv.LaborFehler, match="Laborprofil"):
        await lv.zaehlen(db, uid)
    assert db.zeilen == vor


async def test_freigeschaltetes_laborprofil_wird_nicht_geloescht(db):
    db.profil(A, "platz", labor_aktiv=True)
    vor = db.anzahl(A)
    with pytest.raises(lv.LaborFehler, match="freigeschaltet"):
        await lv.loeschen(db, A)
    assert db.anzahl(A) == vor


async def test_laufende_sitzung_blockiert_und_eine_abgestuerzte_nicht(db):
    db.zeilen["sessions"].append({"id": "laeuft", "user_id": A, "started_at": JETZT - timedelta(minutes=20), "ended_at": None})
    with pytest.raises(lv.LaborFehler, match="Sitzung"):
        await lv.loeschen(db, A)
    db.zeilen["sessions"][-1]["started_at"] = JETZT - timedelta(minutes=ll.SITZUNG_MAX_MINUTEN + 1)  # abgestürzt, ohne Ende
    ergebnis = await lv.loeschen(db, A)
    assert ergebnis.geloescht["sessions"] == 2 and all(n == 0 for n in db.anzahl(A).values())


async def test_verkehrtes_fenster_und_ungueltige_kennung(db):
    with pytest.raises(lv.LaborFehler, match="Fenster|nach"):
        await lv.loeschen(db, A, ab=JETZT, bis=JETZT - timedelta(hours=1))
    with pytest.raises(lv.LaborFehler, match="UUID"):
        await lv.loeschen(db, "kein-uuid")
    with pytest.raises(lv.LaborFehler, match="UUID"):
        await lv.zaehlen(db, "'; drop table x; --")
    assert db.aufrufe == []  # eine ungültige Kennung erreicht die Datenbank nie


async def test_zaehlen_veraendert_nichts_und_nennt_nur_zahlen(db):
    vor = {t: list(z) for t, z in db.zeilen.items()}
    zahlen = await lv.zaehlen(db, A)
    assert set(zahlen) == set(ll.tabellen()) and all(isinstance(n, int) for n in zahlen.values())
    assert db.zeilen == vor
    assert "labor_zaehlen" in db.aufrufe[0][0] and "labor_loeschen" not in db.aufrufe[0][0]


async def test_ergebnis_mit_fehlender_oder_fremder_tabelle_wird_abgelehnt(db):
    async def zuwenig(sql, *args):
        return '{"turns": 1}'

    db.fetchval = zuwenig
    with pytest.raises(lv.LaborFehler, match="Tabellen"):
        await lv.zaehlen(db, A)

    async def fremd(sql, *args):
        return '{' + ", ".join(f'"{t}": 0' for t in (*ll.tabellen(), "neue_tabelle")) + '}'

    db.fetchval = fremd
    with pytest.raises(lv.LaborFehler, match="Tabellen"):
        await lv.zaehlen(db, A)


async def test_aufruf_geht_nur_an_die_funktionen_in_dem_schema_und_ohne_werte_im_sql(db):
    await lv.loeschen(db, A, ab=JETZT - timedelta(hours=9))
    for sql, args in db.aufrufe:
        assert re.fullmatch(r"select wamo_test\.labor_(loeschen|zaehlen)\(\$1::uuid(, \$2::timestamptz, \$3::timestamptz)?\)", sql), sql
        assert A not in sql
    assert not re.search(r"\b(delete|update|insert|truncate)\b", " ".join(s for s, _ in db.aufrufe), re.I)  # nie eine eigene Löschung


# ---------- 3. Anbindung an wamo_admin.py (Unterbefehle labor-zaehlen und labor-loeschen) ----------

def _admin(monkeypatch, db: LaborDb):
    import wamo_admin

    monkeypatch.setattr(wamo_admin, "admin_database", lambda: db)
    monkeypatch.setenv("WAMO_ADMIN_DATABASE_URL", "postgres://unbenutzt")
    monkeypatch.setattr(wamo_admin, "_load_env", lambda: None)
    return wamo_admin


def test_wamo_admin_labor_loeschen_nutzt_die_verwaltungsverbindung(monkeypatch, db, capsys):
    admin = _admin(monkeypatch, db)
    code = admin.main(["labor-loeschen", "--user-id", A])
    aus = capsys.readouterr().out
    assert code == 0 and "turns" in aus and "nichts mehr da" in aus
    assert all(n == 0 for n in db.anzahl(A).values()) and db.geschlossen


def test_wamo_admin_labor_zaehlen_und_fehlerfall(monkeypatch, db, capsys):
    admin = _admin(monkeypatch, db)
    assert admin.main(["labor-zaehlen", "--user-id", A]) == 0
    assert "sessions" in capsys.readouterr().out
    assert admin.main(["labor-loeschen", "--user-id", C]) == 1  # gewöhnliches Mitglied
    assert "Laborprofil" in capsys.readouterr().err
    assert all(n >= 1 for n in db.anzahl(C).values())


def test_wamo_admin_labor_loeschen_mit_fenster(monkeypatch, db, capsys):
    admin = _admin(monkeypatch, db)
    code = admin.main(["labor-loeschen", "--user-id", B, "--ab", (JETZT - timedelta(hours=6)).isoformat(),
                       "--bis", (JETZT - timedelta(hours=4)).isoformat()])
    assert code == 0 and all(n == 1 for n in db.anzahl(B).values())
    assert admin.main(["labor-loeschen", "--user-id", B, "--ab", "2026-11-28 09:00"]) == 1  # ohne Zeitzone
    assert "Zeitzone" in capsys.readouterr().err


def test_kein_text_aus_den_zeilen_im_ergebnis():
    """Die Funktionen liefern nur Zahlen je Tabelle; das Ergebnis trägt nie Gesprächstext."""
    ergebnis = lv.LoeschErgebnis(geloescht={t: 1 for t in ll.tabellen()}, rest={t: 0 for t in ll.tabellen()})
    assert all(isinstance(n, int) for n in ergebnis.geloescht.values())
    assert "Zeilen" in ergebnis.zusammenfassung() or "gelöscht" in ergebnis.zusammenfassung()
    assert not any(ord(c) > 0x2000 and c in "–—" for c in ergebnis.zusammenfassung())  # keine Gedankenstriche
