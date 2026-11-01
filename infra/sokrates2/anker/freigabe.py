"""Freigabe-Dienst des Ankers: die Regeln (Konzept docs/selbstueberarbeitung.md E.2, Schritt 1.4).

Ein Admin gibt eine Release-Anfrage frei. Der Dienst ist ein eigener kleiner Prozess ohne root (Benutzer wamo-anker), damit weder
der Kern noch der Release-Dienst Freigaben schreiben können. Dieses Modul enthält nur die Regeln und eine Schnittstelle zur
Datenbank (wie release_db.py): Es öffnet kein Netz, keinen Socket und keine Datei. Der Prozess darum (Unix-Socket, Caddy-Route,
Verbindung zur Datenbank als Rolle wamo_anker, Schlüsselsatz von GoTrue) folgt in einem eigenen Schritt.

Was der Dienst selbst prüft, bevor er schreibt (die Trigger der Migration 0013 prüfen es als zweite Linie noch einmal):
  * Das Token trägt eine gültige Signatur (ES256, Schlüssel nach kid), den erwarteten Aussteller und die erwartete Zielgruppe
    und ist nicht abgelaufen. Der Algorithmus kommt nie aus dem Token (kein "none", kein HS256 mit dem öffentlichen Schlüssel).
  * Das Token hat aal2, und der JÜNGSTE totp-Eintrag in amr ist höchstens 60 Sekunden alt und nicht aus der Zukunft. (Gegenstück
    zu auth_gate.anmeldezeit, das den frühesten nimmt: Dort zählt, wann die Sitzung begann, hier, wann der Code getippt wurde.)
  * Der Freigeber sieht die Fassung, die er freigibt: Commit-ID und Tree-ID aus der Oberfläche müssen den gespeicherten Werten
    der Anfrage entsprechen, und die Anfrage muss im Status wartet_freigabe stehen.
  * höchstens 8 Versuche je Profil in 10 Minuten (auch abgelehnte zählen).
Alles andere (Freigeber ist aktiver Admin, bei sonder ein anderer Admin, Wartezeit, mindestens zwei Admins) entscheidet die
Datenbank; ihre Ablehnungen kommen mit festen Codes und deutschen Sätzen zurück, nie mit dem Text des Servers.

Nie in einer Ausgabe, einem Fehler oder einer Protokollzeile: das Token, der Schlüsselsatz, Teile davon.
"""

from __future__ import annotations

import ipaddress
import re
import threading
import time
import uuid
from collections import deque
from typing import Callable, Protocol

import jwt

AAL2_MAX_ALTER_S = 60
AAL2_ZUKUNFT_S = 5
VERSUCHE_MAX = 8
VERSUCHE_FENSTER_S = 600.0
TOKEN_MAX_ZEICHEN = 8192
GILT_S = {"normal": 3600, "sonder": 86400}  # Verfall der Freigabe (Konzept E.2), die Datenbank erzwingt ihn bei der Veröffentlichung

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
COMMIT_RE = re.compile(r"^([0-9a-f]{40}|[0-9a-f]{64})$")


class FreigabeFehler(Exception):
    """Eine Freigabe wurde abgelehnt. code ist maschinenlesbar, text deutsch und ohne Geheimnisse, http der Statuscode."""

    def __init__(self, code: str, text: str, http: int):
        super().__init__(f"{code}: {text}")
        self.code, self.text, self.http = code, text, http


FEHLER = {
    "angaben": ("Die Angaben sind unvollständig oder haben die falsche Form.", 400),
    "token_ungueltig": ("Die Anmeldung ist nicht gültig. Bitte neu anmelden.", 401),
    "aal2_fehlt": ("Für die Freigabe ist der zweite Faktor nötig.", 403),
    "aal2_alt": ("Der Code des zweiten Faktors ist zu alt. Bitte einen neuen Code eingeben.", 403),
    "aal2_zukunft": ("Der Zeitstempel des zweiten Faktors liegt in der Zukunft.", 403),
    "anfrage_unbekannt": ("Diese Anfrage gibt es nicht.", 404),
    "falscher_status": ("Die Anfrage wartet nicht auf eine Freigabe.", 409),
    "fassung_passt_nicht": ("Die Anfrage hat sich geändert. Bitte die Karte neu laden und die neue Fassung lesen.", 409),
    "keine_berechtigung": ("Dir fehlt die Berechtigung für diese Freigabe.", 403),
    "zweiter_admin": ("Braucht einen zweiten Admin.", 403),
    "wartezeit": ("Die Wartezeit der Sonderfreigabe ist noch nicht vorbei.", 409),
    "schon_freigegeben": ("Du hast diese Anfrage schon freigegeben.", 409),
    "regel": ("Die Datenbank lehnt die Freigabe nach ihren Regeln ab.", 409),
    "zu_viele": ("Zu viele Versuche. Bitte in ein paar Minuten noch einmal.", 429),
    "datenbank": ("Die Datenbank ist gerade nicht erreichbar. Bitte gleich noch einmal versuchen.", 503),
}


