"""Tests für das Kern-Kanal-Modul des Dispatchers (infra/sokrates2/wamo_job_kernkanal.py), Paket R7.

Das Modul besteht aus reinen Funktionen: alle Pfade, der Dateizugriff, die Uhr und der freie Platz werden eingespeist.
Die Tests laufen unter Windows und Linux, Eigentümer und Rechte der Dateien werden über einen Attrappen-Zugriff
nachgestellt. Ausführen (im Ordner voice-core/):  python -m pytest -q test_kernkanal.py
"""

from __future__ import annotations

import ast
import base64
import errno
import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parent.parent / "infra" / "sokrates2"
MODUL_PFAD = INFRA / "wamo_job_kernkanal.py"


def _lade():
    spec = importlib.util.spec_from_file_location("wamo_job_kernkanal", MODUL_PFAD)
    modul = importlib.util.module_from_spec(spec)
    sys.modules["wamo_job_kernkanal"] = modul  # nötig für @dataclass
    spec.loader.exec_module(modul)
    return modul


K = _lade()

JOB = "abc1234567890def"
JETZT = datetime(2026, 11, 11, 12, 0, 0).timestamp()
HEUTE = "20261111"
EIGENE_UID = 995  # der Benutzer wamo, unter dem der Dispatcher läuft

PFADE = K.Pfade(kern_schluessel=Path("/t/ssh/wamo-kern"), pc_schluessel=Path("/t/ssh/wamo"), konfig=Path("/t/etc/kernkanal.conf"),
                notaus=Path("/t/etc/kernkanal.notaus"), zaehler=Path("/t/logs/kernkanal.jsonl"), platz=Path("/t/srv"))
AUTH = "/t/tmp/sshd_auth_1"
SSH = {"SSH_CONNECTION": "192.168.178.136 50000 192.168.178.136 22", "SSH_ORIGINAL_COMMAND": "ping", "SSH_USER_AUTH": AUTH}


def schluessel(n: int) -> str:
    """Ein gültig aufgebauter ed25519-Schlüssel (Typ und Base64-Teil), je n ein anderer."""
    blob = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes([n]) * 32
    return "ssh-ed25519 " + base64.b64encode(blob).decode("ascii")


KERN_KEY, PC_KEY, FREMD_KEY = schluessel(1), schluessel(2), schluessel(3)
KERN_ZEILE = f'restrict,from="192.168.178.136",command="/opt/wamo/bin/wamo-job-dispatch" {KERN_KEY} wamo-kern-jobs\n'
PC_ZEILE = f'restrict,from="192.168.178.0/24",command="/opt/wamo/bin/wamo-job-dispatch" {PC_KEY} wamo-jobs\n'


class Dateien:
    """Attrappe des Dateizugriffs: Pfad -> (Inhalt, uid, Modus) oder eine Ausnahme, die das Lesen wirft."""

    def __init__(self):
        self.d: dict[Path, object] = {}

    def setze(self, pfad, inhalt: str | bytes, uid: int = 0, modus: int = 0o100640):
        self.d[Path(pfad)] = (inhalt.encode("utf-8") if isinstance(inhalt, str) else inhalt, uid, modus)
        return self

    def fehler(self, pfad, exc: Exception):
        self.d[Path(pfad)] = exc
        return self

    def __call__(self, pfad, limit):
        eintrag = self.d.get(Path(pfad))
        if eintrag is None:
            raise FileNotFoundError(errno.ENOENT, "nicht vorhanden")
        if isinstance(eintrag, Exception):
            raise eintrag
        inhalt, uid, modus = eintrag
        return inhalt, os.stat_result((modus, 0, 0, 1, uid, 0, len(inhalt), 0, 0, 0))

    def vorhanden(self, pfad) -> bool:
        return Path(pfad) in self.d


def konfig_text(**ueber) -> str:
    werte = {"KANAL": "offen", "ABLAUF": "20261201", "ARTEN": "recherche,review", "REPOS": "acme/test-repo", "DIENSTE": "",
             "MAX_TURNS": "40", "MAX_BUDGET_USD": "2.00", "MAX_START_STUNDE": "4", "MAX_START_TAG": "8",
             "MAX_LAUFZEIT_TAG_S": "14400", "MAX_FOLGEN": "5", "MIN_ABSTAND_S": "60", "MIN_PLATZ_MB": "2048", "MAX_VERSUCH_STUNDE": "20"}
    werte.update(ueber)
    return "# Regeln des Kern-Kanals\n" + "".join(f"{k}={v}\n" for k, v in werte.items() if v is not None)


def welt(*, kern: bool = True, pc: bool = True, konfig: str | None = None, notaus: bool = False, auth_key: str | None = None,
         auth_uid: int = EIGENE_UID, auth_modus: int = 0o100600) -> Dateien:
    d = Dateien()
    if kern:
        d.setze(PFADE.kern_schluessel, KERN_ZEILE, modus=0o100644)
    if pc:
        d.setze(PFADE.pc_schluessel, PC_ZEILE, modus=0o100644)
    if konfig is not None:
        d.setze(PFADE.konfig, konfig)
    if notaus:
        d.setze(PFADE.notaus, "Notaus am Test")
    if auth_key is not None:
        d.setze(AUTH, f"publickey {auth_key}\n", uid=auth_uid, modus=auth_modus)
    return d


def kanal(d: Dateien, env=None, uid=EIGENE_UID):
    return K.kanal_bestimmen(SSH if env is None else env, PFADE, lesen=d, vorhanden=d.vorhanden, eigene_uid=uid)


# ---------------------------------------------------------------- Kanal aus dem Schlüssel
def test_ohne_ssh_variablen_ist_der_kanal_lokal_auch_im_strikten_modus():
    for d in (welt(kern=False, pc=True), welt(konfig=konfig_text())):
        e = kanal(d, env={"PATH": "/usr/bin"})
        assert (e.kanal, e.grund) == ("lokal", None)
    assert kanal(welt(konfig=konfig_text()), env={}).strikt is True
    assert kanal(welt(kern=False), env={}).strikt is False


def test_ohne_kern_schluessel_und_ohne_konfiguration_ist_jeder_ssh_aufruf_pc():
    d = welt(kern=False, pc=True, auth_key=FREMD_KEY)  # selbst ein unbekannter Schlüssel: Stufe 0, es kann kein Kern ankommen
    e = kanal(d)
    assert (e.kanal, e.strikt, e.grund) == ("pc", False, None)
    e = kanal(welt(kern=False, pc=False), env={"SSH_CONNECTION": "1.2.3.4 5 6.7.8.9 22"})  # nicht einmal SSH_USER_AUTH
    assert (e.kanal, e.strikt) == ("pc", False)


def test_ein_kern_schluessel_mit_und_ohne_from_ergibt_kern():
    for zeile in (KERN_ZEILE, f"{KERN_KEY} wamo-kern-jobs\n", f"{KERN_KEY}\n", f'command="x" {KERN_KEY}\n'):
        d = welt(auth_key=KERN_KEY)
        d.setze(PFADE.kern_schluessel, zeile, modus=0o100644)
        e = kanal(d)
        assert (e.kanal, e.strikt, e.grund) == ("kern", True, None), zeile


def test_ein_pc_schluessel_ergibt_pc_im_strikten_modus():
    e = kanal(welt(auth_key=PC_KEY))
    assert (e.kanal, e.strikt, e.grund) == ("pc", True, None)


