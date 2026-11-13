"""Lasttest für das WAMO-Testlabor (docs/testlabor-wamo.md, 3.7, Anleitung docs/labor-lasttest.md): 1, 2, 4 und 8 gleichzeitige Sitzungen am echten Server.

Ein Lauf nimmt N Laborprofile (labor-01 bis labor-08), schaltet sie frei, öffnet je Platz einen Durchlauf im Dienst (Einwilligung Version 1), startet je Sitzung
eine eigene Chrome-Instanz mit virtuellem Mikrofon (scripts/wamo-last-browser.cjs), meldet sie mit Passwort und Code aus der Umschlag-Datei an und lässt sie
die Kette A1, J1, G3 sprechen, mit zufälliger Denkpause von 8 bis 20 Sekunden (fester Seed, wiederholbar). Am Kern wird nur gelesen (CPU und Speicher aus der
cgroup, Zählung im Journal). Am Ende steht ein JSON und ein Bericht mit Bestehen oder Durchfallen je Kriterium. Danach wird aufgeräumt.

Aufrufe (im Repo-Hauptordner, nur Standardbibliothek plus Node mit Playwright für die Browser):
  python scripts/wamo_labor_last.py lauf                                  # Trockenlauf (Standard): Plan, Dauer, Kosten, lokale Befunde, nichts wird gestartet
  python scripts/wamo_labor_last.py lauf --ausfuehren --bestaetigung LASTTEST      # Echtlauf, nur mit beiden Schaltern
  python scripts/wamo_labor_last.py clips                                 # fehlende Sprechproben mit der Windows-Stimme erzeugen
Optionen: --sitzungen 1,2,4,8  --minuten 15 (höchstens 20)  --murmeln 20,10,5 (Stufen mit 8 Sitzungen und Signal-Rausch-Abstand in dB)  --murmeln-datei WAV
--seed 1  --lauf NAME  --schema wamo_dev  --host wamo-sokrates2  --slot b  --einwilligung 1  --freigabe ablehnen|freigeben|keine  --kopflos
--ratenlimit-anfragen N  --ratenlimit-tokens N  --preis-eingabe USD  --preis-ausgabe USD  (Dollar je Million Tokens)  --kosten-obergrenze USD
--clips-ordner, --ausgabe-ordner, --umschlag-datei (Standard unter %USERPROFILE%\\.wamo-labor, nie im Repo).

Sicherheit: Der Trockenlauf ruft nie den Server an. Der Echtlauf braucht --ausfuehren UND --bestaetigung LASTTEST. Höchstgrenzen im Code (scripts/wamo_labor_last_plan.py):
höchstens 8 Sitzungen, höchstens 20 Minuten je Stufe, Abbruch bei 429, bei mehr als 2 Abbrüchen und bei Kernspeicher über 3 GiB. Der Browser darf nur die feste Adresse
der Oberfläche auf Sokrates-2 öffnen. Am Server wird nur gelesen (Kern) oder über die vorhandenen Wege geschrieben (wamo_labor.py aktivieren und sperren, die
Betreuer-Schnittstelle des Dienstes für die Plätze 1 bis 8). Das Aufräumen steht in einem finally und läuft auch bei Strg+C.

Ausgänge: 0 Lauf vollständig und bestanden (oder Trockenlauf), 1 durchgefallen, vorzeitig beendet oder Aufräumen gescheitert, 2 Eingabe oder Einstellung falsch
(nichts wurde angefasst), 3 Lauf vollständig, Urteil offen (zum Beispiel ohne Angabe des Ratenlimits), 130 Abbruch mit Strg+C.
Geheimnisse: Passwörter, Seeds und Codes erscheinen nie in einer Befehlszeile, Datei, Ausgabe oder im Bericht.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wamo_labor as wl  # noqa: E402
import wamo_labor_faktoren_signale as sg  # noqa: E402
import wamo_labor_faktoren_totp as tt  # noqa: E402
import wamo_labor_faktoren_umschlag as ufm  # noqa: E402
import wamo_labor_last_audio as au  # noqa: E402
import wamo_labor_last_auswertung as aw  # noqa: E402
import wamo_labor_last_browser as br  # noqa: E402
import wamo_labor_last_plan as pl  # noqa: E402
import wamo_labor_last_server as sv  # noqa: E402
from wamo_labor_last_lauf import Lauf  # noqa: E402
from wamo_labor_last_umgebung import FREIGABEN, Echtzeit, Einstellungen, Umgebung  # noqa: E402
from wamo_labor_platz_adressen import WAMO_URL  # noqa: E402

WURZEL = Path(__file__).resolve().parent.parent
EXIT_OK, EXIT_FEHLER, EXIT_EINGABE, EXIT_OFFEN, EXIT_ABBRUCH = 0, 1, 2, 3, 130
SCHEMA_RE = re.compile(r"wamo_(dev|test|prod)")
VOICE = "Microsoft Hedda Desktop"
RATE = 16000


# ---------- Eingaben ----------

def standardorte(heim: Path | None = None) -> dict[str, Path]:
    basis = (heim if heim is not None else Path.home()) / ".wamo-labor"
    return {"clips": basis / "last" / "clips", "ausgabe": basis / "last", "umschlag": ufm.standard_pfad(heim)}


def _zahlen(text: str, name: str) -> list[int]:
    try:
        return [int(t) for t in text.split(",") if t.strip()]
    except ValueError:
        raise pl.EingabeFehler(f"{name}: erwartet wird eine Liste ganzer Zahlen, zum Beispiel 1,2,4,8") from None


def _ausserhalb_des_repos(pfad: Path, was: str) -> Path:
    aufgeloest = pfad.expanduser().resolve()
    if aufgeloest == WURZEL or WURZEL in aufgeloest.parents:
        raise pl.EingabeFehler(f"{was} darf nicht im Repo liegen (Aufnahmen, Ergebnisse und Passwörter gehören nie in ein Repo)")
    return aufgeloest


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wamo_labor_last", description="Lasttest für das WAMO-Testlabor (Standard: Trockenlauf)")
    sub = p.add_subparsers(dest="befehl", required=True)
    lauf = sub.add_parser("lauf", help="Plan zeigen (Standard) oder mit --ausfuehren und --bestaetigung LASTTEST wirklich laufen")
    lauf.add_argument("--sitzungen", default="1,2,4,8")
    lauf.add_argument("--minuten", type=int, default=pl.STANDARD_STUFE_MINUTEN)
    lauf.add_argument("--murmeln", default="")
    lauf.add_argument("--murmeln-datei")
    lauf.add_argument("--seed", type=int, default=1)
    lauf.add_argument("--lauf")
    lauf.add_argument("--schema", default="wamo_dev")
    lauf.add_argument("--host", default=os.environ.get("WAMO_SSH_HOST", "wamo-sokrates2"))
    lauf.add_argument("--slot", default="b", choices=("a", "b"))
    lauf.add_argument("--einwilligung", type=int, default=1)
    lauf.add_argument("--freigabe", default="ablehnen", choices=FREIGABEN)
    lauf.add_argument("--kopflos", action="store_true", help="Chrome ohne Fenster (nicht erprobt)")
    lauf.add_argument("--ratenlimit-anfragen", type=int)
    lauf.add_argument("--ratenlimit-tokens", type=int)
    lauf.add_argument("--preis-eingabe", type=float)
    lauf.add_argument("--preis-ausgabe", type=float)
    lauf.add_argument("--kosten-obergrenze", type=float)
    lauf.add_argument("--umschlag-datei")
    lauf.add_argument("--ausfuehren", action="store_true", help="wirklich laufen (nur zusammen mit --bestaetigung LASTTEST)")
    lauf.add_argument("--bestaetigung", default="")
    for s in (lauf, sub.add_parser("clips", help="fehlende Sprechproben mit der Windows-Stimme erzeugen")):
        s.add_argument("--clips-ordner")
    lauf.add_argument("--ausgabe-ordner")
    return p


def einstellungen_bauen(args: argparse.Namespace) -> Einstellungen:
    orte = standardorte()
    if not SCHEMA_RE.fullmatch(args.schema):
        raise pl.EingabeFehler("Schema muss wamo_dev, wamo_test oder wamo_prod sein")
    if not sv.HOST_RE.fullmatch(args.host):
        raise pl.EingabeFehler("Ungültiger Host für ssh (Buchstaben, Ziffern, Punkt, Unterstrich, Strich, kein Strich am Anfang)")
    if not 1 <= args.einwilligung <= 99:
        raise pl.EingabeFehler("Die Einwilligungsversion liegt zwischen 1 und 99")
    stufen = pl.stufen_bauen(_zahlen(args.sitzungen, "--sitzungen"), args.minuten, _zahlen(args.murmeln, "--murmeln"))
    lauf = pl.lauf_pruefen(args.lauf or datetime.now().strftime("last-%Y%m%d-%H%M%S"))
    kosten = pl.kosten_schaetzen(stufen, pl.Annahmen(), args.preis_eingabe, args.preis_ausgabe)
    pl.kosten_pruefen(kosten, args.kosten_obergrenze)
    murmeln = None if not args.murmeln_datei else Path(args.murmeln_datei).expanduser().resolve()
    return Einstellungen(
        lauf=lauf, seed=args.seed, stufen=stufen, clips_ordner=Path(args.clips_ordner or orte["clips"]).expanduser().resolve(),
        ausgabe_ordner=_ausserhalb_des_repos(Path(args.ausgabe_ordner or orte["ausgabe"]), "Der Ausgabeordner"), schema=args.schema, host=args.host,
        slot=args.slot, einwilligung=args.einwilligung, murmeln_datei=murmeln, freigabe=args.freigabe, kopflos=args.kopflos,
        ratenlimit_anfragen=args.ratenlimit_anfragen, ratenlimit_tokens=args.ratenlimit_tokens, kosten=kosten)


# ---------- Plan (Trockenlauf) ----------

def _dauer_text(sekunden: int) -> str:
    minuten = round(sekunden / 60)
    stunden, rest = divmod(minuten, 60)
    teile = ([f"{stunden} Stunde{'n' if stunden != 1 else ''}"] if stunden else []) + [f"{rest} Minuten"]
    return " ".join(teile)


def _zahl(wert: float) -> str:
    return f"{wert:.2f}".replace(".", ",")


def plan_text(ein: Einstellungen, echt: bool = False) -> list[str]:
    k = ein.kosten or pl.kosten_schaetzen(ein.stufen)
    a = ein.annahmen
    n = ein.sitzungen_max
    zeilen = [
        f"Lasttest {ein.lauf}: " + ("ECHTLAUF, jetzt wird gestartet." if echt else "Trockenlauf, nichts wird gestartet, kein Aufruf am Server."),
        f"Server: Sokrates-2 (ssh-Alias {ein.host}), Kern-Instanz wamo-core@{ein.slot}, Schema {ein.schema}, nur lesend am Kern",
        f"Adresse der Oberfläche: {ein.url} (die einzige, die der Browser öffnen darf)",
        f"Profile: {ein.profile[0]} bis {ein.profile[-1]} ({n} Profile), Plätze 1 bis {n}, Einwilligung Version {ein.einwilligung}",
        "Stufen:", *[f"  {s.nummer}. {s.name}, {s.minuten} Minuten" for s in ein.stufen],
        f"Kette je Sitzung: A1, J1, G3 im Wechsel, Denkpause {pl.DENKPAUSE_MIN_S:.0f} bis {pl.DENKPAUSE_MAX_S:.0f} Sekunden, Seed {ein.seed}, "
        f"Freigabekarte bei J1: {ein.freigabe}",
        f"Dauer geschätzt: {_dauer_text(pl.dauer_schaetzen(ein.stufen))} (Stufen, Pausen, Vorbereitung und Aufräumen)",
        f"Tokens und Anfragen geschätzt: {k.zuege} Züge, {k.eingabe_tokens} Eingabe-Tokens, {k.ausgabe_tokens} Ausgabe-Tokens, {k.anfragen:.0f} Anfragen",
        "Kosten in Dollar: " + ("nicht geschätzt (keine Preise angegeben, --preis-eingabe und --preis-ausgabe)" if k.usd is None else f"{_zahl(k.usd)} USD"),
        f"Annahmen (nicht gemessen): {a.eingabe_tokens_je_zug} Eingabe-Tokens je Zug, {a.ausgabe_tokens_je_zug} Ausgabe-Tokens je Zug, "
        f"{str(a.anfragen_je_zug).replace('.', ',')} Anfragen je Zug",
        f"Abbruch bei: einem 429, mehr als {pl.MAX_ABBRUECHE} Abbrüchen, Speicher des Kerns über {pl.ABBRUCH_SPEICHER_BYTES // pl.GIB} GiB, "
        "dreimal ausgefallener Messung",
        f"Höchstgrenzen im Code: höchstens {pl.MAX_SITZUNGEN} Sitzungen, höchstens {pl.MAX_STUFE_MINUTEN} Minuten je Stufe",
        "Aufräumen (immer): Gespräche beenden, Browser schließen, Profile sperren, Durchläufe beenden, Plätze leeren, Nachweis, Temp-Dateien löschen",
    ]
    return zeilen


def lokale_befunde(ein: Einstellungen, umschlag_pfad: Path, umschlag_oeffnen: Callable) -> list[str]:
    """Nur lokale Prüfungen ohne Netz: Sprechproben, Seeds (nur ob vorhanden), Voraussetzungen. Nie ein Wert aus der Umschlag-Datei."""
    zeilen = []
    try:
        au.clips_pruefen(ein.clips_ordner)
        zeilen.append(f"Sprechproben: alle {len(au.SPRECHPROBEN)} vorhanden")
    except pl.EingabeFehler as e:
        fehlend = str(e).removeprefix("Es fehlen Sprechproben: ").split(". ")[0]
        zeilen.append(f"Sprechproben fehlen: {fehlend} (erzeugen mit: python scripts/wamo_labor_last.py clips)")
    try:
        umschlag = umschlag_oeffnen(umschlag_pfad)
        for kennung in ein.profile:
            try:
                ok = tt.seed_gueltig(umschlag.zugang(kennung).seed)
                zeilen.append(f"  {kennung}: Seed vorhanden" if ok else f"  {kennung}: Seed fehlt (scripts/wamo_labor_faktoren.py einrichten)")
            except ufm.UmschlagFehler as e:
                zeilen.append(f"  {kennung}: {e}")
    except ufm.UmschlagFehler as e:
        zeilen.append(f"Umschlag-Datei: {e}")
    zeilen += [f"Node: {'gefunden' if shutil.which('node') else 'fehlt'}, ssh: {'gefunden' if shutil.which('ssh') else 'fehlt'}"]
    return zeilen


# ---------- Echtlauf ----------

def echte_umgebung(ein: Einstellungen, args: argparse.Namespace) -> Umgebung:
    try:
        umschlag_pfad = ufm.pfad_pruefen(args.umschlag_datei or standardorte()["umschlag"], WURZEL)
    except ufm.UmschlagFehler as e:
        raise pl.EingabeFehler(str(e)) from None
    return Umgebung(
        uhr=Echtzeit(), treiber_fabrik=lambda i: br.NodeTreiber(br.standard_befehl(), name=f"s{i + 1}"), server=sv.Server(ein.host, slot=ein.slot),
        dienst=sv.Dienst(ein.host), kern=wl.run_remote, umschlag=ufm.Umschlag(umschlag_pfad),
        tempordner_fabrik=lambda: Path(tempfile.mkdtemp(prefix="wamo-last-")), ausgabe=lambda t: print(t, flush=True),
        speichern=lambda e: ergebnis_schreiben(ein.ausgabe_ordner, ein.lauf, e))


def ergebnis_schreiben(ordner: Path, lauf: str, ergebnis: dict) -> None:
    """JSON und Bericht, je atomar (Wegwerf-Datei im selben Ordner, dann ersetzen). Enthält keine Geheimnisse."""
    ordner.mkdir(parents=True, exist_ok=True)
    for endung, text in (("json", json.dumps(ergebnis, ensure_ascii=False, indent=1)), ("md", aw.bericht_text(ergebnis))):
        ziel = ordner / f"{lauf}.{endung}"
        wegwerf = ordner / f".{lauf}.{endung}.tmp-{secrets.token_hex(4)}"
        try:
            wegwerf.write_text(text + "\n", encoding="utf-8", newline="\n")
            os.replace(wegwerf, ziel)
        finally:
            with contextlib.suppress(OSError):
                wegwerf.unlink(missing_ok=True)


def ausgangscode(e: dict) -> int:
    if e.get("von_aussen"):
        return EXIT_ABBRUCH
    if e.get("abbruchgrund") or e.get("gesamt") == aw.DURCHGEFALLEN or any(not a["ok"] for a in e.get("aufraeumen", [])):
        return EXIT_FEHLER
    return EXIT_OK if e.get("gesamt") == aw.BESTANDEN else EXIT_OFFEN


def _schluessel_pruefen() -> None:
    try:
        wl.admin_passwort()
    except wl.Fehler as e:
        raise pl.EingabeFehler(str(e)) from None


def echtlauf(ein: Einstellungen, args: argparse.Namespace, umgebung_bauen: Callable) -> int:
    _schluessel_pruefen()
    for zeile in plan_text(ein, echt=True):
        print(zeile, flush=True)
    umg = umgebung_bauen(ein, args)
    alt = sg.umsetzen()
    try:
        ergebnis = Lauf(ein, umg).ausfuehren()
    finally:
        sg.zuruecksetzen(alt)
    ergebnis_schreiben(ein.ausgabe_ordner, ein.lauf, ergebnis)
    code = ausgangscode(ergebnis)
    print(f"Ergebnis: {ergebnis['gesamt']}" + (f", Grund: {ergebnis['abbruchgrund']}" if ergebnis["abbruchgrund"] else "")
          + f". Dateien: {ein.ausgabe_ordner / (ein.lauf + '.json')} und {ein.lauf}.md", flush=True)
    return code


# ---------- Clips ----------

def sprechen_windows(text: str, ziel: Path) -> None:
    """Spricht einen Text mit der Windows-Stimme Hedda in eine WAV-Datei (16 kHz, 16 Bit, mono). Text und Ziel gehen über die Umgebung, nie in den Befehl."""
    ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          f"$s.SelectVoice('{VOICE}'); $s.Rate = 0; "
          f"$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo({RATE}, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, "
          "[System.Speech.AudioFormat.AudioChannel]::Mono); $s.SetOutputToWaveFile($env:WAMO_SPRECH_ZIEL, $f); $s.Speak($env:WAMO_SPRECH_TEXT); $s.Dispose()")
    env = {**os.environ, "WAMO_SPRECH_ZIEL": str(ziel), "WAMO_SPRECH_TEXT": text}
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        raise pl.EingabeFehler("Die Windows-Stimme ließ sich nicht starten (PowerShell fehlt oder die Stimme hängt)") from None
    if r.returncode != 0:
        raise pl.EingabeFehler(f"Die Stimme {VOICE} sprach nicht (ist sie installiert?)")


def clips_befehl(args: argparse.Namespace, sprechen: Callable) -> int:
    ordner = Path(args.clips_ordner or standardorte()["clips"]).expanduser().resolve()
    neu = au.clips_erzeugen(ordner, sprechen)
    print(f"Sprechproben in {ordner}: {len(neu)} neu erzeugt, {len(au.SPRECHPROBEN) - len(neu)} waren schon da.")
    return EXIT_OK


# ---------- Einstieg ----------

def _umschlag_standard(pfad: Path):
    return ufm.Umschlag(ufm.pfad_pruefen(pfad, WURZEL))


def lauf_befehl(args: argparse.Namespace, umgebung_bauen: Callable, umschlag_oeffnen: Callable) -> int:
    ein = einstellungen_bauen(args)
    if args.ausfuehren and args.bestaetigung != pl.BESTAETIGUNG:
        raise pl.EingabeFehler(f"Der Echtlauf braucht --ausfuehren UND --bestaetigung {pl.BESTAETIGUNG}")
    pfad = Path(args.umschlag_datei or standardorte()["umschlag"]).expanduser()
    _ausserhalb_des_repos(pfad, "Die Umschlag-Datei")
    if args.ausfuehren:
        return echtlauf(ein, args, umgebung_bauen)
    for zeile in plan_text(ein) + ["Lokale Befunde:", *lokale_befunde(ein, pfad, umschlag_oeffnen)]:
        print(zeile)
    print(f"Zum Ausführen: python scripts/wamo_labor_last.py lauf --ausfuehren --bestaetigung {pl.BESTAETIGUNG} (mit denselben Optionen)")
    return EXIT_OK


def main(argv: list[str] | None = None, *, umgebung_bauen: Callable = echte_umgebung, umschlag_oeffnen: Callable = _umschlag_standard,
         sprechen: Callable = sprechen_windows) -> int:
    for strom in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            strom.reconfigure(encoding="utf-8", errors="replace")
    try:
        args = _parser().parse_args(argv)
        if args.befehl == "clips":
            return clips_befehl(args, sprechen)
        return lauf_befehl(args, umgebung_bauen, umschlag_oeffnen)
    except pl.EingabeFehler as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return EXIT_EINGABE


if __name__ == "__main__":
    sys.exit(main())