def fehler(code: str) -> FreigabeFehler:
    text, http = FEHLER[code]
    return FreigabeFehler(code, text, http)


class DatenbankFehler(Exception):
    """Vorübergehend: nicht erreichbar, Zeitüberschreitung."""


class DatenbankAbgelehnt(Exception):
    """Dauerhaft: Eine Regel der Datenbank lehnt ab. code ist der SQLSTATE, text der Satz des Triggers (nie an den Aufrufer)."""

    def __init__(self, code: str, text: str = ""):
        super().__init__(f"{code} {text}".strip())
        self.code, self.text = code, text


class FreigabeDatenbank(Protocol):
    def ist_admin(self, profil: str) -> bool:
        """Ist das Profil ein aktiver Admin (Funktion release_ist_admin, Migration 0013)? Wird vor jeder Abfrage der Anfrage gefragt."""

    def anfrage_lesen(self, anfrage: str) -> dict | None:
        """{"status", "art", "v_commit", "tree_id"} der Anfrage oder None."""

    def freigabe_schreiben(self, anfrage: str, freigeber: str, commit_id: str, tree_id: str, aal2_zeit: float,
                           quell_ip: str | None) -> str:
        """Legt die Freigabe an (Rolle wamo_anker, Migration 0013) und gibt ihre Kennung zurück. Wirft DatenbankAbgelehnt."""


# ------------------------------------------------------------------------------------------------ Token
def token_pruefen(token: object, *, schluessel: Callable[[str | None], object], aussteller: str, zielgruppe: str) -> dict:
    """Prüft Form, Algorithmus, Signatur, Aussteller, Zielgruppe und Ablauf; gibt die Claims zurück.

    schluessel(kid) liefert den öffentlichen Schlüssel zu der Kennung oder wirft KeyError. Sonst gibt es keinen Weg zum Schlüssel."""
    if not isinstance(token, str) or not 20 <= len(token) <= TOKEN_MAX_ZEICHEN or token.count(".") != 2:
        raise fehler("token_ungueltig")
    try:
        kopf = jwt.get_unverified_header(token)
        if kopf.get("alg") != "ES256":  # der Algorithmus ist fest: nie aus dem Token übernehmen
            raise fehler("token_ungueltig")
        kid = kopf.get("kid")
        if kid is not None and not isinstance(kid, str):
            raise fehler("token_ungueltig")
        key = schluessel(kid)
        claims = jwt.decode(token, key, algorithms=["ES256"], audience=zielgruppe, issuer=aussteller,
                            options={"require": ["exp", "iat", "sub", "aud", "iss"]})
    except FreigabeFehler:
        raise
    except Exception:  # noqa: BLE001  jede Panne beim Prüfen oder bei der Schlüsselquelle heißt "nicht gültig" (fail closed, ohne Text)
        raise fehler("token_ungueltig") from None
    sub = claims.get("sub")
    if not isinstance(sub, str) or not UUID_RE.fullmatch(sub):
        raise fehler("token_ungueltig")
    return claims


def aal2_zeit(claims: dict, jetzt: float) -> float:
    """Zeitpunkt des jüngsten totp-Codes. Wirft aal2_fehlt, aal2_zukunft oder aal2_alt."""
    if claims.get("aal") != "aal2":
        raise fehler("aal2_fehlt")
    amr = claims.get("amr")
    zeiten = []
    for eintrag in amr if isinstance(amr, list) else []:
        if isinstance(eintrag, dict) and eintrag.get("method") == "totp":
            t = eintrag.get("timestamp")
            if isinstance(t, (int, float)) and not isinstance(t, bool) and t > 0:
                zeiten.append(float(t))
    if not zeiten:
        raise fehler("aal2_fehlt")
    jung = max(zeiten)
    if jung > jetzt + AAL2_ZUKUNFT_S:
        raise fehler("aal2_zukunft")
    if jetzt - jung > AAL2_MAX_ALTER_S:
        raise fehler("aal2_alt")
    return jung