def test_eine_datei_in_beiden_listen_ergibt_kern():
    d = welt(auth_key=KERN_KEY)
    d.setze(PFADE.pc_schluessel, PC_ZEILE + KERN_ZEILE, modus=0o100644)
    assert kanal(d).kanal == "kern"


def test_die_pc_datei_darf_mehrere_schluessel_haben_und_die_zuordnung_bleibt_pc():
    d = welt(auth_key=schluessel(9))
    d.setze(PFADE.pc_schluessel, PC_ZEILE + f"{schluessel(9)} zweiter-pc\n", modus=0o100644)
    assert kanal(d).kanal == "pc"


@pytest.mark.parametrize("fall", ["fremder_schluessel", "ohne_auth_variable", "auth_datei_fehlt", "fremder_eigentuemer",
                                  "gruppe_schreibbar", "alle_schreibbar", "lesefehler", "kein_publickey", "kaputtes_base64",
                                  "kern_datei_unlesbar", "pc_datei_unlesbar", "leere_auth_datei", "uid_unbekannt",
                                  "pc_datei_fehlt_und_schluessel_nicht_kern"])
def test_im_strikten_modus_ergibt_alles_unklare_herkunft_unklar(fall):
    d, env, uid = welt(auth_key=PC_KEY), dict(SSH), EIGENE_UID
    if fall == "fremder_schluessel":
        d = welt(auth_key=FREMD_KEY)
    elif fall == "ohne_auth_variable":
        env.pop("SSH_USER_AUTH")
    elif fall == "auth_datei_fehlt":
        d = welt()
    elif fall == "fremder_eigentuemer":
        d = welt(auth_key=PC_KEY, auth_uid=0)
    elif fall == "gruppe_schreibbar":
        d = welt(auth_key=PC_KEY, auth_modus=0o100620)
    elif fall == "alle_schreibbar":
        d = welt(auth_key=PC_KEY, auth_modus=0o100666)
    elif fall == "lesefehler":
        d.fehler(AUTH, PermissionError(errno.EACCES, "keine Rechte"))
    elif fall == "kein_publickey":
        d.setze(AUTH, "keyboard-interactive\n", uid=EIGENE_UID, modus=0o100600)
    elif fall == "kaputtes_base64":
        d.setze(AUTH, "publickey ssh-ed25519 !!!nicht-base64!!!\n", uid=EIGENE_UID, modus=0o100600)
    elif fall == "kern_datei_unlesbar":
        d.fehler(PFADE.kern_schluessel, PermissionError(errno.EACCES, "keine Rechte"))
    elif fall == "pc_datei_unlesbar":
        d = welt(auth_key=FREMD_KEY)
        d.fehler(PFADE.pc_schluessel, PermissionError(errno.EACCES, "keine Rechte"))
    elif fall == "leere_auth_datei":
        d.setze(AUTH, "", uid=EIGENE_UID, modus=0o100600)
    elif fall == "uid_unbekannt":
        uid = None
    elif fall == "pc_datei_fehlt_und_schluessel_nicht_kern":
        d = welt(pc=False, auth_key=PC_KEY)
    e = kanal(d, env=env, uid=uid)
    assert (e.kanal, e.strikt, e.grund) == (None, True, "herkunft_unklar")


def test_der_strikte_modus_gilt_auch_wenn_nur_die_konfiguration_existiert():
    d = welt(kern=False, konfig=konfig_text(), auth_key=PC_KEY)
    assert kanal(d).kanal == "pc" and kanal(d).strikt is True
    d = welt(kern=False, konfig=konfig_text(), auth_key=FREMD_KEY)
    assert kanal(d).grund == "herkunft_unklar"


def test_eine_unlesbare_oder_leere_kern_datei_macht_den_modus_strikt_und_gilt_als_vorhanden():
    d = welt(kern=False, auth_key=PC_KEY)
    d.setze(PFADE.kern_schluessel, "")  # leer: existiert, enthält aber keinen Schlüssel
    e = kanal(d)
    assert (e.kanal, e.strikt) == ("pc", True)
    assert K.existiert(Path(__file__))
    assert not K.existiert(Path(__file__).with_name("gibt-es-nicht.xyz"))


def test_schluessel_zeilen_werden_tolerant_gelesen_optionen_kommentar_und_zeilenenden():
    inhalt = ("# Kommentar mit ssh-ed25519 AAAA\n\n"
              f'restrict,from="1.2.3.4",command="echo {FREMD_KEY}" {KERN_KEY} wamo-kern-jobs\r\n'
              "kaputte zeile ohne schluessel\n")
    assert K.schluessel_aus_text(inhalt) == {tuple(KERN_KEY.split())}
    assert K.schluessel_aus_text(f"{PC_KEY}") == {tuple(PC_KEY.split())}
    assert K.schluessel_aus_text("") == set()


def test_der_schluessel_aus_ssh_user_auth_ist_der_schluesselteil_der_publickey_zeile():
    text = f"keyboard-interactive\npublickey {PC_KEY}\n"
    assert K.schluessel_aus_auth(text) == tuple(PC_KEY.split())
    assert K.schluessel_aus_auth("password\n") is None


# ---------------------------------------------------------------- konfig_lesen
def lese(text: str | None, **kw):
    d = Dateien()
    if text is not None:
        d.setze(PFADE.konfig, text, **kw)
    return K.konfig_lesen(PFADE.konfig, jetzt=JETZT, lesen=d)


def test_eine_gueltige_konfiguration_wird_gelesen():
    k = lese(konfig_text(ARTEN="recherche,review", REPOS="Acme/Test-Repo,acme/zwei", DIENSTE="jira"))
    assert (k.kanal, k.ablauf.strftime("%Y%m%d")) == ("offen", "20261201")
    assert k.arten == ("recherche", "review") and k.repos == ("acme/test-repo", "acme/zwei") and k.dienste == ("jira",)
    assert (k.max_turns, k.max_budget_usd, k.max_start_stunde, k.max_start_tag) == (40, "2.00", 4, 8)
    assert (k.max_laufzeit_tag_s, k.max_folgen, k.min_abstand_s, k.min_platz_mb) == (14400, 5, 60, 2048)


def test_leere_listen_und_fehlende_schluessel_sind_gueltig_aber_gesperrt_oder_standard():
    k = lese(konfig_text(ARTEN="", REPOS="", DIENSTE=""))
    assert k.arten == () and k.repos == () and k.dienste == ()
    k = lese("# nur ein Kommentar\n\n")
    assert k.kanal is None and k.ablauf is None and k.arten == ()
    assert (k.max_turns, k.max_budget_usd, k.max_start_stunde, k.max_start_tag) == (40, "2.00", 4, 8)  # die Standardwerte aus I2


def test_ablauf_mit_400_tagen_voraus_geht_401_nicht():
    ok = (datetime(2026, 11, 11) + timedelta(days=400)).strftime("%Y%m%d")
    zu_weit = (datetime(2026, 11, 11) + timedelta(days=401)).strftime("%Y%m%d")
    assert lese(konfig_text(ABLAUF=ok)).ablauf.strftime("%Y%m%d") == ok
    with pytest.raises(K.KonfigFehler):
        lese(konfig_text(ABLAUF=zu_weit))
    assert lese(konfig_text(ABLAUF="20200101")).ablauf.year == 2020  # ein vergangener Tag ist lesbar (der Zustand sagt abgelaufen)


