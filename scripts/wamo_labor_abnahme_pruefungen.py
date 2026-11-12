"""Die Prüfungen A1 bis A11 der Abnahme des Test-Jira-Dienstes (docs/labor-dienst-vertrag.md Abschnitt 7), je Anforderung eine Funktion.

Jede Funktion bekommt den Zugang (wamo_labor_abnahme_basis.Zugang) und die Optionen und liefert ein Ergebnis mit einer kurzen Meldung. Sie
arbeitet nur auf den Abnahmeplätzen 15 und 16, legt dort Tickets an und füllt sie, löscht nie etwas (der Vertrag kennt keinen Löschweg) und bricht
Mengenprüfungen an einer Schwelle ab (101 Tickets, 301 Kommentare, über 1 MB Text), auch wenn der Dienst nie mit 429 antwortet. Die
Prüfungen erwarten frisch angelegte Plätze (neuer Durchlauf, Seed); A4 füllt sie und läuft deshalb als letzte (AUSFUEHRUNG).

Die Prüfungen sind einseitig streng: Ein Dienst darf strenger sein als der Vertrag (Quoten, Grenzen), nie lockerer. A1 und A10 haben einen Teil
von Hand (Browserlauf, Netz), A4 die Datenbankgröße; das steht in Ergebnis.von_hand. Die Plätze 15 und 16 sind für die Abnahme reserviert.
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid

from wamo_labor_abnahme_basis import (
    NUMMER_RE,
    PFAD,
    PLATZ_A,
    PLATZ_B,
    UUID_RE,
    VON_HAND,
    ZEIT_RE,
    Antwort,
    Ergebnis,
    Optionen,
    Sammler,
    Zugang,
    betreuer_spezifikation,
    pfad_gueltig,
)

TYPEN = ("bug", "aufgabe", "story")
PRIOS = ("niedrig", "mittel", "hoch", "hoechste")
STATI = ("zu_erledigen", "in_arbeit", "erledigt")
TICKET_FELDER = {"nummer", "titel", "typ", "prioritaet", "status", "beschreibung", "version", "im_durchlauf"}
TICKETS_MAX, KOMMENTARE_MAX, RATE_MAX, BYTES_MAX = 100, 300, 30, 1024 * 1024
GROSS_TITEL, GROSS_TEXT, GROSS_KOMMENTAR = "😀" * 120, "😀" * 1500, "😀" * 500  # vier Bytes je Zeichen: 480, 6000 und 2000 Bytes
MITTEL_TITEL = "G" * 100  # mit GROSS_TEXT etwa 6,3 KB je Ticket: zehn wachsen auf etwa 62,6 KB (über 60, unter 64), neun bleiben unter 60 KB
XSS = ("<script>alert(1)</script>", '"><img src=x onerror=alert(1)>', "javascript:alert(1)")
SQL = ("' OR 1=1 --", "'; DROP TABLE tickets;--", '" OR ""="', "' UNION SELECT NULL--")
FEHLER_CODES = {"token_ungueltig": (401,), "platz_unbekannt": (404,), "ticket_unbekannt": (404,), "durchlauf_beendet": (409,),
                "ticket_veraendert": (409,), "eingabe_ungueltig": (422,), "zu_gross": (413,), "quote_erreicht": (429,), "nicht_erlaubt": (404, 405)}
INTERNA = ("Traceback", 'File "', ".py", "sqlite", "SELECT ", "INSERT ", "UPDATE ", "Exception", "/usr/", "/opt/", "/app/", "\\")


def _dict(a: Antwort) -> dict:
    return a.json if isinstance(a.json, dict) else {}


def _ticket_aus(a: Antwort) -> dict:
    """Das Ticket in der Antwort von V7 ({"kommentar_id": n, "ticket": Ticket})."""
    ticket = _dict(a).get("ticket")
    return ticket if isinstance(ticket, dict) else {}


def _liste(a: Antwort) -> list:
    tickets = _dict(a).get("tickets")
    return tickets if isinstance(tickets, list) else []


def _ganz(wert: object) -> bool:
    return isinstance(wert, int) and not isinstance(wert, bool)


def _ticket_form(t: object) -> str:
    """Leer, wenn t ein Ticket nach Vertrag ist, sonst der Grund."""
    if not isinstance(t, dict) or set(t) != TICKET_FELDER:
        return "Felder weichen vom Vertrag ab"
    if not (isinstance(t["nummer"], str) and NUMMER_RE.fullmatch(t["nummer"]) and t["typ"] in TYPEN and t["prioritaet"] in PRIOS
            and t["status"] in STATI and isinstance(t["titel"], str) and isinstance(t["beschreibung"], str)):
        return "Werte außerhalb der Wertebereiche"
    if not (_ganz(t["version"]) and t["version"] >= 1 and isinstance(t["im_durchlauf"], bool)):
        return "version oder im_durchlauf falsch"
    return ""


def _nr(t: dict) -> int:
    return int(str(t.get("nummer", "LAB-0"))[4:] or 0)


# ---------- A1 XSS ----------
async def a1_xss(z: Zugang, o: Optionen) -> Ergebnis:
    s, p = Sammler(), PLATZ_A
    for nutzlast in XSS:
        kurz = nutzlast[:16]
        a = await z.anlegen(p, nutzlast, nutzlast)
        s.status(a, 201, f"Anlegen mit {kurz}")
        s.koepfe(a, f"Anlegen ({kurz})")
        t = _dict(a)
        s.erwarte(t.get("titel") == nutzlast and t.get("beschreibung") == nutzlast, f"{kurz}: Titel oder Beschreibung kommt verändert zurück")
        if not t.get("nummer"):
            continue
        k = await z.kommentieren(p, t["nummer"], t.get("version", 1), nutzlast)
        s.status(k, 201, f"Kommentar mit {kurz}")
        lesen = await z.senden("GET", f"{PFAD}/tickets/{t['nummer']}", platz=p, kopf={"Accept": "text/html"})
        s.status(lesen, 200, f"Lesen ({kurz})")
        s.koepfe(lesen, f"Lesen ({kurz})")
        s.erwarte([c.get("text") for c in _dict(lesen).get("kommentare", []) if isinstance(c, dict)] == [nutzlast] and _dict(lesen).get("titel") == nutzlast,
                  f"{kurz}: Kommentar oder Titel kommt beim Lesen verändert zurück")
        such = await z.tickets(p, q=nutzlast)
        s.koepfe(such, f"Suche ({kurz})")
        s.erwarte(t["nummer"] in [x.get("nummer") for x in _liste(such) if isinstance(x, dict)], f"{kurz}: die Suche findet das Ticket nicht wörtlich")
    spiegel = await z.senden("GET", f"{PFAD}/tickets/<img src=x onerror=alert(1)>", platz=p)
    s.status(spiegel, 422, "Ticketnummer mit HTML", "eingabe_ungueltig")
    s.koepfe(spiegel, "Fehlerantwort mit HTML im Pfad")
    s.erwarte(b"<img" not in spiegel.inhalt, "die Fehlerantwort spiegelt den Pfad unmaskiert zurück")
    if o.board_url:
        await _board(z, s, o.board_url)
    else:
        s.von_hand.append("Board-Seite (CSP, Referrer-Policy, nosniff): --board-url angeben")
    s.von_hand.append("Rohseite der Board-Seite mit den Nutzlasten und Browserlauf (kein Dialog, keine Skriptausführung, Playwright): nur mit Platz-Token im Browser, "
                      "das Skript schickt der Board-Seite nie den Bearer des Kerns")
    return s.ergebnis("A1", "XSS", "Nutzlasten bleiben Daten (JSON, nosniff)")


async def _board(z: Zugang, s: Sammler, url: str) -> None:
    """Nur die Köpfe der Seite (CSP, nosniff, Referrer-Policy). Ohne Token und Platz zeigt sie keine Tickets: Ihr Text ließe sich nicht prüfen (von Hand)."""
    a = await z.seite(url)
    s.erwarte(a.status == 200, f"Board-Seite: erwartet 200, erhalten {a.beschreibung()}")
    if a.status != 200:
        return
    csp = a.kopf.get("content-security-policy", "")
    s.erwarte(bool(csp), "Board-Seite: Content-Security-Policy fehlt")
    if csp:
        # 'self' (Vertrag D4) oder die strengere Fassung 'none' mit eigenen script-src und style-src (so liefert der eigene Dienst seine Seiten aus)
        s.erwarte(re.search(r"(?:^|;)\s*default-src\s+(?:'self'|'none')\s*(?:;|$)", csp) is not None,
                  "Board-Seite: CSP ohne default-src 'self' oder 'none'")
        s.erwarte("'unsafe-inline'" not in csp and "'unsafe-eval'" not in csp, "Board-Seite: CSP erlaubt unsafe-inline oder unsafe-eval")
        s.erwarte("frame-ancestors 'none'" in csp, "Board-Seite: CSP ohne frame-ancestors 'none'")
    s.erwarte(a.kopf.get("x-content-type-options", "").lower() == "nosniff", "Board-Seite: X-Content-Type-Options: nosniff fehlt")
    s.erwarte(a.kopf.get("referrer-policy", "").lower() == "no-referrer", "Board-Seite: Referrer-Policy: no-referrer fehlt")


# ---------- A2 SQL-Injektion ----------
async def a2_sql(z: Zugang, o: Optionen) -> Ergebnis:
    s, p = Sammler(), PLATZ_A
    gesamt0 = _dict(await z.tickets(p, limit=1)).get("gesamt")
    for n in SQL:
        a = await z.tickets(p, q=n)
        s.status(a, 200, f"Suche mit {n}")
        s.erwarte(_dict(a).get("gesamt") == 0 and _liste(a) == [], f"Suche nach {n} liefert fremde Treffer")
        s.status(await z.tickets(p, nummer=n), 422, f"nummer={n}", "eingabe_ungueltig")
        s.status(await z.ticket(p, n), (404, 422), f"Pfad mit {n}")
        s.status(await z.v1(f"{p}{n}"), 404, f"Platzkopf {p}{n}", "platz_unbekannt")
        s.status(await z.v1(p, bearer=n), 401, f"Bearer {n}", "token_ungueltig")
        s.status(await z.anlegen(p, "Titel", "", durchlauf=n, modus="sonde"), 422, f"X-WAMO-Durchlauf {n}", "eingabe_ungueltig")
        s.status(await z.anlegen(p, "Titel", "", schluessel=n, modus="sonde"), 422, f"Idempotency-Key {n}", "eingabe_ungueltig")
    for n in SQL[:2]:
        a = await z.anlegen(p, n, n)
        s.status(a, 201, f"Titel {n}")
        s.erwarte(_dict(a).get("titel") == n, f"Titel {n} wird nicht wörtlich gespeichert")
    treffer = await z.tickets(p, q=SQL[0])
    s.erwarte(_dict(treffer).get("gesamt") == 1 and [x.get("titel") for x in _liste(treffer)] == [SQL[0]], "wörtliche Suche nach der Einspritzung findet mehr als das eine Ticket")
    s.erwarte(_dict(await z.tickets(p, nummer="LAB-12")).get("gesamt") == 1, "LAB-12 fehlt nach den Einspritzungen (Tabelle verändert)")
    s.erwarte(isinstance(gesamt0, int) and _dict(await z.tickets(p, limit=1)).get("gesamt") == gesamt0 + 2, "die Zahl der Tickets stimmt nach den Einspritzungen nicht")
    return s.ergebnis("A2", "SQL-Injektion", "keine fremden Treffer, kein 5xx")


# ---------- A3 Prozent und Unterstrich ----------
async def a3_prozent(z: Zugang, o: Optionen) -> Ergebnis:
    s, p = Sammler(), PLATZ_A
    for titel in ("100% fertig", "1000 fertig", "a_b Test", "axb Test", "a1b Test", "Pfad C:\\temp"):
        s.status(await z.anlegen(p, titel, ""), 201, f"Anlegen {titel}")
    faelle = (("%", "100% fertig"), ("_", "a_b Test"), ("a_b", "a_b Test"), ("100%", "100% fertig"), ("\\", "Pfad C:\\temp"))
    for q, gesucht in faelle:
        a = await z.tickets(p, q=q)
        s.status(a, 200, f"Suche nach {q}")
        treffer = [t for t in _liste(a) if isinstance(t, dict)]
        fremd = [t.get("titel") for t in treffer if q.casefold() not in f"{t.get('titel')}\n{t.get('beschreibung')}".casefold()]
        s.erwarte(not fremd, f"Suche nach {q} liefert unwörtliche Treffer ({', '.join(map(str, fremd[:2]))})")
        s.erwarte(gesucht in [t.get("titel") for t in treffer], f"Suche nach {q} findet {gesucht} nicht")
        s.erwarte(_dict(a).get("gesamt") == len(treffer) or len(treffer) == 10, f"Suche nach {q}: gesamt passt nicht zur Liste")
    return s.ergebnis("A3", "Prozent und Unterstrich", "Suche ist wörtlich")


# ---------- A5 Größenlimits ----------
def _koerper(laenge: int) -> bytes:
    rumpf = {"typ": "bug", "prioritaet": "hoch", "titel": "Zu gross", "beschreibung": ""}
    fuell = laenge - len(json.dumps(rumpf).encode())
    return json.dumps({**rumpf, "beschreibung": "x" * fuell}).encode()


async def a5_groessen(z: Zugang, o: Optionen) -> Ergebnis:
    s, p = Sammler(), PLATZ_A
    json_kopf = {"Content-Type": "application/json"}
    for groesse in (16 * 1024 + 1, 20 * 1024, 70 * 1024):
        a = await z.schreiben("POST", f"{PFAD}/tickets", None, platz=p, inhalt=_koerper(groesse), kopf=json_kopf, modus="sonde", abbruch_ok=True)
        s.status(a, 413, f"Anfrage mit {groesse} Bytes", "zu_gross")
    v = (_dict(await z.ticket(p, "LAB-12")).get("version") or 1)
    formfehler = {
        "Titel mit 121 Zeichen": lambda: z.anlegen(p, "x" * 121, "", modus="sonde"),
        "Titel mit 2 Zeichen": lambda: z.anlegen(p, "ab", "", modus="sonde"),
        "Beschreibung mit 1501 Zeichen": lambda: z.anlegen(p, "Titel", "x" * 1501, modus="sonde"),
        "Titel mit 121 Zeichen aus Emojis": lambda: z.anlegen(p, "😀" * 121, "", modus="sonde"),
        "Beschreibung mit 1501 Emojis": lambda: z.anlegen(p, "Titel", "😀" * 1501, modus="sonde"),
        "V5 Titel mit 121 Zeichen": lambda: z.aendern(p, "LAB-12", v, {"titel": "x" * 121}, modus="sonde"),
        "V5 Beschreibung mit 1501 Zeichen": lambda: z.aendern(p, "LAB-12", v, {"beschreibung": "x" * 1501}, modus="sonde"),
        "Kommentar mit 501 Zeichen": lambda: z.kommentieren(p, "LAB-12", v, "x" * 501, modus="sonde"),
        "Kommentar mit 0 Zeichen": lambda: z.kommentieren(p, "LAB-12", v, "", modus="sonde"),
    }
    for name, aufruf in formfehler.items():
        a = await aufruf()
        s.status(a, 422, name, "eingabe_ungueltig")
        if a.status != 422:  # der Dienst hat angenommen: die Version hat sich geändert
            v = _dict(await z.ticket(p, "LAB-12")).get("version") or v
    s.status(await z.tickets(p, q="x" * 101), 422, "q mit 101 Zeichen", "eingabe_ungueltig")
    for name, (titel, text) in {"Titel mit 120 und Beschreibung mit 1500 Zeichen": ("x" * 120, "y" * 1500), "kürzester Titel mit 3 Zeichen": ("abc", ""),
                                "120 und 1500 Emojis (Zeichen, nicht Bytes)": ("😀" * 120, "😀" * 1500)}.items():
        a = await z.anlegen(p, titel, text)
        s.status(a, 201, name)
        s.erwarte(_dict(a).get("titel") == titel and _dict(a).get("beschreibung") == text, f"{name}: kommt verändert zurück")
    k = await z.kommentieren(p, "LAB-12", _dict(await z.ticket(p, "LAB-12")).get("version") or 1, "k" * 500)
    s.status(k, 201, "Kommentar mit 500 Zeichen")
    kopf = await z.v1(p, kopf={"X-Gross": "a" * 70000}, abbruch_ok=True)
    s.erwarte(kopf.status in (400, 413, 414, 431) or (kopf.status == 0 and not kopf.fehler.startswith("Zeit")),
              f"Kopf mit 70000 Bytes wird nicht abgelehnt (erhalten {kopf.beschreibung()})")
    return s.ergebnis("A5", "Größenlimits", "413 über 16 KB, Länge plus eins ergibt 422, große Köpfe abgelehnt")


# ---------- A6 Platztrennung ----------
async def a6_platztrennung(z: Zugang, o: Optionen) -> Ergebnis:
    s, a_, b_ = Sammler(), PLATZ_A, PLATZ_B
    liste_b = _liste(await z.tickets(b_, limit=1))
    hoechste_b = _nr(liste_b[0]) if liste_b and isinstance(liste_b[0], dict) else 0
    ticket: dict = {}
    for i in range(6):  # eine Nummer, die es auf Platz 16 nicht gibt
        a = await z.anlegen(a_, f"Platz 15 privat {i}", "nur auf Platz 15")
        s.status(a, 201, "Platz 15 legt an")
        if _nr(_dict(a)) > hoechste_b:
            ticket = _dict(a)
            break
    if not ticket:
        s.erwarte(False, "Vorbedingung: keine Ticketnummer auf Platz 15, die Platz 16 nicht hat")
        return s.ergebnis("A6", "Platztrennung", "")
    nummer = ticket["nummer"]
    s.status(await z.ticket(b_, nummer), 404, f"Platz 16 liest {nummer}", "ticket_unbekannt")
    s.status(await z.aendern(b_, nummer, 1, {"titel": "Von Platz 16 geändert"}), 404, "Platz 16 ändert", "ticket_unbekannt")
    s.status(await z.status_setzen(b_, nummer, 1, "erledigt"), 404, "Platz 16 setzt den Status", "ticket_unbekannt")
    s.status(await z.kommentieren(b_, nummer, 1, "Von Platz 16"), 404, "Platz 16 kommentiert", "ticket_unbekannt")
    s.erwarte(_dict(await z.tickets(b_, nummer=nummer)).get("gesamt") == 0, "Platz 16 findet die Nummer von Platz 15 über die Suche")
    s.erwarte(_dict(await z.tickets(b_, q="Platz 15 privat")).get("gesamt") == 0, "Platz 16 findet den Text von Platz 15 über die Suche")
    nachher = _dict(await z.ticket(a_, nummer))
    s.erwarte(nachher.get("version") == 1 and nachher.get("titel") == ticket.get("titel") and nachher.get("kommentare") == [],
              "das Ticket von Platz 15 hat sich durch Aufrufe von Platz 16 geändert")
    for platz in (b_, a_, 99):
        s.status(await z.v1(platz, bearer=None), 401, f"gefälschter Platzkopf {platz} ohne Bearer", "token_ungueltig")
    s.status(await z.ticket(b_, nummer, bearer=None), 401, "Ticket von Platz 15 über Platz 16 ohne Bearer", "token_ungueltig")
    if o.platz_token and o.platz_pfade:
        for vorlage in (v for v in o.platz_pfade if pfad_gueltig(v)):
            r = await z.senden("GET", vorlage.replace("{platz}", str(b_)), platz=None, bearer=o.platz_token)
            s.status(r, (403, 404), "Platz-Token von Platz 15 gegen den Pfad von Platz 16")
    else:
        s.von_hand.append("Platz-Token gegen den Pfad eines anderen Platzes (--platz-pfad und WAMO_LABOR_PLATZ_TOKEN)")
    return s.ergebnis("A6", "Platztrennung", "Platz 16 sieht nichts von Platz 15")


# ---------- A7 Kein Löschweg ----------
ERLAUBT = {f"{PFAD}/durchlauf": {"GET"}, f"{PFAD}/tickets": {"GET", "POST"}, f"{PFAD}/tickets/LAB-20": {"GET", "PATCH"},
           f"{PFAD}/tickets/LAB-20/status": {"POST"}, f"{PFAD}/tickets/LAB-20/kommentare": {"POST"}}


async def _stand(z: Zugang, platz: int) -> dict:
    stand: dict = {"gesamt": _dict(await z.tickets(platz, limit=1)).get("gesamt"),
                   "im_durchlauf": _dict(await z.tickets(platz, nur_durchlauf="true", limit=1)).get("gesamt")}
    for nr in ("LAB-11", "LAB-12", "LAB-20"):
        t = _dict(await z.ticket(platz, nr))
        stand[nr] = (t.get("titel"), t.get("status"), t.get("version"), len(t.get("kommentare", []))) if t.get("nummer") else "fehlt"
    return stand


async def a7_loeschweg(z: Zugang, o: Optionen) -> Ergebnis:
    s, p, n = Sammler(), PLATZ_A, "LAB-20"
    vorher = await _stand(z, p)
    v = _dict(await z.ticket(p, n)).get("version") or 1
    kopf = {"X-WAMO-Durchlauf": z.durchlauf.get(p, str(uuid.uuid4())), "Idempotency-Key": str(uuid.uuid4())}
    for pfad, erlaubt in ERLAUBT.items():
        for methode in ("DELETE", "PUT", "PATCH", "POST", "TRACE", "PURGE"):
            if methode in erlaubt:
                continue
            mit_inhalt = methode in ("DELETE", "PUT", "PATCH", "POST")
            a = await z.senden(methode, pfad, platz=p, daten={"erwartete_version": v, "status": "erledigt"} if mit_inhalt else None, kopf=kopf, schreibend=True)
            s.status(a, (404, 405, 422) if mit_inhalt else (400, 404, 405, 422, 501), f"{methode} {pfad.removeprefix(PFAD)}")
    for name in ("X-HTTP-Method-Override", "X-HTTP-Method", "X-Method-Override"):
        s.status(await z.ticket(p, n, kopf={name: "DELETE"}), 200, f"GET mit {name}: DELETE (muss ignoriert werden)")
    s.status(await z.tickets(p, _method="DELETE"), (200, 422), "Abfrage _method=DELETE")
    s.status(await z.tickets(p, geloescht="true"), 422, "Abfrage geloescht=true", "eingabe_ungueltig")
    felder = (("PATCH", f"{PFAD}/tickets/{n}", {"erwartete_version": v, "felder": {"geloescht": True}}),
              ("PATCH", f"{PFAD}/tickets/{n}", {"erwartete_version": v, "felder": {"titel": "Gelöscht? Nein", "geloescht": True}}),
              ("PATCH", f"{PFAD}/tickets/{n}", {"erwartete_version": v, "felder": {"status": "erledigt"}}),
              ("PATCH", f"{PFAD}/tickets/{n}", {"erwartete_version": v, "felder": {"nummer": "LAB-99"}}),
              ("POST", f"{PFAD}/tickets/{n}/status", {"erwartete_version": v, "status": "erledigt", "geloescht": True}),
              ("POST", f"{PFAD}/tickets/{n}/kommentare", {"erwartete_version": v, "text": "Löschen", "loeschen": True}),
              ("POST", f"{PFAD}/tickets", {"typ": "bug", "prioritaet": "hoch", "titel": "Löschen", "beschreibung": "", "geloescht": True}))
    for methode, pfad, daten in felder:
        s.status(await z.schreiben(methode, pfad, daten, platz=p, modus="sonde"), 422, f"Feld zum Löschen in {methode} {pfad.removeprefix(PFAD)}", "eingabe_ungueltig")
    s.erwarte(await _stand(z, p) == vorher, "Zeilenzahl oder Inhalt der Seed-Tickets hat sich verändert (ein Ticket ging verloren)")
    return s.ergebnis("A7", "Kein Löschweg", "kein Weg zum Löschen, Zeilenzahl unverändert")


# ---------- A8 Durchlauf und Version ----------
async def _gesamt(z: Zugang, platz: int) -> object:
    return _dict(await z.tickets(platz, limit=1)).get("gesamt")


async def a8_durchlauf_version(z: Zugang, o: Optionen) -> Ergebnis:
    s, p = Sammler(), PLATZ_A
    a = await z.anlegen(p, "Versionstest", "Prüfung von Durchlauf und Version")
    s.status(a, 201, "Anlegen")
    nummer, v = _dict(a).get("nummer", "LAB-12"), _dict(a).get("version", 1)
    stand = await _gesamt(z, p)
    fremd = str(uuid.uuid4())
    aufrufe = {"V4 anlegen": lambda **kw: z.anlegen(p, "Fremder Durchlauf", "", **kw), "V5 ändern": lambda **kw: z.aendern(p, nummer, v, {"titel": "Fremd"}, **kw),
               "V6 Status": lambda **kw: z.status_setzen(p, nummer, v, "erledigt", **kw), "V7 Kommentar": lambda **kw: z.kommentieren(p, nummer, v, "Fremd", **kw)}
    for name, aufruf in aufrufe.items():
        s.status(await aufruf(durchlauf=fremd), 409, f"{name} mit fremder Durchlaufkennung", "durchlauf_beendet")
    t = _dict(await z.ticket(p, nummer))
    s.erwarte(t.get("version") == v and t.get("titel") == "Versionstest" and await _gesamt(z, p) == stand, "ein Schreibaufruf mit fremder Durchlaufkennung hat etwas verändert")
    for name in list(aufrufe)[1:]:
        s.status(await _mit_version(z, p, name, nummer, v + 1), 409, f"{name} mit falscher Version", "ticket_veraendert")
    s.erwarte(_dict(await z.ticket(p, nummer)).get("version") == v, "eine Änderung mit falscher Version hat die Version verändert")
    neu = await z.status_setzen(p, nummer, v, "in_arbeit")
    s.status(neu, 200, "Status mit richtiger Version")
    s.status(await z.status_setzen(p, nummer, v, "erledigt"), 409, "Status mit veralteter Version", "ticket_veraendert")
    await _idempotenz(z, s, p, nummer, _dict(neu).get("version", v + 1))
    await _gleichzeitig(z, s, p, nummer)
    s.von_hand.append("409 durchlauf_beendet bei einem wirklich beendeten Durchlauf (V1 offen=false): Betreuer beendet den Durchlauf, danach ein Schreibaufruf mit der alten "
                      "Kennung (das Skript kann einen Durchlauf nicht beenden und prüft nur eine fremde Kennung)")
    s.von_hand.append("Atomarität in der Transaktion (D13): nur im Review, das Skript prüft eine Stichprobe mit gleichzeitigen Aufrufen")
    return s.ergebnis("A8", "Durchlauf und Version", "409 bei fremder Kennung und falscher Version, Idempotenz, Stichprobe gleichzeitiger Änderungen")


async def _mit_version(z: Zugang, p: int, name: str, nummer: str, version: int) -> Antwort:
    if name.startswith("V5"):
        return await z.aendern(p, nummer, version, {"titel": "Falsche Version"})
    if name.startswith("V6"):
        return await z.status_setzen(p, nummer, version, "erledigt")
    return await z.kommentieren(p, nummer, version, "Falsche Version")


async def _idempotenz(z: Zugang, s: Sammler, p: int, nummer: str, version: int) -> None:
    vorher, schluessel = await _gesamt(z, p), str(uuid.uuid4())
    erst = await z.anlegen(p, "Idempotenzprobe", "einmal", schluessel=schluessel)
    zweit = await z.anlegen(p, "Idempotenzprobe", "einmal", schluessel=schluessel)
    s.status(erst, 201, "Idempotenz: erster Aufruf")
    s.erwarte(zweit.status == erst.status and zweit.json == erst.json, "Idempotenz: dieselbe Anfrage liefert nicht dieselbe Antwort")
    s.erwarte(zweit.kopf.get("idempotent-replayed", "").lower() == "true", "Idempotenz: Kopf Idempotent-Replayed: true fehlt")
    s.erwarte(await _gesamt(z, p) == (vorher + 1 if isinstance(vorher, int) else None), "Idempotenz: es entsteht ein zweites Ticket")
    s.status(await z.anlegen(p, "Anderer Titel", "", schluessel=schluessel), 422, "Idempotenz: derselbe Schlüssel mit anderer Anfrage", "eingabe_ungueltig")
    k = str(uuid.uuid4())
    a = await z.status_setzen(p, nummer, version, "zu_erledigen", schluessel=k)
    b = await z.status_setzen(p, nummer, version, "zu_erledigen", schluessel=k)
    s.erwarte(a.status == b.status == 200 and a.json == b.json and _dict(await z.ticket(p, nummer)).get("version") == version + 1,
              "Idempotenz: die Wiederholung einer Statusänderung zählt die Version zweimal")


async def _gleichzeitig(z: Zugang, s: Sammler, p: int, nummer: str) -> None:
    """Stichprobe zu D13: acht gleiche Änderungen zugleich, genau eine darf durch; fünf gleiche Anlagen mit demselben Schlüssel ergeben ein Ticket.
    Das beweist nicht, dass der Dienst in einer Transaktion prüft, es findet nur Fehler, die sich hier zeigen."""
    v = _dict(await z.ticket(p, nummer)).get("version") or 1
    await z.schreibtakt(p).vor_schreiben(8)
    rs = await asyncio.gather(*[z.status_setzen(p, nummer, v, "erledigt", modus="sonde") for _ in range(8)])
    s.erwarte(sum(r.status == 200 for r in rs) == 1, f"gleichzeitig mit derselben Version: {sum(r.status == 200 for r in rs)} statt einer Änderung angenommen")
    s.erwarte(all(r.ist(409, "ticket_veraendert") for r in rs if r.status != 200), "gleichzeitige Änderungen: die abgelehnten sind nicht alle 409 ticket_veraendert")
    s.erwarte(_dict(await z.ticket(p, nummer)).get("version") == v + 1, "gleichzeitige Änderungen: die Version ist nicht genau um eins gewachsen")
    vorher, schluessel = await _gesamt(z, p), str(uuid.uuid4())
    await z.schreibtakt(p).vor_schreiben(5)
    rs = await asyncio.gather(*[z.anlegen(p, "Parallel", "", schluessel=schluessel, modus="sonde") for _ in range(5)])
    s.erwarte(isinstance(vorher, int) and await _gesamt(z, p) == vorher + 1, "gleichzeitige Anlage mit demselben Schlüssel: es entsteht nicht genau ein Ticket")
    s.erwarte(len({_dict(r).get("nummer") for r in rs if r.status in (200, 201)}) <= 1, "gleichzeitige Anlage mit demselben Schlüssel: verschiedene Antworten")


# ---------- A9 Zugang ----------
async def a9_zugang(z: Zugang, o: Optionen) -> Ergebnis:
    s, p = Sammler(), PLATZ_A
    zufall = uuid.uuid4().hex + uuid.uuid4().hex
    varianten = {"ohne Bearer": {"bearer": None}, "falschem Bearer": {"bearer": zufall}, "leerem Bearer": {"bearer": ""},
                 "angehängtem Zeichen": {"bearer": z.token_mit("x")}, "gekürztem Bearer": {"bearer": z.token_mit(None)},
                 "anderem Schema": {"bearer": None, "kopf": {"Authorization": f"Basic {zufall}"}}}
    schreib = {"X-WAMO-Durchlauf": z.durchlauf.get(p, str(uuid.uuid4())), "Idempotency-Key": str(uuid.uuid4())}
    aufrufe = (("V1", "GET", "/durchlauf", None), ("V2", "GET", "/tickets", None), ("V3", "GET", "/tickets/LAB-12", None),
               ("V4", "POST", "/tickets", {"typ": "bug", "prioritaet": "hoch", "titel": "Ohne Zugang", "beschreibung": ""}),
               ("V5", "PATCH", "/tickets/LAB-12", {"erwartete_version": 1, "felder": {"titel": "Ohne Zugang"}}),
               ("V6", "POST", "/tickets/LAB-12/status", {"erwartete_version": 1, "status": "erledigt"}),
               ("V7", "POST", "/tickets/LAB-12/kommentare", {"erwartete_version": 1, "text": "Ohne Zugang"}))
    for name, wie in varianten.items():
        for aufruf, methode, pfad, daten in aufrufe:
            kopf = {**(schreib if daten else {}), **wie.get("kopf", {})}
            a = await z.senden(methode, PFAD + pfad, platz=p, bearer=wie["bearer"], kopf=kopf, daten=daten, schreibend=bool(daten))
            s.status(a, 401, f"{aufruf} mit {name}", "token_ungueltig")
    if o.betreuer_pfade and o.platz_token:
        for spez in o.betreuer_pfade:
            teile = betreuer_spezifikation(spez)
            if teile is None:
                s.von_hand.append("Betreuer-Pfad ungültig (nur Pfade auf dem Host des Dienstes): nicht gesendet")
                continue
            methode, pfad = teile
            if methode != "GET" and not o.nicht_get_frei:
                s.von_hand.append(f"Betreuer-Pfad {pfad} mit {methode}: nicht gesendet, die Freigabe (--nicht-get-freigeben) fehlt")
                continue
            s.status(await z.senden(methode, pfad, platz=None, bearer=o.platz_token, schreibend=methode != "GET"), 403, f"Betreuer-Pfad {pfad} mit Platz-Token")
            s.status(await z.senden(methode, pfad, platz=p, schreibend=methode != "GET"), (401, 403, 404), f"Betreuer-Pfad {pfad} mit dem Bearer des Kerns")
    else:
        s.von_hand.append("Betreuer-Pfade mit Platz-Token und mit dem Bearer des Kerns (--betreuer-pfad und WAMO_LABOR_PLATZ_TOKEN)")
    return s.ergebnis("A9", "Zugang", "ohne oder mit falschem Bearer immer 401")


# ---------- A10 Netz ----------
async def a10_netz(z: Zugang, o: Optionen) -> Ergebnis:
    return Ergebnis("A10", "Netz", VON_HAND, "von Hand am Server mit den Kollegen (D2): fremder Container, ausgehender Weg, SMTP, DNS")


# ---------- A11 Vertrag ----------
async def _antwort_pruefen(s: Sammler, text: str, a: Antwort, erwartet: int | tuple[int, ...], code: str | None = None) -> Antwort:
    """Status, Code und gemeinsame Köpfe einer Antwort; Fehlerantworten gehen in die Prüfung des Fehlerformats."""
    s.status(a, erwartet, text, code)
    s.koepfe(a, text)
    if a.status >= 400:
        s.proben.append(a)
    return a


async def _v1_und_v2(z: Zugang, s: Sammler, p: int) -> None:
    a = await _antwort_pruefen(s, "V1", await z.v1(p), 200)
    j = _dict(a)
    s.erwarte(set(j) == {"durchlauf_id", "offen", "einwilligung_version", "einwilligung_zeit"}, "V1: Felder weichen vom Vertrag ab")
    s.erwarte(isinstance(j.get("durchlauf_id"), str) and bool(UUID_RE.fullmatch(j["durchlauf_id"])) and j.get("offen") is True, "V1: durchlauf_id oder offen falsch")
    ev, ez = j.get("einwilligung_version"), j.get("einwilligung_zeit")
    s.erwarte((ev is None or _ganz(ev)) and (ez is None or (isinstance(ez, str) and bool(ZEIT_RE.fullmatch(ez)))), "V1: Einwilligung hat die falsche Form")
    await _antwort_pruefen(s, "V1 mit Abfrage", await z.senden("GET", f"{PFAD}/durchlauf", platz=p, params={"x": "1"}), 422, "eingabe_ungueltig")
    voll = await _antwort_pruefen(s, "V2", await z.tickets(p), 200)
    liste = [t for t in _liste(voll) if isinstance(t, dict)]
    s.erwarte(set(_dict(voll)) == {"tickets", "gesamt"} and 0 < len(liste) <= 10 and all(not _ticket_form(t) for t in liste), "V2: Antwortform oder Ticketfelder weichen ab")
    s.erwarte([_nr(t) for t in liste] == sorted((_nr(t) for t in liste), reverse=True), "V2: nicht nach Nummer absteigend sortiert")
    eins = await _antwort_pruefen(s, "V2 limit=1", await z.tickets(p, limit=1), 200)
    s.erwarte(len(_liste(eins)) == 1 and _dict(eins).get("gesamt") == _dict(voll).get("gesamt"), "V2: limit=1 liefert nicht ein Ticket bei gleichem gesamt")
    for filter_, wert in (("typ", "bug"), ("prioritaet", "hoch"), ("status", "erledigt")):
        t = [x for x in _liste(await _antwort_pruefen(s, f"V2 {filter_}", await z.tickets(p, **{filter_: wert}), 200)) if isinstance(x, dict)]
        s.erwarte(all(x.get(filter_) == wert for x in t), f"V2: Filter {filter_} liefert fremde Treffer")
    s.erwarte("LAB-11" in [x.get("nummer") for x in _liste(await z.tickets(p, status="erledigt")) if isinstance(x, dict)], "V2: LAB-11 (Seed, erledigt) fehlt")
    eine = await _antwort_pruefen(s, "V2 nummer", await z.tickets(p, nummer="LAB-12"), 200)
    s.erwarte([x.get("nummer") for x in _liste(eine)] == ["LAB-12"] and _dict(eine).get("gesamt") == 1, "V2: nummer=LAB-12 liefert nicht genau LAB-12")
    s.erwarte(all(x.get("im_durchlauf") is True for x in _liste(await z.tickets(p, nur_durchlauf="true")) if isinstance(x, dict)), "V2: nur_durchlauf liefert Seed-Tickets")
    ungueltig = ({"limit": 0}, {"limit": 11}, {"limit": "abc"}, {"limit": -1}, {"typ": "BUG"}, {"prioritaet": "dringend"}, {"status": "offen"}, {"nur_durchlauf": "ja"},
                 {"nummer": "LAB-012"}, {"nummer": "LAB-0"}, {"nummer": "LAB-10000"}, {"nummer": "lab-12"}, {"q": "x" * 101}, {"foo": "1"})
    for params in ungueltig:
        await _antwort_pruefen(s, f"V2 mit {params}", await z.tickets(p, **params), 422, "eingabe_ungueltig")
    doppelt = await z.senden("GET", f"{PFAD}/tickets", platz=p, params=[("typ", "bug"), ("typ", "story")])
    await _antwort_pruefen(s, "V2 mit doppeltem Parameter", doppelt, 422, "eingabe_ungueltig")


async def _v3(z: Zugang, s: Sammler, p: int) -> None:
    a = await _antwort_pruefen(s, "V3", await z.ticket(p, "LAB-12"), 200)
    j = _dict(a)
    s.erwarte(set(j) == TICKET_FELDER | {"kommentare"} and j.get("nummer") == "LAB-12" and j.get("status") == "in_arbeit" and j.get("im_durchlauf") is False
              and isinstance(j.get("kommentare"), list), "V3: Felder oder Seed (LAB-12 in Arbeit) weichen ab")
    await _antwort_pruefen(s, "V3 unbekanntes Ticket", await z.ticket(p, "LAB-9999"), 404, "ticket_unbekannt")
    for nummer in ("LAB-0", "lab-1", "LAB-1a", "LAB-01", "LAB-10000"):
        await _antwort_pruefen(s, f"V3 mit Nummer {nummer}", await z.ticket(p, nummer), 422, "eingabe_ungueltig")
    for pfad in ("/gibt-es-nicht", "/tickets/LAB-12/loeschen", "/durchlauf/1"):
        await _antwort_pruefen(s, f"unbekannter Pfad {pfad}", await z.senden("GET", PFAD + pfad, platz=p), 404, "nicht_erlaubt")
    falsche = await _antwort_pruefen(s, "POST auf V1", await z.senden("POST", f"{PFAD}/durchlauf", platz=p), 405, "nicht_erlaubt")
    s.erwarte("GET" in falsche.kopf.get("allow", ""), "405 ohne Kopf Allow (mit GET)")
    await _antwort_pruefen(s, "V1 mit Platz 17", await z.v1(17), 404, "platz_unbekannt")
    await _antwort_pruefen(s, "V1 ohne Platzkopf", await z.v1(None), 404, "platz_unbekannt")


FORMFEHLER = {"typ ungültig": {"typ": "fehler"}, "prioritaet ungültig": {"prioritaet": "dringend"}, "Titel fehlt": {"titel": None}, "beschreibung fehlt": {"beschreibung": None},
              "unbekanntes Feld": {"extra": "x"}, "Titel mit Zeilenumbruch": {"titel": "a\nb"}, "Titel mit Steuerzeichen": {"titel": "ab\x00c"},
              "Wagenrücklauf": {"beschreibung": "a\rb"}, "Bidi-Steuerzeichen": {"titel": "ab\u202ec"}, "Nullbreitenzeichen": {"titel": "ab\u200bc"},
              "Zeilentrenner U+2028": {"beschreibung": "a\u2028b"}, "privates Zeichen": {"titel": "ab\ue000c"}, "nicht zugewiesenes Zeichen": {"titel": "ab\u0378c"},
              "nicht NFC": {"titel": "Cafe\u0301 Test"}, "Titel kein Text": {"titel": 123}}


async def _formfehler(z: Zugang, s: Sammler, p: int, nummer: str) -> None:
    basis = {"typ": "bug", "prioritaet": "hoch", "titel": "Formprobe", "beschreibung": ""}
    for name, abw in FORMFEHLER.items():
        daten = {k: v for k, v in {**basis, **abw}.items() if v is not None}
        await _antwort_pruefen(s, f"V4 {name}", await z.schreiben("POST", f"{PFAD}/tickets", daten, platz=p, modus="sonde"), 422, "eingabe_ungueltig")
    kopf_ok = {"X-WAMO-Durchlauf": z.durchlauf.get(p, ""), "Idempotency-Key": str(uuid.uuid4())}
    for name, kw in {"Inhaltstyp text/plain": {"inhalt": b"{}", "kopf": {**kopf_ok, "Content-Type": "text/plain"}},
                     "kein JSON": {"inhalt": b"{nicht json", "kopf": {**kopf_ok, "Content-Type": "application/json"}}}.items():
        await _antwort_pruefen(s, f"V4 {name}", await z.senden("POST", f"{PFAD}/tickets", platz=p, schreibend=True, **kw), 422, "eingabe_ungueltig")
    for name, kopf in {"ohne X-WAMO-Durchlauf": {"X-WAMO-Durchlauf": None}, "ohne Idempotency-Key": {"Idempotency-Key": None},
                       "Durchlauf keine UUID": {"X-WAMO-Durchlauf": "nummer-eins"}, "Schlüssel keine UUID": {"Idempotency-Key": "123"}}.items():
        await _antwort_pruefen(s, f"V4 {name}", await z.schreiben("POST", f"{PFAD}/tickets", basis, platz=p, modus="sonde", kopf=kopf), 422, "eingabe_ungueltig")
    pfad = f"{PFAD}/tickets/{nummer}"
    for name, (methode, ziel, daten) in {
            "V5 Version 0": ("PATCH", pfad, {"erwartete_version": 0, "felder": {"titel": "Neu x"}}), "V5 Version als Text": ("PATCH", pfad, {"erwartete_version": "1", "felder": {"titel": "Neu x"}}),
            "V5 Version als Wahrheitswert": ("PATCH", pfad, {"erwartete_version": True, "felder": {"titel": "Neu x"}}),
            "V5 leere Felder": ("PATCH", pfad, {"erwartete_version": 1, "felder": {}}), "V5 Feld status": ("PATCH", pfad, {"erwartete_version": 1, "felder": {"status": "erledigt"}}),
            "V6 Status offen": ("POST", pfad + "/status", {"erwartete_version": 1, "status": "offen"}), "V7 leerer Text": ("POST", pfad + "/kommentare", {"erwartete_version": 1, "text": ""}),
            "V7 Text mit Wagenrücklauf": ("POST", pfad + "/kommentare", {"erwartete_version": 1, "text": "a\rb"})}.items():
        await _antwort_pruefen(s, name, await z.schreiben(methode, ziel, daten, platz=p, modus="sonde"), 422, "eingabe_ungueltig")


async def _reihenfolge(z: Zugang, s: Sammler, p: int, nummer: str) -> None:
    """Die Reihenfolge der Prüfungen (Abschnitt 2.2): der erste Treffer gewinnt."""
    fremd, gross = str(uuid.uuid4()), b"x" * 20000
    await _antwort_pruefen(s, "Pfad vor Bearer", await z.senden("GET", f"{PFAD}/gibt-es-nicht", bearer=None), 404, "nicht_erlaubt")
    await _antwort_pruefen(s, "Methode vor Bearer", await z.senden("POST", f"{PFAD}/durchlauf", bearer=None), 405, "nicht_erlaubt")
    await _antwort_pruefen(s, "Bearer vor Platz", await z.v1(99, bearer=None), 401, "token_ungueltig")
    await _antwort_pruefen(s, "Platz vor Größe", await z.schreiben("POST", f"{PFAD}/tickets", None, platz=99, inhalt=gross, kopf={"Content-Type": "application/json"}, modus="sonde", abbruch_ok=True),
             404, "platz_unbekannt")
    await _antwort_pruefen(s, "Größe vor Form", await z.schreiben("POST", f"{PFAD}/tickets", None, platz=p, inhalt=gross, kopf={"Content-Type": "application/json"}, modus="sonde", abbruch_ok=True),
             413, "zu_gross")
    await _antwort_pruefen(s, "Form vor Durchlauf", await z.anlegen(p, "x", "", durchlauf=fremd, modus="sonde"), 422, "eingabe_ungueltig")
    await _antwort_pruefen(s, "Form vor Ticket", await z.aendern(p, "LAB-9999", 1, {"geloescht": True}, modus="sonde"), 422, "eingabe_ungueltig")
    await _antwort_pruefen(s, "Durchlauf vor Ticket", await z.status_setzen(p, "LAB-9999", 1, "erledigt", durchlauf=fremd), 409, "durchlauf_beendet")
    await _antwort_pruefen(s, "Ticket vor Version", await z.status_setzen(p, "LAB-9999", 7, "erledigt"), 404, "ticket_unbekannt")
    await _antwort_pruefen(s, "Durchlauf vor Version", await z.status_setzen(p, nummer, 999, "erledigt", durchlauf=fremd), 409, "durchlauf_beendet")


async def _schreibwege(z: Zugang, s: Sammler, p: int) -> str:
    """V4 bis V7 im Erfolgsfall (Felder, Versionen, Status), dazu die letzten 20 Kommentare in V3. Liefert die Nummer des Tickets."""
    erst = await _antwort_pruefen(s, "V4", await z.anlegen(p, "Gross Pruefung XyZ", "Beschreibung\nmit Zeilenumbruch", typ="story", prioritaet="hoechste"), 201)
    t = _dict(erst)
    s.erwarte(not _ticket_form(t) and t.get("version") == 1 and t.get("status") == "zu_erledigen" and t.get("im_durchlauf") is True and t.get("typ") == "story"
              and t.get("prioritaet") == "hoechste" and t.get("beschreibung") == "Beschreibung\nmit Zeilenumbruch", "V4: Ticket weicht vom Vertrag ab")
    nummer = t.get("nummer", "LAB-12")
    zweit = _dict(await z.anlegen(p, "Zweites Ticket", ""))
    s.erwarte(_nr(zweit) > _nr(t) >= 30, "V4: die Nummern kommen nicht aus einem wachsenden Zähler ab 30")
    for q in ("gross pruefung xyz", "GROSS PRUEFUNG XYZ"):
        s.erwarte(nummer in [x.get("nummer") for x in _liste(await z.tickets(p, q=q)) if isinstance(x, dict)], f"V2: die Suche beachtet Groß- und Kleinschreibung ({q})")
    a = _dict(await _antwort_pruefen(s, "V5", await z.aendern(p, nummer, 1, {"titel": "Neuer Titel", "prioritaet": "hoch"}), 200))
    s.erwarte(not _ticket_form(a) and a.get("version") == 2 and a.get("titel") == "Neuer Titel" and a.get("prioritaet") == "hoch" and a.get("typ") == "story"
              and a.get("beschreibung") == "Beschreibung\nmit Zeilenumbruch", "V5: Änderung weicht vom Vertrag ab (version, Felder, übrige Felder)")
    gleich = _dict(await _antwort_pruefen(s, "V5 mit gleichen Werten", await z.aendern(p, nummer, 2, {"titel": "Neuer Titel"}), 200))
    s.erwarte(gleich.get("version") == 3, "V5: gleiche Werte erhöhen die Version nicht um eins")
    st = _dict(await _antwort_pruefen(s, "V6", await z.status_setzen(p, nummer, 3, "erledigt"), 200))
    s.erwarte(st.get("status") == "erledigt" and st.get("version") == 4, "V6: Status oder Version falsch")
    st = _dict(await _antwort_pruefen(s, "V6 auf denselben Status", await z.status_setzen(p, nummer, 4, "erledigt"), 200))
    s.erwarte(st.get("version") == 5, "V6: gleicher Status erhöht die Version nicht um eins")
    version, ids = 5, []
    for i in range(1, 22):
        a = await z.kommentieren(p, nummer, version, f"Kommentar {i}\nzweite Zeile" if i == 1 else f"Kommentar {i}")
        if i == 1:
            await _antwort_pruefen(s, "V7", a, 201)
            s.erwarte(set(_dict(a)) == {"kommentar_id", "ticket"} and not _ticket_form(_dict(a).get("ticket")), "V7: Antwortform weicht ab")
        elif a.status != 201:
            s.status(a, 201, f"V7 Kommentar {i}")
            break
        ids.append(_dict(a).get("kommentar_id"))
        version = _ticket_aus(a).get("version", version + 1)
    kommentare = _dict(await z.ticket(p, nummer)).get("kommentare", [])
    s.erwarte(len(kommentare) == 20 and [c.get("kommentar_id") for c in kommentare] == ids[1:] and kommentare[-1].get("text") == "Kommentar 21",
              "V3: nicht die letzten 20 Kommentare, ältester zuerst")
    return nummer


async def _liste_grenze(z: Zugang, s: Sammler, p: int) -> None:
    """V2 darf nie über 60 KB wachsen und schreibt UTF-8 ohne Maskierung; gesamt bleibt die echte Zahl. Zehn Tickets von etwa 6,3 KB wachsen auf
    etwa 62,6 KB: Ein Dienst mit der Grenze 64 KB liefert sie alle zehn und wird rot, einer mit 60 KB liefert höchstens neun."""
    letzte = ""
    for _ in range(10):
        letzte = _dict(await z.anlegen(p, MITTEL_TITEL, GROSS_TEXT)).get("nummer", letzte)
    a = await _antwort_pruefen(s, "V2 mit großen Tickets", await z.tickets(p), 200)
    liste = _liste(a)
    s.erwarte(len(a.inhalt) <= 60 * 1024 and len(liste) < 10, f"V2: Antwort mit {len(a.inhalt)} Bytes für {len(liste)} Tickets (über 60 KB müssen es weniger Tickets sein)")
    s.erwarte(b"\\u" not in a.inhalt, "V2: Nicht-ASCII-Zeichen sind als \\uXXXX maskiert (UTF-8 ohne Maskierung verlangt)")
    s.erwarte((_dict(a).get("gesamt") or 0) >= 10 and bool(liste) and isinstance(liste[0], dict) and liste[0].get("nummer") == letzte, "V2: gesamt oder Anfang der Liste falsch")


async def a11_vertrag(z: Zugang, o: Optionen) -> Ergebnis:
    s, p, q = Sammler(), PLATZ_A, PLATZ_B
    await _v1_und_v2(z, s, p)
    await _v3(z, s, p)
    nummer = await _schreibwege(z, s, q)
    await _formfehler(z, s, q, nummer)
    await _reihenfolge(z, s, q, nummer)
    await _liste_grenze(z, s, q)
    for a in s.proben:
        j = a.json if isinstance(a.json, dict) else {}
        text = j.get("text")
        s.erwarte(set(j) == {"fehler", "text"} and isinstance(text, str) and 0 < len(text) <= 200, f"Fehlerformat weicht ab ({a.beschreibung()})")
        s.erwarte(a.code in FEHLER_CODES and a.status in FEHLER_CODES.get(a.code or "", ()), f"Code und Status passen nicht zusammen ({a.beschreibung()})")
        s.erwarte(not any(i in str(text) for i in INTERNA), f"Fehlertext nennt Interna ({a.beschreibung()})")
    return s.ergebnis("A11", "Vertrag", "V1 bis V7, Fehlercodes und Reihenfolge nach Abschnitt 2.2")


# ---------- A4 Quoten (zuletzt: füllt die Plätze) ----------
async def _rate(z: Zugang, s: Sammler) -> None:
    p = PLATZ_A
    await z.schreibtakt(p).leeren()
    version = _dict(await z.ticket(p, "LAB-12")).get("version") or 1
    erfolge, gesehen = 0, False
    for _ in range(RATE_MAX + 6):
        a = await z.status_setzen(p, "LAB-12", version, "in_arbeit", modus="frei")
        if a.status == 200:
            erfolge, version = erfolge + 1, _dict(a).get("version", version + 1)
            continue
        gesehen = a.ist(429, "quote_erreicht")
        s.status(a, 429, "Schreibrate", "quote_erreicht")
        break
    s.erwarte(gesehen and erfolge <= RATE_MAX, f"Schreibrate: {erfolge} Schreibaufrufe in einer Minute ohne 429 (Vertrag: höchstens {RATE_MAX})")
    if gesehen and erfolge <= RATE_MAX:
        s.grenze("Schreibrate", erfolge, RATE_MAX, " Aufrufen je Minute")


async def _tickets_und_text(z: Zugang, s: Sammler) -> None:
    p = PLATZ_A
    start = _dict(await z.tickets(p, limit=1)).get("gesamt")
    if not _ganz(start):
        s.erwarte(False, "V2 liefert kein gesamt")
        return
    nummern: list[str] = []
    while True:
        a = await z.anlegen(p, f"Menge {len(nummern) + 1:03d}", "", modus="gebremst")
        if a.status == 201 and _dict(a).get("nummer"):
            nummern.append(_dict(a)["nummer"])
            if start + len(nummern) > TICKETS_MAX:
                s.erwarte(False, f"Tickets: Nummer {start + len(nummern)} wurde angenommen (Obergrenze {TICKETS_MAX}, Abbruch)")
                break
            continue
        if s.status(a, 429, "Tickets an der Obergrenze", "quote_erreicht"):
            s.grenze("Tickets", start + len(nummern), TICKETS_MAX, " Tickets")
        break
    genutzt = 9 * len(nummern)
    for nr in nummern + ["LAB-11", "LAB-12", "LAB-20"]:  # jedes Ticket auf die größte Länge bringen (viele Bytes je Aufruf)
        version = 1 if nr in nummern else (_dict(await z.ticket(p, nr)).get("version") or 1)
        a = await z.aendern(p, nr, version, {"titel": GROSS_TITEL, "beschreibung": GROSS_TEXT}, modus="gebremst")
        if a.status != 200:
            if s.status(a, 429, "Text an der Obergrenze", "quote_erreicht"):
                s.grenze("Text (Änderungen)", genutzt // 1024, BYTES_MAX // 1024, " KB")  # mit Änderungen allein sind es höchstens etwa 630 KB
            break
        genutzt += 6480 - (9 if nr in nummern else 0)
    await _kommentare_bis_429(z, s, genutzt)


async def _kommentare_bis_429(z: Zugang, s: Sammler, genutzt: int) -> None:
    p = PLATZ_A
    version = _dict(await z.ticket(p, "LAB-12")).get("version") or 1
    while True:
        a = await z.kommentieren(p, "LAB-12", version, GROSS_KOMMENTAR, modus="gebremst")
        if a.status == 201:
            genutzt += 2000
            version = _ticket_aus(a).get("version", version + 1)
            if genutzt > BYTES_MAX:
                s.erwarte(False, f"Text: über 1 MB ({genutzt} Bytes) wurden angenommen (Abbruch)")
                return
            if z.kommentare[p] > KOMMENTARE_MAX:
                s.erwarte(False, f"Kommentare: Nummer {z.kommentare[p]} wurde angenommen (Obergrenze {KOMMENTARE_MAX}, Abbruch)")
                return
            continue
        if s.status(a, 429, "Text an der Obergrenze", "quote_erreicht"):  # etwa 1 MB (der Dienst zählt auch Text früherer Prüfungen), oder genau 300 Kommentare
            s.grenze(f"Text und Kommentare auf Platz 15 ({z.kommentare[p]} Kommentare)", genutzt // 1024, BYTES_MAX // 1024, " KB",
                     ab=BYTES_MAX * 0.95 // 1024 if z.kommentare[p] < KOMMENTARE_MAX else genutzt // 1024)
        return


async def _kommentare(z: Zugang, s: Sammler) -> None:
    p = PLATZ_B
    version = _dict(await z.ticket(p, "LAB-12")).get("version") or 1
    while True:
        a = await z.kommentieren(p, "LAB-12", version, "k", modus="gebremst")
        if a.status == 201:
            version = _ticket_aus(a).get("version", version + 1)
            if z.kommentare[p] > KOMMENTARE_MAX:
                s.erwarte(False, f"Kommentare: Nummer {z.kommentare[p]} wurde angenommen (Obergrenze {KOMMENTARE_MAX}, Abbruch)")
                return
            continue
        if s.status(a, 429, "Kommentare an der Obergrenze", "quote_erreicht"):
            s.grenze("Kommentare auf Platz 16", z.kommentare[p], KOMMENTARE_MAX, " Kommentaren")
        return


async def a4_quoten(z: Zugang, o: Optionen) -> Ergebnis:
    s = Sammler()
    await _rate(z, s)
    await asyncio.gather(_tickets_und_text(z, s), _kommentare(z, s))
    for platz in (PLATZ_A, PLATZ_B):
        s.status(await z.v1(platz), 200, f"V1 nach den Mengenprüfungen (Platz {platz}): der Dienst läuft weiter")
    s.von_hand.append("Datenbankgröße (max_page_count, D1): nur im Review prüfbar, das Skript erreicht sie über die Schnittstelle nicht")
    return s.ergebnis("A4", "Quoten", "")


PRUEFUNGEN = {"A1": ("XSS", a1_xss), "A2": ("SQL-Injektion", a2_sql), "A3": ("Prozent und Unterstrich", a3_prozent), "A4": ("Quoten", a4_quoten),
              "A5": ("Größenlimits", a5_groessen), "A6": ("Platztrennung", a6_platztrennung), "A7": ("Kein Löschweg", a7_loeschweg),
              "A8": ("Durchlauf und Version", a8_durchlauf_version), "A9": ("Zugang", a9_zugang), "A10": ("Netz", a10_netz), "A11": ("Vertrag", a11_vertrag)}
AUSFUEHRUNG = ("A1", "A2", "A3", "A5", "A6", "A7", "A8", "A9", "A11", "A4", "A10")  # A4 füllt die Plätze und kommt deshalb zuletzt
PLAN = {"A1": ("Nutzlasten mit script, onerror und javascript: in Titel, Beschreibung und Kommentar; Köpfe der Antworten; Board-Seite mit --board-url", 6),
        "A2": ("Einspritzungen in q, nummer, Titel, Platzkopf, Bearer und weitere Köpfe", 4),
        "A3": ("Suche nach Prozent, Unterstrich und Gegenschrägstrich findet nur wörtliche Treffer", 6),
        "A4": ("Schreibrate, 100 Tickets, 300 Kommentare und 1 MB Text bis zum 429 (bricht an der Schwelle ab), dauert wegen der Rate lange", 740),
        "A5": ("Anfrage über 16 KB, jede Feldlänge plus eins, große Köpfe, Längen in Zeichen statt Bytes", 5),
        "A6": ("Platz 15 legt an, Platz 16 liest, ändert, setzt Status und kommentiert (404), gefälschter Platzkopf ohne Bearer (401)", 6),
        "A7": ("DELETE und andere Methoden auf allen Pfaden, Method-Override, Felder wie geloescht, Zeilenzahl vorher und nachher", 0),
        "A8": ("Fremder Durchlauf und falsche Version (409), Idempotenz, acht gleichzeitige Änderungen", 40),
        "A9": ("V1 bis V7 ohne, mit falschem und mit verändertem Bearer (401), Betreuer-Pfade mit --betreuer-pfad", 0),
        "A10": ("Netz: von Hand am Server mit den Kollegen", 0),
        "A11": ("Jeder Aufruf, jedes Feld, jeder Fehlercode von V1 bis V7 und die Reihenfolge der Prüfungen", 55)}