# ------------------------------------------------------------------------------------------------ Dienst
class Freigabedienst:
    """uhr bestimmt die Frische von aal2 (Zeit des Codes gegen jetzt). exp und iat prüft PyJWT immer gegen die echte Zeit; wer die
    Uhr stellt (Tests), muss Tokens mit echten exp und iat bauen. Die Drosselung läuft auf mono (Standard time.monotonic), damit ein
    Zeitsprung der Systemuhr den Zähler nicht zurücksetzt. Sie gilt je Prozess und überlebt keinen Neustart."""

    def __init__(self, db: FreigabeDatenbank, *, schluessel: Callable[[str | None], object], aussteller: str, zielgruppe: str,
                 uhr: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic):
        self.db, self.schluessel, self.aussteller, self.zielgruppe, self.uhr, self.mono = db, schluessel, aussteller, zielgruppe, uhr, mono
        self._versuche: dict[str, deque[float]] = {}
        self._sperre = threading.Lock()

    def _drosseln(self, profil: str) -> None:
        jetzt = self.mono()
        with self._sperre:
            q = self._versuche.setdefault(profil, deque())
            while q and jetzt - q[0] > VERSUCHE_FENSTER_S:
                q.popleft()
            if len(q) >= VERSUCHE_MAX:
                raise fehler("zu_viele")
            q.append(jetzt)
            if len(self._versuche) > 1024:  # Profile ohne Versuche im Fenster entfernen: der Speicher bleibt begrenzt
                for k in [k for k, v in self._versuche.items() if not v or jetzt - v[-1] > VERSUCHE_FENSTER_S]:
                    del self._versuche[k]

    def freigeben(self, *, token: object, anfrage: object, commit_id: object, tree_id: object, quell_ip: object = None) -> dict:
        """Gibt die Anfrage frei. Wirft FreigabeFehler; bei Erfolg {"ok", "freigabe", "gilt_s"}."""
        if not (isinstance(anfrage, str) and UUID_RE.fullmatch(anfrage) and isinstance(commit_id, str)
                and COMMIT_RE.fullmatch(commit_id) and isinstance(tree_id, str) and COMMIT_RE.fullmatch(tree_id)):
            raise fehler("angaben")
        ip = None
        if quell_ip is not None:
            if not isinstance(quell_ip, str) or "%" in quell_ip:  # nur Text; Scope-Angaben wie fe80::1%eth0 kennt inet nicht
                raise fehler("angaben")
            try:
                ip = str(ipaddress.ip_address(quell_ip))
            except ValueError:
                raise fehler("angaben") from None
        jetzt = self.uhr()
        claims = token_pruefen(token, schluessel=self.schluessel, aussteller=self.aussteller, zielgruppe=self.zielgruppe)
        freigeber = claims["sub"]
        self._drosseln(freigeber)
        zeit = aal2_zeit(claims, jetzt)
        try:
            # Wer kein aktiver Admin ist, erfährt nichts über Anfragen: einheitliche Antwort vor jeder Abfrage (der Trigger prüft es
            # beim Schreiben noch einmal)
            if not self.db.ist_admin(freigeber):
                raise fehler("keine_berechtigung")
            a = self.db.anfrage_lesen(anfrage)
            if a is None:
                raise fehler("anfrage_unbekannt")
            if a.get("status") != "wartet_freigabe":
                raise fehler("falscher_status")
            if a.get("v_commit") != commit_id or a.get("tree_id") != tree_id:
                raise fehler("fassung_passt_nicht")
            art = a.get("art") if a.get("art") in GILT_S else "normal"
            fid = self.db.freigabe_schreiben(anfrage, freigeber, commit_id, tree_id, zeit, ip)
        except FreigabeFehler:
            raise
        except DatenbankAbgelehnt as e:
            raise self._abbildung(e) from None
        except Exception:  # noqa: BLE001  DatenbankFehler und jede andere Panne: neutral, nie der Text des Servers
            raise fehler("datenbank") from None
        return {"ok": True, "freigabe": fid, "gilt_s": GILT_S[art]}

    @staticmethod
    def _abbildung(e: DatenbankAbgelehnt) -> FreigabeFehler:
        """Feste Codes aus dem SQLSTATE und Stichwörtern der Trigger, nie der Text des Servers."""
        t = e.text.lower()
        if e.code == "23505":
            return fehler("schon_freigegeben")
        if e.code == "23503":
            return fehler("anfrage_unbekannt")
        if e.code == "42501":
            return fehler("zweiter_admin") if "zweiten admin" in t else fehler("keine_berechtigung")
        if e.code == "23514":
            if "wartezeit" in t:
                return fehler("wartezeit")
            if "älter als 60" in t:
                return fehler("aal2_alt")
            if "zukunft" in t:
                return fehler("aal2_zukunft")
            if "passt nicht" in t:
                return fehler("fassung_passt_nicht")
            if "nur im status" in t:
                return fehler("falscher_status")
        return fehler("regel")


