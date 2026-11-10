#!/usr/bin/python3 -I
"""Rauchtest des Dispatcher-Befehls pruefen auf Sokrates-2 (ohne Claude, ohne Netz, ohne Token).

NUR GESCHRIEBEN: Dieser Test wurde noch nie ausgeführt. Er läuft erst, wenn der Befehl pruefen eingerichtet werden soll, und
nur dann. Installiert als /opt/wamo/bin/wamo-pruefen-rauchtest durch wamo-job-einrichten.sh. Ausführen als wamo auf Sokrates-2:

    sudo -u wamo -H /opt/wamo/bin/wamo-pruefen-rauchtest

Er braucht WAMO_PRUEFEN=an NICHT in /etc/wamo/geheim.env: Das Kindprogramm (--pruefen) schaltet den Schalter nur für sich ein,
indem es lese_geheimnisse ersetzt; der Rest des Dispatchers läuft echt (Sperren per flock, Wächter, Entschärfen, Selbsttest in der
Sandbox, Läufer wamo-job.sh --modus pruefen, systemd-Einheiten, Audit). Das Zeitlimit des Befehls (120 s) wird für die Fälle
Endlosschleife und Fork-Bombe im Kind auf 10 s gesenkt; sonst bliebe der Test minutenlang stehen.

Gemessen und geprüft wird:
  * Schalter aus (WAMO_PRUEFEN fehlt): pruefen ist abgeschaltet, Prozess-Exit 2, keine Einheit, kein Selbsttest.
  * Abweisungen vor jeder Ausführung (Exit 2): fremdes Programm, Option außerhalb der Allowlist, zusätzliches Feld in der
    stdin-Zeile, Auftrag ohne Lauf, die uv-Form ohne .venv ("nicht möglich"), zweiter Aufruf bei belegtem Auftrag (Exit 4, belegt).
  * Echte Programme in der Sandbox (die dort vorhanden sind: python3, node 22, npm, make, grep, eslint, git, uv): make test,
    npm test, grep. Es FEHLEN dort pytest, ruff, mypy, tsc, pnpm, yarn, go, cargo und dotnet: pytest scheitert mit "nicht
    gefunden", ok ist false. Das ist kein Fehler des Dispatchers, das Kriterium gilt als nicht erfüllt.
  * Die Sandbox ist enger als der Auftragslauf: kein Netz (unshare der Netz-Namensräume: NETZ:zu), keine Umgebungsvariable mit
    Token oder Claude (UMGEBUNG:leer), nichts von /etc/wamo, Claude oder dem Zustand des Auftrags (SICHT:leer), nur lo als
    Netzschnittstelle, das Projekt ist beschreibbar, / ist es nicht.
  * Endlosschleife und Fork-Bombe sterben am Zeitlimit, danach läuft keine Einheit und kein Prozess des Befehls mehr.
  * cancel beendet den Lauf, das Ende der Lebenslinie (stdin zu, grund verlassen) ebenso, jeweils samt Einheit.
  * Das Audit (auftraege.log) hat Job, argv, Exit und Dauer (dauer=), nie die Ausgabe; die Dauer des Selbsttests wird ausgegeben.
Das Projekt rauchtest-pruefen und die Auftragsordner werden danach entfernt. Das Projekt wird nur gelöscht, wenn eine Markerdatei
dieses Tests darin liegt (sie legt der Test beim Anlegen selbst an): Ein Ordner gleichen Namens ohne Marker bleibt unberührt, der
Test bricht dann vor dem Anlegen ab.
"""

from __future__ import annotations

import hashlib
import importlib.machinery
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

