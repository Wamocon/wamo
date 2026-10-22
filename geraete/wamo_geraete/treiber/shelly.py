"""Schaltbare Steckdosen Shelly Plug S Gen3 (RPC-API Gen2+, http://<ip>/rpc/...).

Status: Switch.GetStatus (an/aus, Leistung, Spannung, Temperatur, Energie). Steuern (change): Switch.Set an oder aus.
Ist an der Steckdose ein Passwort gesetzt (auth_en), kommt es aus geraete.env (WAMO_GERAETE_<ZUGANG>_PASSWORT,
Benutzer immer admin, Digest SHA-256). Am 20.10.2026 hatten alle drei Steckdosen keinen Schutz (auth_en false):
Im Bericht empfohlen, ein Passwort zu setzen.

Optionen je Steckdose (geraete.toml): schutz = true (der Kern schaltet nur nach einer Freigabe-Karte; der Dienst lehnt
ohne freigegeben=true ab), max_laufzeit_s (Selbstabschaltung nach dem Einschalten, z. B. für Heizgeräte)."""

from __future__ import annotations

import json

from ..http_klein import HttpFehler, anfrage
from ..konfig import Geraet, Konfig
from ..text import zahl
from . import Abgelehnt, GeraetFehler, NichtEingerichtet


def _digest(geraet: Geraet, konfig: Konfig) -> tuple[str, str] | None:
    passwort = konfig.geheimnis(geraet.opt("zugang"), "PASSWORT")
    return ("admin", passwort) if passwort else None


async def _rpc(geraet: Geraet, konfig: Konfig, pfad: str) -> dict:
    if not geraet.adresse:
        raise NichtEingerichtet("Adresse der Steckdose fehlt")
    try:
        antwort = await anfrage(geraet.adresse, geraet.port or 80, "GET", pfad, timeout=3, max_bytes=32 * 1024,
                                digest=_digest(geraet, konfig))
    except HttpFehler as e:
        raise GeraetFehler(f"Steckdose nicht erreichbar ({e})") from e
    if antwort.status == 401:
        raise NichtEingerichtet("Steckdose verlangt ein Passwort (geraete.env)")
    if antwort.status != 200:
        raise GeraetFehler(f"Steckdose antwortet mit HTTP {antwort.status}")
    try:
        daten = json.loads(antwort.rumpf.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise GeraetFehler("Antwort der Steckdose ist kein JSON") from e
    if not isinstance(daten, dict):
        raise GeraetFehler("Antwort der Steckdose unerwartet")
    return daten


async def status(geraet: Geraet, konfig: Konfig) -> dict:
    d = await _rpc(geraet, konfig, "/rpc/Switch.GetStatus?id=0")
    energie = d.get("aenergy") if isinstance(d.get("aenergy"), dict) else {}
    temp = d.get("temperature") if isinstance(d.get("temperature"), dict) else {}
    leistung = zahl(d.get("apower"), 0, 10000)
    return {"erreichbar": True, "an": d.get("output") is True,
            "leistung_w": round(leistung, 1) if leistung is not None else None,
            "spannung_v": round(zahl(d.get("voltage"), 0, 400) or 0, 1),
            "temperatur_c": round(zahl(temp.get("tC"), -40, 150) or 0, 1),
            "energie_kwh": round((zahl(energie.get("total"), 0, 1e10) or 0) / 1000, 2)}


async def steuern(geraet: Geraet, konfig: Konfig, aktion: str) -> dict:
    """an oder aus. Ist max_laufzeit_s gesetzt, schaltet die Steckdose beim Einschalten von selbst wieder aus
    (Shelly: toggle_after), auch wenn WAMO oder das Netz danach ausfällt."""
    if aktion not in ("an", "aus"):
        raise Abgelehnt("Steckdosen kennen nur an und aus")
    pfad = f"/rpc/Switch.Set?id=0&on={'true' if aktion == 'an' else 'false'}"
    laufzeit = geraet.opt("max_laufzeit_s")
    if aktion == "an" and laufzeit:
        pfad += f"&toggle_after={int(laufzeit)}"
    d = await _rpc(geraet, konfig, pfad)
    erg = {"an": aktion == "an", "vorher_an": d.get("was_on") is True}
    if aktion == "an" and laufzeit:
        erg["schaltet_aus_nach_s"] = int(laufzeit)
    return erg