FEHLER_FAELLE = [
    pytest.param(None, {}, id="datei_fehlt"),
    pytest.param(konfig_text(), {"uid": 1000}, id="fremder_eigentuemer"),
    pytest.param(konfig_text(), {"modus": 0o100660}, id="gruppe_schreibbar"),
    pytest.param(konfig_text(), {"modus": 0o100602}, id="alle_schreibbar"),
    pytest.param(konfig_text() + "UNBEKANNT=1\n", {}, id="unbekannter_schluessel"),
    pytest.param(konfig_text() + "KANAL=zu\n", {}, id="doppelter_schluessel"),
    pytest.param(konfig_text() + "MAX_TURNS=41\n", {}, id="doppelter_schluessel_anderer_wert"),
    pytest.param(konfig_text() + "kanal=zu\n", {}, id="kleingeschriebener_schluessel"),
    pytest.param(konfig_text() + "KANAL = zu\n", {}, id="leerzeichen_um_das_gleichheitszeichen"),
    pytest.param(konfig_text() + "irgendein text\n", {}, id="zeile_ohne_gleichheitszeichen"),
    pytest.param(konfig_text() + "KANAL\n", {}, id="schluessel_ohne_wert"),
    pytest.param(konfig_text(KANAL="auf"), {}, id="kanal_wert_unbekannt"),
    pytest.param(konfig_text(KANAL=""), {}, id="kanal_leer"),
    pytest.param(konfig_text(ABLAUF="2026-12-01"), {}, id="ablauf_mit_strichen"),
    pytest.param(konfig_text(ABLAUF="20261301"), {}, id="ablauf_monat_13"),
    pytest.param(konfig_text(ABLAUF="20261232"), {}, id="ablauf_tag_32"),
    pytest.param(konfig_text(ABLAUF="morgen"), {}, id="ablauf_text"),
    pytest.param(konfig_text(ABLAUF=""), {}, id="ablauf_leer"),
    pytest.param(konfig_text(MAX_TURNS="0"), {}, id="turns_0"),
    pytest.param(konfig_text(MAX_TURNS="201"), {}, id="turns_201"),
    pytest.param(konfig_text(MAX_TURNS="vierzig"), {}, id="turns_text"),
    pytest.param(konfig_text(MAX_TURNS="4.5"), {}, id="turns_dezimal"),
    pytest.param(konfig_text(MAX_TURNS="-1"), {}, id="turns_negativ"),
    pytest.param(konfig_text(MAX_TURNS="040"), {}, id="turns_fuehrende_null"),
    pytest.param(konfig_text(MAX_BUDGET_USD="0"), {}, id="budget_0"),
    pytest.param(konfig_text(MAX_BUDGET_USD="0.00"), {}, id="budget_0_00"),
    pytest.param(konfig_text(MAX_BUDGET_USD="20.01"), {}, id="budget_ueber_20"),
    pytest.param(konfig_text(MAX_BUDGET_USD="21"), {}, id="budget_21"),
    pytest.param(konfig_text(MAX_BUDGET_USD="2.001"), {}, id="budget_drei_stellen"),
    pytest.param(konfig_text(MAX_BUDGET_USD="2,00"), {}, id="budget_komma"),
    pytest.param(konfig_text(MAX_BUDGET_USD=".5"), {}, id="budget_ohne_null"),
    pytest.param(konfig_text(MAX_BUDGET_USD="1e1"), {}, id="budget_exponent"),
    pytest.param(konfig_text(MAX_BUDGET_USD="-2"), {}, id="budget_negativ"),
    pytest.param(konfig_text(MAX_START_STUNDE="0"), {}, id="stunde_0"),
    pytest.param(konfig_text(MAX_START_STUNDE="61"), {}, id="stunde_61"),
    pytest.param(konfig_text(MAX_START_TAG="0"), {}, id="tag_0"),
    pytest.param(konfig_text(MAX_START_TAG="201"), {}, id="tag_201"),
    pytest.param(konfig_text(MAX_LAUFZEIT_TAG_S="59"), {}, id="laufzeit_59"),
    pytest.param(konfig_text(MAX_LAUFZEIT_TAG_S="86401"), {}, id="laufzeit_86401"),
    pytest.param(konfig_text(MAX_FOLGEN="21"), {}, id="folgen_21"),
    pytest.param(konfig_text(MAX_FOLGEN="-1"), {}, id="folgen_negativ"),
    pytest.param(konfig_text(MIN_ABSTAND_S="3601"), {}, id="abstand_3601"),
    pytest.param(konfig_text(MIN_PLATZ_MB="99"), {}, id="platz_99"),
    pytest.param(konfig_text(MIN_PLATZ_MB="1000001"), {}, id="platz_zu_gross"),
    pytest.param(konfig_text(REPOS="acme/wamo"), {}, id="repo_wamo"),
    pytest.param(konfig_text(REPOS="Acme/WAMO"), {}, id="repo_wamo_gross"),
    pytest.param(konfig_text(REPOS="acme/a,acme/wamo"), {}, id="repo_wamo_in_liste"),
    pytest.param(konfig_text(REPOS="acme/*"), {}, id="repo_platzhalter"),
    pytest.param(konfig_text(REPOS="*"), {}, id="repo_stern"),
    pytest.param(konfig_text(REPOS="acme"), {}, id="repo_ohne_owner"),
    pytest.param(konfig_text(REPOS="acme/x/y"), {}, id="repo_zwei_schraegstriche"),
    pytest.param(konfig_text(REPOS="acme/x y"), {}, id="repo_leerzeichen"),
    pytest.param(konfig_text(REPOS="acme/x.git"), {}, id="repo_dot_git"),
    pytest.param(konfig_text(REPOS="acme/a,acme/a"), {}, id="repo_doppelt"),
    pytest.param(konfig_text(REPOS=",".join(f"acme/r{i}" for i in range(11))), {}, id="elf_repos"),
    pytest.param(konfig_text(REPOS="acme/a,,acme/b"), {}, id="leeres_listenelement"),
    pytest.param(konfig_text(DIENSTE="supabase"), {}, id="dienst_supabase"),
    pytest.param(konfig_text(DIENSTE="jira,supabase"), {}, id="dienst_supabase_in_liste"),
    pytest.param(konfig_text(DIENSTE="Supabase"), {}, id="dienst_supabase_gross"),
    pytest.param(konfig_text(DIENSTE="vercel;id"), {}, id="dienst_sonderzeichen"),
    pytest.param(konfig_text(ARTEN="gibtsnicht"), {}, id="art_unbekannt"),
    pytest.param(konfig_text(ARTEN="recherche,recherche"), {}, id="art_doppelt"),
    pytest.param("KANAL=offen\x00\n", {}, id="steuerzeichen"),
    pytest.param(konfig_text() + "BÜRO=1\n", {}, id="nicht_ascii"),
]


@pytest.mark.parametrize("text, kw", FEHLER_FAELLE)
def test_jeder_konfigurationsfehler_ist_konfig_ungueltig(text, kw):
    d = Dateien()
    if text is not None:
        d.setze(PFADE.konfig, text, **kw)
    with pytest.raises(K.KonfigFehler) as info:
        K.konfig_lesen(PFADE.konfig, jetzt=JETZT, lesen=d, bekannte_arten=("recherche", "review", "umsetzung"))
    assert info.value.grund == "konfig_ungueltig"


def test_ein_lesefehler_der_konfiguration_ist_konfig_ungueltig():
    d = Dateien().fehler(PFADE.konfig, PermissionError(errno.EACCES, "keine Rechte"))
    with pytest.raises(K.KonfigFehler):
        K.konfig_lesen(PFADE.konfig, jetzt=JETZT, lesen=d)
    d.fehler(PFADE.konfig, OSError(errno.ELOOP, "Link"))
    with pytest.raises(K.KonfigFehler):
        K.konfig_lesen(PFADE.konfig, jetzt=JETZT, lesen=d)