D = importlib.machinery.SourceFileLoader("wamo_job_dispatch", "/opt/wamo/bin/wamo-job-dispatch").load_module()
DISPATCH = str(D.BIN / "wamo-job-dispatch")
PROJEKT = "rauchtest-pruefen"
PROJ = D.PROJEKTE / PROJEKT
# Das Wegwerfprojekt wird nur gelöscht, wenn diese Datei mit diesem Inhalt darin liegt (sie legt der Test selbst an): ein fremder
# Ordner gleichen Namens bleibt unberührt, der Test bricht dann vor dem Anlegen ab.
MARKER_NAME = ".wamo-pruefen-rauchtest"
MARKER_INHALT = "Wegwerfprojekt des Skripts wamo-pruefen-rauchtest, darf mit dem Test verschwinden\n"
ERGEBNISSE: list[tuple[str, bool]] = []
SCHLUESSEL = ["v", "ok", "exit", "abgebrochen", "grund", "dauer_s", "ausgabe", "gekuerzt"]
KURZ_S = 10  # Zeitlimit des Befehls in den Fällen Endlosschleife und Fork-Bombe (sonst 120 s)

MAKEFILE = "test:\n\t@echo hallo-make; echo ok > ausgabe.txt\nlint:\n\t@exit 3\ncheck:\n\t@sleep 100\nbuild:\n\t@sh -c 'b() { b | b & }; b'\n"
PACKAGE = json.dumps({"name": "rauchtest", "version": "1.0.0", "private": True,
                      "scripts": {"test": "node probe.js", "lint": "node schleife.js"}})
PROBE = r'''
const net = require('net'), fs = require('fs');
const geheim = Object.keys(process.env).filter(k => /ANTHROPIC|CLAUDE|^GH_|GITHUB|VERCEL|SUPABASE|JIRA|TOKEN|SECRET|PASSWORD|^WAMO_/i.test(k));
console.log('UMGEBUNG:' + (geheim.length ? 'enthaelt ' + geheim.join(',') : 'leer'));
const sicht = ['/etc/wamo', '/srv/wamo/.claude', '/srv/wamo/.ssh', '/run/wamo/zustand', '/srv/wamo/.local/share/claude']
  .filter(p => fs.existsSync(p));
console.log('SICHT:' + (sicht.length ? 'sichtbar ' + sicht.join(',') : 'leer'));
const dev = fs.readFileSync('/proc/net/dev', 'utf8').split('\n').slice(2).map(z => z.split(':')[0].trim()).filter(Boolean);
console.log('SCHNITTSTELLEN:' + dev.join(','));
let schreibbar = 'nein';
try { fs.writeFileSync('beschreibbar.txt', 'x'); schreibbar = 'ja'; } catch (e) {}
let wurzel = 'schreibbar';
try { fs.writeFileSync('/etc/wamo-rauchtest', 'x'); } catch (e) { wurzel = 'schreibgeschuetzt'; }
console.log('PROJEKT:' + (schreibbar === 'ja' ? 'schreibbar' : 'gesperrt') + ' WURZEL:' + wurzel);
const ziele = [['1.1.1.1', 443], ['192.168.178.1', 443], ['127.0.0.1', 22], ['127.0.0.1', 5432]];
let offen = [], offene = ziele.length;
ziele.forEach(([h, p]) => {
  const s = net.connect({ host: h, port: p, timeout: 3000 });
  let gemeldet = false;
  const fertig = () => {
    if (gemeldet) return;
    gemeldet = true;
    s.destroy();
    if (--offene === 0) console.log('NETZ:' + (offen.length ? 'offen ' + offen.join(',') : 'zu'));
  };
  s.on('connect', () => { offen.push(h + ':' + p); fertig(); });
  s.on('error', fertig);
  s.on('timeout', fertig);
});
'''
SCHLEIFE = "console.log('Endlosschleife'); while (true) {}\n"


def pruefe(name: str, ok: bool, detail: str = "") -> bool:
    ERGEBNISSE.append((name, ok))
    print(f"[{'OK    ' if ok else 'FEHLER'}] {name}" + (f"  ({detail})" if detail else ""), flush=True)
    return ok


