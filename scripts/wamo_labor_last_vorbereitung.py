"""Vorprüfung, Vorbereitung und Aufräumen des Lasttests (scripts/wamo_labor_last_lauf.py, nur Standardbibliothek).

Vorprüfung   nur lesen: Adresse, Sprechproben, Zugangsdaten (nur ob ein Seed da ist), Profile im Kern (gesperrt?), Plätze im Dienst (leer?), Kern
             (läuft, Speicher). Scheitert eine Prüfung, wurde nichts verändert.
Vorbereitung Profile freischalten (wamo_labor.py aktivieren), Durchläufe öffnen (Betreuer-Schnittstelle, Einwilligung Version 1), Browser starten und
             anmelden (Passwort und Code gehen nur an den Browser-Treiber, nie in eine Ausgabe).
Aufräumen    Gespräche beenden, Browser schließen, Profile sperren, Durchläufe beenden, Plätze leeren, Nachweis, Temp-Dateien löschen. Jeder Schritt läuft
             für sich: Scheitert einer, laufen die übrigen weiter, und der Bericht nennt ihn. Es wird nur zurückgenommen, was der Lauf angefasst hat.
"""
from __future__ import annotations

import shutil
from functools import partial
from typing import Callable

import wamo_labor as wl
import wamo_labor_faktoren_totp as tt
import wamo_labor_faktoren_umschlag as ufm
import wamo_labor_last_audio as au
import wamo_labor_last_plan as pl
import wamo_labor_last_server as sv
from wamo_labor_last_browser import TreiberFehler
from wamo_labor_last_umgebung import Einstellungen, Umgebung, Zustand, kurz, parallel
from wamo_labor_nachlauf import STATUS_RE
from wamo_labor_platz_adressen import adresse_zulaessig

GIB = pl.GIB
UNERWARTET_KERN = "Die Antwort des Servers zur Zählung im Kern ist unerwartet."


class VorpruefFehler(Exception):
    """Eine Voraussetzung fehlt. Es wurde nichts verändert."""


class VorbereitungsFehler(Exception):
    """Ein Schritt der Vorbereitung ist gescheitert. Der Lauf nimmt zurück, was er angefasst hat."""


# ---------- Vorprüfung ----------

def profil_stand(ein: Einstellungen, umg: Umgebung) -> dict[str, bool]:
    """Je Laborprofil: freigeschaltet (wahr) oder gesperrt. Unerwartete Zeilen werden nie angezeigt (es könnte ein Text sein)."""
    e = umg.kern(ein.host, {"aktion": "status", "schema": ein.schema, "ausfuehren": False})
    if e.code != 0:
        raise VorpruefFehler(f"Kern, Stand der Profile: {kurz(e.stderr) or 'Fehler ohne Angabe'}")
    stand: dict[str, bool] = {}
    for zeile in (z.strip() for z in e.stdout.splitlines() if z.strip()):
        treffer = STATUS_RE.fullmatch(zeile)
        if not treffer:
            raise VorpruefFehler(UNERWARTET_KERN)
        stand[treffer.group(1)] = treffer.group(2) == "freigeschaltet"
    return stand


def _profile_pruefen(ein: Einstellungen, umg: Umgebung) -> None:
    stand = profil_stand(ein, umg)
    for kennung in ein.profile:
        if kennung not in stand:
            raise VorpruefFehler(f"Das Laborprofil {kennung} gibt es im Kern nicht (scripts/wamo_labor.py anlegen)")
        if stand[kennung]:
            raise VorpruefFehler(f"Das Laborprofil {kennung} ist schon freigeschaltet: Ein Lasttest läuft nur mit gesperrten Profilen")


def _plaetze_pruefen(ein: Einstellungen, umg: Umgebung) -> None:
    try:
        plaetze = umg.dienst.plaetze()
    except sv.ServerFehler as e:
        raise VorpruefFehler(f"Dienst, Stand der Plätze: {kurz(e)}") from None
    for platz in ein.plaetze:
        if not plaetze[platz - 1].frei:
            raise VorpruefFehler(f"Platz {platz} ist nicht leer (offen, Tickets, Kommentare oder Einwilligung): Der Lasttest löscht dort Daten")


def _umschlag_pruefen(ein: Einstellungen, umg: Umgebung) -> None:
    for kennung in ein.profile:
        try:
            zugang = umg.umschlag.zugang(kennung)
        except ufm.UmschlagFehler as e:
            raise VorpruefFehler(str(e)) from None
        if not tt.seed_gueltig(zugang.seed):
            raise VorpruefFehler(f"Für {kennung} fehlt der Seed in der Umschlag-Datei (scripts/wamo_labor_faktoren.py einrichten)")