def test_die_randwerte_der_grenzen_sind_gueltig():
    k = lese(konfig_text(MAX_TURNS="200", MAX_BUDGET_USD="20", MAX_START_STUNDE="60", MAX_START_TAG="200", MAX_LAUFZEIT_TAG_S="86400",
                         MAX_FOLGEN="20", MIN_ABSTAND_S="3600", MIN_PLATZ_MB="1000000"))
    assert (k.max_turns, k.max_budget_usd, k.max_start_stunde) == (200, "20", 60)
    k = lese(konfig_text(MAX_TURNS="1", MAX_BUDGET_USD="0.01", MAX_START_STUNDE="1", MAX_START_TAG="1", MAX_LAUFZEIT_TAG_S="60",
                         MAX_FOLGEN="0", MIN_ABSTAND_S="0", MIN_PLATZ_MB="100"))
    assert (k.max_turns, k.max_budget_usd, k.max_folgen, k.min_abstand_s) == (1, "0.01", 0, 0)
    assert len(lese(konfig_text(REPOS=",".join(f"acme/r{i}" for i in range(10)))).repos) == 10


def test_eine_datei_mit_gruppenleserecht_ist_gueltig_und_die_datei_ist_zu_gross_nicht():
    assert lese(konfig_text(), modus=0o100640).kanal == "offen"
    assert lese(konfig_text(), modus=0o100644).kanal == "offen"
    with pytest.raises(K.KonfigFehler):
        lese("# " + "x" * 70000 + "\n")


# ---------------------------------------------------------------- Zustand des Kanals
def zustand(konfig=None, notaus=False, **kw):
    d = welt(konfig=konfig, notaus=notaus)
    return K.kanal_zustand(PFADE, jetzt=JETZT, lesen=d, vorhanden=d.vorhanden, bekannte_arten=("recherche", "review", "umsetzung"), **kw)


def test_offen_ohne_grund_bei_gueltiger_konfiguration_und_ablauf_heute_zaehlt_noch():
    z = zustand(konfig_text(ABLAUF=HEUTE))
    assert (z.grund, z.notaus, z.tage_rest) == (None, False, 0)
    z = zustand(konfig_text(ABLAUF="20261201"))
    assert (z.grund, z.tage_rest) == (None, 20)


@pytest.mark.parametrize("text, erwartet", [
    (konfig_text(KANAL="zu"), "kanal_zu"), (konfig_text(KANAL=None), "kanal_zu"),
    (konfig_text(ABLAUF="20261110"), "abgelaufen"), (konfig_text(ABLAUF=None), "abgelaufen"),
    (konfig_text(MAX_TURNS="0"), "konfig_ungueltig"), (None, "konfig_ungueltig"),
    (konfig_text(KANAL="zu", ABLAUF="20261110"), "kanal_zu"),  # die Reihenfolge der Regel 2
    (konfig_text(KANAL="zu") + "UNBEKANNT=1\n", "konfig_ungueltig"),
])
def test_der_zustand_nennt_den_ersten_grund_der_regel_2(text, erwartet):
    assert zustand(text).grund == erwartet


def test_die_notaus_datei_geht_allem_voran():
    z = zustand(konfig_text(), notaus=True)
    assert (z.grund, z.notaus) == ("notaus", True)
    assert zustand(None, notaus=True).grund == "notaus"
    assert zustand(konfig_text(KANAL="zu"), notaus=True).grund == "notaus"


def test_der_ping_bericht_enthaelt_nur_die_felder_aus_i7():
    z = zustand(konfig_text(ARTEN="recherche", REPOS="acme/test-repo"))
    b = K.kernkanal_bericht(z, strikt=True)
    assert b == {"strikt": True, "offen": True, "grund": None, "arten": ["recherche"], "repos": ["acme/test-repo"],
                 "ablauf": "20261201", "tage_rest": 20}
    b = K.kernkanal_bericht(zustand(konfig_text(KANAL="zu")), strikt=True)
    assert (b["offen"], b["grund"]) == (False, "kanal_zu")
    b = K.kernkanal_bericht(zustand(None), strikt=False)
    assert b == {"strikt": False, "offen": False, "grund": None, "arten": [], "repos": [], "ablauf": None, "tage_rest": None}
    b = K.kernkanal_bericht(zustand(konfig_text(), notaus=True), strikt=True)
    assert (b["offen"], b["grund"]) == (False, "notaus")
    assert set(b) == {"strikt", "offen", "grund", "arten", "repos", "ablauf", "tage_rest"}


# ---------------------------------------------------------------- Befehlsliste (Regel 1 bis 4)
OFFEN = None


def offener_zustand():
    return zustand(konfig_text(ARTEN="recherche,review,umsetzung", REPOS="acme/test-repo", DIENSTE="jira"))


@pytest.mark.parametrize("befehl", ["ping", "start", "status", "pending", "approve", "cancel"])
def test_bei_offenem_kanal_gehen_die_sechs_befehle(befehl):
    assert K.pruefe_befehl(befehl, offener_zustand()) is None


@pytest.mark.parametrize("befehl", ["pruefen", "id", "", "sftp", "Ping", "ping;id", "start-all", "approve2"])
def test_jeder_andere_befehl_ist_befehl(befehl):
    assert K.pruefe_befehl(befehl, offener_zustand()) == "befehl"


@pytest.mark.parametrize("befehl", ["ping", "status", "pending", "cancel"])
def test_bei_geschlossenem_kanal_gehen_nur_ping_status_pending_cancel(befehl):
    for text in (konfig_text(KANAL="zu"), konfig_text(ABLAUF="20260101"), konfig_text(MAX_TURNS="0"), None):
        assert K.pruefe_befehl(befehl, zustand(text)) is None


@pytest.mark.parametrize("befehl", ["start", "approve", "pruefen"])
def test_bei_geschlossenem_kanal_gehen_start_und_freigaben_nicht_und_der_grund_ist_der_des_zustands(befehl):
    for text, grund in ((konfig_text(KANAL="zu"), "kanal_zu"), (konfig_text(ABLAUF="20260101"), "abgelaufen"),
                        (konfig_text(MAX_TURNS="0"), "konfig_ungueltig"), (None, "konfig_ungueltig")):
        assert K.pruefe_befehl(befehl, zustand(text)) == grund


@pytest.mark.parametrize("befehl, erwartet", [("ping", None), ("status", None), ("start", "notaus"), ("pending", "notaus"),
                                              ("approve", "notaus"), ("cancel", "notaus"), ("pruefen", "notaus"), ("id", "notaus")])
def test_bei_notaus_gehen_nur_ping_und_status(befehl, erwartet):
    for text in (konfig_text(), konfig_text(KANAL="zu"), None, konfig_text(MAX_TURNS="0")):
        assert K.pruefe_befehl(befehl, zustand(text, notaus=True)) == erwartet


def test_nur_auftraege_mit_kanal_kern_sind_eigene():
    assert K.pruefe_eigener_auftrag({"job": JOB, "kanal": "kern"}) is None
    for meta in (None, {}, {"job": JOB}, {"job": JOB, "kanal": "pc"}, {"job": JOB, "kanal": "KERN"}, {"job": JOB, "kanal": None},
                 [], "kern", {"kanal": ["kern"]}, {"kanal": True}):
        assert K.pruefe_eigener_auftrag(meta) == "fremd", meta


