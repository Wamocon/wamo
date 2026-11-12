"""Abnahme des Test-Jira-Dienstes der Kollegen gegen den Vertrag (docs/labor-dienst-vertrag.md, Abschnitt 7): bestanden oder durchgefallen je Anforderung.

Das Skript spricht einen laufenden Dienst mit rohem HTTP an (V1 bis V7 unter /api/kern/), nur auf den Abnahmeplätzen 15 und 16, und prüft
A1 XSS, A2 SQL-Injektion, A3 Prozent und Unterstrich, A4 Quoten, A5 Größenlimits, A6 Platztrennung, A7 kein Löschweg, A8 Durchlauf und Version,
A9 Zugang und A11 Vertrag. A10 (Netz) bleibt von Hand am Server, ebenso Browserlauf und Datenbankgröße (die Tabelle nennt, was offen bleibt).
Das Skript hat selbst keinen Löschaufruf und schickt jeder schreibenden Anfrage den festen Platzkopf 15 oder 16 (dazu Proben mit ungültigem
Platz wie 99, die der Dienst mit 404 ablehnen muss). Es legt Tickets und Kommentare an, die Plätze müssen frisch sein (neuer Durchlauf mit
Einwilligung, Seed) und nach dem Lauf wieder zurückgesetzt werden. Ein Dienst, der trotz Vertrag einen Löschweg anbietet, kann dabei Tickets der
Plätze 15 und 16 verlieren: Genau das prüft A7. Vor dem ersten Schreiben verlangt das Skript die Bestätigung --ja. A4 füllt die Plätze bis zu den
Quoten und dauert wegen der Schreibrate (30 je Minute) gegen den echten Dienst etwa 20 bis 25 Minuten; Mengenprüfungen brechen an einer Schwelle
ab (101 Tickets, 301 Kommentare, über 1 MB Text).

Aufrufe (aus dem Ordner scripts, mit der Python-Umgebung von voice-core):
  python wamo_labor_abnahme.py https://HOST[:PORT] --ja       Token aus WAMO_LABOR_TOKEN (oder --token-datei DATEI), nie als Argument
  python wamo_labor_abnahme.py https://HOST --trocken         zeigt nur den Plan, sendet nichts (ohne --ja)
  python wamo_labor_abnahme.py https://HOST --ja --auswahl A2 A9   nur diese Prüfungen (Teillauf, nie grün)
  python wamo_labor_abnahme.py --attrappe                     startet die Attrappe von WAMO im Prozess und prüft sie (muss grün sein)
  python wamo_labor_abnahme.py --verwundbar [--defekt NAME]   startet die absichtlich fehlerhafte Variante (jeder Defekt muss rot werden)
Optionen: --ca-datei (oder WAMO_CA_FILE) für das Zertifikat der WAMO-CA, --frist SEKUNDEN je Anfrage (Standard 5 lesend, 8 schreibend, so lange
wartet der Kern), --board-url URL (nur Köpfe der Board-Seite für A1, ohne Bearer), --betreuer-pfad "[METHODE ]PFAD" und --platz-pfad "PFAD mit {platz}"
(A9 und A6, mit dem Platz-Token des Platzes 15 aus WAMO_LABOR_PLATZ_TOKEN oder --platz-token-datei; der Vertrag legt diese Pfade nicht fest). Beide
nehmen nur Pfade auf dem Host des Dienstes an (mit / am Anfang, ohne // und ://), Betreuer-Pfade nur mit GET, eine andere Methode nur mit der
ausdrücklichen Freigabe --nicht-get-freigeben.

Ausgang: 0 alle automatischen Prüfungen bestanden (offen bleibt, was in der Tabelle als "von Hand" steht), 1 mindestens eine Prüfung durchgefallen
(hat Vorrang vor 2), 2 Aufruf, Verbindung, Zeit, Ausgangsstand der Plätze oder ein Fehler im Skript selbst (kein Urteil über den Dienst), 3 Teillauf
oder eine Grenze, die strenger ist als der Vertrag (A4: von Hand prüfen), ohne durchgefallene Prüfung.
Ein Fehler in A1, A2, A6 oder A7 heißt: Der Dienst geht nicht an Besucher.

Geheimnisse: Der Token steht nur in der Umgebung oder in einer Datei (nie im Repo), wird nie als Argument gelesen und nie ausgegeben (jede Ausgabe
ersetzt ihn durch [Token]). Falsche Argumente zeigen ihre Werte nicht.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import inspect
import os
import re
import ssl
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wamo_labor_abnahme_pruefungen as pr  # noqa: E402
from wamo_labor_abnahme_basis import (  # noqa: E402
    ABGEBROCHEN,
    BESTANDEN,
    DURCHGEFALLEN,
    NICHT_GELAUFEN,
    PFAD,
    PLATZ_A,
    PLATZ_B,
    SKRIPTFEHLER,
    STRENGER,
    UUID_RE,
    VON_HAND,
    EchterTakt,
    Ergebnis,
    LaufAbbruch,
    Optionen,
    Takt,
    Zugang,
    betreuer_spezifikation,
    pfad_gueltig,
)

if TYPE_CHECKING:
    from wamo_labor_abnahme_verwundbar import Ziel

EXIT_OK, EXIT_DURCHGEFALLEN, EXIT_AUFRUF, EXIT_TEILLAUF = 0, 1, 2, 3
TOKEN_RE = re.compile(r"[A-Za-z0-9._~+/=-]{32,200}")
KRITISCH = ("A1", "A2", "A6", "A7")  # ein Fehler hier heißt: der Dienst geht nicht an Besucher
REIHENFOLGE = ("A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10", "A11")


class AufrufFehler(Exception):
    """Falscher Aufruf oder falsche Einstellung. Die Meldung nennt nie einen Wert aus der Befehlszeile oder ein Geheimnis."""


@dataclass
class LaufErgebnis:
    ergebnisse: list[Ergebnis]
    abbruch: str | None = None
    teillauf: bool = False
    nach_nr: dict[str, Ergebnis] = field(init=False)

    def __post_init__(self) -> None:
        self.nach_nr = {e.nr: e for e in self.ergebnisse}


def exit_code(lauf: LaufErgebnis) -> int:
    """Rot hat Vorrang: Ein durchgefallener Befund bleibt ein Befund, auch wenn der Lauf danach abbrach. Abbruch und Skriptfehler sind nie ein Befund."""
    status = {e.status for e in lauf.ergebnisse}
    if DURCHGEFALLEN in status:
        return EXIT_DURCHGEFALLEN
    if lauf.abbruch or status & {ABGEBROCHEN, SKRIPTFEHLER}:
        return EXIT_AUFRUF
    return EXIT_TEILLAUF if lauf.teillauf or STRENGER in status else EXIT_OK


# ---------- Ausgabe ----------
def bereinigen(text: str, geheimnisse: list[str]) -> str:
    """Ersetzt jedes bekannte Geheimnis durch [Token]. Gilt für jede Ausgabe, auch für Meldungen der Prüfungen."""
    for g in sorted((g for g in geheimnisse if g), key=len, reverse=True):
        text = text.replace(g, "[Token]")
    return text


def tabelle(lauf: LaufErgebnis, ziel: str) -> str:
    zeilen = [f"WAMO Labor-Abnahme gegen {ziel}", "", f"{'Nr':<4} {'Anforderung':<24} {'Ergebnis':<15} Meldung", "-" * 100]
    for e in lauf.ergebnisse:
        zeilen.append(f"{e.nr:<4} {e.titel:<24} {e.status:<15} {e.meldung}")
    zeilen.append("-" * 100)
    zaehler = {s: sum(e.status == s for e in lauf.ergebnisse) for s in (BESTANDEN, DURCHGEFALLEN, STRENGER, ABGEBROCHEN, SKRIPTFEHLER, VON_HAND, NICHT_GELAUFEN)}
    zeilen.append("Zusammenfassung: " + ", ".join(f"{n} {s}" for s, n in zaehler.items()))
    offen = [f"{e.nr}: {rest}" for e in lauf.ergebnisse for rest in e.von_hand]
    if offen:
        zeilen += ["Noch von Hand:"] + [f"  {o}" for o in offen]
    rot = [e.nr for e in lauf.ergebnisse if e.status == DURCHGEFALLEN]
    streng = [e.nr for e in lauf.ergebnisse if e.status == STRENGER]
    unvollstaendig = lauf.abbruch or any(e.status in (ABGEBROCHEN, SKRIPTFEHLER) for e in lauf.ergebnisse)
    if rot:
        zeilen.append(f"Ergebnis: DURCHGEFALLEN ({', '.join(rot)})" + (", der Lauf blieb unvollständig (Abbruch oder Skriptfehler)" if unvollstaendig else ""))
    elif unvollstaendig:
        zeilen.append("Ergebnis: Lauf unvollständig (Abbruch oder Skriptfehler), kein Urteil über den Dienst")
    elif streng:
        zeilen.append(f"Ergebnis: nicht abnahmefähig, weil der Dienst strenger ist als der Vertrag ({', '.join(streng)}): von Hand prüfen")
    elif lauf.teillauf:
        zeilen.append("Ergebnis: Teillauf, nicht abnahmefähig (nur die gewählten Prüfungen liefen, alle bestanden)")
    else:
        zeilen.append("Ergebnis: BESTANDEN (alle automatischen Prüfungen)")
    if any(nr in rot for nr in KRITISCH):
        zeilen.append(f"Der Dienst geht nicht an Besucher (Fehler in {', '.join(n for n in KRITISCH if n in rot)}; Vertrag Abschnitt 5).")
    return "\n".join(zeilen)


def plan(ziel: str, auswahl: list[str] | None) -> str:
    zeilen = [f"Plan der Abnahme gegen {ziel} (keine Anfrage gesendet)", "",
              f"Plätze: Platz {PLATZ_A} und Platz {PLATZ_B} (für die Abnahme reserviert, frisch zurückgesetzt, mit offenem Durchlauf)", ""]
    schreibaufrufe = 0
    for nr in REIHENFOLGE:
        text, n = pr.PLAN[nr]
        gewaehlt = auswahl is None or nr in auswahl
        schreibaufrufe += n if gewaehlt else 0
        zeilen.append(f"{nr:<4} {'ja ' if gewaehlt else 'nein'} {text} ({n} Schreibaufrufe)")
    zeilen += ["", f"Schreibaufrufe zusammen: etwa {schreibaufrufe}. Der Dienst nimmt höchstens 30 je Minute und Platz an, das Skript hält 25 je 62 Sekunden ein.",
               "Das dauert gegen den echten Dienst etwa 20 bis 25 Minuten (fast alles A4). Danach den Durchlauf beider Plätze neu anlegen lassen (Seed)."]
    return "\n".join(zeilen)


# ---------- Aufruf ----------
class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # noqa: ARG002 (argparse würde Werte der Befehlszeile wiedergeben, auch einen Token)
        self.exit(2, "Fehler: Falsche Angaben in der Befehlszeile (Werte werden nicht wiedergegeben). Der Token gehört nie in die Befehlszeile, "
                     "sondern in WAMO_LABOR_TOKEN oder --token-datei. Hilfe: --help\n")


def _parser() -> argparse.ArgumentParser:
    p = _Parser(prog="wamo_labor_abnahme.py", allow_abbrev=False, description="Abnahme des Test-Jira-Dienstes gegen den Vertrag Version 1.")
    p.add_argument("adresse", nargs="?", help="https://HOST[:PORT] des Dienstes (nur Schema, Host und Port)")
    p.add_argument("--attrappe", action="store_true", help="die Attrappe von WAMO im Prozess prüfen (muss grün sein)")
    p.add_argument("--verwundbar", action="store_true", help="die absichtlich verwundbare Variante prüfen (jeder Defekt muss rot werden)")
    p.add_argument("--defekt", action="append", default=[], help="nur diesen Defekt einbauen (mit --verwundbar, mehrfach möglich)")
    p.add_argument("--token-datei", help="Datei mit dem Bearer-Token (sonst WAMO_LABOR_TOKEN)")
    p.add_argument("--platz-token-datei", help="Datei mit dem Platz-Token des Platzes 15 (sonst WAMO_LABOR_PLATZ_TOKEN)")
    p.add_argument("--ca-datei", help="CA-Datei (sonst WAMO_CA_FILE)")
    p.add_argument("--auswahl", nargs="+", help="nur diese Prüfungen, zum Beispiel A2 A9 oder A2,A9")
    p.add_argument("--trocken", action="store_true", help="nur den Plan zeigen, nichts senden")
    p.add_argument("--ja", action="store_true", help="bestätigt, dass auf Platz 15 und 16 des Dienstes Tickets und Kommentare angelegt werden dürfen")
    p.add_argument("--nicht-get-freigeben", action="store_true", help="erlaubt Betreuer-Pfade mit einer anderen Methode als GET")
    p.add_argument("--frist", type=float, help="Frist je Anfrage in Sekunden (Standard 5 lesend, 8 schreibend)")
    p.add_argument("--board-url", help="Adresse der Board-Seite (A1: Köpfe und Rohseite)")
    p.add_argument("--betreuer-pfad", action="append", default=[], help='Betreuer-Pfad, "PFAD" oder "METHODE PFAD" (A9)')
    p.add_argument("--platz-pfad", action="append", default=[], help="Pfad mit {platz} (A6: Platz-Token von Platz 15 gegen Platz 16)")
    return p


def adresse_pruefen(text: object, *, mit_pfad: bool = False) -> str:
    """Schema, Host und Port (mit mit_pfad auch der Pfad, für die Board-Seite), sonst AufrufFehler ohne den Wert zu nennen: er könnte Zugangsdaten enthalten."""
    fehler = AufrufFehler("Adresse fehlt oder ist ungültig: erwartet https://HOST[:PORT] ohne Zugangsdaten" + ("" if mit_pfad else ", Pfad und Abfrage")
                          + " (Klartext-HTTP nur für Loopback).")
    if not isinstance(text, str) or not text:
        raise fehler
    try:
        t = urlsplit(text)
        host, port = (t.hostname or "").lower(), t.port
    except ValueError:
        raise fehler from None
    loopback = host in ("localhost", "127.0.0.1", "::1")
    if t.scheme not in ("https", "http") or not host or t.username is not None or t.password is not None or t.fragment or (not mit_pfad and (t.query or t.path not in ("", "/"))):
        raise fehler
    if t.scheme == "http" and not loopback:
        raise fehler
    basis = f"{t.scheme}://{'[' + host + ']' if ':' in host else host}" + (f":{port}" if port else "")
    return basis + (t.path + (f"?{t.query}" if t.query else "") if mit_pfad else "")


def _lesen(datei: str, was: str) -> str:
    """Text einer Datei als UTF-8 (ein BOM von Notepad stört nicht). UTF-16 aus PowerShell oder Latin-1 ergeben eine klare Meldung ohne den Inhalt."""
    try:
        return Path(datei).read_text(encoding="utf-8-sig").strip()
    except OSError:
        raise AufrufFehler(f"Die {was}-Datei kann nicht gelesen werden.") from None
    except UnicodeError:
        raise AufrufFehler(f"Die {was}-Datei ist nicht UTF-8 (UTF-16 aus PowerShell oder Latin-1?): als UTF-8 speichern, zum Beispiel mit Set-Content -Encoding utf8.") from None


def token_lesen(env: Mapping[str, str], datei: str | None) -> str:
    text = _lesen(datei, "Token") if datei else (env.get("WAMO_LABOR_TOKEN") or "").strip()
    if not text:
        raise AufrufFehler("Kein Token: WAMO_LABOR_TOKEN setzen oder --token-datei angeben (der Token steht nie in der Befehlszeile).")
    if not TOKEN_RE.fullmatch(text):
        raise AufrufFehler("Der Token ist unbrauchbar (32 bis 200 Zeichen aus A-Z a-z 0-9 und ._~+/=-).")
    return text


def _auswahl(werte: list[str] | None) -> list[str] | None:
    if not werte:
        return None
    namen = [n.strip().upper() for w in werte for n in w.split(",") if n.strip()]
    unbekannt = [n for n in namen if n not in pr.PRUEFUNGEN]
    if unbekannt:
        raise AufrufFehler(f"Unbekannte Prüfung: {', '.join(unbekannt)} (erlaubt: {', '.join(REIHENFOLGE)}).")
    return namen


def _vertrauen(ca_datei: str | None):
    if not ca_datei:
        return True
    import certifi

    kontext = ssl.create_default_context(cafile=certifi.where())
    try:
        kontext.load_verify_locations(cafile=ca_datei)
    except (OSError, ssl.SSLError):
        raise AufrufFehler("Die CA-Datei kann nicht gelesen werden.") from None
    return kontext


# ---------- Lauf ----------
def _unerreichbar(a, basis: str) -> str:
    if a.fehler.startswith("Zeit"):
        return f"{a.fehler} bei GET {PFAD}/durchlauf an {basis}: Der Dienst antwortet nicht in der Zeit des Kerns (lesend 5 s, schreibend 8 s)."
    return f"Dienst nicht erreichbar unter {basis}: {a.fehler}."


async def vorbedingungen(z: Zugang) -> None:
    """Beide Plätze brauchen einen offenen Durchlauf und den Ausgangsstand (kein Ticket aus einem früheren Lauf)."""
    for platz in (PLATZ_A, PLATZ_B):
        a = await z.v1(platz, abbruch_ok=True)
        if a.status == 0:
            raise LaufAbbruch(_unerreichbar(a, z.basis))
        j = a.json if isinstance(a.json, dict) else {}
        if a.status != 200 or not isinstance(j.get("durchlauf_id"), str) or not UUID_RE.fullmatch(j["durchlauf_id"]) or not isinstance(j.get("offen"), bool):
            raise LaufAbbruch(f"Der Dienst antwortet auf V1 (Durchlauf lesen) für Platz {platz} nicht wie im Vertrag ({a.beschreibung()}). "
                              "Die Abnahme kann nicht beginnen.")
        if not j["offen"]:
            raise LaufAbbruch(f"Platz {platz}: Der Durchlauf ist nicht offen. Der Betreuer legt zuerst einen neuen Durchlauf mit Einwilligung für den Platz an.")
        z.durchlauf[platz] = j["durchlauf_id"]
        b = await z.tickets(platz, nur_durchlauf="true", limit=1)
        n = b.json.get("gesamt") if isinstance(b.json, dict) else None
        if b.status != 200 or not isinstance(n, int):
            raise LaufAbbruch(f"Der Dienst antwortet auf V2 (Tickets suchen) für Platz {platz} nicht wie im Vertrag ({b.beschreibung()}).")
        if n:
            raise LaufAbbruch(f"Platz {platz} wurde schon benutzt ({n} Tickets im Durchlauf). Der Betreuer legt zuerst einen neuen Durchlauf an, "
                              "damit der Platz auf den Ausgangsstand (Seed) zurückgeht.")


async def lauf(z: Zugang, optionen: Optionen, auswahl: list[str] | None = None) -> LaufErgebnis:
    """Führt die gewählten Prüfungen aus (A4 zuletzt). LaufAbbruch vor der ersten Prüfung geht an den Aufrufer, danach ins Ergebnis."""
    await vorbedingungen(z)
    ergebnisse: dict[str, Ergebnis] = {}
    abbruch: str | None = None
    for nr in pr.AUSFUEHRUNG:
        titel, funktion = pr.PRUEFUNGEN[nr]
        if abbruch:
            ergebnisse[nr] = Ergebnis(nr, titel, NICHT_GELAUFEN, "nach dem Abbruch nicht gelaufen")
        elif auswahl and nr not in auswahl:
            ergebnisse[nr] = Ergebnis(nr, titel, NICHT_GELAUFEN, "nicht gewählt")
        else:
            try:
                ergebnis = funktion(z, optionen)
                ergebnisse[nr] = await ergebnis if inspect.isawaitable(ergebnis) else ergebnis
            except LaufAbbruch as e:
                ergebnisse[nr], abbruch = Ergebnis(nr, titel, ABGEBROCHEN, "Lauf abgebrochen, kein Befund über den Dienst"), str(e)
            except Exception as e:  # noqa: BLE001 (ein Fehler im Skript ist nie ein Befund über den Dienst: Ausgang 2, die übrigen Prüfungen laufen weiter)
                ergebnisse[nr] = Ergebnis(nr, titel, SKRIPTFEHLER, f"Skriptfehler ({type(e).__name__}), kein Befund über den Dienst")
    teil = bool(auswahl) and set(auswahl) != set(pr.PRUEFUNGEN)
    return LaufErgebnis([ergebnisse[nr] for nr in REIHENFOLGE], abbruch, teil)


async def _ablauf(client: httpx.AsyncClient, token: str, takt: Takt, basis: str, frist: float | None, optionen: Optionen, auswahl: list[str] | None) -> LaufErgebnis:
    async with client:
        return await lauf(Zugang(client, token, takt, frist=frist, basis=basis), optionen, auswahl)


def pruefe_ziel(ziel: Ziel, *, optionen: Optionen | None = None, auswahl: list[str] | None = None, transport: httpx.AsyncBaseTransport | None = None,
                frist: float | None = None) -> LaufErgebnis:
    """Prüft eine Nachbildung aus wamo_labor_abnahme_verwundbar.starten (für Tests und --attrappe). LaufAbbruch geht an den Aufrufer."""
    client = httpx.AsyncClient(base_url=ziel.url, transport=transport or ziel.transport, follow_redirects=False, trust_env=False)
    return asyncio.run(_ablauf(client, ziel.token, ziel.takt, ziel.url, frist, optionen or Optionen(), auswahl))


def _betreuer_pfade(werte: list[str], frei: bool) -> tuple[str, ...]:
    """"METHODE PFAD" je Angabe. Nur Pfade (der Bearer des Kerns darf nie an einen fremden Host gehen), nur GET ohne ausdrückliche Freigabe."""
    pfade = []
    for wert in werte:
        teile = betreuer_spezifikation(wert)
        if teile is None:
            raise AufrufFehler('Betreuer-Pfad ungültig: erwartet "/pfad" oder "METHODE /pfad", nur ein Pfad auf dem Host des Dienstes (mit / am Anfang, ohne // und ://).')
        if teile[0] != "GET" and not frei:
            raise AufrufFehler(f"Betreuer-Pfad mit {teile[0]}: Betreuer-Pfade gehen nur mit GET, jede andere Methode nur mit der Freigabe --nicht-get-freigeben.")
        pfade.append(f"{teile[0]} {teile[1]}")
    return tuple(pfade)


def _platz_pfade(werte: list[str]) -> tuple[str, ...]:
    if not all(pfad_gueltig(w) for w in werte):
        raise AufrufFehler("Platz-Pfad ungültig: erwartet ein Pfad mit {platz} auf dem Host des Dienstes (mit / am Anfang, ohne // und ://).")
    return tuple(werte)


def _optionen(args: argparse.Namespace, env: Mapping[str, str]) -> Optionen:
    platz_token = _lesen(args.platz_token_datei, "Platz-Token") if args.platz_token_datei else (env.get("WAMO_LABOR_PLATZ_TOKEN") or "").strip() or None
    if platz_token and not TOKEN_RE.fullmatch(platz_token):
        raise AufrufFehler("Der Platz-Token ist unbrauchbar (32 bis 200 Zeichen aus A-Z a-z 0-9 und ._~+/=-).")
    board = adresse_pruefen(args.board_url, mit_pfad=True) if args.board_url else None
    return Optionen(board_url=board, platz_token=platz_token, betreuer_pfade=_betreuer_pfade(args.betreuer_pfad, args.nicht_get_freigeben),
                    platz_pfade=_platz_pfade(args.platz_pfad), nicht_get_frei=args.nicht_get_freigeben)


def _ausgeben(lauf_: LaufErgebnis, ziel: str, geheimnisse: list[str]) -> int:
    print(bereinigen(tabelle(lauf_, ziel), geheimnisse))
    if lauf_.abbruch:
        print(bereinigen(f"Fehler: {lauf_.abbruch}", geheimnisse), file=sys.stderr)
    return exit_code(lauf_)


def _gegen_nachbildung(args: argparse.Namespace, optionen: Optionen, auswahl: list[str] | None) -> int:
    import wamo_labor_abnahme_verwundbar as vw

    defekte = tuple(args.defekt) if args.verwundbar else ()
    unbekannt = [d for d in defekte if d not in vw.DEFEKTE]
    if unbekannt:
        raise AufrufFehler(f"Unbekannter Defekt: {', '.join(unbekannt)} (erlaubt: {', '.join(sorted(vw.DEFEKTE))}).")
    if args.verwundbar and not defekte:
        defekte = tuple(sorted(vw.DEFEKTE))
    ziel_text = ("die verwundbare Variante der Attrappe (Defekte: " + ", ".join(defekte) + ")" if args.verwundbar
                 else "die Attrappe von WAMO (im Prozess, kein Netz)")
    with vw.starten(defekte=defekte) as ziel:
        try:
            ergebnis = pruefe_ziel(ziel, optionen=optionen, auswahl=auswahl, frist=args.frist)
        except LaufAbbruch as e:
            raise AufrufFehler(str(e)) from None
        return _ausgeben(ergebnis, ziel_text, [ziel.token, optionen.platz_token or ""])


def _gegen_dienst(args: argparse.Namespace, env: Mapping[str, str], optionen: Optionen, auswahl: list[str] | None, transport: httpx.AsyncBaseTransport | None,
                  takt: Takt | None) -> int:
    adresse = adresse_pruefen(args.adresse)
    token = token_lesen(env, args.token_datei)
    if args.trocken:
        print(bereinigen(plan(adresse, auswahl), [token, optionen.platz_token or ""]))
        return EXIT_OK
    if not args.ja:
        raise AufrufFehler(f"Bestätigung fehlt, es wurde nichts gesendet. Das Skript legt auf Platz {PLATZ_A} und Platz {PLATZ_B} des Dienstes {adresse} Tickets und "
                           "Kommentare an und füllt die Plätze bis zu den Quoten (jede Anfrage trägt den Platzkopf 15 oder 16, dazu Proben mit ungültigem Platz). "
                           "Mit --ja bestätigen, --trocken zeigt nur den Plan.")
    client = httpx.AsyncClient(base_url=adresse, transport=transport, verify=_vertrauen(args.ca_datei or env.get("WAMO_CA_FILE")), follow_redirects=False,
                               trust_env=False, timeout=httpx.Timeout(args.frist or 8.0, connect=3.0))
    try:
        ergebnis = asyncio.run(_ablauf(client, token, takt or EchterTakt(), adresse, args.frist, optionen, auswahl))
    except LaufAbbruch as e:
        raise AufrufFehler(bereinigen(str(e), [token, optionen.platz_token or ""])) from None
    return _ausgeben(ergebnis, f"{adresse} (Plätze {PLATZ_A} und {PLATZ_B})", [token, optionen.platz_token or ""])


def main(argv: list[str] | None = None, *, env: Mapping[str, str] | None = None, transport: httpx.AsyncBaseTransport | None = None, takt: Takt | None = None) -> int:
    """env, transport und takt sind für Tests (Umgebung, Transport im Prozess, falsche Uhr)."""
    env = os.environ if env is None else env
    args = _parser().parse_args(argv)
    try:
        auswahl, optionen = _auswahl(args.auswahl), _optionen(args, env)
        if args.attrappe or args.verwundbar:
            if args.adresse or (args.attrappe and args.verwundbar):
                raise AufrufFehler("--attrappe, --verwundbar und eine Adresse schließen sich aus.")
            if args.trocken:
                raise AufrufFehler("--trocken gilt nur mit der Adresse eines Dienstes.")
            return _gegen_nachbildung(args, optionen, auswahl)
        if args.defekt:
            raise AufrufFehler("--defekt gilt nur mit --verwundbar.")
        if not args.adresse:
            raise AufrufFehler("Adresse fehlt: https://HOST[:PORT] des Dienstes angeben (oder --attrappe, --verwundbar).")
        return _gegen_dienst(args, env, optionen, auswahl, transport, takt)
    except AufrufFehler as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return EXIT_AUFRUF


if __name__ == "__main__":
    for _strom in (sys.stdout, sys.stderr):  # Umlaute auch in Pipe und Datei (die Windows-Voreinstellung ist keine UTF-8-Kodierung)
        with contextlib.suppress(AttributeError, ValueError):
            _strom.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