def _kern_pruefen(ein: Einstellungen, umg: Umgebung) -> sv.Messung:
    try:
        m = umg.server.messen(None)
    except sv.ServerFehler as e:
        raise VorpruefFehler(f"Messung am Kern: {kurz(e)}") from None
    if not m.aktiv:
        raise VorpruefFehler(f"Der Kern wamo-core@{ein.slot} läuft nicht (keine cgroup)")
    if m.speicher is not None and m.speicher >= pl.ABBRUCH_SPEICHER_BYTES:
        raise VorpruefFehler(f"Der Speicher des Kerns liegt schon über {pl.ABBRUCH_SPEICHER_BYTES / GIB:.0f} GiB")
    return m


def vorpruefen(ein: Einstellungen, umg: Umgebung) -> sv.Messung:
    """Alle Prüfungen vor dem ersten verändernden Aufruf. Gibt die erste Messung am Kern zurück (Serveruhr, Ausgangswerte)."""
    if not adresse_zulaessig(ein.url):
        raise VorpruefFehler("Adresse nicht zulässig: nur die feste Adresse der Oberfläche auf Sokrates-2 ist erlaubt")
    try:
        au.clips_pruefen(ein.clips_ordner)
    except pl.EingabeFehler as e:
        raise VorpruefFehler(str(e)) from None
    _umschlag_pruefen(ein, umg)
    _profile_pruefen(ein, umg)
    _plaetze_pruefen(ein, umg)
    return _kern_pruefen(ein, umg)


# ---------- Vorbereitung ----------

def murmeln_vorbereiten(ein: Einstellungen, z: Zustand) -> dict:
    """Je Signal-Rausch-Abstand der Stufen die Murmeldatei (im Temp-Ordner) und ihre Verstärkung."""
    aus = {}
    for stufe in ein.stufen:
        if stufe.snr_db is not None and stufe.snr_db not in aus:
            try:
                aus[stufe.snr_db] = au.murmeln_vorbereiten(ein.clips_ordner, ein.murmeln_datei, stufe.snr_db, ein.seed, z.tempordner)
            except pl.EingabeFehler as e:
                raise VorbereitungsFehler(str(e)) from None
    return aus


def profile_freischalten(ein: Einstellungen, umg: Umgebung, z: Zustand) -> None:
    kennungen = list(ein.profile)
    z.profile.extend(kennungen)  # vor dem Aufruf: Das Aufräumen sperrt sie in jedem Fall
    code = wl.main(["aktivieren", "--kennung", *kennungen, "--schema", ein.schema, "--host", ein.host, "--ausfuehren"], remote=umg.kern)
    if code != 0:
        raise VorbereitungsFehler("Die Laborprofile ließen sich nicht freischalten")


def durchlaeufe_oeffnen(ein: Einstellungen, umg: Umgebung, z: Zustand) -> None:
    for platz in ein.plaetze:
        z.plaetze.append(platz)
        try:
            umg.dienst.durchlauf_oeffnen(platz, ein.einwilligung)
        except sv.ServerFehler as e:
            raise VorbereitungsFehler(kurz(e)) from None


def anmelde_code(seed: str, versatz_s: float, uhr) -> str:
    """Der Code für jetzt auf der Uhr des Servers, aus einem Fenster mit mindestens 2 Sekunden Rest (sonst wird bis zum nächsten gewartet)."""
    zeit = tt.frisches_fenster(lambda: uhr.jetzt() + versatz_s, uhr.schlafen)
    return tt.totp_code(seed, zeit)


def _browser_start(i: int, ein: Einstellungen, umg: Umgebung, z: Zustand, versatz_s: float) -> None:
    treiber = umg.treiber_fabrik(i)
    z.treiber[i] = treiber
    treiber.starten(ein.url, ein.clips_ordner, ein.kopflos)
    zugang = umg.umschlag.zugang(ein.profile[i])
    treiber.anmelden(zugang.email, zugang.passwort, anmelde_code(zugang.seed, versatz_s, umg.uhr))


def browser_starten(ein: Einstellungen, umg: Umgebung, z: Zustand, versatz_s: float) -> None:
    fehler = parallel([partial(_browser_start, i, ein, umg, z, versatz_s) for i in range(ein.sitzungen_max)], "start")
    for i, f in enumerate(fehler):
        if f is not None:
            text = kurz(f) if isinstance(f, (TreiberFehler, ufm.UmschlagFehler)) else f"unerwarteter Fehler ({type(f).__name__})"
            raise VorbereitungsFehler(f"Browser {i + 1} ({ein.profile[i]}): {text}")