# ---------------------------------------------------------------- Projektname (I5)
@pytest.mark.parametrize("repo, wort, erwartet", [
    ("Acme/Test-Repo", None, "kern-test-repo"), ("acme/my_repo.js", None, "kern-my-repo-js"), ("acme/a__b", None, "kern-a-b"),
    ("acme/-x-", None, "kern-x"), (None, "Marktstudie", "kern-marktstudie"), ("acme/" + "a" * 45, None, "kern-" + "a" * 45),
    ("acme/a.b-c_d", None, "kern-a-b-c-d"), (None, "Markt Studie", "kern-marktstudie"), (None, "Märkte", "kern-mrkte"),
    (None, "a--b", "kern-a-b"), (None, "__x__", "kern-x"), ("acme/Wamo-Extra", None, "kern-wamo-extra"),
    ("acme/x", "ignoriert", "kern-x"),  # mit Repo zählt nur das Repo
])
def test_wertetabelle_i5_gute_zeilen(repo, wort, erwartet):
    assert K.kern_projektname(repo, wort) == erwartet


@pytest.mark.parametrize("repo, wort, grund", [
    ("acme/wamo", None, "repo"), ("acme/WAMO", None, "repo"), ("acme/Wamo", None, "repo"), ("acme/wamo.", None, "repo"),
    ("acme/" + "a" * 46, None, "projekt"), ("acme/" + "a" * 100, None, "projekt"),
    (None, None, "projekt"), (None, "", "projekt"), (None, "äöü", "projekt"), (None, "---", "projekt"), (None, "a" * 46, "projekt"),
    ("acme/..", None, "projekt"), ("acme/___", None, "projekt"), ("kein-repo", None, "repo"), ("a/b/c", None, "repo"), ("", None, "projekt"),
])
def test_wertetabelle_i5_abgelehnte_zeilen(repo, wort, grund):
    with pytest.raises(K.ProjektnameFehler) as info:
        K.kern_projektname(repo, wort)
    assert info.value.grund == grund


@pytest.mark.parametrize("projekt, repo, erwartet", [
    ("kern-test-repo", "Acme/Test-Repo", None), ("kern-my-repo-js", "acme/my_repo.js", None),
    ("test-repo", "acme/test-repo", "projekt"),  # Präfix fehlt
    ("kern-anderes", "acme/test-repo", "projekt"), ("kern-Test-Repo", "acme/test-repo", "projekt"),
    ("kern-marktstudie", None, None), ("marktstudie", None, "projekt"), ("kern-", None, "projekt"), ("wamo", None, "projekt"),
    ("kern-Markt", None, "projekt"), ("kern-a--b", None, "projekt"), ("kern--a", None, "projekt"), ("kern-a-", None, "projekt"),
    ("kern-" + "a" * 46, None, "projekt"), ("kern-" + "a" * 45, None, None), ("kern-markt_studie", None, "projekt"),
    ("kern-x", "acme/wamo", "repo"),
])
def test_projekt_im_kern_kanal_nach_i5(projekt, repo, erwartet):
    assert K.projekt_pruefen(projekt, repo) == erwartet


def test_der_pc_kanal_darf_den_namensraum_kern_nicht_oeffnen():
    for projekt in ("kern-test-repo", "kern-x", "kern-"):
        assert K.pc_projekt_erlaubt(projekt) is False
    for projekt in ("test-repo", "kernel-x", "mein-kern-x", "kern"):
        assert K.pc_projekt_erlaubt(projekt) is True


# ---------------------------------------------------------------- Start: Regeln 5 bis 7
def anfrage(**ueber):
    werte = dict(job=JOB, projekt="kern-marktstudie", art="recherche", repo=None, zugang="folgt", profil="1a2b3c4d",
                 github=False, dienste=(), app_modus=True)
    werte.update(ueber)
    return K.StartAnfrage(**werte)


def github_anfrage(**ueber):
    werte = dict(projekt="kern-test-repo", art="review", repo="acme/test-repo", github=True)
    werte.update(ueber)
    return anfrage(**werte)


def start(a, z=None, ereignisse=(), platz=10_000, jetzt=JETZT):
    return K.pruefe_start(a, z or offener_zustand(), ereignisse=list(ereignisse), jetzt=jetzt, freier_platz_mb=platz)


def test_eine_gute_recherche_und_ein_guter_review_laufen_durch():
    assert start(anfrage()) is None
    assert start(github_anfrage()) is None
    assert start(github_anfrage(repo="Acme/Test-Repo", projekt="kern-test-repo")) is None  # klein verglichen


def test_regel_1_und_2_stehen_auch_vor_den_startregeln():
    assert start(anfrage(), zustand(konfig_text(), notaus=True)) == "notaus"
    assert start(anfrage(), zustand(konfig_text(KANAL="zu"))) == "kanal_zu"
    assert start(anfrage(), zustand(konfig_text(ABLAUF="20260101"))) == "abgelaufen"
    assert start(anfrage(), zustand(konfig_text(MAX_TURNS="0"))) == "konfig_ungueltig"
    assert start(anfrage(art="gibtsnicht"), zustand(konfig_text(KANAL="zu"))) == "kanal_zu"  # Reihenfolge: 2 vor 5


def test_regel_4_ein_bestehender_fremder_auftrag_wird_vor_der_art_abgewiesen():
    assert start(anfrage(fremd=True)) == "fremd"
    assert start(anfrage(fremd=True, art="gibtsnicht")) == "fremd"
    assert start(anfrage(fremd=True), zustand(konfig_text(KANAL="zu"))) == "kanal_zu"  # 2 vor 4
    assert start(anfrage(fremd=False)) is None


def test_regel_5_art():
    assert start(anfrage(art="umsetzung", github=True, repo="acme/test-repo", projekt="kern-test-repo"),
                 zustand(konfig_text(ARTEN="recherche"))) == "art"
    assert start(anfrage(art="gibtsnicht")) == "art"


def test_regel_5_github_arten_brauchen_ein_repo_aus_repos_und_den_app_modus():
    assert start(github_anfrage(repo=None, projekt="kern-test-repo")) == "repo_fehlt"
    assert start(github_anfrage(repo="acme/anderes", projekt="kern-anderes")) == "repo"
    assert start(github_anfrage(repo="evil/test-repo")) == "repo"  # der Owner zählt
    assert start(github_anfrage(app_modus=False)) == "repo"  # kein Rückfall auf einen persönlichen Token
    assert start(anfrage(repo="acme/anderes", projekt="kern-anderes")) == "repo"  # ein Repo, das gesetzt ist, muss auch ohne GitHub-Art erlaubt sein
    assert start(anfrage(repo="acme/test-repo", projekt="kern-test-repo")) is None


def test_regel_5_dienste_ausser_github_nur_aus_dienste_und_supabase_nie():
    assert start(anfrage(dienste=("jira",))) is None
    assert start(anfrage(dienste=("vercel",))) == "dienst"
    assert start(anfrage(dienste=("jira", "vercel"))) == "dienst"
    assert start(anfrage(dienste=("supabase",))) == "dienst"
    assert start(anfrage(dienste=("jira",)), zustand(konfig_text(DIENSTE=""))) == "dienst"