def entfernen(pfad: Path) -> None:
    def frei(func, p, _exc):
        os.chmod(p, stat.S_IRWXU)
        os.chmod(os.path.dirname(p), stat.S_IRWXU)
        func(p)
    if pfad.exists():
        for wurzel, ordner, _ in os.walk(pfad):
            for o in ordner:
                try:
                    os.chmod(os.path.join(wurzel, o), stat.S_IRWXU)
                except OSError:
                    pass
        shutil.rmtree(pfad, onerror=frei)


def marker_vorhanden(pfad: Path) -> bool:
    """Liegt in pfad die Markerdatei dieses Tests (ein echter Ordner, darin eine reguläre Datei ohne Link mit genau dem Inhalt)?"""
    marker = pfad / MARKER_NAME
    try:
        return (pfad.is_dir() and not pfad.is_symlink() and marker.is_file() and not marker.is_symlink()
                and marker.read_text(encoding="utf-8") == MARKER_INHALT)
    except (OSError, ValueError):
        return False


def projekt_freigeben(pfad: Path) -> bool:
    """Das Wegwerfprojekt entfernen, aber nur, wenn es nicht existiert oder die Markerdatei dieses Tests darin liegt. Ein Ordner
    gleichen Namens ohne Marker (auch ein leerer, auch ein Link) gehört jemand anderem und bleibt unberührt: False."""
    if not (pfad.exists() or pfad.is_symlink()):
        return True
    if not marker_vorhanden(pfad):
        return False
    entfernen(pfad)
    return True


def einheiten(job: str) -> str:
    p = subprocess.run(["systemctl", "--user", "list-units", "--no-legend", "--plain", f"wamo-job-{job}-*"],
                       capture_output=True, text=True, env=D.basis_env())
    return p.stdout.strip()


def neuer_auftrag(mit_lauf: bool = True) -> tuple[str, Path]:
    """Ein Auftrag im Rauchtest-Projekt. Mit mit_lauf gilt er als nach einem beendeten Lauf (status.json mit start)."""
    job = uuid.uuid4().hex[:16]
    jobdir = D.job_ordner(job)
    D.vorbereiten(jobdir, job, PROJEKT, "umsetzung")
    D.lauf_vorbereiten(jobdir, PROJEKT)
    if mit_lauf:
        jetzt = int(time.time())
        D._status(jobdir, job, PROJEKT, "umsetzung", start=jetzt - 60, ende=jetzt, ergebnis="fertig", code=0)
    return job, jobdir


# ---------------------------------------------------------------- der Dispatcher als Kind, wie über SSH aufgerufen
def pruefen_kind(job: str, schalter: str, zeitlimit: int) -> int:
    """Kindprogramm: der echte Dispatcher mit dem Schalter nur für diesen Prozess. SSH_CONNECTION fehlt (basis_env), also
    zählen die Argumente von main."""
    echt = D.lese_geheimnisse

    def geheimnisse(path=None):
        daten = {k: v for k, v in echt(path).items() if not k.startswith("WAMO_PRUEF")}
        if schalter == "an":
            daten["WAMO_PRUEFEN"] = "an"
        return daten
    D.lese_geheimnisse = geheimnisse
    D.pruefen_modul().ZEITLIMIT_S = zeitlimit
    D.beenden(D.main(["pruefen", job]))
    return 0


