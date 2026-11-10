"""Befehlskriterien auf Sokrates-2 (abnahme_remote.py): der Kern ruft den Dispatcher-Befehl pruefen, wertet streng und fragt per ping,
ob der Dispatcher das überhaupt kann.

Kein ssh, kein Server: Der Prozess ist die Attrappe FakeProc aus test_dev_agent_sokrates2.py, das Beenden und das Abbrechen sind
Zähler. Geprüft werden: eine einzige stdin-Zeile mit argv bei offenem stdin, die strenge Auswertung (nur ok true, ein echter int 0,
nicht abgebrochen, v gleich 1 und Prozess-Exit 0 sind erfüllt), jeder andere Ausgang mit einem Grund aus einer festen Liste (nie
Text aus der Antwort), das Auflegen (ssh beenden und cancel), der Ping-Cache (5 Sekunden je Aufruf, 5 Minuten gültig, Fehler heißt
nicht möglich, nie eine Ausnahme).

Ausführen:  .venv\\Scripts\\python.exe -m pytest -q -p no:cacheprovider test_abnahme_remote.py   (im Ordner voice-core/)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from unittest import mock

import pytest

import abnahme_befehl
import abnahme_remote
from abnahme_remote import GRUENDE, PING_GRUENDE, DispatcherPing, RemoteAusfuehrer
from test_dev_agent_sokrates2 import FakeProc

JOB = "0123456789abcdef"
ARGV = ("pytest", "-q")
SHA = hashlib.sha256(Path(abnahme_befehl.__file__).read_bytes()).hexdigest()
FALLE = "SCHLIMME-ANWEISUNG-AUS-DER-ANTWORT"  # steht in Antwortfeldern und darf nie in einem Grund auftauchen


def antwort(**ueber) -> dict:
    basis = {"v": 1, "ok": True, "exit": 0, "abgebrochen": False, "grund": None, "dauer_s": 1.5, "ausgabe": "3 passed",
             "gekuerzt": False}
    basis.update(ueber)
    return basis


def ping_antwort(**ueber) -> dict:
    basis = {"ok": True, "version": "6", "runner": True, "waechter": True, "github": "app", "serverzugang": True,
             "kontingent": True, "pruefen": True, "abnahme_befehl_sha256": SHA}
    basis.update(ueber)
    return basis


class Umgebung:
    """Attrappen für den Prozessstart (asyncio.create_subprocess_exec), das Beenden und den Dispatcher-Befehl cancel."""

    def __init__(self, *procs: FakeProc, ssh_fehler: Exception | None = None):
        self.procs, self.starts, self.beendet, self.cancels = list(procs), [], [], []
        self.ssh_fehler = ssh_fehler

    def ssh_argv(self, args: list[str]) -> list[str]:
        if self.ssh_fehler is not None:
            raise self.ssh_fehler
        return ["ssh", "--", "wamo@host", " ".join(args)]

    async def start(self, *argv, **kwargs):
        self.starts.append((list(argv), kwargs))
        proc = self.procs.pop(0)
        if isinstance(proc, Exception):
            raise proc
        return proc

    async def beenden(self, proc) -> None:
        self.beendet.append(proc)
        if isinstance(proc, FakeProc) and proc.returncode is None:
            proc.beenden()

    async def abbrechen(self) -> None:
        self.cancels.append(JOB)

    def ausfuehrer(self, **kw) -> RemoteAusfuehrer:
        return RemoteAusfuehrer(JOB, self.ssh_argv, beenden=self.beenden, abbrechen=self.abbrechen, **kw)

    def patch(self):
        return mock.patch.object(abnahme_remote.asyncio, "create_subprocess_exec", side_effect=self.start)


async def lauf(env: Umgebung, argv=ARGV, **kw):
    with env.patch():
        return await env.ausfuehrer(**kw)(argv, Path("/srv/wamo/projekte/rezepte-app"))


def grund_ist_fest(ergebnis: tuple[int | None, str]) -> bool:
    exit_, text = ergebnis
    return exit_ is None and text in set(GRUENDE.values()) and FALLE not in text


# ---------------------------------------------------------------- Aufruf
async def test_sendet_genau_eine_zeile_argv_und_laesst_stdin_als_lebenslinie_offen():
    proc = FakeProc([antwort()])
    env = Umgebung(proc)
    assert await lauf(env) == (0, "")
    (argv, kwargs), = env.starts
    assert argv[-1] == f"pruefen {JOB}"  # der SSH-Befehl besteht nur aus geprüften Wörtern, das Projekt steht nicht darin
    gesendet = proc.stdin.data
    assert gesendet.endswith(b"\n") and gesendet.count(b"\n") == 1
    assert json.loads(gesendet) == {"argv": list(ARGV)}
    assert proc.stdin_offen_beim_warten is True  # EOF wäre das Zeichen zum Abbrechen
    assert proc.stdin.closed and env.beendet == [proc]  # am Ende zu und beendet
    assert kwargs["stdin"] is not None and kwargs["stdout"] is not None


async def test_der_ausfuehrer_ist_als_entfernt_markiert():
    assert RemoteAusfuehrer.entfernt is True
    assert Umgebung().ausfuehrer().entfernt is True


async def test_gescheiterter_befehl_gibt_exit_und_ausgabe_zurueck():
    env = Umgebung(FakeProc([antwort(ok=False, exit=1, ausgabe="x\n2 failed in 0.3s")]))
    assert await lauf(env) == (1, "x\n2 failed in 0.3s")


async def test_der_befehl_bleibt_unveraendert_auch_mit_sonderzeichen_im_argument():
    proc = FakeProc([antwort()])
    await lauf(Umgebung(proc), argv=("grep", "-q", "ä \"x\"", "a.txt"))
    assert json.loads(proc.stdin.data)["argv"] == ["grep", "-q", "ä \"x\"", "a.txt"]


async def test_ein_zu_langer_befehl_wird_nicht_gesendet():
    env = Umgebung(FakeProc([antwort()]))
    erg = await lauf(env, argv=("grep", "-q", "x" * 9000))
    assert erg == (None, GRUENDE["zu_lang"]) and env.starts == []


# ---------------------------------------------------------------- strenge Auswertung
def kaputt_oder_falsch() -> list:
    ohne = antwort()
    del ohne["gekuerzt"]
    return [
        pytest.param("kein json", id="kein-json"),
        pytest.param("[1, 2]", id="liste"),
        pytest.param("null", id="null"),
        pytest.param(json.dumps(antwort(exit=False)), id="bool-als-exit-false"),
        pytest.param(json.dumps(antwort(ok=False, exit=True)), id="bool-als-exit-true"),
        pytest.param(json.dumps(antwort(exit=0.0)), id="float-als-exit"),
        pytest.param(json.dumps(antwort(exit="0")), id="text-als-exit"),
        pytest.param(json.dumps(antwort(exit=256)), id="exit-zu-gross"),
        pytest.param(json.dumps(antwort(zusatz=1)), id="zusaetzliches-feld"),
        pytest.param(json.dumps(ohne), id="fehlendes-feld"),
        pytest.param(json.dumps(antwort(v=2)), id="v-2"),
        pytest.param(json.dumps(antwort(v=True)), id="v-bool"),
        pytest.param(json.dumps(antwort(v="1")), id="v-text"),
        pytest.param(json.dumps(antwort(v=1.0)), id="v-float"),
        pytest.param(json.dumps(antwort(ok=1)), id="ok-int"),
        pytest.param(json.dumps(antwort(ok="true")), id="ok-text"),
        pytest.param(json.dumps(antwort(abgebrochen=1)), id="abgebrochen-int"),
        pytest.param(json.dumps(antwort(grund=5)), id="grund-zahl"),
        pytest.param(json.dumps(antwort(ausgabe=None)), id="ausgabe-null"),
        pytest.param(json.dumps(antwort(ausgabe=7)), id="ausgabe-zahl"),
        pytest.param(json.dumps(antwort(gekuerzt="nein")), id="gekuerzt-text"),
        pytest.param(json.dumps(antwort(dauer_s="1")), id="dauer-text"),
        pytest.param(json.dumps(antwort(dauer_s=True)), id="dauer-bool"),
        pytest.param(json.dumps(antwort(dauer_s=-1)), id="dauer-negativ"),
        pytest.param(json.dumps(antwort()).replace('"ok": true', '"ok": false, "ok": true'), id="doppeltes-feld"),
        pytest.param(json.dumps(antwort()).replace('"dauer_s": 1.5', '"dauer_s": NaN'), id="dauer-nan"),
    ]


@pytest.mark.parametrize("zeile", kaputt_oder_falsch())
async def test_ungueltige_antwort_ist_nicht_erfuellt_mit_festem_grund(zeile):
    env = Umgebung(FakeProc([zeile.replace("3 passed", FALLE)]))  # Text aus der Antwort darf nie als Grund erscheinen
    erg = await lauf(env)
    assert grund_ist_fest(erg), erg
    assert erg[1] == GRUENDE["antwort"]


async def test_ok_gilt_nur_bei_ok_true_int_null_nicht_abgebrochen_und_v_eins():
    """Jede einzelne Bedingung für sich: ist eine verletzt, ist es nicht erfüllt."""
    assert await lauf(Umgebung(FakeProc([antwort()]))) == (0, "")
    for ueber in ({"ok": False}, {"exit": 1}, {"abgebrochen": True}, {"ok": False, "exit": None}):
        erg = await lauf(Umgebung(FakeProc([antwort(**ueber)])))
        assert erg[0] != 0, ueber
    erg = await lauf(Umgebung(FakeProc([antwort(ok=True, exit=None)])))
    assert erg[0] is None and erg[1] in set(GRUENDE.values())


async def test_abgebrochen_ist_nicht_erfuellt_auch_mit_ok_und_exit_null():
    erg = await lauf(Umgebung(FakeProc([antwort(abgebrochen=True)])))
    assert erg == (None, GRUENDE["abgebrochen"])


async def test_keine_ausgabe_vom_prozess_ist_nicht_erfuellt():
    erg = await lauf(Umgebung(FakeProc([], returncode=0)))
    assert erg == (None, GRUENDE["keine_antwort"])


async def test_zeile_ohne_zeilenende_und_zweite_zeile_sind_ungueltig():
    erg = await lauf(Umgebung(FakeProc([json.dumps(antwort())] * 2)))
    assert erg == (None, GRUENDE["antwort"])  # nach der Antwort darf nichts mehr kommen
    proc = FakeProc([], haengt=True)
    proc.stdout.feed_data(json.dumps(antwort()).encode())  # EOF ohne Zeilenende
    proc.stdout.feed_eof()
    proc._ende.set()
    assert await lauf(Umgebung(proc)) == (None, GRUENDE["antwort"])


async def test_eine_zu_grosse_antwort_wird_nicht_ausgewertet():
    zeile = json.dumps(antwort(ausgabe="x" * 200_000))
    erg = await lauf(Umgebung(FakeProc([zeile])))
    assert erg == (None, GRUENDE["antwort"])


@pytest.mark.parametrize("code, schluessel", [(2, "abgewiesen"), (3, "selbsttest"), (4, "belegt"), (6, "intern"),
                                              (124, "zeitlimit"), (130, "abgebrochen"), (255, "verbindung"),
                                              (1, "unerwartet"), (5, "unerwartet"), (7, "unerwartet"), (-9, "unerwartet")])
async def test_prozess_exit_ausser_null_ist_nicht_erfuellt_auch_wenn_die_antwort_ok_sagt(code, schluessel):
    env = Umgebung(FakeProc([antwort(grund=FALLE, ausgabe=FALLE)], returncode=code))  # eine Antwort, die ok behauptet
    erg = await lauf(env)
    assert erg == (None, GRUENDE[schluessel])


async def test_prozess_exit_ohne_antwort_zeigt_denselben_grund():
    for code, schluessel in ((255, "verbindung"), (2, "abgewiesen"), (124, "zeitlimit")):
        assert await lauf(Umgebung(FakeProc([], returncode=code))) == (None, GRUENDE[schluessel])


async def test_abweisung_ohne_venv_hat_ihren_eigenen_festen_grund():
    meldung = "nicht möglich: Im Projektordner liegt keine .venv, und uv installiert hier nichts. " + FALLE
    erg = await lauf(Umgebung(FakeProc([antwort(ok=False, exit=None, grund=meldung, ausgabe="")], returncode=2)))
    assert erg == (None, GRUENDE["kein_venv"])
    anders = await lauf(Umgebung(FakeProc([antwort(ok=False, exit=None, grund=FALLE, ausgabe="")], returncode=2)))
    assert anders == (None, GRUENDE["abgewiesen"])


async def test_alle_gruende_sind_kurz_einzeilig_und_ohne_gedankenstrich():
    for text in [*GRUENDE.values(), *PING_GRUENDE.values()]:
        assert 0 < len(text) <= 190 and "\n" not in text
        assert " - " not in text and "–" not in text and "—" not in text


# ---------------------------------------------------------------- Ausgabe ist ein Datenfeld
async def test_ausgabe_hoechstens_zwei_kib_ohne_steuerzeichen_das_ende_bleibt():
    roh = "\x1b[31mrot\x1b[0m\x00‮" + "a" * 5000 + "\nEnde: 2 failed"
    exit_, text = await lauf(Umgebung(FakeProc([antwort(ok=False, exit=1, ausgabe=roh)])))
    assert exit_ == 1
    assert len(text.encode("utf-8")) <= 2048
    assert text.endswith("Ende: 2 failed")
    assert not any(ord(c) < 32 and c not in "\n\t" for c in text) and "\x7f" not in text and "‮" not in text


async def test_ausgabe_mit_mehrbytezeichen_wird_nie_mitten_im_zeichen_geschnitten():
    text = (await lauf(Umgebung(FakeProc([antwort(ok=False, exit=1, ausgabe="ä" * 3000)]))))[1]
    assert len(text.encode("utf-8")) <= 2048 and set(text) == {"ä"}


# ---------------------------------------------------------------- SSH-Fehler und Zeit
async def test_ssh_fehler_sind_nicht_erfuellt_und_werfen_nie():
    for env in (Umgebung(OSError("ssh weg")), Umgebung(ssh_fehler=RuntimeError("ssh nicht gefunden")),
                Umgebung(ssh_fehler=ValueError("ungültig"))):
        assert await lauf(env) == (None, GRUENDE["verbindung"])
    proc = FakeProc([antwort()])

    def bruch(daten):
        raise BrokenPipeError

    proc.stdin.write = bruch
    assert await lauf(Umgebung(proc)) == (None, GRUENDE["verbindung"])


async def test_unerwarteter_fehler_im_kern_ist_nicht_erfuellt_und_wirft_nie():
    env = Umgebung(KeyError("kaputt"))  # eine Ausnahme, mit der niemand rechnet
    assert await lauf(env) == (None, GRUENDE["kern"])


async def test_zeitueberschreitung_beendet_ssh_und_ist_nicht_erfuellt():
    proc = FakeProc([], haengt=True)
    env = Umgebung(proc)
    erg = await lauf(env, frist_s=0.1)
    assert erg == (None, GRUENDE["zeitlimit_kern"])
    assert env.beendet == [proc] and proc.killed and proc.stdin.closed  # EOF und Ende: der Dispatcher beendet die Sandbox selbst
    assert env.cancels == []  # ein einzelnes Zeitlimit vergiftet den Auftrag nicht: cancel würde jede weitere Prüfung abweisen


async def test_antwort_ohne_prozessende_ist_eine_zeitueberschreitung():
    proc = FakeProc([antwort()], haengt=True)
    proc.stdout.feed_eof()
    env = Umgebung(proc)
    with mock.patch.object(abnahme_remote, "NACHLAUF_S", 0.1):
        assert await lauf(env, frist_s=5) == (None, GRUENDE["zeitlimit_kern"])
    assert proc.killed


# ---------------------------------------------------------------- Auflegen
async def test_auflegen_beendet_ssh_und_ruft_cancel():
    proc = FakeProc([], haengt=True)
    env = Umgebung(proc)
    with env.patch():
        task = asyncio.create_task(env.ausfuehrer()(ARGV, Path("x")))
        for _ in range(50):
            if proc.stdin.data:
                break
            await asyncio.sleep(0.01)
        assert proc.stdin.data  # die Zeile ist raus, die Antwort steht noch aus
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert env.beendet == [proc] and proc.killed and proc.stdin.closed
    assert env.cancels == [JOB]


async def test_ein_fehlschlag_von_cancel_verschluckt_den_abbruch_nicht():
    proc = FakeProc([], haengt=True)
    env = Umgebung(proc)

    async def kaputt():
        raise OSError("cancel ging schief")

    ausfuehrer = RemoteAusfuehrer(JOB, env.ssh_argv, beenden=env.beenden, abbrechen=kaputt)
    with env.patch():
        task = asyncio.create_task(ausfuehrer(ARGV, Path("x")))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert proc.killed


async def test_ohne_abbruch_wird_cancel_nie_gerufen():
    env = Umgebung(FakeProc([antwort()]))
    await lauf(env)
    assert env.cancels == []


# ---------------------------------------------------------------- Ping
class Uhr:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def ping_umgebung(*procs, **kw):
    env = Umgebung(*procs)
    uhr = Uhr()
    ping = DispatcherPing(env.ssh_argv, beenden=env.beenden, uhr=uhr, **kw)
    return env, uhr, ping


async def holen(env: Umgebung, ping: DispatcherPing) -> str | None:
    with env.patch():
        await ping.auffrischen()
    return ping.grund()


async def test_ohne_ping_ist_nichts_moeglich_und_die_abfrage_blockiert_nie():
    _, _, ping = ping_umgebung()
    assert ping.grund() == PING_GRUENDE["nicht_erreichbar"]
    assert ping.angeboten() is False


async def test_ping_mit_pruefen_und_gleicher_pruefsumme_ist_moeglich():
    proc = FakeProc([ping_antwort()])
    env, _, ping = ping_umgebung(proc)
    assert await holen(env, ping) is None
    assert ping.angeboten() is True
    (argv, _), = env.starts
    assert argv[-1] == "ping"
    assert env.beendet == [proc]


@pytest.mark.parametrize("ueber, schluessel", [
    ({"pruefen": False}, "ohne_pruefen"),
    ({"pruefen": "true"}, "ohne_pruefen"),
    ({"pruefen": 1}, "ohne_pruefen"),
    ({"abnahme_befehl_sha256": "0" * 64}, "pruefsumme"),
    ({"abnahme_befehl_sha256": None}, "pruefsumme"),
    ({"abnahme_befehl_sha256": SHA.upper()[:10]}, "pruefsumme"),
    ({"ok": False}, "nicht_erreichbar"),
])
async def test_ping_der_nicht_passt_lehnt_ab_mit_dem_konkreten_grund(ueber, schluessel):
    env, _, ping = ping_umgebung(FakeProc([ping_antwort(**ueber)]))
    assert await holen(env, ping) == PING_GRUENDE[schluessel]


async def test_alter_dispatcher_ohne_das_feld_pruefen_lehnt_ab():
    alt = ping_antwort(version="5")
    del alt["pruefen"], alt["abnahme_befehl_sha256"]
    env, _, ping = ping_umgebung(FakeProc([alt]))
    assert await holen(env, ping) == PING_GRUENDE["ohne_pruefen"]
    assert ping.angeboten() is False


async def test_ping_exit_2_heisst_dispatcher_ohne_pruefen_andere_fehler_nicht_erreichbar():
    env, _, ping = ping_umgebung(FakeProc([{"ok": False}], returncode=2))
    assert await holen(env, ping) == PING_GRUENDE["ohne_pruefen"]
    for code in (255, 6, 124):
        env, _, ping = ping_umgebung(FakeProc([ping_antwort()], returncode=code))
        assert await holen(env, ping) == PING_GRUENDE["nicht_erreichbar"]


@pytest.mark.parametrize("zeile", ["kein json", "[]", "null", '{"ok": true, "pruefen": true, "ok": true}', ""])
async def test_unbrauchbarer_ping_heisst_nicht_moeglich_ohne_ausnahme(zeile):
    env, _, ping = ping_umgebung(FakeProc([zeile] if zeile else []))
    assert await holen(env, ping) == PING_GRUENDE["nicht_erreichbar"]


async def test_ssh_fehler_beim_ping_heisst_nicht_moeglich_ohne_ausnahme():
    for env in (Umgebung(OSError("weg")), Umgebung(ssh_fehler=RuntimeError("ssh nicht gefunden"))):
        ping = DispatcherPing(env.ssh_argv, beenden=env.beenden, uhr=Uhr())
        assert await holen(env, ping) == PING_GRUENDE["nicht_erreichbar"]


async def test_haengender_ping_endet_nach_der_frist_und_beendet_den_prozess():
    proc = FakeProc([], haengt=True)
    env, _, ping = ping_umgebung(proc, frist_s=0.1)
    zeit = asyncio.get_running_loop().time()
    assert await holen(env, ping) == PING_GRUENDE["nicht_erreichbar"]
    assert asyncio.get_running_loop().time() - zeit < 1.0
    assert proc.killed and env.beendet == [proc]


async def test_die_frist_je_aufruf_ist_fuenf_sekunden_und_die_gueltigkeit_fuenf_minuten():
    assert abnahme_remote.PING_FRIST_S == 5.0
    assert abnahme_remote.PING_GUELTIG_S == 300.0


async def test_ping_ist_fuenf_minuten_gueltig_danach_wird_neu_gefragt():
    env, uhr, ping = ping_umgebung(FakeProc([ping_antwort()]), FakeProc([ping_antwort(pruefen=False)]))
    assert await holen(env, ping) is None
    uhr.t += 299
    assert await holen(env, ping) is None and len(env.starts) == 1  # aus dem Cache, kein zweiter Aufruf
    uhr.t += 2  # 301 Sekunden
    assert ping.grund() == PING_GRUENDE["nicht_erreichbar"]  # abgelaufen heißt unbekannt, nicht mehr möglich
    assert await holen(env, ping) == PING_GRUENDE["ohne_pruefen"] and len(env.starts) == 2


async def test_ein_fehler_wird_nur_kurz_gemerkt_damit_nicht_jeder_aufruf_ssh_startet():
    env, uhr, ping = ping_umgebung(FakeProc([], returncode=255), FakeProc([ping_antwort()]))
    assert await holen(env, ping) == PING_GRUENDE["nicht_erreichbar"]
    assert await holen(env, ping) == PING_GRUENDE["nicht_erreichbar"] and len(env.starts) == 1
    uhr.t += abnahme_remote.PING_FEHLER_GUELTIG_S + 1
    assert await holen(env, ping) is None and len(env.starts) == 2


async def test_gleichzeitige_aufrufe_fragen_nur_einmal():
    env, _, ping = ping_umgebung(FakeProc([ping_antwort()]), FakeProc([ping_antwort()]))
    with env.patch():
        await asyncio.gather(ping.auffrischen(), ping.auffrischen(), ping.auffrischen())
    assert len(env.starts) == 1 and ping.grund() is None


async def test_angeboten_bleibt_nach_ablauf_bekannt_und_ein_fehler_aendert_es_nicht():
    env, uhr, ping = ping_umgebung(FakeProc([ping_antwort()]), FakeProc([], returncode=255), FakeProc([ping_antwort(pruefen=False)]))
    await holen(env, ping)
    uhr.t += 10_000
    assert ping.grund() is not None and ping.angeboten() is True  # nur für die Beschreibung, nie als Freigabe
    await holen(env, ping)  # Fehler
    assert ping.angeboten() is True
    uhr.t += 10_000
    await holen(env, ping)  # eindeutig: ohne pruefen
    assert ping.angeboten() is False


async def test_die_pruefsumme_ist_die_der_lokalen_datei_abnahme_befehl_py():
    assert abnahme_remote.lokale_pruefsumme() == SHA
    env = Umgebung(FakeProc([ping_antwort(abnahme_befehl_sha256="ab" * 32)]))
    ping = DispatcherPing(env.ssh_argv, beenden=env.beenden, uhr=Uhr(), lokale_sha256=lambda: "ab" * 32)
    assert await holen(env, ping) is None


async def test_ein_abbruch_beim_ping_beendet_den_prozess_und_wird_nicht_verschluckt():
    proc = FakeProc([], haengt=True)
    env, _, ping = ping_umgebung(proc)
    with env.patch():
        task = asyncio.create_task(ping.auffrischen())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert proc.killed