def test_regel_6_projekt():
    assert start(anfrage(projekt="marktstudie")) == "projekt"
    assert start(github_anfrage(projekt="test-repo")) == "projekt"
    assert start(github_anfrage(projekt="kern-anderes")) == "projekt"
    assert start(anfrage(projekt="kern-")) == "projekt"
    assert start(anfrage(projekt="kern-" + "a" * 46)) == "projekt"


def test_regel_7_zugang_nur_folgt_mit_profil():
    assert start(anfrage(zugang=None)) == "zugang"
    assert start(anfrage(zugang="server")) == "zugang"
    assert start(anfrage(zugang="")) == "zugang"
    assert start(anfrage(zugang="folgt", profil=None)) == "zugang"


def test_die_reihenfolge_der_regeln_5_bis_9():
    schlecht = anfrage(art="gibtsnicht", projekt="x", zugang=None)
    assert start(schlecht) == "art"
    assert start(anfrage(projekt="x", zugang=None)) == "projekt"
    assert start(anfrage(zugang=None), ereignisse=[_start(i) for i in range(8)]) == "zugang"  # 7 vor 9
    assert start(github_anfrage(repo=None, projekt="x", dienste=("vercel",))) == "repo_fehlt"  # Repo vor Dienst und Projekt
    assert start(github_anfrage(dienste=("vercel",), zugang=None)) == "dienst"  # Dienst vor Projekt und Zugang


# ---------------------------------------------------------------- Regel 9: Grenzen am Rand
def _start(alter_s: float, job: str = "f" * 16) -> dict:
    return {"t": int(JETZT - alter_s), "job": job, "ereignis": "start"}


def _ende(alter_s: float, dauer: int, job: str = "f" * 16) -> dict:
    return {"t": int(JETZT - alter_s), "job": job, "ereignis": "ende", "dauer_s": dauer}


def z(**ueber):
    return zustand(konfig_text(ARTEN="recherche", MIN_ABSTAND_S="0", **ueber))


def test_grenze_start_je_stunde_der_vierte_start_geht_der_fuenfte_nicht():
    drei = [_start(100 + i * 10) for i in range(3)]
    assert start(anfrage(), z(), drei) is None  # das wäre Start Nummer 4 = MAX
    vier = drei + [_start(400)]
    assert start(anfrage(), z(), vier) == "grenze_start"  # Start Nummer 5 = MAX + 1


def test_grenze_start_je_stunde_zaehlt_nur_die_letzte_stunde_aber_der_tag_zaehlt_mit():
    alt = [_start(3601 + i * 10) for i in range(7)]  # sieben Starts, alle älter als eine Stunde
    assert start(anfrage(), z(), alt) is None  # Stunde: 0, Tag: 7, das wäre Start Nummer 8
    assert start(anfrage(), z(), alt + [_start(3700)]) == "grenze_start"  # Tag: acht Starts
    sehr_alt = [_start(86401 + i) for i in range(20)]
    assert start(anfrage(), z(), sehr_alt) is None  # älter als ein Tag zählt nicht


def test_grenze_start_je_tag_der_achte_geht_der_neunte_nicht():
    sieben = [_start(4000 + i * 600) for i in range(7)]
    assert start(anfrage(), z(), sieben) is None
    assert start(anfrage(), z(), sieben + [_start(30000)]) == "grenze_start"


def test_grenze_start_zaehlt_nur_starts_nicht_enden():
    assert start(anfrage(), z(), [_ende(100 + i, 5) for i in range(20)]) is None


def test_grenze_laufzeit_je_tag():
    assert start(anfrage(), z(), [_ende(5000, 14399)]) is None
    assert start(anfrage(), z(), [_ende(5000, 14400)]) == "grenze_laufzeit"
    assert start(anfrage(), z(), [_ende(5000, 7200), _ende(9000, 7200)]) == "grenze_laufzeit"
    assert start(anfrage(), z(), [_ende(86500, 99999)]) is None  # älter als ein Tag
    assert start(anfrage(), z(), [_start(5000)]) is None  # ein Start ohne Ende zählt keine Laufzeit


def test_grenze_folgen_je_auftrag():
    erster = [_start(500, JOB)]
    assert start(anfrage(), z(MAX_FOLGEN="0"), []) is None
    assert start(anfrage(), z(MAX_FOLGEN="0"), erster) == "grenze_folgen"
    fuenf_folgen = [_start(500 + i * 60, JOB) for i in range(5)]  # Auftrag + 4 Folgen: die fünfte Folge geht
    assert start(anfrage(), z(MAX_FOLGEN="5", MAX_START_STUNDE="60", MAX_START_TAG="200"), fuenf_folgen) is None
    sechs = fuenf_folgen + [_start(100, JOB)]
    assert start(anfrage(), z(MAX_FOLGEN="5", MAX_START_STUNDE="60", MAX_START_TAG="200"), sechs) == "grenze_folgen"
    andere = [_start(500 + i * 60, "e" * 16) for i in range(6)]
    assert start(anfrage(), z(MAX_FOLGEN="5", MAX_START_STUNDE="60", MAX_START_TAG="200"), andere) is None  # andere Aufträge zählen nicht


def test_abstand_zum_letzten_lauf():
    zs = zustand(konfig_text(ARTEN="recherche", MIN_ABSTAND_S="60"))
    assert start(anfrage(), zs, [_ende(59, 5)]) == "abstand"
    assert start(anfrage(), zs, [_ende(60, 5)]) is None
    assert start(anfrage(), zs, [_start(10)]) == "abstand"  # ein laufender Lauf (Start ohne Ende) zählt als letzter Zeitpunkt
    assert start(anfrage(), zs, [_ende(500, 5), _start(30)]) == "abstand"
    assert start(anfrage(), zs, []) is None
    assert start(anfrage(), zustand(konfig_text(ARTEN="recherche", MIN_ABSTAND_S="0")), [_ende(0, 5)]) is None
    assert start(anfrage(), zs, [{"t": int(JETZT + 100), "job": "f" * 16, "ereignis": "ende", "dauer_s": 1}]) == "abstand"  # Uhrsprung: gesperrt


def test_ein_nicht_lesbarer_zaehler_sperrt_erst_bei_regel_9():
    zs = z()
    assert K.pruefe_start(anfrage(), zs, ereignisse=None, jetzt=JETZT, freier_platz_mb=10_000) == "grenze_start"
    assert K.pruefe_start(anfrage(zugang=None), zs, ereignisse=None, jetzt=JETZT, freier_platz_mb=10_000) == "zugang"  # frühere Regeln zuerst
    assert K.pruefe_start(anfrage(), zustand(konfig_text(), notaus=True), ereignisse=None, jetzt=JETZT, freier_platz_mb=1) == "notaus"


def test_platz_vor_jedem_start():
    assert start(anfrage(), z(), platz=2048) is None
    assert start(anfrage(), z(), platz=2047) == "platz"
    assert start(anfrage(), z(), platz=None) == "platz"  # nicht messbar ist gesperrt


def test_die_reihenfolge_der_grenzen_in_regel_9():
    alles = [_start(100 + i * 10) for i in range(4)] + [_ende(50, 14400)]
    zs = zustand(konfig_text(ARTEN="recherche", MIN_ABSTAND_S="60", MAX_FOLGEN="0"))
    assert start(anfrage(), zs, alles, platz=1) == "grenze_start"
    assert start(anfrage(), zs, [_ende(50, 14400)], platz=1) == "grenze_laufzeit"
    assert start(anfrage(), zs, [_ende(50, 5), _start(900, JOB)], platz=1) == "grenze_folgen"
    assert start(anfrage(), zs, [_ende(50, 5)], platz=1) == "abstand"
    assert start(anfrage(), zs, [], platz=1) == "platz"


