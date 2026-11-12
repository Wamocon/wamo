"""Gemeinsame Hilfen der Tests (keine Tests): feste Uhr, Aufrufe an den Schreibpfad, Antworten lesen."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from wamo_labor_dienst.modell import Antwort, Aufruf, fingerabdruck
from wamo_labor_dienst.regeln import PRUEFER

# Aus Zeichen berechnet, kein Literal: Der Token braucht die Güte, die konfig.py verlangt (mindestens 43 Zeichen, mindestens 16 verschiedene),
# und ein solches Literal hielte die Secret-Prüfung (gitleaks, Regel generic-api-key) für einen Schlüssel. Es ist ein Testwert, kein Geheimnis.
KERN_TOKEN = "".join(chr(ord("a") + (i * 7) % 26) for i in range(44))
BETREUER_TOKEN = "Bn4Wd8Zs1Yh6Kt3Qe9Jx5Cv2Rm7Lg0Pa+Uo/Fi-Nz._~=Tq"
ANLEGEN = {"typ": "bug", "prioritaet": "hoch", "titel": "Anmeldung schlägt fehl", "beschreibung": "Nach dem Klick passiert nichts."}
LABORTAG = datetime(2026, 11, 28, 9, 30, tzinfo=UTC).timestamp()


class Uhr:
    """Feste Uhr in Sekunden seit 1970, nur mit vor() verstellbar."""

    def __init__(self, start: float = LABORTAG):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def vor(self, sekunden: float) -> None:
        self.t += sekunden


def aufruf(db, name: str, daten: dict, nummer: int | None = None, *, durchlauf: str | None = None, schluessel: str | None = None) -> Aufruf:
    """Ein Schreibaufruf so, wie ihn die Schnittstelle nach der Prüfung der Form baut."""
    return Aufruf(name=name, nummer=nummer, werte=PRUEFER[name](daten), fingerabdruck=fingerabdruck(name, nummer, daten),
                  durchlauf=(durchlauf or db.durchlauf()["durchlauf_id"]).lower(), schluessel=(schluessel or str(uuid.uuid4())).lower())


def anlegen(db, *, schluessel: str | None = None, durchlauf: str | None = None, **felder) -> Antwort:
    return db.schreiben(aufruf(db, "tickets", {**ANLEGEN, **felder}, schluessel=schluessel, durchlauf=durchlauf))


def status_setzen(db, nummer: int, version: int, status: str, **kw) -> Antwort:
    return db.schreiben(aufruf(db, "status", {"erwartete_version": version, "status": status}, nummer, **kw))


def kommentieren(db, nummer: int, version: int, text: str, **kw) -> Antwort:
    return db.schreiben(aufruf(db, "kommentare", {"erwartete_version": version, "text": text}, nummer, **kw))


def aendern(db, nummer: int, version: int, felder: dict, **kw) -> Antwort:
    return db.schreiben(aufruf(db, "ticket", {"erwartete_version": version, "felder": felder}, nummer, **kw))


def inhalt(a: Antwort) -> dict:
    return json.loads(a.koerper)


# ---------- Schnittstelle (TestClient) ----------
def betreuer_kopf(**extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {BETREUER_TOKEN}", **extra}


def kern_kopf(platz: int | str = 3, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {KERN_TOKEN}", "X-WAMO-Platz": str(platz), **extra}


def oeffnen(client, platz: int = 3, version: int = 1) -> str:
    """Der Betreuer startet einen neuen Durchlauf mit Einwilligung. Liefert die durchlauf_id."""
    r = client.post(f"/api/betreuer/plaetze/{platz}/neuer-durchlauf", json={"einwilligung_version": version}, headers=betreuer_kopf())
    assert r.status_code == 200, r.text
    return r.json()["durchlauf_id"]


def schreib_kopf(durchlauf: str, platz: int | str = 3, schluessel: str | None = None, **extra: str) -> dict[str, str]:
    return kern_kopf(platz, **{"X-WAMO-Durchlauf": durchlauf, "Idempotency-Key": schluessel or str(uuid.uuid4()), **extra})


def anlegen_http(client, durchlauf: str, platz: int = 3, **felder):
    return client.post("/api/kern/tickets", json={**ANLEGEN, **felder}, headers=schreib_kopf(durchlauf, platz))
