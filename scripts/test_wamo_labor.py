"""Tests für scripts/wamo_labor.py (Laborprofile verwalten: anlegen, aktivieren, sperren, status, loeschen).

Aufruf (aus voice-core, dort liegt die Python-Umgebung):  python -m pytest ../scripts/test_wamo_labor.py
Kein ssh, kein Server, keine Datenbank: der Weg zum Server (run_remote) wird durch eine Attrappe ersetzt, und das Programm, das auf dem
Server läuft (REMOTE), wird in-process mit einer Attrappe für psql und GoTrue ausgeführt.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import types
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wamo_labor as wl  # noqa: E402

WURZEL = Path(__file__).resolve().parent.parent
UID = "00000000-0000-4000-8000-00000000b001"
PASSWORT_MARKER = "PASSWORT_NOETIG"
DB_PASSWORT = "DBPASSWORT-nie-ausgeben-0123456789"
ADMIN_DSN = f"postgresql://wamo_admin.wamo:{DB_PASSWORT}@db.invalid:55322/postgres?sslmode=verify-full"


@pytest.fixture(autouse=True)
def verwaltungszugang(monkeypatch, tmp_path):
    """Die Anmeldung der Rolle wamo_admin kommt aus WAMO_ADMIN_DATABASE_URL; die echten .env-Dateien werden nie gelesen."""
    monkeypatch.setenv(wl.ADMIN_DSN_ENV, ADMIN_DSN)
    monkeypatch.setattr(wl, "ENV_DATEIEN", (tmp_path / "keine-voice-core.env", tmp_path / "keine-wurzel.env"))


# ---------- Hilfen ----------

class Server:
    """Attrappe für den ssh-Weg: merkt sich jede Nutzlast und antwortet nach einem Drehbuch."""

    def __init__(self, *antworten):
        self.nutzlasten: list[dict] = []
        self.antworten = list(antworten)

    def __call__(self, host: str, nutzlast: dict):
        self.nutzlasten.append(dict(nutzlast))
        antwort = self.antworten.pop(0) if self.antworten else (0, "ok", "")
        return wl.Ergebnis(*antwort)


def lauf(argv, server=None):
    ausgabe, fehler = io.StringIO(), io.StringIO()
    with redirect_stdout(ausgabe), redirect_stderr(fehler):
        try:
            code = wl.main(argv, remote=server or Server())
        except SystemExit as e:
            code = e.code
    return code, ausgabe.getvalue(), fehler.getvalue()


# ---------- Kennung, E-Mail, Passwort ----------

@pytest.mark.parametrize("kennung,art,platz", [
    ("labor-01", "platz", 1), ("labor-08", "platz", 8), ("labor-09", "platz", 9), ("labor-16", "platz", 16),
    ("labor-schau", "schaufenster", None),
])
def test_kennung_gueltig(kennung, art, platz):
    assert wl.kennung_pruefen(kennung) == (art, platz)


@pytest.mark.parametrize("kennung", [
    "labor-00", "labor-17", "labor-1", "labor-001", "LABOR-01", "labor-01 ", " labor-01", "labor-schau2", "admin", "",
    "labor-01; drop table x", "labor-0١", "labor-überall", "labor-01\n", "../labor-01",
])
def test_kennung_ungueltig(kennung):
    with pytest.raises(wl.Fehler):
        wl.kennung_pruefen(kennung)


def test_email_liegt_unter_der_testdomain():
    assert wl.email_fuer("labor-03") == "labor-03@wamo.test"
    assert wl.email_fuer("labor-schau") == "labor-schau@wamo.test"


def test_passwort_ist_lang_zufaellig_und_urlsicher():
    a, b = wl.passwort_erzeugen(), wl.passwort_erzeugen()
    assert a != b and len(a) == 24 and len(b) == 24
    assert re.fullmatch(r"[A-Za-z0-9_-]{24}", a)


# ---------- Das SQL: nur die Verwaltungsrolle schreibt, als Login-Rolle und nie per SET ROLE (F1) ----------

def test_kein_sql_nimmt_per_set_role_eine_rolle_an():
    """postgres darf im Supabase-Image keine Rolle per SET ROLE annehmen (kein Superuser, kein SET auf wamo_admin)."""
    for name, sql in wl.SQL.items():
        assert not re.search(r"\b(set\s+(local\s+|session\s+)?role|reset\s+role|set\s+session\s+authorization)\b", sql, re.I), name


def test_nur_die_kontosuche_laeuft_als_betriebsrolle_und_sie_liest_nur():
    assert wl.ALS_POSTGRES == ("konto_suchen",)
    assert wl.SQL["konto_suchen"].lstrip().lower().startswith("select id from auth.users"), "nur lesen, nur die Kennung des Kontos"


class Lauf:
    """Attrappe für subprocess.run (im Programm auf dem Server und im Aufruf von ssh): merkt sich Befehl, Umgebung und Eingabe."""

    def __init__(self, stdout: str = "ok\n", stderr: str = "", returncode: int = 0):
        self.aufrufe: list[tuple[list[str], dict | None, str | None]] = []
        self._antwort = types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    def __call__(self, cmd, **kw):
        self.aufrufe.append((list(cmd), kw.get("env"), kw.get("input")))
        return self._antwort


def sql_lauf(monkeypatch, anweisung: str, **vars):
    """Führt eine Anweisung des Serverprogramms mit der Attrappe aus und gibt (Befehl, Umgebung, Eingabe) zurück."""
    ns = remote_namensraum()
    lauf_ = Lauf()
    monkeypatch.setattr(ns["subprocess"], "run", lauf_)
    ns["sql_ausfuehren"](wl.SQL[anweisung], DB_PASSWORT, **vars)
    assert len(lauf_.aufrufe) == 1
    return lauf_.aufrufe[0]


def test_jede_anweisung_ausser_der_kontosuche_laeuft_als_login_rolle_wamo_admin(monkeypatch):
    for name in wl.SQL:
        if name in wl.ALS_POSTGRES:
            continue
        cmd, env, eingabe = sql_lauf(monkeypatch, name, schema="wamo_dev", uid=UID, art="platz", platz="3", name="Gast", ab="", bis="")
        assert cmd[cmd.index("-U") + 1] == "wamo_admin", name
        assert cmd[cmd.index("-h") + 1] == "127.0.0.1", name  # Anmeldung über TCP, nicht über die Betriebsanmeldung des Sockets
        assert eingabe == wl.SQL[name], name


def test_die_kontosuche_laeuft_als_postgres_ohne_das_passwort_der_verwaltungsrolle(monkeypatch):
    cmd, env, _ = sql_lauf(monkeypatch, "konto_suchen", email="labor-03@wamo.test")
    assert cmd[cmd.index("-U") + 1] == "postgres" and "PGPASSWORD" not in cmd
    assert env is None or "PGPASSWORD" not in env


def test_das_passwort_geht_nur_ueber_die_umgebung_von_docker_und_nie_in_die_argumente(monkeypatch):
    cmd, env, eingabe = sql_lauf(monkeypatch, "status", schema="wamo_dev")
    assert cmd[cmd.index("-e") + 1] == "PGPASSWORD"  # ohne Wert: docker nimmt ihn aus der Umgebung des Aufrufers
    assert env["PGPASSWORD"] == DB_PASSWORT
    assert DB_PASSWORT not in " ".join(cmd) and DB_PASSWORT not in eingabe


def test_ohne_passwort_der_verwaltungsrolle_wird_nichts_ausgefuehrt(monkeypatch):
    ns = remote_namensraum()
    lauf_ = Lauf()
    monkeypatch.setattr(ns["subprocess"], "run", lauf_)
    with pytest.raises(ns["Fehler"]):
        ns["sql_ausfuehren"](wl.SQL["status"], None, schema="wamo_dev")
    assert lauf_.aufrufe == []


def test_die_anmeldung_kommt_aus_der_dsn_der_verwaltungsrolle():
    assert wl.admin_passwort() == DB_PASSWORT


def test_ohne_dsn_oder_mit_der_dsn_einer_anderen_rolle_bricht_das_skript_ab_ohne_den_wert_zu_nennen(monkeypatch):
    monkeypatch.delenv(wl.ADMIN_DSN_ENV)
    with pytest.raises(wl.Fehler, match="WAMO_ADMIN_DATABASE_URL"):
        wl.admin_passwort()
    kern = "GEHEIMKERN123"
    for dsn in (f"postgresql://wamo_core.wamo:{kern}@db.invalid:55322/postgres", f"postgresql://postgres:{kern}@db.invalid/postgres",
                "postgresql://wamo_admin.wamo@db.invalid/postgres", "kein-dsn"):
        monkeypatch.setenv(wl.ADMIN_DSN_ENV, dsn)
        with pytest.raises(wl.Fehler) as e:
            wl.admin_passwort()
        assert kern not in str(e.value) and "db.invalid" not in str(e.value), dsn


def test_die_dsn_darf_auch_in_der_env_datei_stehen_die_umgebung_hat_vorrang(monkeypatch, tmp_path):
    voice_core, wurzel = tmp_path / "vc.env", tmp_path / "root.env"
    voice_core.write_text("# Kommentar\nWAMO_DATABASE_URL=postgresql://wamo_core.wamo:x@h/p\n", encoding="utf-8")
    wurzel.write_text(f"export {wl.ADMIN_DSN_ENV}='postgresql://wamo_admin.wamo:AUSDATEI@h/p'\n", encoding="utf-8")
    monkeypatch.setattr(wl, "ENV_DATEIEN", (voice_core, wurzel))
    assert wl.admin_passwort() == DB_PASSWORT  # die Umgebung (Fixture) gewinnt
    monkeypatch.delenv(wl.ADMIN_DSN_ENV)
    assert wl.admin_passwort() == "AUSDATEI"


def test_run_remote_sendet_das_passwort_nur_ueber_stdin_und_gibt_es_nie_aus(monkeypatch):
    lauf_ = Lauf(stdout=f"Treffer {DB_PASSWORT}\n", stderr=f"Fehler {DB_PASSWORT}", returncode=1)
    monkeypatch.setattr(wl.subprocess, "run", lauf_)
    ergebnis = wl.run_remote("wamo-sokrates2", {"aktion": "status", "schema": "wamo_dev", "ausfuehren": False})
    cmd, _, eingabe = lauf_.aufrufe[0]
    assert DB_PASSWORT not in " ".join(cmd)
    assert json.loads(eingabe)["db_passwort"] == DB_PASSWORT
    assert DB_PASSWORT not in ergebnis.stdout and DB_PASSWORT not in ergebnis.stderr and ergebnis.code == 1


def test_ohne_dsn_kein_aufruf_des_servers_und_eine_klare_meldung(monkeypatch):
    monkeypatch.delenv(wl.ADMIN_DSN_ENV)
    lauf_ = Lauf()
    monkeypatch.setattr(wl.subprocess, "run", lauf_)
    code, aus, fehler = lauf(["sperren", "--kennung", "labor-03", "--ausfuehren"], wl.run_remote)
    assert code == 1 and lauf_.aufrufe == [] and "WAMO_ADMIN_DATABASE_URL" in fehler


def test_das_sql_schreibt_nie_etwas_ausser_dem_vorgesehenen():
    for name, sql in wl.SQL.items():
        kleinschrift = sql.lower()
        for verboten in ("service_role", "grant ", "revoke ", "drop ", "truncate", "alter ", "create ", "delete from", "rolle =",
                         "set aktiv", " aktiv = true", "reset role", "set role", "auth.users set", "update auth."):
            assert verboten not in kleinschrift, (name, verboten)
    # Schreibende Anweisungen: genau drei (anlegen, aktivieren, sperren) plus der Aufruf der Löschfunktion
    schreibend = {n for n, s in wl.SQL.items() if re.search(r"\b(insert|update)\b", s, re.I)}
    assert schreibend == {"anlegen", "aktivieren", "sperren", "sperren_alle"}
    for name in ("aktivieren", "sperren", "sperren_alle"):
        assert re.search(r"update :\"schema\"\.profiles set labor_aktiv = (true|false)\b", wl.SQL[name]), name
        assert "labor_art is not null" in wl.SQL[name] or "labor_art = :'art'" in wl.SQL[name], name  # nie ein gewöhnliches Profil
    anlegen = " ".join(wl.SQL["anlegen"].split())
    assert "'mitglied', false" in anlegen and "labor_aktiv" in anlegen and "false)" in anlegen
    assert "on conflict (user_id) do nothing" in anlegen  # ein vorhandenes Profil wird nie überschrieben
    assert "labor_loeschen(" in wl.SQL["loeschen"] and "labor_zaehlen(" in wl.SQL["zaehlen"]


def test_werte_gehen_nie_per_zeichenkette_ins_sql():
    for name, sql in wl.SQL.items():
        assert not re.search(r"%s|\{[a-z_]*\}|\bf\"", sql), name
        assert not re.search(r"\bwamo_(dev|test|prod)\b", sql), name  # das Schema kommt als :"schema"
        for treffer in re.findall(r"(?<!:):'?\"?([a-z_]+)", sql):
            assert treffer in {"schema", "uid", "email", "name", "art", "platz", "ab", "bis"}, (name, treffer)


# ---------- Trockenlauf ist der Standard ----------

@pytest.mark.parametrize("argv", [
    ["sperren", "--kennung", "labor-03"],
    ["sperren", "--alle"],
    ["aktivieren", "--kennung", "labor-03"],
    ["anlegen", "--kennung", "labor-03"],
])
def test_ohne_ausfuehren_wird_nichts_am_server_geaendert(argv):
    server = Server()
    code, aus, fehler = lauf(argv, server)
    assert code == 0 and server.nutzlasten == [], (aus, fehler)
    assert "Trockenlauf" in aus and "--ausfuehren" in aus


def test_mit_ausfuehren_geht_genau_eine_nutzlast_an_den_server():
    server = Server((0, "Laborprofil gesperrt.", ""))
    code, aus, _ = lauf(["sperren", "--kennung", "labor-03", "--ausfuehren", "--schema", "wamo_test"], server)
    assert code == 0 and "gesperrt" in aus
    assert server.nutzlasten == [{"aktion": "sperren", "schema": "wamo_test", "art": "platz", "platz": "3", "ausfuehren": True}]


def test_aktivieren_setzt_nur_labor_aktiv_und_nennt_es():
    server = Server((0, "Laborprofil freigeschaltet.", ""))
    code, aus, _ = lauf(["aktivieren", "--kennung", "labor-schau", "--ausfuehren"], server)
    assert code == 0
    assert server.nutzlasten[0]["aktion"] == "aktivieren" and server.nutzlasten[0]["art"] == "schaufenster"
    assert server.nutzlasten[0]["platz"] == ""


def test_status_liest_nur_und_braucht_kein_ausfuehren():
    server = Server((0, "labor-01 | platz | gesperrt | 0 Zeilen", ""))
    code, aus, _ = lauf(["status"], server)
    assert code == 0 and "labor-01" in aus
    assert server.nutzlasten == [{"aktion": "status", "schema": "wamo_dev", "ausfuehren": False}]


def test_schema_und_kennung_werden_vor_dem_server_geprueft():
    server = Server()
    assert lauf(["sperren", "--kennung", "labor-99", "--ausfuehren"], server)[0] != 0
    assert lauf(["sperren", "--kennung", "labor-01", "--schema", "public", "--ausfuehren"], server)[0] != 0
    assert lauf(["sperren", "--ausfuehren"], server)[0] != 0  # weder Kennung noch --alle
    assert lauf(["sperren", "--alle", "--kennung", "labor-01", "--ausfuehren"], server)[0] != 0
    assert server.nutzlasten == []


# ---------- loeschen ----------

def test_loeschen_im_trockenlauf_zaehlt_nur_und_loescht_nichts():
    server = Server((0, "labor-03: turns 4, actions 2", ""))
    code, aus, _ = lauf(["loeschen", "--kennung", "labor-03"], server)
    assert code == 0 and "Trockenlauf" in aus and "labor-03" in aus
    assert server.nutzlasten[0]["aktion"] == "zaehlen" and server.nutzlasten[0]["ausfuehren"] is False


def test_loeschen_mit_ausfuehren_und_fenster():
    server = Server((0, "labor-03: gelöscht", ""))
    ab, bis = "2026-11-28T09:00:00+01:00", "2026-11-28T09:30:00+01:00"
    code, _, _ = lauf(["loeschen", "--kennung", "labor-03", "--ab", ab, "--bis", bis, "--ausfuehren"], server)
    assert code == 0
    assert server.nutzlasten[0] == {"aktion": "loeschen", "schema": "wamo_dev", "art": "platz", "platz": "3", "ausfuehren": True,
                                    "ab": ab, "bis": bis}


def test_loeschen_alle_hat_keine_kennung_und_kein_fenster():
    server = Server((0, "ok", ""))
    assert lauf(["loeschen", "--alle", "--ausfuehren"], server)[0] == 0
    assert server.nutzlasten[0]["aktion"] == "loeschen" and server.nutzlasten[0]["alle"] is True
    assert "ab" not in server.nutzlasten[0] and "platz" not in server.nutzlasten[0]
    assert lauf(["loeschen", "--alle", "--ab", "2026-11-28T09:00:00+01:00", "--ausfuehren"], server)[0] != 0


@pytest.mark.parametrize("ab,bis", [
    ("2026-11-28 09:00", None),  # ohne Zeitzone
    ("gestern", None), ("2026-11-28T09:00:00+01:00", "2026-11-28T08:00:00+01:00"),  # verkehrt herum
])
def test_loeschen_fenster_muss_eindeutig_sein(ab, bis):
    argv = ["loeschen", "--kennung", "labor-03", "--ab", ab, "--ausfuehren"] + (["--bis", bis] if bis else [])
    server = Server()
    assert lauf(argv, server)[0] != 0 and server.nutzlasten == []


# ---------- anlegen: das Passwort geht nie auf den Bildschirm ----------

def test_anlegen_schreibt_das_passwort_in_die_umschlagdatei_und_nie_auf_den_bildschirm(tmp_path, monkeypatch):
    datei = tmp_path / "umschlag.txt"
    server = Server((10, PASSWORT_MARKER, ""), (0, "Laborprofil labor-03 angelegt.", ""))
    code, aus, fehler = lauf(["anlegen", "--kennung", "labor-03", "--ausfuehren", "--umschlag-datei", str(datei)], server)
    assert code == 0, (aus, fehler)
    inhalt = datei.read_text(encoding="utf-8")
    passwort = server.nutzlasten[1]["password"]
    assert len(passwort) == 24 and passwort in inhalt and "labor-03@wamo.test" in inhalt
    assert passwort not in aus and passwort not in fehler
    assert "password" not in server.nutzlasten[0]  # der erste Aufruf fragt nur, ob ein Konto fehlt
    assert "Umschlag" in aus
    if os.name == "posix":
        assert (datei.stat().st_mode & 0o777) == 0o600


def test_anlegen_ohne_neues_konto_braucht_kein_passwort(tmp_path):
    datei = tmp_path / "umschlag.txt"
    server = Server((0, "Konto vorhanden. Laborprofil labor-03 besteht.", ""))
    code, _, _ = lauf(["anlegen", "--kennung", "labor-03", "--ausfuehren", "--umschlag-datei", str(datei)], server)
    assert code == 0 and len(server.nutzlasten) == 1 and not datei.exists()


def test_anlegen_verlangt_eine_umschlagdatei_ausserhalb_des_repos():
    server = Server()
    assert lauf(["anlegen", "--kennung", "labor-03", "--ausfuehren"], server)[0] != 0
    im_repo = WURZEL / "docs" / "umschlag.txt"
    assert lauf(["anlegen", "--kennung", "labor-03", "--ausfuehren", "--umschlag-datei", str(im_repo)], server)[0] != 0
    assert server.nutzlasten == [] and not im_repo.exists()


def test_anlegen_mehrere_kennungen_je_ein_aufruf(tmp_path):
    datei = tmp_path / "umschlag.txt"
    server = Server(*([(0, "ok", "")] * 3))
    code, _, _ = lauf(["anlegen", "--kennung", "labor-01", "labor-02", "labor-schau", "--ausfuehren",
                       "--umschlag-datei", str(datei)], server)
    assert code == 0 and [n["art"] for n in server.nutzlasten] == ["platz", "platz", "schaufenster"]
    assert [n.get("email") for n in server.nutzlasten] == ["labor-01@wamo.test", "labor-02@wamo.test", "labor-schau@wamo.test"]


def test_fehler_des_servers_geben_einen_fehlercode_und_keinen_geheimtext(tmp_path):
    server = Server((1, "", "Datenbankfehler: das Laborprofil ist noch freigeschaltet"))
    code, _, fehler = lauf(["loeschen", "--kennung", "labor-03", "--ausfuehren"], server)
    assert code != 0 and "freigeschaltet" in fehler


# ---------- Das Programm auf dem Server (REMOTE), in-process mit Attrappen ----------

class Psql:
    """Attrappe für psql auf dem Server. Eine Antwort der Form ("fehler", text) löst den Fehler des Serverprogramms aus."""

    def __init__(self, antworten: dict[str, list | str]):
        self.antworten = {k: (list(v) if isinstance(v, list) else [v]) for k, v in antworten.items()}
        self.aufrufe: list[tuple[str, dict]] = []
        self.fehlerklasse = RuntimeError  # wird von verarbeite() auf die Klasse des Serverprogramms gesetzt

    def __call__(self, sql: str, **vars):
        name = next(n for n, s in wl.SQL.items() if s == sql)
        self.aufrufe.append((name, vars))
        antwort = self.antworten.get(name, [""])
        wert = antwort.pop(0) if len(antwort) > 1 else antwort[0]
        if isinstance(wert, tuple) and wert[0] == "fehler":
            raise self.fehlerklasse(wert[1])
        return wert


def remote_namensraum():
    ns: dict = {"__name__": "remote_test"}
    exec(compile(wl.remote_programm(), "<remote>", "exec"), ns)  # noqa: S102 (das ist das Programm, das auf dem Server läuft)
    return ns


def verarbeite(d: dict, psql: Psql, konto=None, warte=None):
    ns = remote_namensraum()
    psql.fehlerklasse = ns["Fehler"]
    ausgabe = io.StringIO()
    with redirect_stdout(ausgabe), redirect_stderr(ausgabe):
        code = ns["verarbeite"](d, psql, konto or (lambda email, passwort: UID), warte or (lambda sekunden: None))
    return code, ausgabe.getvalue()


def test_remote_anlegen_ohne_konto_fragt_nach_dem_passwort():
    psql = Psql({"konto_suchen": ""})
    code, aus = verarbeite({"aktion": "anlegen", "schema": "wamo_dev", "email": "labor-03@wamo.test", "art": "platz", "platz": "3",
                            "ausfuehren": True}, psql)
    assert code == 10 and PASSWORT_MARKER in aus
    assert [n for n, _ in psql.aufrufe] == ["konto_suchen"]  # noch nichts geschrieben


def test_remote_anlegen_legt_konto_und_profil_an_und_prueft_das_ergebnis():
    konten: list[tuple] = []
    psql = Psql({"konto_suchen": "", "anlegen": "INSERT", "pruefen": "platz|3|false|false"})
    code, aus = verarbeite({"aktion": "anlegen", "schema": "wamo_dev", "email": "labor-03@wamo.test", "art": "platz", "platz": "3",
                            "password": "pw" * 12, "ausfuehren": True}, psql,
                           konto=lambda email, passwort: konten.append((email, passwort)) or UID)
    assert code == 0 and konten == [("labor-03@wamo.test", "pw" * 12)]
    schritte = [n for n, _ in psql.aufrufe]
    assert schritte == ["konto_suchen", "anlegen", "pruefen"]
    angelegt = dict(psql.aufrufe[1][1])
    assert angelegt == {"schema": "wamo_dev", "uid": UID, "name": "Gast", "art": "platz", "platz": "3"}
    assert "pw" * 12 not in aus


def test_remote_anlegen_bricht_ab_wenn_das_konto_zu_einem_anderen_profil_gehoert():
    psql = Psql({"konto_suchen": UID, "anlegen": "", "pruefen": "|||"})
    code, aus = verarbeite({"aktion": "anlegen", "schema": "wamo_dev", "email": "labor-03@wamo.test", "art": "platz", "platz": "3",
                            "ausfuehren": True}, psql)
    assert code == 1 and "passt nicht" in aus


def test_remote_anlegen_mit_falschem_platz_im_vorhandenen_profil_bricht_ab():
    psql = Psql({"konto_suchen": UID, "anlegen": "", "pruefen": "platz|7|false|false"})
    code, aus = verarbeite({"aktion": "anlegen", "schema": "wamo_dev", "email": "labor-03@wamo.test", "art": "platz", "platz": "3",
                            "ausfuehren": True}, psql)
    assert code == 1


def test_remote_aktivieren_und_sperren_nutzen_nur_ihr_sql():
    psql = Psql({"aktivieren": "1", "sperren": "1", "sperren_alle": "9"})
    assert verarbeite({"aktion": "aktivieren", "schema": "wamo_test", "art": "platz", "platz": "3", "ausfuehren": True}, psql)[0] == 0
    assert verarbeite({"aktion": "sperren", "schema": "wamo_test", "art": "platz", "platz": "3", "ausfuehren": True}, psql)[0] == 0
    assert verarbeite({"aktion": "sperren", "schema": "wamo_test", "alle": True, "ausfuehren": True}, psql)[0] == 0
    assert [n for n, _ in psql.aufrufe] == ["aktivieren", "sperren", "sperren_alle"]


def test_remote_aktivieren_meldet_wenn_kein_laborprofil_betroffen_ist():
    psql = Psql({"aktivieren": ""})
    code, aus = verarbeite({"aktion": "aktivieren", "schema": "wamo_dev", "art": "platz", "platz": "3", "ausfuehren": True}, psql)
    assert code == 1 and "kein laborprofil" in aus.lower()


def test_remote_ohne_ausfuehren_schreibt_nichts():
    psql = Psql({})
    for aktion in ("anlegen", "aktivieren", "sperren", "loeschen"):
        code, aus = verarbeite({"aktion": aktion, "schema": "wamo_dev", "email": "labor-03@wamo.test", "art": "platz", "platz": "3",
                                "ausfuehren": False}, psql)
        assert code == 0 and "Trockenlauf" in aus, aktion
    assert psql.aufrufe == []


def test_remote_loeschen_zaehlt_danach_und_verlangt_null():
    psql = Psql({"ids": f"labor-03|{UID}", "loeschen": '{"turns": 4, "actions": 2}',
                 "zaehlen": '{"turns": 0, "actions": 0}'})
    code, aus = verarbeite({"aktion": "loeschen", "schema": "wamo_dev", "art": "platz", "platz": "3", "ausfuehren": True}, psql)
    assert code == 0 and "labor-03" in aus and "turns 4" in aus
    assert [n for n, _ in psql.aufrufe] == ["ids", "loeschen", "zaehlen"]
    loeschen = dict(psql.aufrufe[1][1])
    assert loeschen["uid"] == UID and loeschen["ab"] == "" and loeschen["bis"] == ""


def test_remote_loeschen_schlaegt_an_wenn_danach_noch_etwas_da_ist():
    psql = Psql({"ids": f"labor-03|{UID}", "loeschen": '{"turns": 4}', "zaehlen": '{"turns": 1}'})
    code, aus = verarbeite({"aktion": "loeschen", "schema": "wamo_dev", "art": "platz", "platz": "3", "ausfuehren": True}, psql)
    assert code == 1 and "noch zeilen vorhanden" in aus.lower()


def test_remote_loeschen_mit_fenster_verlangt_nicht_null():
    psql = Psql({"ids": f"labor-03|{UID}", "loeschen": '{"turns": 4}', "zaehlen": '{"turns": 2}'})
    code, _ = verarbeite({"aktion": "loeschen", "schema": "wamo_dev", "art": "platz", "platz": "3", "ausfuehren": True,
                          "ab": "2026-11-28T09:00:00+01:00", "bis": "2026-11-28T09:30:00+01:00"}, psql)
    assert code == 0
    assert dict(psql.aufrufe[1][1])["ab"] == "2026-11-28T09:00:00+01:00"


def test_remote_loeschen_alle_geht_jedes_laborprofil_durch():
    psql = Psql({"ids_alle": f"labor-01|{UID}\nlabor-schau|{UID[:-1]}2", "loeschen": '{"turns": 1}', "zaehlen": '{"turns": 0}'})
    code, aus = verarbeite({"aktion": "loeschen", "schema": "wamo_dev", "alle": True, "ausfuehren": True}, psql)
    assert code == 0 and "labor-01" in aus and "labor-schau" in aus
    assert [n for n, _ in psql.aufrufe] == ["ids_alle", "loeschen", "zaehlen", "loeschen", "zaehlen"]


SPERRFRIST = ("fehler", "Datenbankfehler: ERROR:  labor_loeschen: Sperrfrist, noch etwa 95 Sekunden: das Profil wurde vor weniger als "
                        "3 Minuten geändert")
LOESCHEN = {"aktion": "loeschen", "schema": "wamo_dev", "art": "platz", "platz": "3", "ausfuehren": True}


def test_remote_loeschen_wartet_in_der_sperrfrist_und_versucht_es_erneut():
    """F2: Die Datenbank lehnt in den ersten 3 Minuten nach einer Änderung des Profils ab (der Kern kennt das Profil bis zu
    2 Minuten aus dem Zwischenspeicher). Das Skript wartet die genannte Zeit plus 5 Sekunden und ruft die Funktion noch einmal auf."""
    psql = Psql({"ids": f"labor-03|{UID}", "loeschen": [SPERRFRIST, '{"turns": 4}'], "zaehlen": '{"turns": 0}'})
    gewartet: list[int] = []
    code, aus = verarbeite(LOESCHEN, psql, warte=gewartet.append)
    assert code == 0 and gewartet == [100]
    assert [n for n, _ in psql.aufrufe] == ["ids", "loeschen", "loeschen", "zaehlen"]
    assert "Sperrfrist" in aus and "100 Sekunden" in aus and "turns 4" in aus


def test_remote_loeschen_gibt_nach_drei_versuchen_mit_klarer_meldung_auf():
    psql = Psql({"ids": f"labor-03|{UID}", "loeschen": [SPERRFRIST]})  # die letzte Antwort bleibt für jeden weiteren Aufruf
    gewartet: list[int] = []
    code, aus = verarbeite(LOESCHEN, psql, warte=gewartet.append)
    assert code == 1 and len(gewartet) == 2 and [n for n, _ in psql.aufrufe].count("loeschen") == 3
    assert "Sperrfrist" in aus and "spaeter" in aus.lower()


def test_remote_loeschen_wartet_hoechstens_200_sekunden_und_nur_bei_der_sperrfrist():
    psql = Psql({"ids": f"labor-03|{UID}", "loeschen": [("fehler", "ERROR:  labor_loeschen: Sperrfrist, noch etwa 9999 Sekunden"),
                                                         '{"turns": 1}'], "zaehlen": '{"turns": 0}'})
    gewartet: list[int] = []
    assert verarbeite(LOESCHEN, psql, warte=gewartet.append)[0] == 0 and gewartet == [200]
    # jeder andere Fehler (zum Beispiel noch freigeschaltet) wird sofort gemeldet, ohne Warten und ohne zweiten Aufruf
    psql = Psql({"ids": f"labor-03|{UID}", "loeschen": [("fehler", "ERROR:  labor_loeschen: das Laborprofil ist noch freigeschaltet")]})
    gewartet = []
    code, aus = verarbeite(LOESCHEN, psql, warte=gewartet.append)
    assert code == 1 and gewartet == [] and [n for n, _ in psql.aufrufe].count("loeschen") == 1 and "freigeschaltet" in aus


def test_remote_gibt_das_passwort_der_verwaltungsrolle_nie_aus_auch_nicht_im_fehlerfall():
    psql = Psql({"ids": f"labor-03|{UID}", "loeschen": [("fehler", f"Datenbankfehler: Anmeldung mit {DB_PASSWORT} abgelehnt")]})
    code, aus = verarbeite({**LOESCHEN, "db_passwort": DB_PASSWORT}, psql)
    assert code == 1 and DB_PASSWORT not in aus


def test_remote_loeschen_trockenlauf_ruft_nur_zaehlen_auf():
    psql = Psql({"ids": f"labor-03|{UID}", "zaehlen": '{"turns": 4, "actions": 2}'})
    code, aus = verarbeite({"aktion": "zaehlen", "schema": "wamo_dev", "art": "platz", "platz": "3", "ausfuehren": False}, psql)
    assert code == 0 and "turns 4" in aus
    assert [n for n, _ in psql.aufrufe] == ["ids", "zaehlen"]


def test_remote_unbekanntes_schema_oder_aktion():
    assert verarbeite({"aktion": "status", "schema": "public"}, Psql({}))[0] == 1
    assert verarbeite({"aktion": "zerstoeren", "schema": "wamo_dev"}, Psql({}))[0] == 1


def test_remote_gibt_nie_das_passwort_aus_auch_nicht_im_fehlerfall():
    def kaputt(email, passwort):
        raise wl_fehler(f"Konto konnte nicht angelegt werden: {passwort}")

    ns = remote_namensraum()
    wl_fehler = ns["Fehler"]
    ausgabe = io.StringIO()
    with redirect_stdout(ausgabe), redirect_stderr(ausgabe):
        code = ns["verarbeite"]({"aktion": "anlegen", "schema": "wamo_dev", "email": "labor-03@wamo.test", "art": "platz",
                                 "platz": "3", "password": "GEHEIMGEHEIMGEHEIM1234", "ausfuehren": True},
                                Psql({"konto_suchen": ""}), kaputt)
    assert code == 1 and "GEHEIMGEHEIMGEHEIM1234" not in ausgabe.getvalue()


# ---------- Allgemeine Regeln ----------

def test_das_skript_enthaelt_weder_schluessel_noch_feste_zugangsdaten():
    text = (Path(wl.__file__)).read_text(encoding="utf-8")
    assert not re.search(r"sk-ant-|eyJhbGci|SERVICE_ROLE_KEY\s*=\s*['\"]", text)
    assert "wamo_admin" in text and "service_role" not in wl.remote_programm().lower().replace("service_role_key", "")


def test_keine_gedankenstriche_und_hilfe_laeuft():
    text = Path(wl.__file__).read_text(encoding="utf-8")
    assert chr(0x2013) not in text and chr(0x2014) not in text
    code, aus, _ = lauf(["--help"])
    assert code == 0 and "anlegen" in aus and "loeschen" in aus