def test_alle_codes_der_regeln_gehoeren_zur_festen_liste_i6():
    assert K.KANAL_GRUENDE == ("notaus", "konfig_ungueltig", "kanal_zu", "abgelaufen", "befehl", "fremd", "art", "repo", "repo_fehlt",
                               "dienst", "projekt", "zugang", "fork", "herkunft_unklar", "grenze_start", "grenze_laufzeit",
                               "grenze_folgen", "abstand", "platz")
    quelle = MODUL_PFAD.read_text(encoding="utf-8")
    for code in K.KANAL_GRUENDE:
        assert quelle.count(f'"{code}"') >= 2, code  # jeder Code steht in der Liste und wird irgendwo geliefert
    assert K.BEFEHLE_KERN == ("ping", "start", "status", "pending", "approve", "cancel")


# ---------------------------------------------------------------- Fork-Prüfung (Regel 8)
def repo_antwort(**ueber):
    antwort = {"full_name": "acme/test-repo", "name": "test-repo", "fork": False, "private": True}
    antwort.update(ueber)
    return antwort


def test_ein_normales_repo_besteht_die_fork_pruefung():
    assert K.fork_pruefen(repo_antwort()) is None
    assert K.fork_pruefen(repo_antwort(), repo="Acme/Test-Repo") is None
    assert K.fork_pruefen(repo_antwort(template_repository=None, parent=None, source=None)) is None


@pytest.mark.parametrize("antwort", [
    repo_antwort(fork=True),
    repo_antwort(fork=True, parent={"full_name": "acme/anderes"}, source={"full_name": "acme/anderes"}),
    repo_antwort(template_repository={"full_name": "acme/vorlage"}),
    repo_antwort(parent={"full_name": "wamocon/wamo"}),
    repo_antwort(source={"full_name": "wamocon/WAMO"}),
    repo_antwort(fork=True, source={"full_name": "x/Wamo"}),
    repo_antwort(template_repository={"full_name": "x/wamo"}),
    repo_antwort(fork="ja"), repo_antwort(fork=None), repo_antwort(fork=1),
    {"full_name": "acme/test-repo"},  # fork fehlt
    {}, [], None, "text", 5, b"{}",
    repo_antwort(parent="wamocon/wamo"), repo_antwort(source=["wamocon/wamo"]),
    repo_antwort(parent={"full_name": 5}), repo_antwort(parent={}),
    repo_antwort(template_repository="x"),
], ids=lambda a: repr(a)[:60])
def test_fork_vorlage_ursprung_wamo_und_unklare_antworten_ergeben_fork(antwort):
    assert K.fork_pruefen(antwort) == "fork"


def test_ein_anderes_repo_als_angefragt_ist_unklar_und_damit_gesperrt():
    assert K.fork_pruefen(repo_antwort(full_name="acme/anderes"), repo="acme/test-repo") == "fork"
    assert K.fork_pruefen({"fork": False, "name": "test-repo"}, repo="acme/test-repo") == "fork"  # full_name fehlt


# ---------------------------------------------------------------- Zähler
def test_zaehler_schreiben_und_lesen_roundtrip(tmp_path):
    pfad = tmp_path / "kernkanal.jsonl"
    K.zaehler_schreiben(pfad, JOB, "start", JETZT)
    K.zaehler_schreiben(pfad, JOB, "ende", JETZT + 90.7, dauer_s=90)
    zeilen = [json.loads(z) for z in pfad.read_text(encoding="utf-8").splitlines()]
    assert zeilen == [{"t": int(JETZT), "job": JOB, "ereignis": "start"}, {"t": int(JETZT + 90.7), "job": JOB, "ereignis": "ende", "dauer_s": 90}]
    assert K.zaehler_lesen(pfad) == zeilen


def test_zaehler_lesen_ignoriert_kaputte_zeilen_und_fehlt_die_datei_ist_er_leer(tmp_path):
    pfad = tmp_path / "z.jsonl"
    pfad.write_text('kein json\n{"t": 1}\n{"t": "x", "job": "a", "ereignis": "start"}\n'
                    f'{{"t": 5, "job": "{JOB}", "ereignis": "start"}}\n'
                    f'{{"t": 6, "job": "../x", "ereignis": "start"}}\n'
                    f'{{"t": 7, "job": "{JOB}", "ereignis": "sonst"}}\n'
                    f'{{"t": 8, "job": "{JOB}", "ereignis": "ende", "dauer_s": -3}}\n'
                    f'{{"t": 9, "job": "{JOB}", "ereignis": "ende", "dauer_s": 4}}\n[]\n', encoding="utf-8")
    assert K.zaehler_lesen(pfad) == [{"t": 5, "job": JOB, "ereignis": "start"}, {"t": 9, "job": JOB, "ereignis": "ende", "dauer_s": 4}]
    assert K.zaehler_lesen(tmp_path / "gibt-es-nicht") == []