def aufruf(job: str, argv: list[str] | None = None, *, schalter: str = "an", zeitlimit: int = 120, zeile: bytes | None = None,
           stdin_zu_nach_s: float | None = None, cancel_nach_s: float | None = None, waehrend=None,
           warte_s: float = 400) -> tuple[int, dict | None, str, float]:
    """Startet pruefen <job> als Kind: eine stdin-Zeile, stdin bleibt offen (Lebenslinie), bis der Test sie schließt.
    Liefert (Prozess-Exit, Antwortzeile oder None, stderr, Dauer in s). Prüft, dass genau eine JSON-Zeile auf stdout kam."""
    start = time.monotonic()
    p = subprocess.Popen([sys.executable, "-I", __file__, "--pruefen", job, schalter, str(zeitlimit)], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=D.basis_env())
    puffer: dict[str, bytes] = {}
    leser = [threading.Thread(target=lambda k=k, s=s: puffer.__setitem__(k, s.read()), daemon=True)
             for k, s in (("out", p.stdout), ("err", p.stderr))]
    for t in leser:
        t.start()
    p.stdin.write(zeile if zeile is not None else (json.dumps({"argv": argv}) + "\n").encode())
    p.stdin.flush()
    if waehrend is not None:
        time.sleep(8)  # Selbsttest und Start der Sandbox
        waehrend()
    if cancel_nach_s is not None:
        time.sleep(cancel_nach_s)
        subprocess.run([DISPATCH, "cancel", job], capture_output=True, env=D.basis_env())
    if stdin_zu_nach_s is not None:
        time.sleep(stdin_zu_nach_s)
        p.stdin.close()
    try:
        rc = p.wait(timeout=warte_s)
    except subprocess.TimeoutExpired:
        p.kill()
        rc = -1
    for t in leser:
        t.join(timeout=10)
    if not p.stdin.closed:
        p.stdin.close()
    zeilen = [z for z in puffer.get("out", b"").decode("utf-8", "replace").splitlines() if z.strip()]
    antwort = None
    if len(zeilen) == 1:
        try:
            antwort = json.loads(zeilen[0])
        except ValueError:
            antwort = None
    return rc, antwort, puffer.get("err", b"").decode("utf-8", "replace"), time.monotonic() - start


def form_ok(a: dict | None) -> bool:
    return isinstance(a, dict) and list(a) == SCHLUESSEL and a["v"] == 1 and isinstance(a["ok"], bool)


# ---------------------------------------------------------------- Teile
def ping_und_schalter() -> None:
    p = subprocess.run([DISPATCH, "ping"], capture_output=True, text=True, env=D.basis_env())
    ping = json.loads(p.stdout.splitlines()[0]) if p.stdout.strip() else {}
    installiert = D.WAECHTER / "abnahme_befehl.py"
    summe = hashlib.sha256(installiert.read_bytes()).hexdigest() if installiert.is_file() else None
    pruefe("ping: Version 6, Felder pruefen und abnahme_befehl_sha256 (Summe der installierten Kopie)",
           ping.get("version") == "6" and "pruefen" in ping and ping.get("abnahme_befehl_sha256") == summe,
           f"Version {ping.get('version')}, pruefen={ping.get('pruefen')}")
    job, _ = neuer_auftrag()
    rc, a, err, _ = aufruf(job, ["make", "test"], schalter="aus")
    pruefe("Schalter aus (WAMO_PRUEFEN fehlt): pruefen ist abgeschaltet, Exit 2, Antwortzeile mit ok false",
           rc == D.EXIT_ABGEWIESEN and form_ok(a) and a["ok"] is False and "abgeschaltet" in a["grund"]
           and "abgeschaltet" in err and not einheiten(job), f"Exit {rc}, {a and a['grund']}")
    pruefe("Schalter aus: kein Selbsttest, kein Lauf, nichts im Projekt",
           not (PROJ / "ausgabe.txt").exists() and "selbsttest" not in audit(job), audit(job)[-200:])
    entfernen(D.JOBS / job)


