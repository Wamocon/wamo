"""Büro-Schranken für Entwicklungsaufträge (Kern auf Sokrates-2, WAMO_BETRIEB=buero).

Im Büro läuft jeder Auftrag über den Kern-Kanal des Dispatchers. Dieses Modul hält alles, was der Kern dafür selbst
entscheidet, damit dev_agent.py (über 800 Zeilen) und bot.py nur dünne Einhängepunkte brauchen:

  * Schalter und Einstellungen: WAMO_ENTWICKLUNG_BUERO (an oder aus, Standard aus), WAMO_ENTWICKLUNG_ORT (im Büro nur
    sokrates2), WAMO_ENTWICKLUNG_PROFILE (eine oder mehrere user_id, Pflicht), die beiden Kostengrenzen in USD
    (Pflicht) und WAMO_ENTWICKLUNG_BEFEHLE_BUERO (Befehlskriterien, Standard aus). Die Units behalten
    WAMO_ENTWICKLUNG=aus, der alte Schalter wird im Büro ignoriert. Nur Releases mit diesen Schranken lesen den neuen
    Schalter, ältere starten deshalb mit Entwicklung aus.
  * Zulassung: BueroZulassung entsteht allein aus der geprüften Identität (Admin, Profil in der Liste). Ohne sie
    startet im Büro nichts.
  * Fail closed: unbekannt, fehlend oder ungültig heißt aus, mit einem Grund für Log und Statusleiste (Admins). Nichts
    davon wirft beim Start des Kerns, ein Fehler schaltet nur die Entwicklung aus. Gründe nennen nie Werte oder Pfade.

Der Release-Nachweis steht in der Zeile SCHRANKEN_VERSION unten (das Skript auf Sokrates-2 prüft sie, Schnittstelle I3).
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from loguru import logger

from memory import ENTWICKLUNGSAUFTRAG, KOSTEN_JE_ZEILE_MAX, normalize_uuid

SCHRANKEN_VERSION = 1

ENV_SCHALTER = "WAMO_ENTWICKLUNG_BUERO"
ENV_ORT = "WAMO_ENTWICKLUNG_ORT"
ENV_PROFILE = "WAMO_ENTWICKLUNG_PROFILE"
ENV_KOSTEN_AUFTRAG = "WAMO_ENTWICKLUNG_KOSTEN_AUFTRAG_USD"
ENV_KOSTEN_TAG = "WAMO_ENTWICKLUNG_KOSTEN_TAG_USD"
ENV_BEFEHLE = "WAMO_ENTWICKLUNG_BEFEHLE_BUERO"
ALLE_VARIABLEN = (ENV_SCHALTER, ENV_PROFILE, ENV_KOSTEN_AUFTRAG, ENV_KOSTEN_TAG, ENV_BEFEHLE)

MAX_PROFILE = 20
ANZEIGE_ZEICHEN = 120  # Grund in der Statusnachricht (nur Admins)
_AN = ("an", "on", "ja", "1", "true")
_AUS = ("aus", "off", "nein", "0", "false")
_KOSTEN_RE = re.compile(r"\d{1,5}(?:\.\d{1,4})?")

GRUND_KEIN_ZUGANG = "kein eigener Zugang für Claude Code (Karte Modellzugang)"
GRUND_UEBERGANG = "kein eigener Zugang für Claude Code, der Zugang des Servers gilt im Büro nicht"


def _an_aus(name: str) -> str:
    """Wert einer Schaltervariablen: "an", "aus" (auch ungesetzt) oder "ungueltig"."""
    wert = (os.getenv(name) or "").strip().lower()
    if not wert or wert in _AUS:
        return "aus"
    return "an" if wert in _AN else "ungueltig"


def buero_schalter() -> str:
    """WAMO_ENTWICKLUNG_BUERO als "an", "aus" oder "ungueltig". WAMO_ENTWICKLUNG zählt im Büro nicht."""
    return _an_aus(ENV_SCHALTER)


def befehle_buero_an() -> bool:
    """WAMO_ENTWICKLUNG_BEFEHLE_BUERO=an: Befehlskriterien (WAMO_ABNAHME_BEFEHLE) wirken im Büro erst damit. Standard aus,
    ein ungültiger Wert gilt als aus."""
    return _an_aus(ENV_BEFEHLE) == "an"


def profile() -> tuple[str, ...] | None:
    """Die user_id der Profile, die im Büro Entwicklungsaufträge starten dürfen (WAMO_ENTWICKLUNG_PROFILE, durch Komma
    getrennt), kanonisch geschrieben. None bei fehlendem oder ungültigem Wert (auch nur ein ungültiger Eintrag)."""
    roh = (os.getenv(ENV_PROFILE) or "").strip()
    if not roh:
        return None
    gefunden: list[str] = []
    for teil in roh.split(","):
        uid = normalize_uuid(teil.strip()) if teil.strip() else None
        if uid is None:
            return None
        if uid not in gefunden:
            gefunden.append(uid)
    return tuple(gefunden) if len(gefunden) <= MAX_PROFILE else None


def _kosten(name: str) -> float | None:
    roh = (os.getenv(name) or "").strip()
    if not _KOSTEN_RE.fullmatch(roh):
        return None
    wert = float(roh)
    return wert if wert > 0 else None


def kosten_auftrag() -> float | None:
    """Grenze je Auftrag in USD (alle Läufe zusammen) oder None, wenn sie fehlt oder ungültig ist."""
    return _kosten(ENV_KOSTEN_AUFTRAG)


def kosten_tag() -> float | None:
    """Grenze je Tag und Profil in USD (Europe/Berlin) oder None, wenn sie fehlt oder ungültig ist."""
    return _kosten(ENV_KOSTEN_TAG)


def buero_problem(ssh_pruefung: Callable[[], str | None] | None = None) -> str | None:
    """Grund, warum Entwicklungsaufträge im Büro aus sind (Text ohne Werte und Pfade), oder None. Wirft nie. Geprüft
    werden Schalter, Ort (nur sokrates2), Profilbindung, beide Kostengrenzen und zuletzt ssh_pruefung (dev_agent.ssh_problem:
    Schlüssel und known_hosts vorhanden und lesbar). Aufrufen nur im Büro."""
    try:
        if buero_schalter() == "ungueltig":
            return f"{ENV_SCHALTER} ist ungültig (erlaubt: an, aus)"
        ort = (os.getenv(ENV_ORT) or "sokrates2").strip().lower()
        if ort == "lokal":
            return f"{ENV_ORT}=lokal gibt es im Büro nicht (nur sokrates2)"
        if ort != "sokrates2":
            return f"{ENV_ORT} ist ungültig (im Büro nur sokrates2)"
        if profile() is None:
            return f"{ENV_PROFILE} fehlt oder ist ungültig (eine oder mehrere user_id, durch Komma getrennt)"
        if kosten_auftrag() is None:
            return f"{ENV_KOSTEN_AUFTRAG} fehlt oder ist ungültig (positive Zahl mit Punkt)"
        if kosten_tag() is None:
            return f"{ENV_KOSTEN_TAG} fehlt oder ist ungültig (positive Zahl mit Punkt)"
        return ssh_pruefung() if ssh_pruefung is not None else None
    except Exception as e:  # noqa: BLE001 (ein Fehler hier schaltet nur die Entwicklung aus, nie den Kern)
        return f"Prüfung der Büro-Einstellungen nicht möglich ({type(e).__name__})"


@dataclass(frozen=True)
class BueroZulassung:
    """Nachweis, dass diese Sitzung im Büro Entwicklungsaufträge starten darf: Admin und Profil aus WAMO_ENTWICKLUNG_PROFILE.
    Unveränderlich. Erzeugen soll sie nur bot.build_session über zulassung_erteilen; der DevAgent verlangt sie im Büro und
    prüft sie bei jedem Schritt erneut (zulassung_gueltig)."""

    user_id: str

    def __post_init__(self) -> None:
        uid = normalize_uuid(self.user_id) if isinstance(self.user_id, str) else None
        if uid is None:
            raise ValueError("BueroZulassung braucht eine gültige user_id")
        object.__setattr__(self, "user_id", uid)

    @property
    def kennung(self) -> str:
        """Kurzkennung aus 8 Hexzeichen für Log und Audit (nie die volle user_id)."""
        return self.user_id.replace("-", "")[:8]

    def __repr__(self) -> str:
        return f"BueroZulassung(kennung={self.kennung})"


class ZulassungFehlt(PermissionError):
    """Der DevAgent wurde im Büro ohne gültige Zulassung gebaut."""


SATZ_KEINE_ZULASSUNG = "Entwicklungsaufträge sind für dieses Profil im Büro nicht freigegeben."


def kennung_von(user_id: object) -> str:
    """Kurzkennung (8 Hexzeichen) einer user_id für Log und Audit, ersatzweise ein Fragezeichen. Nie die volle Kennung."""
    uid = normalize_uuid(user_id) if user_id else None
    return uid.replace("-", "")[:8] if uid else "?"


def zulassung_erteilen(user_id: object, ist_admin: bool) -> BueroZulassung | None:
    """Die Zulassung für eine geprüfte Identität oder None: nur Admins, nur mit einer user_id aus WAMO_ENTWICKLUNG_PROFILE.
    Ist die Liste fehlerhaft, gibt es keine Zulassung."""
    uid = normalize_uuid(user_id) if user_id else None
    liste = profile()
    if not ist_admin or uid is None or liste is None or uid not in liste:
        return None
    return BueroZulassung(uid)


def zulassung_gueltig(zulassung: object, user_id: object) -> bool:
    """Gilt die Zulassung noch für dieses Profil? Wird bei jedem Schritt eines Auftrags erneut gefragt: Typ, Person und
    Profilbindung (die Liste kann sich in der Umgebung nie ändern, aber ein Aufrufer kann ein fremdes Objekt übergeben)."""
    if not isinstance(zulassung, BueroZulassung):
        return False
    uid = normalize_uuid(user_id) if user_id else None
    liste = profile()
    return uid is not None and liste is not None and zulassung.user_id == uid and uid in liste


def grund_vorab(user_id: object, ist_admin: bool, problem: str | None = None) -> str | None:
    """Warum die Entwicklung in dieser Büro-Sitzung aus ist (ohne Blick auf den Zugang), sonst None. problem ist das
    Ergebnis von dev_agent.entwicklung_problem."""
    if not ist_admin:
        return "Entwicklungsaufträge gibt es nur für Admins"
    schalter = buero_schalter()
    if schalter == "ungueltig":
        return "Der Schalter für Entwicklungsaufträge im Büro ist ungültig"
    if schalter == "aus":
        return "Der Schalter für Entwicklungsaufträge im Büro ist aus"
    if problem:
        return problem
    if zulassung_erteilen(user_id, ist_admin) is None:
        return "Dieses Profil ist für Entwicklungsaufträge im Büro nicht freigegeben"
    return None


def grund_quelle(quelle: str) -> str | None:
    """Grund aus der Zugangsquelle des Profils (boss.entwicklung_quelle) oder None. Im Büro zählt nur ein eigener Zugang
    (token oder api); uebergang (Zugang des Servers) gilt wie keine."""
    if quelle in ("token", "api"):
        return None
    return GRUND_UEBERGANG if quelle == "uebergang" else GRUND_KEIN_ZUGANG


def anzeige_grund(ist_admin: bool, grund: str | None) -> str | None:
    """Grund für das Feld entwicklung_grund der Statusnachricht: nur Admins, einzeilig, höchstens 120 Zeichen."""
    if not ist_admin or not grund:
        return None
    text = " ".join(grund.split())
    return text[:ANZEIGE_ZEICHEN] or None


def protokollieren(an: bool, grund: str | None, user_id: object, fehler: bool = False) -> None:
    """Eine Logzeile je Büro-Sitzung, auch bei Schalter aus: "Entwicklungsaufträge aus: <Grund>" oder entwicklung=an. Nur
    die Kurzkennung des Profils, nie Werte. fehler: der Grund ist ein Fehler der Einstellung (Log-Stufe error)."""
    kennung = kennung_von(user_id)
    if an:
        logger.info("Entwicklungsaufträge an (Büro): entwicklung=an, Profil {}", kennung)
    else:
        stufe = logger.error if fehler else logger.info
        stufe("Entwicklungsaufträge aus: {} (Profil {})", grund or "unbekannter Grund", kennung)


# ---------------------------------------------------------------- Projektname und Repo-Pflicht (Schnittstelle I5)
KERN_PRAEFIX = "kern-"
PROJEKT_MAX = 50  # Gesamtlänge mit Präfix, wie PROJECT_NAME in dev_agent.py und check_project im Dispatcher
SATZ_REPO_FEHLT = "Für diese Auftragsart brauche ich das Repo als owner/name, zum Beispiel acme/testrepo. Bitte nenne es."


class ProjektnameFehler(ValueError):
    """Aus Repo oder Wort lässt sich kein Projektname bilden. grund ist der Code des Dispatchers: "repo" oder "projekt"."""

    def __init__(self, grund: str, text: str):
        super().__init__(text)
        self.grund = grund


def _projekt_teil(text: str) -> str:
    """Die Regel aus der Schnittstelle I5, Wort für Wort wie im Dispatcher (wamo_job_kernkanal._normal): klein schreiben, "." und
    "_" durch "-" ersetzen, jedes andere Zeichen außerhalb von a-z0-9- verwerfen (auch Leerraum, Umlaute und ß: keine Umschrift,
    "Märkte" ergibt "mrkte"), mehrere "-" zu einem, "-" am Rand streichen."""
    t = text.lower().replace(".", "-").replace("_", "-")
    t = re.sub(r"[^a-z0-9-]", "", t)
    return re.sub(r"-{2,}", "-", t).strip("-")


def kern_projektname(repo: str | None, wort: str | None = None) -> str:
    """Projektname eines Büro-Auftrags: kern- plus Name. Der Name kommt aus dem Repo (Teil nach dem Schrägstrich), sonst aus dem
    Projektwort des Modells. Ein Repo hat Vorrang. Dieselbe Regel erzwingt der Dispatcher (Wertetabelle in der Arbeitsliste).
    ProjektnameFehler: grund "repo" (kein brauchbares Repo, das WAMO-Repo) oder "projekt" (leer oder länger als 50 Zeichen)."""
    from dev_guard import SELF_REPO  # erst hier: dev_guard lädt viel, der Selbstschutz bleibt dort

    if isinstance(repo, str) and repo.strip():
        owner, _, name = repo.strip().partition("/")
        if not owner or not name or "/" in name:
            raise ProjektnameFehler("repo", "Repo: Form owner/name")
        teil = _projekt_teil(name)
        if name.lower() == SELF_REPO or teil == SELF_REPO:
            raise ProjektnameFehler("repo", "Das WAMO-Repo selbst ist für Aufträge gesperrt")
    else:
        teil = _projekt_teil(wort if isinstance(wort, str) else "")
        if teil == SELF_REPO:
            raise ProjektnameFehler("projekt", "Das WAMO-Repo selbst ist für Aufträge gesperrt")
    if not teil:
        raise ProjektnameFehler("projekt", "Aus den Angaben lässt sich kein Projektname bilden")
    name = KERN_PRAEFIX + teil
    if len(name) > PROJEKT_MAX:
        raise ProjektnameFehler("projekt", f"Der Name des Repos ist zu lang (höchstens {PROJEKT_MAX - len(KERN_PRAEFIX)} Zeichen)")
    return name


def buero_projekt(art: str, repo: str | None, wort: object) -> str:
    """Projektname für einen Büro-Auftrag oder ValueError mit einem Satz für das Modell: Arten mit GitHub-Stufe brauchen ein
    Repo (nur die Recherche geht ohne), das Projekt wird nach kern_projektname aus dem Repo abgeleitet."""
    from werkzeugkarte import github_stufe

    if github_stufe(art) is not None and not repo:
        raise ValueError(SATZ_REPO_FEHLT)
    try:
        return kern_projektname(repo, wort if isinstance(wort, str) else None)
    except ProjektnameFehler as e:
        raise ValueError(str(e)) from e


# ---------------------------------------------------------------- Gründe des Kern-Kanals (Schnittstelle I6)
# Schlüssel: die Codes aus dem Ereignis kanal_gesperrt (der Dispatcher hält sie als KANAL_GRUENDE). Je ein kurzer, sprechbarer
# Satz ohne Pfade und Werte; er geht in die Quittung und in den Status des Auftrags.
GRUND_SAETZE: dict[str, str] = {
    "notaus": "Der Notaus ist aktiv, es startet kein Auftrag.",
    "konfig_ungueltig": "Die Regeln des Kanals auf dem Server sind fehlerhaft, es startet kein Auftrag.",
    "kanal_zu": "Der Kanal ist geschlossen, es startet kein Auftrag.",
    "abgelaufen": "Die Freigabe des Kanals ist abgelaufen.",
    "befehl": "Dieser Befehl ist im Kanal nicht erlaubt.",
    "fremd": "Dieser Auftrag gehört nicht zu diesem Kanal.",
    "art": "Diese Auftragsart ist im Kanal nicht erlaubt.",
    "repo": "Dieses Repo ist im Kanal nicht freigegeben.",
    "repo_fehlt": "Für diese Auftragsart fehlt das Repo.",
    "dienst": "Einer der gewünschten Dienste ist im Kanal nicht erlaubt.",
    "projekt": "Der Projektname passt nicht zu den Regeln des Kanals.",
    "zugang": "Der Claude-Zugang kam nicht wie vorgesehen am Server an.",
    "fork": "Das Repo ist eine Kopie oder Vorlage und deshalb gesperrt.",
    "herkunft_unklar": "Die Herkunft der Verbindung ist unklar, deshalb startet nichts.",
    "grenze_start": "Die Obergrenze für Starts ist erreicht.",
    "grenze_laufzeit": "Die Obergrenze für die Laufzeit ist erreicht.",
    "grenze_folgen": "Die Obergrenze für Fortsetzungen ist erreicht.",
    "abstand": "Zwischen zwei Aufträgen muss etwas Zeit liegen, bitte gleich noch einmal versuchen.",
    "platz": "Auf dem Server ist zu wenig Platz frei.",
}
GRUND_UNBEKANNT = "Der Kanal hat den Auftrag abgewiesen."
# Gründe, die nicht vom einzelnen Auftrag abhängen: Ein unveränderter zweiter Versuch scheitert genauso. Nach so einer Abweisung stellt
# WAMO KANAL_SPERRE_SECS lang keine neue Karte (DevAgent._kanal_sperre_satz), statt Karte um Karte abweisen zu lassen.
GRUENDE_DAUERHAFT = frozenset({"notaus", "konfig_ungueltig", "kanal_zu", "abgelaufen", "herkunft_unklar"})
KANAL_SPERRE_SECS = 120.0
SATZ_BELEGT = "Es läuft schon ein Auftrag. Bitte warte, bis er fertig ist."
SATZ_PROBLEM = "Der Auftrag startet nicht: Die Einstellungen für Entwicklungsaufträge sind nicht in Ordnung."
SATZ_UNBEKANNT = "Dieser Auftrag ist nicht mehr bekannt. Bitte stelle ihn neu."
SATZ_KEIN_ZUGANG = "Für dieses Profil ist kein Claude-Zugang hinterlegt, bitte in der Karte Modellzugang eintragen."
SATZ_INTERN = "Der Auftrag ist wegen eines Fehlers im Kern nicht gestartet."
GRUND_NICHT_GESTARTET = "Der Auftrag ist nicht gestartet."


def grund_satz(code: object) -> str:
    """Der Satz zu einem Grund-Code des Kanals; unbekannte Codes bekommen den allgemeinen Satz."""
    return GRUND_SAETZE.get(code, GRUND_UNBEKANNT) if isinstance(code, str) else GRUND_UNBEKANNT


def kanal_status(ereignis: object) -> str:
    """Statustext aus dem Ereignis kanal_gesperrt: nicht gestartet, mit dem Satz des Grundes (ohne Schlusspunkt)."""
    code = ereignis.get("grund") if isinstance(ereignis, dict) else None
    return f"nicht gestartet ({grund_satz(code).rstrip('.')})"


def ssh_fehler_satz(stderr: str) -> str:
    """Satz zu einem Fehler von ssh (Exit 255), aus der Fehlerausgabe eingeordnet: Hostschlüssel, Schlüssel abgelehnt, Schlüsseldatei,
    nicht erreichbar. Der Satz enthält nie Pfade, Adressen oder Teile der Ausgabe."""
    t = (stderr or "").lower()
    if "load key" in t:
        return "Der SSH-Schlüssel des Kerns lässt sich nicht lesen."
    if any(k in t for k in ("host key verification failed", "remote host identification has changed", "host key is known",
                            "no matching host key", "host key for")):
        return "Der Hostschlüssel von Sokrates-2 stimmt nicht mit dem hinterlegten überein."
    if any(k in t for k in ("permission denied", "too many authentication failures", "no more authentication methods")):
        return "Sokrates-2 hat den Schlüssel des Kerns abgelehnt."
    if any(k in t for k in ("timed out", "refused", "no route to host", "unreachable", "could not resolve",
                            "connection closed", "connection reset", "kex_exchange_identification", "broken pipe")):
        return "Sokrates-2 ist nicht erreichbar."
    return "Die Verbindung zu Sokrates-2 ist fehlgeschlagen."


def ablehn_grund(returncode: int | None, ereignisse: dict, stderr: str = "") -> str:
    """Der Satz für die Quittung, wenn ein Start nicht angenommen wurde: aus den Ereignissen des Dispatchers, sonst aus dem Exit-Code
    (255: Fehler von ssh, aus stderr eingeordnet)."""
    if "kanal_gesperrt" in ereignisse:
        ev = ereignisse["kanal_gesperrt"]
        return grund_satz(ev.get("grund") if isinstance(ev, dict) else None)
    if "belegt" in ereignisse:
        return SATZ_BELEGT
    if "zugang_ungueltig" in ereignisse:
        return "Der Claude-Zugang wurde nicht angenommen, bitte in der Karte Modellzugang neu hinterlegen."
    if "keine_zugangsdaten" in ereignisse:
        return "Es ist kein Claude-Zugang angekommen, bitte in der Karte Modellzugang prüfen."
    if "selbsttest_fehlgeschlagen" in ereignisse:
        return "Der Wächter-Selbsttest auf Sokrates-2 ist fehlgeschlagen."
    if "start_abgelehnt" in ereignisse:
        return "Das GitHub-Token für den Auftrag konnte nicht erzeugt werden."
    if returncode == 255:
        return ssh_fehler_satz(stderr)
    if returncode == 2:
        return "Der Dispatcher hat den Auftrag abgewiesen."
    return GRUND_NICHT_GESTARTET


# ---------------------------------------------------------------- Audit und Kostenbremse über actions (E9)
VERALTET_STUNDEN = 2
SATZ_SITZUNG_ENDE = "Die Sitzung ist beendet, der Auftrag ist nicht gestartet."
SATZ_DATENBANK = "Die Datenbank ist nicht erreichbar, deshalb startet kein Auftrag."
SATZ_TAGESGRENZE = "Die Kostengrenze für heute ist erreicht, es startet kein Auftrag mehr."
SATZ_AUFTRAGSGRENZE = "Die Kostengrenze für diesen Auftrag ist erreicht, es folgt kein weiterer Lauf."
STATUS_AUFTRAGSGRENZE = "nicht fortgesetzt (Kostengrenze je Auftrag erreicht)"


def kosten_aus_ereignis(wert: object) -> float | None:
    """Die Kosten (USD) aus total_cost_usd eines result-Ereignisses des Dispatchers (aus der Sandbox, also nicht vertrauenswürdig):
    nur eine endliche Zahl von 0 bis KOSTEN_JE_ZEILE_MAX zählt. Alles andere ergibt None, der Lauf gilt dann als ohne gemeldete Kosten
    und zählt mindestens die Auftragsgrenze: ein fehlendes Feld (zum Beispiel bei einem Abo-Token ohne Kosten je Anfrage, sonst wirkte
    die Bremse nie), negativ, NaN, unendlich, zu groß, Wahrheitswert, Text, Liste."""
    if isinstance(wert, bool) or not isinstance(wert, (int, float, str)):
        return None
    try:
        kosten = float(wert)
    except (ValueError, OverflowError):
        return None
    return kosten if math.isfinite(kosten) and 0.0 <= kosten <= KOSTEN_JE_ZEILE_MAX else None


def kosten_mit_untergrenze(kosten_usd: float) -> float:
    """Die Kosten, die ein Auftrag mindestens gekostet hat, wenn sein letzter Lauf gestartet ist, aber keine verwertbaren Kosten
    gemeldet hat (Abbruch, Auflegen, Zeitlimit, fehlender oder ungültiger Wert): mindestens die Auftragsgrenze (wie bei
    veraltete_schliessen). Ohne gültige Grenze gibt es nichts zu schätzen."""
    grenze = kosten_auftrag()
    return kosten_usd if grenze is None else max(kosten_usd, grenze)


def auftrag_ergebnis(job: str, art: str, projekt: str, repo: str | None, status: str, dauer_s: float, kosten_usd: float,
                     runden: int, grund: str | None = None, geschaetzt: bool = False) -> dict:
    """Das result der Audit-Zeile: Job, Art, Projekt, Repo, Status, Dauer, Kosten und Runden. Nie ein Zugangswert. geschaetzt: die
    Kosten sind die Untergrenze, gemeldet wurden keine."""
    ergebnis = {"job": job, "art": art, "projekt": projekt, "repo": repo, "status": str(status)[:80],
                "dauer_s": int(max(0, dauer_s)), "kosten_usd": round(float(kosten_usd), 4), "runden": int(runden)}
    if grund:
        ergebnis["grund"] = grund
    if geschaetzt:
        ergebnis["geschaetzt"] = True
    return ergebnis


@runtime_checkable
class AuftragsSpeicher(Protocol):
    """Was die Buchführung vom Gedächtnis braucht (memory.MemoryStore erfüllt es, die Tests nutzen eine Attrappe)."""

    async def veraltete_schliessen(self, user_id: str | None = None, nach_stunden: int = 2, kosten_usd: float = 0.0) -> int | None: ...

    async def tageskosten(self, user_id: str | None = None, laufend_usd: float = 0.0) -> float | None: ...

    async def log_action(self, tool: str, params: dict, reason: str, risk: str) -> str | None: ...

    async def update_action(self, action_id: str | None, status: str) -> bool: ...

    async def aktion_abschliessen(self, action_id: str | None, status: str, result: dict) -> bool: ...


class AuftragsBuch:
    """Buchführung eines Büro-Kerns in der Tabelle actions: je Klick auf Starten eine Zeile entwicklungsauftrag nach dem Automaten
    pending, approved, running, done oder failed (ohne Migration), dazu die Tagesgrenze der Kosten. Ist die Datenbank nicht
    erreichbar, startet nichts. params und result enthalten nie einen Zugangswert, die user_id steht nur in der Spalte der Zeile."""

    def __init__(self, memory: AuftragsSpeicher, zulassung: BueroZulassung):
        self._memory = memory
        self._kennung = zulassung.kennung

    async def beginnen(self, job: str, art: str, projekt: str, repo: str | None, schreibend: bool,
                       abbruch: Callable[[], bool] | None = None) -> tuple[str | None, str | None]:
        """Vor dem Start: veraltete running-Zeilen schließen (sie zählen mit der Auftragsgrenze), Tagesgrenze prüfen, die Zeile anlegen
        und freigeben (pending, approved) und auf running setzen. Liefert (aktion_id, None) oder (None, Satz für die Quittung).
        abbruch fragt nach jedem Warten auf die Datenbank, ob die Sitzung inzwischen beendet ist (close): dann entsteht keine Zeile
        mehr oder die angelegte wird zurückgezogen. Eine unerwartete Ausnahme lässt nie eine offene Zeile zurück und geht weiter."""
        auftrag_grenze, tag_grenze = kosten_auftrag(), kosten_tag()
        if auftrag_grenze is None or tag_grenze is None:
            return None, SATZ_PROBLEM
        m = self._memory

        def beendet() -> bool:
            return abbruch is not None and abbruch()

        if await m.veraltete_schliessen(nach_stunden=VERALTET_STUNDEN, kosten_usd=auftrag_grenze) is None:
            return None, SATZ_DATENBANK
        if beendet():
            return None, SATZ_SITZUNG_ENDE
        heute = await m.tageskosten(laufend_usd=auftrag_grenze)  # laufende Zeilen zählen mindestens mit der Auftragsgrenze
        if heute is None:
            return None, SATZ_DATENBANK
        if not heute < tag_grenze:  # auch ein NaN gilt als erreicht (nan >= grenze wäre immer False)
            logger.warning("Entwicklungsauftrag {} nicht gestartet: Tageskosten erreicht, Profil {}", job, self._kennung)
            return None, SATZ_TAGESGRENZE
        if beendet():
            return None, SATZ_SITZUNG_ENDE
        aktion = await m.log_action(
            tool=ENTWICKLUNGSAUFTRAG, params={"job": job, "art": art, "projekt": projekt, "repo": repo, "profil": self._kennung},
            reason="Entwicklungsauftrag im Büro, per Klick gestartet", risk="change" if schreibend else "read")
        if aktion is None:
            return None, SATZ_DATENBANK
        stufe = "pending"
        try:
            if beendet():  # noch nicht freigegeben: zurückziehen (rejected gibt es nur aus pending)
                await m.update_action(aktion, "rejected")
                return None, SATZ_SITZUNG_ENDE
            if not await m.update_action(aktion, "approved"):
                await m.update_action(aktion, "rejected")
                return None, SATZ_DATENBANK
            stufe = "approved"
            if not await m.update_action(aktion, "running"):
                return None, SATZ_DATENBANK
        except Exception:
            await self._notfall_schliessen(aktion, stufe, auftrag_ergebnis(job, art, projekt, repo, "fehlgeschlagen", 0, 0.0, 0))
            raise
        logger.info("Entwicklungsauftrag {} Start im Büro: Art {}, Projekt {}, Profil {}", job, art, projekt, self._kennung)
        return aktion, None

    async def _notfall_schliessen(self, aktion: str, stufe: str, ergebnis: dict) -> None:
        """Nach einer unerwarteten Ausnahme in beginnen: die angelegte Zeile nicht offen lassen (nach dem Automaten: pending wird
        zurückgezogen, approved geht über running nach failed). Ein Fehler dabei wird nur geloggt, nie weitergegeben."""
        m = self._memory
        try:
            if stufe == "pending":
                await m.update_action(aktion, "rejected")
            elif await m.update_action(aktion, "running"):
                await m.aktion_abschliessen(aktion, "failed", ergebnis)
        except Exception as e:  # noqa: BLE001 (best effort: der Fehler, der hierher führte, geht weiter)
            logger.warning("Entwicklungsauftrag: Zeile nach einem Fehler nicht geschlossen ({})", type(e).__name__)

    async def abschliessen(self, aktion: str, erfolg: bool, ergebnis: dict) -> bool:
        """Ende der Zeile: done oder failed mit result. False, wenn die Datenbank nicht antwortet (nur ein Logeintrag, der Auftrag
        ist dann schon vorbei)."""
        ok = bool(await self._memory.aktion_abschliessen(aktion, "done" if erfolg else "failed", ergebnis))
        logger.info("Entwicklungsauftrag {} Ende im Büro: {} (Profil {}, Kosten {:.2f} USD, Dauer {} s, gebucht {})",
                    ergebnis.get("job"), "done" if erfolg else "failed", self._kennung, ergebnis.get("kosten_usd", 0),
                    ergebnis.get("dauer_s", 0), "ja" if ok else "nein")
        return ok


def auftragsgrenze_erreicht(kosten_bisher: float) -> bool:
    """Ist die Kostengrenze je Auftrag erreicht (alle Läufe zusammen)? Eine fehlende oder ungültige Grenze gilt als erreicht, ebenso
    ein Wert, der keine Zahl unter der Grenze ist (NaN: nan >= grenze wäre immer False)."""
    grenze = kosten_auftrag()
    return grenze is None or not kosten_bisher < grenze


def grund_aus_ausgabe(ausgabe: str) -> str | None:
    """Der Satz zu einem kanal_gesperrt in der Ausgabe eines kurzen Dispatcher-Aufrufs (approve, cancel ...) oder None."""
    for zeile in (ausgabe or "").splitlines()[-20:]:
        try:
            ev = json.loads(zeile)
        except ValueError:
            continue
        if isinstance(ev, dict) and ev.get("type") == "wamo" and ev.get("ereignis") == "kanal_gesperrt":
            return grund_satz(ev.get("grund"))
    return None