# ------------------------------------------------------------------------------------------------ Attrappe
class AttrappeFreigabeDatenbank:
    """Bildet die Regeln des Triggers release_freigaben_pruefen (Migration 0013) im Kleinen nach. Nur für Tests und Rauchtest."""

    def __init__(self, uhr: Callable[[], float] = time.time):
        self.uhr = uhr
        self.admins: set[str] = set()
        self.anfragen: dict[str, dict] = {}
        self.freigaben: list[dict] = []
        self.ausfall = False

    def ist_admin(self, profil: str) -> bool:
        if self.ausfall:
            raise DatenbankFehler("Attrappe: nicht erreichbar")
        return profil in self.admins

    def anfrage_lesen(self, anfrage: str) -> dict | None:
        if self.ausfall:
            raise DatenbankFehler("Attrappe: nicht erreichbar")
        a = self.anfragen.get(anfrage)
        return None if a is None else {k: a.get(k) for k in ("status", "art", "v_commit", "tree_id")}

    def freigabe_schreiben(self, anfrage: str, freigeber: str, commit_id: str, tree_id: str, aal2_zeit: float,
                           quell_ip: str | None) -> str:
        if self.ausfall:
            raise DatenbankFehler("Attrappe: nicht erreichbar")
        a = self.anfragen.get(anfrage)
        if a is None:
            raise DatenbankAbgelehnt("23503", "release_freigaben: Anfrage nicht gefunden")
        jetzt = self.uhr()
        # Reihenfolge wie im BEFORE-Trigger der Migration; die Eindeutigkeit (Unique-Index) greift erst danach
        if a["status"] != "wartet_freigabe":
            raise DatenbankAbgelehnt("23514", "release_freigaben: Freigabe nur im Status wartet_freigabe")
        if a.get("v_commit") is None or a.get("tree_id") is None:
            raise DatenbankAbgelehnt("23514", "release_freigaben: Freigabe nur mit gespeicherter Commit-ID und Tree-ID der Anfrage")
        if commit_id != a.get("v_commit") or tree_id != a.get("tree_id"):
            raise DatenbankAbgelehnt("23514", "release_freigaben: Commit-ID oder Tree-ID passt nicht zur Anfrage")
        if freigeber not in self.admins:
            raise DatenbankAbgelehnt("42501", "release_freigaben: der Freigeber ist kein aktiver Admin")
        if aal2_zeit > jetzt + AAL2_ZUKUNFT_S:
            raise DatenbankAbgelehnt("23514", "release_freigaben: aal2_zeit liegt in der Zukunft")
        if jetzt - aal2_zeit > AAL2_MAX_ALTER_S:
            raise DatenbankAbgelehnt("23514", "release_freigaben: der zweite Faktor ist älter als 60 Sekunden")
        if a.get("art") == "sonder":
            if freigeber == a["angefordert_von"]:
                raise DatenbankAbgelehnt("42501", "release_freigaben: bei Art sonder darf der Beauftragte nicht selbst freigeben")
            if a["angefordert_von"] not in self.admins:
                raise DatenbankAbgelehnt("42501", "release_freigaben: bei Art sonder muss auch der Beauftragte ein aktiver Admin sein")
            if len(self.admins) < 2:
                raise DatenbankAbgelehnt("42501", "release_freigaben: Braucht einen zweiten Admin (Sonderfreigabe gesperrt)")
            if a.get("wartet_bis") is None or jetzt < a["wartet_bis"]:
                raise DatenbankAbgelehnt("23514", "release_freigaben: die Wartezeit der Sonderfreigabe ist noch nicht vorbei")
        if any(f["anfrage"] == anfrage and f["freigeber"] == freigeber for f in self.freigaben):
            raise DatenbankAbgelehnt("23505", "release_freigaben_eine_je_person")
        fid = str(uuid.uuid4())
        self.freigaben.append({"id": fid, "anfrage": anfrage, "freigeber": freigeber, "commit_id": commit_id, "tree_id": tree_id,
                               "aal2_zeit": aal2_zeit, "quell_ip": quell_ip, "erstellt": jetzt})
        return fid