def audit(job: str) -> str:
    """Die Zeilen des Audits zu diesem Auftrag (auftraege.log)."""
    try:
        text = (D.LOGS / "auftraege.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(z for z in text.splitlines() if job in z)


def abweisungen() -> None:
    job, _ = neuer_auftrag()
    for name, argv in (("fremdes Programm (bash -c id)", ["bash", "-c", "id"]), ("Option außerhalb der Allowlist (pytest -p x)", ["pytest", "-p", "x"]),
                       ("Interpreter (python3 -c)", ["python3", "-c", "print(1)"]), ("Pfad nach außen (grep ../x)", ["grep", "-q", "x", "../x"]),
                       ("Verkettung im Argument", ["make", "test;id"]), ("npm install", ["npm", "install"])):
        rc, a, _, _ = aufruf(job, argv)
        pruefe(f"abgewiesen vor jeder Ausführung, Exit 2: {name}", rc == D.EXIT_ABGEWIESEN and form_ok(a) and a["ok"] is False and a["exit"] is None,
               f"Exit {rc}")
    rc, a, _, _ = aufruf(job, zeile=b'{"argv": ["make", "test"], "projekt": "anderes-projekt"}\n')
    pruefe("zusätzliches Feld in der stdin-Zeile (Projekt vom Aufrufer): abgewiesen", rc == D.EXIT_ABGEWIESEN and form_ok(a))
    rc, a, _, _ = aufruf(job, zeile=b'{"argv": ["make", "test"]}')
    pruefe("stdin-Zeile ohne Zeilenende: abgewiesen", rc == D.EXIT_ABGEWIESEN and form_ok(a))
    rc, a, _, _ = aufruf(job, zeile=b'{"argv": ["make", "test"], "x": "' + b"a" * 9000 + b'"}\n')
    pruefe("stdin-Zeile über 8 KiB: abgewiesen", rc == D.EXIT_ABGEWIESEN and form_ok(a))
    rc, a, _, _ = aufruf(job, ["uv", "run", "--no-sync", "--offline", "pytest", "-q"])
    pruefe("uv-Form ohne .venv im Projekt: Ergebnis nicht möglich mit Hinweis, Exit 2",
           rc == D.EXIT_ABGEWIESEN and form_ok(a) and a["grund"].startswith("nicht möglich") and ".venv" in a["grund"], f"Exit {rc}")
    ohne_lauf, _ = neuer_auftrag(mit_lauf=False)
    rc, a, _, _ = aufruf(ohne_lauf, ["make", "test"])
    pruefe("Auftrag ohne beendeten Lauf: pruefen gilt nur danach, Exit 2", rc == D.EXIT_ABGEWIESEN and form_ok(a), f"Exit {rc}")
    entfernen(D.JOBS / ohne_lauf)
    entfernen(D.JOBS / job)


def echte_programme() -> None:
    job, _ = neuer_auftrag()
    (PROJ / "ausgabe.txt").unlink(missing_ok=True)
    rc, a, err, dauer = aufruf(job, ["make", "test"])
    pruefe("make test in der Sandbox: ok true, exit 0, Ausgabe hallo-make (bwrap findet make über den gesetzten PATH)",
           rc == 0 and form_ok(a) and a["ok"] is True and a["exit"] == 0 and "hallo-make" in a["ausgabe"], f"Exit {rc}, {dauer:.1f} s, {err[-120:]}")
    pruefe("das Projekt ist beschreibbar: ausgabe.txt liegt danach im Projektordner", (PROJ / "ausgabe.txt").is_file())
    rc, a, _, _ = aufruf(job, ["make", "lint"])
    pruefe("make lint scheitert: ok false, exit ungleich 0, Prozess-Exit 0 (der Befehl lief)",
           rc == 0 and form_ok(a) and a["ok"] is False and isinstance(a["exit"], int) and a["exit"] != 0, f"{a and a['exit']}")
    rc, a, _, _ = aufruf(job, ["grep", "-q", "hallo-make", "Makefile"])
    pruefe("grep -q im Projekt: ok true", rc == 0 and form_ok(a) and a["ok"] is True)
    rc, a, _, _ = aufruf(job, ["pytest", "-q"])
    pruefe("pytest fehlt in der Sandbox: scheitert, ok false, die Ausgabe nennt pytest (kein Fehler des Dispatchers, nicht erfüllt)",
           rc == 0 and form_ok(a) and a["ok"] is False and "pytest" in a["ausgabe"].lower(), f"exit {a and a['exit']}, {a and a['ausgabe'][-100:]!r}")
    rc, a, _, _ = aufruf(job, ["npm", "test"])
    ausgabe = a["ausgabe"] if a else ""
    pruefe("npm test (node 22) läuft: ok true", rc == 0 and form_ok(a) and a["ok"] is True, f"Exit {rc}, {ausgabe[-150:]!r}")
    pruefe("keine Token und kein Claude in der Umgebung der Sandbox (UMGEBUNG:leer)", "UMGEBUNG:leer" in ausgabe, ausgabe[-200:])
    pruefe("nichts von /etc/wamo, Claude oder dem Zustand des Auftrags sichtbar (SICHT:leer)", "SICHT:leer" in ausgabe)
    pruefe("kein Netz: Internet, LAN und Loopback des Hosts sind zu (NETZ:zu)", "NETZ:zu" in ausgabe, ausgabe[-200:])
    pruefe("nur die Schnittstelle lo (eigener Netz-Namensraum, unshare)", "SCHNITTSTELLEN:lo\n" in ausgabe + "\n" and ",eth" not in ausgabe,
           re.sub(r".*(SCHNITTSTELLEN:[^\n]*).*", r"\1", ausgabe, flags=re.S))
    pruefe("Projekt beschreibbar, Wurzel nicht", "PROJEKT:schreibbar WURZEL:schreibgeschuetzt" in ausgabe)
    log = audit(job)
    pruefe("Audit (auftraege.log): Job, argv, Exit und Dauer, nie die Ausgabe",
           "pruefen_ende" in log and "exit=0" in log and "dauer=" in log and "make test" in log and "hallo-make" not in log
           and "UMGEBUNG" not in log, log[-300:])
    dauern = re.findall(r"selbsttest \w+ pruefen ok dauer=([0-9.]+)s", log)
    print(f"        Selbsttest im Modus pruefen: {', '.join(dauern)} s (je Aufruf, nie gemerkt)", flush=True)
    pruefe("der Selbsttest lief vor jedem Aufruf im Modus pruefen und bestand", len(dauern) >= 5, f"{len(dauern)} Selbsttests")
    entfernen(D.JOBS / job)


def zeitlimit() -> None:
    """Endlosschleife und Fork-Bombe sterben am (hier auf 10 s gesenkten) Zeitlimit; danach bleibt nichts zurück."""
    for name, argv in (("Endlosschleife (npm run lint)", ["npm", "run", "lint"]), ("Fork-Bombe (make build)", ["make", "build"])):
        job, _ = neuer_auftrag()
        rc, a, _, dauer = aufruf(job, argv, zeitlimit=KURZ_S)
        time.sleep(2)
        reste = subprocess.run(["pgrep", "-u", str(os.getuid()), "-f", "schleife.js|sh -c b\\(\\)"], capture_output=True, text=True).stdout.split()
        pruefe(f"{name}: endet am Zeitlimit (Exit 124, grund zeitlimit, nicht ok), in unter 60 s",
               rc == D.EXIT_ZEITLIMIT and form_ok(a) and a["ok"] is False and a["grund"] == "zeitlimit" and a["abgebrochen"] is True and dauer < 60,
               f"Exit {rc}, {dauer:.1f} s, {a and a['grund']}")
        pruefe(f"{name}: danach läuft keine Einheit und kein Prozess mehr", not einheiten(job) and not reste, f"{einheiten(job)!r} {reste}")
        entfernen(D.JOBS / job)
    job, _ = neuer_auftrag()
    rc, a, _, _ = aufruf(job, ["make", "test"])
    pruefe("das System lebt nach der Fork-Bombe: der nächste Aufruf läuft normal", rc == 0 and form_ok(a) and a["ok"] is True)
    entfernen(D.JOBS / job)


def abbruch_und_sperren() -> None:
    job, _ = neuer_auftrag()
    start = time.monotonic()
    rc, a, _, _ = aufruf(job, ["make", "check"], cancel_nach_s=8)  # make check schläft 100 s
    pruefe("cancel beendet den Lauf (abgebrochen, nicht ok) samt Einheit, in unter 60 s",
           form_ok(a) and a["abgebrochen"] is True and a["ok"] is False and not einheiten(job) and time.monotonic() - start < 60,
           f"Exit {rc}, {a and a['grund']}")
    entfernen(D.JOBS / job)
    job, _ = neuer_auftrag()
    start = time.monotonic()
    rc, a, _, _ = aufruf(job, ["make", "check"], stdin_zu_nach_s=8)
    pruefe("Ende der Lebenslinie (stdin zu): der Lauf endet sofort, grund verlassen, samt Einheit",
           rc == D.EXIT_ABGEBROCHEN and form_ok(a) and a["grund"] == "verlassen" and a["ok"] is False and not einheiten(job)
           and time.monotonic() - start < 60, f"Exit {rc}, {a and a['grund']}")
    entfernen(D.JOBS / job)
    job, _ = neuer_auftrag()
    zweiter: list = []
    rc, a, _, _ = aufruf(job, ["make", "check"], cancel_nach_s=6,
                         waehrend=lambda: zweiter.append(aufruf(job, ["make", "test"], warte_s=60)[:2]))
    pruefe("zweiter Aufruf bei belegtem Auftrag: belegt, Exit 4, nichts läuft",
           bool(zweiter) and zweiter[0][0] == D.EXIT_BELEGT and form_ok(zweiter[0][1]) and zweiter[0][1]["grund"].startswith("belegt"),
           str(zweiter)[:160])
    entfernen(D.JOBS / job)


def ausfuehren() -> int:
    if os.getuid() == 0 or Path.home() != D.HEIM:
        print("Bitte als wamo ausführen: sudo -u wamo -H /opt/wamo/bin/wamo-pruefen-rauchtest", file=sys.stderr)
        return 1
    if not projekt_freigeben(PROJ):
        print(f"{PROJ} gibt es schon und die Markerdatei dieses Tests ({MARKER_NAME}) fehlt: Der Ordner gehört nicht dem Rauchtest, "
              "er wird nicht gelöscht und der Test bricht ab. Anderen Projektnamen nehmen oder den Ordner selbst prüfen.", file=sys.stderr)
        return 1
    PROJ.mkdir(mode=0o750)
    (PROJ / MARKER_NAME).write_text(MARKER_INHALT, encoding="utf-8")
    (PROJ / "Makefile").write_text(MAKEFILE, encoding="utf-8")
    (PROJ / "package.json").write_text(PACKAGE, encoding="utf-8")
    (PROJ / "probe.js").write_text(PROBE, encoding="utf-8")
    (PROJ / "schleife.js").write_text(SCHLEIFE, encoding="utf-8")
    (D.JOBS / ".projekte" / f"{PROJEKT}.json").unlink(missing_ok=True)
    try:
        ping_und_schalter()
        abweisungen()
        echte_programme()
        zeitlimit()
        abbruch_und_sperren()
    finally:
        if projekt_freigeben(PROJ):
            (D.JOBS / ".projekte" / f"{PROJEKT}.json").unlink(missing_ok=True)
        else:  # ein Prüfbefehl hat die Markerdatei entfernt: lieber liegen lassen als einen womöglich fremden Ordner löschen
            print(f"Hinweis: {PROJ} bleibt liegen (die Markerdatei fehlt oder wurde verändert), bitte von Hand prüfen.", file=sys.stderr)
    fehler = [n for n, ok in ERGEBNISSE if not ok]
    print(f"\n{len(ERGEBNISSE) - len(fehler)} von {len(ERGEBNISSE)} Prüfungen bestanden.")
    return 1 if fehler else 0


def main() -> int:
    if len(sys.argv) == 5 and sys.argv[1] == "--pruefen":
        return pruefen_kind(sys.argv[2], sys.argv[3], int(sys.argv[4]))
    return ausfuehren()


if __name__ == "__main__":
    sys.exit(main())