def test_zaehler_lesen_liest_bei_grossen_dateien_nur_das_ende(tmp_path):
    pfad = tmp_path / "gross.jsonl"
    zeile = json.dumps({"t": 1, "job": JOB, "ereignis": "start"}) + "\n"
    pfad.write_bytes(zeile.encode("utf-8") * ((K.MAX_ZAEHLER_BYTES // len(zeile)) + 500))
    gelesen = K.zaehler_lesen(pfad)
    assert 0 < len(gelesen) <= K.MAX_ZAEHLER_BYTES // len(zeile) + 1
    assert all(e == {"t": 1, "job": JOB, "ereignis": "start"} for e in gelesen)  # die angeschnittene erste Zeile fällt weg


def test_zaehler_schreiben_prueft_seine_eingaben(tmp_path):
    pfad = tmp_path / "z.jsonl"
    with pytest.raises(ValueError):
        K.zaehler_schreiben(pfad, "../x", "start", JETZT)
    with pytest.raises(ValueError):
        K.zaehler_schreiben(pfad, JOB, "unfug", JETZT)
    with pytest.raises(ValueError):
        K.zaehler_schreiben(pfad, JOB, "ende", JETZT, dauer_s=-1)
    assert not pfad.exists()


def test_ein_nicht_beschreibbarer_zaehler_wirft_oserror(tmp_path):
    with pytest.raises(OSError):
        K.zaehler_schreiben(tmp_path / "gibt-es" / "nicht" / "z.jsonl", JOB, "start", JETZT)


def test_der_freie_platz_wird_in_mebibyte_gemessen_und_ein_fehler_ist_none(tmp_path):
    assert isinstance(K.freier_platz_mb(tmp_path), int) and K.freier_platz_mb(tmp_path) > 0
    assert K.freier_platz_mb(tmp_path / "gibt-es-nicht") is None


# ---------------------------------------------------------------- Eigenschaften des Moduls
def _baum():
    return ast.parse(MODUL_PFAD.read_text(encoding="utf-8"))


def test_das_modul_hat_keinen_zugriff_auf_geheimnisse_netz_oder_prozesse():
    baum = _baum()
    importe = {a.name.split(".")[0] for n in ast.walk(baum) if isinstance(n, ast.Import) for a in n.names}
    importe |= {n.module.split(".")[0] for n in ast.walk(baum) if isinstance(n, ast.ImportFrom) and n.module}
    assert importe <= {"__future__", "base64", "binascii", "dataclasses", "datetime", "errno", "json", "os", "pathlib", "re", "shutil",
                       "stat", "time", "typing", "collections"}, importe
    text = MODUL_PFAD.read_text(encoding="utf-8").lower()
    for verboten in ("geheim.env", "lese_geheimnisse", "gh_token", "os.environ", "getenv", "subprocess", "socket", "popen", "password"):
        assert verboten not in text, verboten


def test_der_import_hat_keine_nebenwirkung_nur_definitionen_und_konstanten():
    for knoten in _baum().body:
        assert isinstance(knoten, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign, ast.Expr)), \
            f"Zeile {knoten.lineno}: {type(knoten).__name__}"
        if isinstance(knoten, ast.Expr):  # nur der Docstring
            assert isinstance(knoten.value, ast.Constant) and isinstance(knoten.value.value, str)


def test_das_modul_hat_unix_zeilenenden_und_keine_gedankenstriche():
    roh = MODUL_PFAD.read_bytes()
    assert b"\r" not in roh and not roh.startswith(b"\xef\xbb\xbf")
    text = roh.decode("utf-8")
    assert chr(0x2014) not in text and chr(0x2013) not in text
    for muster in (r"sk-ant-[A-Za-z0-9_-]{20,}", r"ghp_[A-Za-z0-9]{20,}", r"-----BEGIN"):
        assert not re.search(muster, text)


# ---------------------------------------------------------------- Nachbesserung nach der Sicherheitsprüfung
def _versuch(alter_s: float, job: str = "f" * 16) -> dict:
    return {"t": int(JETZT - alter_s), "job": job, "ereignis": "versuch"}


def test_versuche_je_stunde_der_zwanzigste_geht_der_einundzwanzigste_nicht():
    neunzehn = [_versuch(100 + i) for i in range(19)]
    assert start(anfrage(), z(), neunzehn) is None  # das wäre Versuch Nummer 20 = MAX
    assert start(anfrage(), z(), neunzehn + [_versuch(500)]) == "grenze_start"  # Nummer 21


def test_versuche_zaehlen_nur_die_letzte_stunde_und_der_wert_darf_nur_nach_unten_abweichen():
    assert start(anfrage(), z(), [_versuch(3601 + i) for i in range(40)]) is None
    drei = z(MAX_VERSUCH_STUNDE="3")
    assert start(anfrage(), drei, [_versuch(100), _versuch(200)]) is None
    assert start(anfrage(), drei, [_versuch(100), _versuch(200), _versuch(300)]) == "grenze_start"
    assert lese(konfig_text(MAX_VERSUCH_STUNDE="20")).max_versuch_stunde == 20
    assert lese(konfig_text(MAX_VERSUCH_STUNDE="1")).max_versuch_stunde == 1
    assert lese(konfig_text(MAX_VERSUCH_STUNDE=None)).max_versuch_stunde == 20  # fester Standard
    for schlecht in ("21", "0", "-1", "zwanzig", "020", ""):
        with pytest.raises(K.KonfigFehler):
            lese(konfig_text(MAX_VERSUCH_STUNDE=schlecht))


def test_versuche_zaehlen_weder_als_start_noch_als_folge_noch_als_abstand_noch_als_laufzeit():
    viele = [_versuch(i, JOB) for i in range(1, 15)]
    zs = zustand(konfig_text(ARTEN="recherche", MIN_ABSTAND_S="60", MAX_FOLGEN="0", MAX_START_STUNDE="1", MAX_LAUFZEIT_TAG_S="60"))
    assert start(anfrage(), zs, viele) is None


def test_der_zaehler_kennt_das_ereignis_versuch(tmp_path):
    pfad = tmp_path / "z.jsonl"
    K.zaehler_schreiben(pfad, JOB, "versuch", JETZT)
    K.zaehler_schreiben(pfad, JOB, "start", JETZT)
    assert [e["ereignis"] for e in K.zaehler_lesen(pfad)] == ["versuch", "start"]
    with pytest.raises(ValueError):
        K.zaehler_schreiben(pfad, JOB, "versuch", JETZT, dauer_s=-1)


def test_der_abstand_gilt_nicht_fuer_folgen_desselben_auftrags_aber_fuer_jeden_anderen():
    zs = zustand(konfig_text(ARTEN="recherche", MIN_ABSTAND_S="60", MAX_FOLGEN="2", MAX_START_STUNDE="60", MAX_START_TAG="200"))
    runde1 = [_start(300, JOB), _ende(5, 10, JOB)]  # Runde 1 ist eben zu Ende
    assert start(anfrage(), zs, runde1) is None  # Runde 2 sofort danach
    runde2 = runde1 + [_start(20, JOB), _ende(2, 5, JOB)]
    assert start(anfrage(), zs, runde2) is None  # Runde 3 ebenso
    assert start(anfrage(job="e" * 16), zs, runde2) == "abstand"  # ein zweiter, fremder Auftrag wartet
    runde3 = runde2 + [_start(1, JOB)]
    assert start(anfrage(), zs, runde3) == "grenze_folgen"  # MAX_FOLGEN begrenzt weiter (Auftrag plus zwei Folgen)


def test_ein_projekt_das_es_schon_gibt_und_nicht_aus_einem_kern_auftrag_stammt_ist_gesperrt():
    assert start(anfrage(projekt_fremd=True)) == "projekt"
    assert start(anfrage(projekt_fremd=True, zugang=None)) == "projekt"  # Regel 6 vor Regel 7
    assert start(anfrage(projekt_fremd=False)) is None


def test_normalisierte_namen_kollidieren_nur_bei_verschiedenen_repos():
    assert K.repo_kollision("acme/my_repo", ["acme/my-repo"]) is True
    assert K.repo_kollision("acme/my-repo", ["acme/my_repo", "acme/my-repo"]) is True
    assert K.repo_kollision("Acme/My-Repo", ["acme/my-repo"]) is False
    assert K.repo_kollision("acme/x", [None]) is False and K.repo_kollision(None, ["acme/x"]) is False
    assert K.repo_kollision("acme/x", []) is False
    assert start(anfrage(projekt_kollision=True)) == "projekt"
    assert start(anfrage(projekt_kollision=True, zugang=None)) == "projekt"


def test_kern_wamo_ohne_repo_wird_auch_im_dispatcher_abgelehnt():
    assert K.projekt_pruefen("kern-wamo", None) == "projekt"
    for wort in ("wamo", "WAMO", "Wamo", "wamo_"):
        with pytest.raises(K.ProjektnameFehler) as info:
            K.kern_projektname(None, wort)
        assert info.value.grund == "projekt"
    assert K.projekt_pruefen("kern-wamo-extra", None) is None


@pytest.mark.parametrize("trenner", ["\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " "])
def test_zeilen_trennt_nur_der_zeilenumbruch_wie_die_shell(trenner):
    # Die Shell liest eine Zeile bis \n. Ein Zeichen, das Python als Zeilenende kennt, darf keine zweite Zeile erzeugen.
    with pytest.raises(K.KonfigFehler):
        lese(konfig_text(MAX_TURNS=f"41{trenner}MIN_ABSTAND_S=5", MIN_ABSTAND_S=None))
    assert K.schluessel_aus_text(KERN_KEY + trenner + PC_KEY) == set()
    assert K.schluessel_aus_auth(f"keyboard-interactive{trenner}publickey {PC_KEY}\n") is None
    text = f"# Kommentar{trenner}" + KERN_KEY + "\n"
    assert K.schluessel_aus_text(text) == set()  # die ganze Zeile ist ein Kommentar, der Schlüssel dahinter zählt nicht