def vorbereiten(ein: Einstellungen, umg: Umgebung, z: Zustand, versatz_s: float) -> dict:
    """Temp-Ordner, Murmeln, Profile, Durchläufe, Browser. Gibt die Murmeln je Abstand zurück."""
    z.tempordner = umg.tempordner_fabrik()
    murmeln = murmeln_vorbereiten(ein, z)
    profile_freischalten(ein, umg, z)
    durchlaeufe_oeffnen(ein, umg, z)
    browser_starten(ein, umg, z, versatz_s)
    return murmeln


# ---------- Aufräumen ----------

def _sammeln(fehler: list, namen: list[str], was: str) -> None:
    schlecht = [(n, f) for n, f in zip(namen, fehler) if f is not None]
    if schlecht:
        raise RuntimeError(f"{len(schlecht)} von {len(namen)} {was}: " + "; ".join(f"{n} {kurz(f, 80)}" for n, f in schlecht[:3]))


def _gespraeche_beenden(z: Zustand) -> None:
    indizes = sorted(i for i in z.gespraeche if i in z.treiber)
    _sammeln(parallel([z.treiber[i].gespraech_beenden for i in indizes], "beenden"), [f"Sitzung {i + 1}" for i in indizes], "Gesprächen nicht beendet")
    z.gespraeche.clear()


def _browser_schliessen(z: Zustand) -> None:
    indizes = sorted(z.treiber)
    _sammeln(parallel([z.treiber[i].schliessen for i in indizes], "schliessen"), [f"Browser {i + 1}" for i in indizes], "Browsern nicht geschlossen")


def _profile_sperren(ein: Einstellungen, umg: Umgebung, z: Zustand) -> None:
    code = wl.main(["sperren", "--kennung", *z.profile, "--schema", ein.schema, "--host", ein.host, "--ausfuehren"], remote=umg.kern)
    if code != 0:
        raise RuntimeError("Das Sperren der Laborprofile meldete einen Fehler")


def _je_platz(z: Zustand, aufgabe: Callable[[int], None], was: str) -> None:
    fehler = []
    for platz in z.plaetze:
        try:
            aufgabe(platz)
        except sv.ServerFehler as e:
            fehler.append((platz, e))
    if fehler:
        raise RuntimeError(f"{len(fehler)} von {len(z.plaetze)} Plätzen {was}: " + "; ".join(f"Platz {p} {kurz(e, 80)}" for p, e in fehler[:3]))


def _nachweis(ein: Einstellungen, umg: Umgebung, z: Zustand) -> None:
    probleme = []
    stand = profil_stand(ein, umg)
    probleme += [f"{k} ist noch freigeschaltet" for k in z.profile if stand.get(k, False)]
    plaetze = umg.dienst.plaetze()
    probleme += [f"Platz {p} ist nicht leer" for p in z.plaetze if not plaetze[p - 1].frei]
    if probleme:
        raise RuntimeError("; ".join(probleme))


def _temp_loeschen(z: Zustand) -> None:
    if z.tempordner is not None and z.tempordner.exists():
        shutil.rmtree(z.tempordner)


def aufraeumen(ein: Einstellungen, umg: Umgebung, z: Zustand) -> list[dict]:
    """Nimmt zurück, was der Lauf angefasst hat. Jeder Schritt für sich, in dieser Reihenfolge. Gibt je Schritt {schritt, ok, text} zurück."""
    schritte = [
        ("Gespräche beendet", bool(z.gespraeche), lambda: _gespraeche_beenden(z)),
        ("Browser geschlossen", bool(z.treiber), lambda: _browser_schliessen(z)),
        ("Profile gesperrt", bool(z.profile), lambda: _profile_sperren(ein, umg, z)),
        ("Durchläufe beendet", bool(z.plaetze), lambda: _je_platz(z, umg.dienst.durchlauf_beenden, "nicht beendet")),
        ("Plätze geleert", bool(z.plaetze), lambda: _je_platz(z, umg.dienst.daten_loeschen, "nicht geleert")),
        ("Nachweis: Profile gesperrt, Plätze leer", bool(z.profile or z.plaetze), lambda: _nachweis(ein, umg, z)),
        ("Temp-Dateien gelöscht", z.tempordner is not None, lambda: _temp_loeschen(z)),
    ]
    ergebnis = []
    for name, noetig, funktion in schritte:
        if not noetig:
            continue
        try:
            funktion()
            ergebnis.append({"schritt": name, "ok": True, "text": ""})
        except Exception as e:  # noqa: BLE001 (ein Schritt darf die übrigen nie aufhalten)
            ergebnis.append({"schritt": name, "ok": False, "text": kurz(e, 300)})
    return ergebnis
