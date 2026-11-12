"""Zweiten Faktor (TOTP) der Laborprofile labor-01 bis labor-09 automatisch einrichten (Entscheidung E15, docs/testlabor-wamo.md).

Der Vorgang ist derselbe wie in der Oberfläche (ui/src/components/LoginGate.tsx): Anmeldung mit dem Passwort ergibt aal1, ein TOTP-Faktor
wird angelegt, der Code wird selbst berechnet und bestätigt (challenge und verify), danach trägt das Token aal2. Der Seed (Base32) kommt
als vierte Spalte in die Umschlag-Datei auf dem PC, damit der Mess-PC und die Betreuer am Platz die Codes berechnen können.

Aufrufe (im Repo-Hauptordner, nur Standardbibliothek):
  python scripts/wamo_labor_faktoren.py einrichten --trocken                      # nur der Plan, kein Netz, nichts wird geändert
  python scripts/wamo_labor_faktoren.py einrichten                                # alle neun Profile
  python scripts/wamo_labor_faktoren.py einrichten --profile labor-01,labor-02    # nur diese
  python scripts/wamo_labor_faktoren.py einrichten --profile labor-03 --neu       # Faktor entfernen und neu einrichten
  python scripts/wamo_labor_faktoren.py pruefen                                   # je Profil ja oder nein, nur die Datei, kein Netz
  python scripts/wamo_labor_faktoren.py codes --profil labor-03                   # der aktuelle Code für genau ein Profil
Optionen: --umschlag-datei (Standard %USERPROFILE%\\.wamo-labor\\umschlag-labor.txt, nie im Repo), --basis (Standard SUPABASE_URL aus der
Umgebung oder .env, sonst https://192.168.178.136:55443), --ca (Standard WAMO_CA_FILE, sonst infra/sokrates2/wamo-ca.crt), --env-datei
(weitere .env-Datei mit SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL und WAMO_CA_FILE).

Arbeitsweise je Profil: Passwort aus der Datei, Anmeldung per TLS, vorhandenen Faktor erkennen (ohne --neu wird übersprungen, wenn Faktor
UND Seed vorhanden sind), Faktor anlegen, Code berechnen (RFC 6238, mit dem Versatz der Serveruhr aus dem Kopf Date), bestätigen, prüfen,
dass ein neues Token aal2 trägt, Sitzung beenden und erst dann den Seed schreiben (Sicherung mit Zeitstempel, atomar). Scheitert ein Profil
nach dem Anlegen des Faktors und vor dem Speichern des Seeds, wird der Faktor wieder entfernt. Bricht ein Profil ab, laufen die übrigen
weiter.

Ausgänge: 0 alles in Ordnung (eingerichtet oder übersprungen), 1 mindestens ein Profil ist gescheitert (oder pruefen meldet nein),
2 Eingabe oder Einstellung falsch (nichts wurde am Server angefasst).

Geheimnisse: Passwörter, Seeds, Token und Codes erscheinen nie in der Ausgabe, im Fehlertext oder in einer Befehlszeile. Eine einzige
Ausnahme ist der Befehl codes, der genau einen Code für genau ein Profil ausgibt. Die Umschlag-Datei wird nie angezeigt.
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import wamo_labor_faktoren_api as api
import wamo_labor_faktoren_totp as tt
import wamo_labor_faktoren_umschlag as um

WURZEL = Path(__file__).resolve().parent.parent
# Reihenfolge wie in scripts/wamo_labor.py: die Umgebung gewinnt, dann voice-core/.env, dann die .env im Hauptordner
ENV_DATEIEN: tuple[Path, ...] = (WURZEL / "voice-core" / ".env", WURZEL / ".env")
CA_STANDARD = WURZEL / "infra" / "sokrates2" / "wamo-ca.crt"
STANDARD_PROFILE = tuple(f"labor-{n:02d}" for n in range(1, 10))
EXIT_OK, EXIT_FEHLER, EXIT_EINGABE, EXIT_ABBRUCH = 0, 1, 2, 130
EINGERICHTET, UEBERSPRUNGEN, FEHLER, GEPLANT = "eingerichtet", "übersprungen", "fehlgeschlagen", "geplant"
HINWEIS_UHR_AB_S = 3.0
RECHTE_SETZEN: Callable[[Path], None] = um.rechte_nur_benutzer  # Tests ersetzen es, um nicht jedes Mal icacls aufzurufen


class EingabeFehler(Exception):
    """Falsche Eingabe oder Einstellung, vor dem ersten Aufruf des Servers erkannt."""


@dataclass(frozen=True)
class Ergebnis:
    art: str
    kennung: str
    text: str


@dataclass(frozen=True)
class Einstellung:
    basis: str
    ca: Path | None
    apikey: str = field(repr=False)


class Ausgabe:
    """Alles, was der Lauf ausgibt, geht hier durch. Gemerkte Werte (Passwörter, Seeds, Token, Codes) werden vorher geschwärzt."""

    def __init__(self) -> None:
        self._geheim: list[str] = []

    def merke(self, *werte: object) -> None:
        for w in werte:
            if isinstance(w, str) and len(w) >= 6 and w not in self._geheim:
                self._geheim.append(w)

    def schwaerzen(self, text: str) -> str:
        for g in sorted(self._geheim, key=len, reverse=True):
            text = text.replace(g, "***")
        return text

    def zeile(self, text: str) -> None:
        print(self.schwaerzen(text), file=sys.stdout, flush=True)

    def fehler(self, text: str) -> None:
        print(self.schwaerzen(text), file=sys.stderr, flush=True)


# ---------- Einstellung ----------

def _env_wert(name: str, dateien: tuple[Path, ...]) -> str:
    """Wert aus der Umgebung, sonst aus den .env-Dateien (NAME=Wert, die erste Datei gewinnt)."""
    wert = os.environ.get(name, "").strip()
    if wert:
        return wert
    for datei in dateien:
        if not datei.is_file():
            continue
        for zeile in datei.read_text(encoding="utf-8", errors="replace").splitlines():
            zeile = zeile.strip().removeprefix("export ").lstrip()
            schluessel, trenner, rest = zeile.partition("=")
            if trenner and schluessel.strip() == name and rest.strip().strip("'\"").strip():
                return rest.strip().strip("'\"").strip()
    return ""


def einstellung_laden(args: argparse.Namespace) -> Einstellung:
    dateien = tuple(Path(p) for p in (args.env_datei or ())) + ENV_DATEIEN
    apikey = _env_wert("SUPABASE_PUBLISHABLE_KEY", dateien)
    if not apikey:
        raise EingabeFehler("SUPABASE_PUBLISHABLE_KEY fehlt (Umgebung oder .env, --env-datei nennt eine weitere Datei)")
    basis = args.basis or _env_wert("SUPABASE_URL", dateien) or api.STANDARD_BASIS
    ca_text = args.ca or _env_wert("WAMO_CA_FILE", dateien)
    ca = Path(ca_text) if ca_text else (CA_STANDARD if CA_STANDARD.is_file() else None)
    return Einstellung(basis, ca, apikey)


def profile_lesen(text: str | None) -> list[str]:
    if not text:
        return list(STANDARD_PROFILE)
    ergebnis: list[str] = []
    for teil in (t.strip() for t in text.split(",")):
        if not um.KENNUNG_RE.fullmatch(teil):
            raise EingabeFehler(f"Ungültiges Profil {teil!r}: erlaubt sind labor-01 bis labor-16")
        if teil not in ergebnis:
            ergebnis.append(teil)
    return ergebnis


# ---------- ein Profil einrichten ----------

class _Lauf:
    """Der Weg eines Profils und das, was bei einem Abbruch aufzuräumen ist."""

    def __init__(self, kennung: str, zugang: um.Zugang, verbindung: api.Verbindung, umschlag: um.Umschlag, ausgabe: Ausgabe,
                 warte: Callable[[float], None]):
        self.kennung, self.z, self.v, self.um, self.aus, self.warte = kennung, zugang, verbindung, umschlag, ausgabe, warte
        self.token = ""
        self.refresh = ""
        self.neuer: api.NeuerFaktor | None = None
        self.alte_entfernt = False
        self.gespeichert = False

    def _sitzung(self, s: api.Sitzung) -> api.Sitzung:
        self.token, self.refresh = s.access_token, s.refresh_token
        self.aus.merke(s.access_token, s.refresh_token)
        return s

    def _code(self, seed: str) -> str:
        code = tt.totp_code(seed, tt.frisches_fenster(self.v.jetzt, self.warte))
        self.aus.merke(code)
        return code

    def _bestaetigen(self, faktor_id: str, seed: str) -> api.Sitzung:
        """challenge und verify mit dem selbst berechneten Code. Die Sitzung hat danach aal2."""
        challenge = self.v.challenge(self.token, faktor_id)
        s = self._sitzung(self.v.verify(self.token, faktor_id, challenge, self._code(seed)))
        if s.aal != "aal2":
            raise api.ApiFehler("Das Token trägt nach der Bestätigung nicht aal2")
        return s

    def _alten_faktor_bestaetigen(self, faktor: api.Faktor) -> None:
        """Ein bestätigter Faktor lässt sich nur mit aal2 entfernen: dafür dient der Seed aus der Datei."""
        if not tt.seed_gueltig(self.z.seed):
            raise api.ApiFehler("Der vorhandene Faktor lässt sich ohne Seed in der Datei nicht entfernen (aal2 nötig), "
                                "bitte als Admin am Server entfernen")
        try:
            self._bestaetigen(faktor.id, self.z.seed)
        except api.ApiFehler as e:
            raise api.ApiFehler(f"Der Seed in der Datei passt nicht zum vorhandenen Faktor ({e}), bitte als Admin am Server "
                                "entfernen") from None

    def _alte_entfernen(self, alt: list[api.Faktor]) -> None:
        for f in (f for f in alt if f.status != "verified"):  # liegengebliebene, nie bestätigte Faktoren, wie in der Oberfläche
            self.v.faktor_loeschen(self.token, f.id)
        bestaetigt = [f for f in alt if f.status == "verified"]
        if bestaetigt:
            self._alten_faktor_bestaetigen(bestaetigt[0])
            for f in bestaetigt:
                self.v.faktor_loeschen(self.token, f.id)
            self.alte_entfernt = True

    def _neu_einrichten(self) -> None:
        self.neuer = self.v.faktor_anlegen(self.token, "WAMO-" + secrets.token_hex(3))
        self.aus.merke(self.neuer.seed)
        self._bestaetigen(self.neuer.id, self.neuer.seed)
        if self._sitzung(self.v.erneuern(self.refresh)).aal != "aal2":
            raise api.ApiFehler("Ein neues Token trägt nicht aal2")

    def _abmelden(self) -> None:
        if self.token:
            self.v.abmelden(self.token)
            self.token = ""

    def ausfuehren(self, neu: bool) -> Ergebnis:
        k = self.kennung
        s = self._sitzung(self.v.anmelden(self.z.email, self.z.passwort))
        alt = [f for f in self.v.faktoren(s.access_token) if f.typ == "totp"]  # wie die Oberfläche: die Faktoren aus /user
        if any(f.status == "verified" for f in alt) and not neu:
            self._abmelden()
            if tt.seed_gueltig(self.z.seed):
                return Ergebnis(UEBERSPRUNGEN, k, "Faktor und Seed vorhanden, übersprungen")
            return Ergebnis(FEHLER, k, "Der Faktor ist vorhanden, aber der Seed fehlt in der Datei (mit --neu neu einrichten)")
        self._alte_entfernen(alt)
        self._neu_einrichten()
        self._abmelden()
        self.um.seed_speichern(k, self.neuer.seed)  # erst nach dem Ende der Sitzung
        self.gespeichert = True
        return Ergebnis(EINGERICHTET, k, "Faktor eingerichtet, aal2 geprüft")

    # ---- Abbruch ----

    def _faktor_aufraeumen(self) -> bool:
        """Entfernt den neu angelegten Faktor. Ist die Sitzung schon beendet, wird mit dem Seed aus dem Speicher erneut bestätigt."""
        if self.neuer is None:
            return True
        try:
            if not self.token:
                self._sitzung(self.v.anmelden(self.z.email, self.z.passwort))
                self._bestaetigen(self.neuer.id, self.neuer.seed)
            self.v.faktor_loeschen(self.token, self.neuer.id)
        except api.ApiFehler:
            return False
        self.neuer = None
        return True

    def _seed_der_datei_loeschen(self) -> bool:
        try:
            self.um.seed_entfernen(self.kennung)
        except um.UmschlagFehler:
            return False
        return True

    def abbruch(self, grund: str) -> str:
        hinweise = []
        if self.neuer is not None and not self.gespeichert:
            hinweise.append("Faktor wieder entfernt" if self._faktor_aufraeumen()
                            else "der neue Faktor konnte NICHT entfernt werden, bitte als Admin am Server prüfen")
        if self.alte_entfernt and not self.gespeichert:
            hinweise.append("der alte Faktor war schon entfernt, der alte Seed wurde aus der Datei gelöscht" if self._seed_der_datei_loeschen()
                            else "der alte Faktor war schon entfernt, der alte Seed steht noch in der Datei und gilt nicht mehr")
        try:
            self._abmelden()
        except api.ApiFehler:
            pass
        return grund + ("; " + "; ".join(hinweise) if hinweise else "")


def profil_einrichten(kennung: str, umschlag: um.Umschlag, verbindung: api.Verbindung, neu: bool, ausgabe: Ausgabe,
                      warte: Callable[[float], None]) -> Ergebnis:
    try:
        zugang = umschlag.zugang(kennung)
    except um.UmschlagFehler as e:
        return Ergebnis(FEHLER, kennung, str(e))
    ausgabe.merke(zugang.passwort, zugang.seed)
    lauf = _Lauf(kennung, zugang, verbindung, umschlag, ausgabe, warte)
    try:
        return lauf.ausfuehren(neu)
    except (api.ApiFehler, um.UmschlagFehler) as e:
        grund = lauf.abbruch(str(e))
    except KeyboardInterrupt:
        lauf.abbruch("abgebrochen")
        raise
    except Exception as e:  # nie der Text einer unerwarteten Ausnahme: er könnte einen Wert enthalten
        grund = lauf.abbruch(f"unerwarteter Fehler ({type(e).__name__})")
    return Ergebnis(FEHLER, kennung, grund)


# ---------- Befehle ----------

def _plan(kennung: str, umschlag: um.Umschlag, neu: bool) -> Ergebnis:
    try:
        zugang = umschlag.zugang(kennung)
    except um.UmschlagFehler as e:
        return Ergebnis(FEHLER, kennung, str(e))
    seed = "ja" if tt.seed_gueltig(zugang.seed) else "nein"
    tat = ("den Faktor neu einrichten (ein vorhandener wird ersetzt)" if neu
           else "einen Faktor einrichten, falls keiner vorhanden ist (sonst überspringen)")
    return Ergebnis(GEPLANT, kennung, f"Trockenlauf, würde anmelden und {tat}. Seed in der Datei: {seed}")


def befehl_einrichten(args: argparse.Namespace, profile: list[str], umschlag: um.Umschlag, ausgabe: Ausgabe,
                      zeit: Callable[[], float], warte: Callable[[float], None]) -> int:
    einstellung = einstellung_laden(args)
    verbindung = api.Verbindung(einstellung.basis, einstellung.apikey, einstellung.ca, zeit=zeit)  # prüft Adresse und CA-Datei
    ausgabe.merke(einstellung.apikey)
    for kennung in profile:  # Passwörter vorab merken, damit keine Meldung eines anderen Profils eines nennt
        try:
            ausgabe.merke(umschlag.zugang(kennung).passwort)
        except um.UmschlagFehler:
            pass
    if not args.trocken:
        umschlag.schreibprobe()
    ergebnisse = []
    for kennung in profile:
        e = _plan(kennung, umschlag, args.neu) if args.trocken else profil_einrichten(kennung, umschlag, verbindung, args.neu, ausgabe, warte)
        _melden(ausgabe, e)
        ergebnisse.append(e.art)
    return _abschluss(ergebnisse, args.trocken, verbindung, ausgabe)


def _melden(ausgabe: Ausgabe, e: Ergebnis) -> None:
    if e.art == FEHLER:
        ausgabe.fehler(f"{e.kennung}: FEHLER: {e.text}")
    else:
        ausgabe.zeile(f"{e.kennung}: {e.text}")


def _abschluss(ergebnisse: list[str], trocken: bool, verbindung: api.Verbindung, ausgabe: Ausgabe) -> int:
    n = {art: ergebnisse.count(art) for art in (EINGERICHTET, UEBERSPRUNGEN, FEHLER, GEPLANT)}
    if trocken:
        ausgabe.zeile(f"Zusammenfassung (Trockenlauf, nichts geändert): {n[GEPLANT]} Profile bereit, {n[FEHLER]} mit Problem")
    else:
        ausgabe.zeile(f"Zusammenfassung: {n[EINGERICHTET]} eingerichtet, {n[UEBERSPRUNGEN]} übersprungen, {n[FEHLER]} fehlgeschlagen")
        if abs(verbindung.versatz) >= HINWEIS_UHR_AB_S:
            ausgabe.zeile(f"Hinweis: Die lokale Uhr weicht um etwa {abs(round(verbindung.versatz))} Sekunden von der des Servers ab. "
                          "Der Befehl codes rechnet mit der lokalen Uhr, bitte die Uhrzeit per NTP abgleichen.")
    return EXIT_FEHLER if n[FEHLER] else EXIT_OK


def _seed_vorhanden(umschlag: um.Umschlag, kennung: str) -> bool:
    try:
        return tt.seed_gueltig(umschlag.zugang(kennung).seed)
    except um.UmschlagFehler:
        return False


def befehl_pruefen(profile: list[str], umschlag: um.Umschlag, ausgabe: Ausgabe) -> int:
    """Nur die Datei, kein Netz: Ein Faktor gilt als vorhanden, wenn sein Seed in der Datei steht."""
    ergebnisse = [_seed_vorhanden(umschlag, k) for k in profile]
    for kennung, ok in zip(profile, ergebnisse):
        ausgabe.zeile(f"{kennung}: Faktor und Seed in der Datei: {'ja' if ok else 'nein'}")
    return EXIT_OK if all(ergebnisse) else EXIT_FEHLER


def befehl_codes(kennung: str, umschlag: um.Umschlag, ausgabe: Ausgabe, zeit: Callable[[], float]) -> int:
    """Der aktuelle Code mit der lokalen Uhr, sonst nichts auf der Standardausgabe."""
    try:
        seed = umschlag.zugang(kennung).seed
    except um.UmschlagFehler as e:
        ausgabe.fehler(f"{kennung}: {e}")
        return EXIT_FEHLER
    if not tt.seed_gueltig(seed):
        ausgabe.fehler(f"{kennung}: In der Datei steht kein Seed")
        return EXIT_FEHLER
    jetzt = zeit()
    print(f"{tt.totp_code(seed, jetzt)} (noch {tt.ablauf_sekunden(jetzt)} s)", file=sys.stdout, flush=True)
    return EXIT_OK


# ---------- Aufruf ----------

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wamo_labor_faktoren", description="Zweiten Faktor der Laborprofile automatisch einrichten")
    sub = p.add_subparsers(dest="befehl", required=True)
    ein = sub.add_parser("einrichten", help="Faktor je Profil einrichten und den Seed in die Umschlag-Datei schreiben")
    ein.add_argument("--profile", help="Profile mit Komma getrennt (Standard: labor-01 bis labor-09)")
    ein.add_argument("--neu", action="store_true", help="vorhandenen Faktor entfernen und neu einrichten")
    ein.add_argument("--trocken", action="store_true", help="nur der Plan, kein Netz, nichts wird geändert")
    ein.add_argument("--basis", help="Adresse des Anmeldeservers (Standard SUPABASE_URL)")
    ein.add_argument("--ca", help="CA-Datei (Standard WAMO_CA_FILE, sonst infra/sokrates2/wamo-ca.crt)")
    ein.add_argument("--env-datei", action="append", help="weitere .env-Datei, darf mehrfach stehen")
    pr = sub.add_parser("pruefen", help="je Profil ja oder nein: Faktor und Seed in der Datei (kein Netz)")
    pr.add_argument("--profile", help="Profile mit Komma getrennt (Standard: labor-01 bis labor-09)")
    co = sub.add_parser("codes", help="aktuellen Code für genau ein Profil ausgeben")
    co.add_argument("--profil", required=True, help="genau ein Profil, zum Beispiel labor-03")
    for s in (ein, pr, co):
        s.add_argument("--umschlag-datei", help="Standard %%USERPROFILE%%\\.wamo-labor\\umschlag-labor.txt")
    return p


def _utf8() -> None:
    for strom in (sys.stdout, sys.stderr):
        try:
            strom.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _profile_fuer(args: argparse.Namespace) -> list[str]:
    if args.befehl != "codes":
        return profile_lesen(args.profile)
    profile = profile_lesen(args.profil)
    if len(profile) != 1:
        raise EingabeFehler("--profil nennt genau ein Profil")
    return profile


def main(argv: list[str] | None = None, *, zeit: Callable[[], float] = time.time, warte: Callable[[float], None] = time.sleep) -> int:
    _utf8()
    args = _parser().parse_args(argv)
    ausgabe = Ausgabe()
    try:
        profile = _profile_fuer(args)
        datei = um.pfad_pruefen(args.umschlag_datei or um.standard_pfad(), WURZEL)
        umschlag = um.Umschlag(datei, rechte=RECHTE_SETZEN, jetzt=zeit)
        if args.befehl == "pruefen":
            return befehl_pruefen(profile, umschlag, ausgabe)
        if args.befehl == "codes":
            return befehl_codes(profile[0], umschlag, ausgabe, zeit)
        return befehl_einrichten(args, profile, umschlag, ausgabe, zeit, warte)
    except (EingabeFehler, um.UmschlagFehler, api.ApiFehler) as e:
        ausgabe.fehler(f"Fehler: {e}")
        return EXIT_EINGABE
    except KeyboardInterrupt:
        ausgabe.fehler("Abgebrochen.")
        return EXIT_ABBRUCH


if __name__ == "__main__":
    sys.exit(main())
