"""Ablauf im Gerätedienst: Rollen, Geräteklassen, Grenzen, Ablage je Person, Antwortformen (Treiber als Attrappen)."""

import uuid

import pytest

from wamo_geraete import dienst as d
from wamo_geraete.ablage import Ablage
from wamo_geraete.audit import Audit
from wamo_geraete.grenzen import Grenzen
from wamo_geraete.konfig import aus_daten
from wamo_geraete.treiber import GeraetFehler, NichtEingerichtet

ANNA, BERND = str(uuid.uuid4()), str(uuid.uuid4())
GERAETE = [
    {"kennung": "drucker", "klasse": "drucker", "treiber": "ipp", "adresse": "192.168.178.24"},
    {"kennung": "scanner", "klasse": "scanner", "treiber": "escl", "adresse": "192.168.178.24"},
    {"kennung": "router", "klasse": "router", "treiber": "upnp-igd", "adresse": "192.168.178.1"},
    {"kennung": "steckdose-1", "klasse": "steckdose", "treiber": "shelly", "adresse": "192.168.178.126"},
    {"kennung": "lautsprecher", "klasse": "lautsprecher", "treiber": "upnp-renderer", "adresse": "192.168.178.90",
     "port": 49152},
    {"kennung": "nas-1", "klasse": "nas", "treiber": "smb", "adresse": "192.168.178.25", "zugang": "NAS1"},
    {"kennung": "imac", "klasse": "mac", "treiber": "anwesenheit"},
]


@pytest.fixture
def dienst(tmp_path):
    konfig = aus_daten({"geraet": GERAETE})
    return d.Dienst(konfig, ablage=Ablage(str(tmp_path / "ablage")), audit=Audit(str(tmp_path / "audit.jsonl")),
                    grenzen=Grenzen())


def anfrage(werkzeug, geraet=None, rolle="mitglied", user=ANNA, **argumente):
    a = {"v": 1, "werkzeug": werkzeug, "argumente": argumente,
         "kontext": {"user_id": user, "rolle": rolle, "session_id": str(uuid.uuid4()), "name": "Anna"}}
    if geraet is not None:
        a["geraet"] = geraet
    return a


async def test_form_und_kontext(dienst):
    for falsch in ({}, {"v": 2}, {"v": 1, "werkzeug": "shell"}, {"v": 1, "werkzeug": "ping", "kontext": {}},
                   {"v": 1, "werkzeug": "ping", "kontext": {"user_id": ANNA, "rolle": "gast"}}):
        antwort, anhang = await dienst.bearbeiten(falsch)
        assert antwort["status"] == "rejected" and anhang == b""
    antwort, _ = await dienst.bearbeiten(anfrage("ping"))
    assert antwort["status"] == "success" and antwort["dienst"] == "wamo-geraete"


async def test_unbekanntes_geraet_und_falsche_klasse(dienst):
    antwort, _ = await dienst.bearbeiten(anfrage("status", "laptop-anna"))
    assert antwort["status"] == "rejected"
    antwort, _ = await dienst.bearbeiten(anfrage("drucken", "steckdose-1", dokument={}))
    assert antwort["status"] == "rejected"
    antwort, _ = await dienst.bearbeiten(anfrage("steuern", "drucker", aktion="aus"))
    assert antwort["status"] == "rejected"


async def test_router_geraete_nur_admin(dienst, monkeypatch):
    async def liste(_g, _k):
        return [{"name": "telefon-anna", "adresse": "192.168.178.50", "aktiv": True, "verbindung": "WLAN"}]
    monkeypatch.setattr(d.upnp, "router_geraete", liste)
    antwort, _ = await dienst.bearbeiten(anfrage("router_geraete", "router"))
    assert antwort["status"] == "rejected"
    antwort, _ = await dienst.bearbeiten(anfrage("router_geraete", "router", rolle="admin"))
    assert antwort["status"] == "success" and antwort["anzahl"] == 1
    geraete, _ = await dienst.bearbeiten(anfrage("geraete"))
    router = next(x for x in geraete["geraete"] if x["kennung"] == "router")
    assert "router_geraete" not in router["faehigkeiten"]


async def test_steuern_nur_erlaubte_aktionen(dienst, monkeypatch):
    aufrufe = []

    async def schalten(g, k, aktion):
        aufrufe.append((g.kennung, aktion))
        return {"an": aktion == "an"}
    monkeypatch.setattr(d.shelly, "steuern", schalten)
    antwort, _ = await dienst.bearbeiten(anfrage("steuern", "steckdose-1", aktion="an"))
    assert antwort["status"] == "success" and aufrufe == [("steckdose-1", "an")]
    for aktion in ("lautstaerke", "firmware", "reset", None):
        antwort, _ = await dienst.bearbeiten(anfrage("steuern", "steckdose-1", aktion=aktion))
        assert antwort["status"] == "rejected"
    assert len(aufrufe) == 1


async def test_fehlerformen(dienst, monkeypatch):
    async def nicht(_g):
        raise NichtEingerichtet("Adresse fehlt")

    async def kaputt(_g):
        raise GeraetFehler("Gerät nicht erreichbar")

    async def intern(_g):
        raise RuntimeError("Pfad /var/lib/geheim")
    monkeypatch.setattr(d.escl, "status", nicht)
    antwort, _ = await dienst.bearbeiten(anfrage("status", "scanner"))
    assert antwort == {"status": "nicht_eingerichtet", "summary": "Adresse fehlt"}
    monkeypatch.setattr(d.ipp, "status", kaputt)
    antwort, _ = await dienst.bearbeiten(anfrage("status", "drucker"))
    assert antwort["status"] == "error"
    monkeypatch.setattr(d.lautsprecher, "status", intern)
    antwort, _ = await dienst.bearbeiten(anfrage("status", "lautsprecher"))
    assert antwort["status"] == "error" and "geheim" not in antwort["summary"]


async def test_status_alle_und_zwischenspeicher(dienst, monkeypatch):
    zaehler = {"n": 0}

    async def drucker(_g):
        zaehler["n"] += 1
        return {"erreichbar": True, "zustand": "bereit"}
    monkeypatch.setattr(d.ipp, "status", drucker)
    for name_ in ("escl", "upnp", "shelly", "lautsprecher", "nas", "anwesenheit"):
        modul = getattr(d, name_)
        attr = "router_status" if name_ == "upnp" else "status"

        async def nicht(*_a, **_k):
            raise NichtEingerichtet("fehlt")
        monkeypatch.setattr(modul, attr, nicht)
    antwort, _ = await dienst.bearbeiten(anfrage("status", "alle"))
    assert antwort["status"] == "success" and len(antwort["geraete"]) == len(GERAETE)
    await dienst.bearbeiten(anfrage("status", "drucker"))
    assert zaehler["n"] == 1  # zweiter Abruf aus dem Zwischenspeicher


async def test_grenze_status(dienst, monkeypatch):
    async def ok(_g):
        return {"erreichbar": True}
    monkeypatch.setattr(d.ipp, "status", ok)
    for _ in range(30):
        antwort, _ = await dienst.bearbeiten(anfrage("status", "drucker"))
        assert antwort["status"] == "success"
    antwort, _ = await dienst.bearbeiten(anfrage("status", "drucker"))
    assert antwort["status"] == "grenze"
    antwort, _ = await dienst.bearbeiten(anfrage("status", "drucker", user=BERND))
    assert antwort["status"] == "success"


async def test_ablage_nur_eigene(dienst):
    eintrag = dienst.ablage.ablegen(ANNA, "scan", "Scan.pdf", "application/pdf", b"%PDF-1.4 test", "scanner")
    liste, _ = await dienst.bearbeiten(anfrage("ablage_liste"))
    assert [e["id"] for e in liste["eintraege"]] == [eintrag.id]
    antwort, anhang = await dienst.bearbeiten(anfrage("ablage_holen", id=eintrag.id))
    assert antwort["status"] == "success" and anhang == b"%PDF-1.4 test" and antwort["anhang"] == len(anhang)
    fremd, anhang = await dienst.bearbeiten(anfrage("ablage_holen", user=BERND, id=eintrag.id))
    assert fremd["status"] == "rejected" and anhang == b""
    liste, _ = await dienst.bearbeiten(anfrage("ablage_liste", user=BERND))
    assert liste["eintraege"] == []
    for falsch in ("../x", "", "a" * 32 + "/../b"):
        antwort, _ = await dienst.bearbeiten(anfrage("ablage_holen", id=falsch))
        assert antwort["status"] == "rejected"
    geloescht, _ = await dienst.bearbeiten(anfrage("ablage_loeschen", user=BERND, id=eintrag.id))
    assert geloescht["geloescht"] is False
    geloescht, _ = await dienst.bearbeiten(anfrage("ablage_loeschen", id=eintrag.id))
    assert geloescht["geloescht"] is True


async def test_drucken_grenzen_und_ablauf(dienst, monkeypatch):
    gesendet = []

    async def drucken(g, daten, mime, *, titel, kopien, farbe):
        gesendet.append((mime, kopien, farbe, daten[:4]))
        return {"auftrag": 17, "zustand": "wartet"}
    monkeypatch.setattr(d.ipp, "drucken", drucken)
    dok = {"art": "dokument", "titel": "Testseite", "text": "Hallo"}
    antwort, _ = await dienst.bearbeiten(anfrage("drucken", "drucker", dokument=dok, kopien=6))
    assert antwort["status"] == "rejected"
    antwort, _ = await dienst.bearbeiten(anfrage("drucken", "drucker", dokument=dok, kopien=2))
    assert antwort["status"] == "success" and antwort["auftraege"] == [17] and antwort["seiten"] == 1
    assert gesendet == [("image/pwg-raster", 2, False, b"RaS2")]
    dienst.grenzen.seiten_buchen(ANNA, 48)
    antwort, _ = await dienst.bearbeiten(anfrage("drucken", "drucker", dokument=dok, kopien=1))
    assert antwort["status"] == "grenze"


async def test_scannen_einer_zugleich(dienst, monkeypatch):
    import asyncio

    freigabe = asyncio.Event()

    async def scannen(_g, quelle, farbe, aufloesung):
        await freigabe.wait()
        return b"%PDF-1.4 scan", 1
    monkeypatch.setattr(d.escl, "scannen", scannen)
    erster = asyncio.create_task(dienst.bearbeiten(anfrage("scannen", "scanner", quelle="glas")))
    await asyncio.sleep(0.05)
    zweiter, _ = await dienst.bearbeiten(anfrage("scannen", "scanner", quelle="glas", user=BERND))
    assert zweiter["status"] == "rejected"
    freigabe.set()
    antwort, _ = await erster
    assert antwort["status"] == "success" and antwort["eintrag"]["art"] == "scan"
    assert len(dienst.ablage.liste(ANNA)) == 1 and dienst.ablage.liste(BERND) == []
    antwort, _ = await dienst.bearbeiten(anfrage("scannen", "scanner", quelle="glas", aufloesung=1200))
    assert antwort["status"] == "rejected"


async def test_audit_ohne_inhalte(dienst, tmp_path, monkeypatch):
    async def drucken(*_a, **_k):
        return {"auftrag": 1, "zustand": "wartet"}
    monkeypatch.setattr(d.ipp, "drucken", drucken)
    await dienst.bearbeiten(anfrage("drucken", "drucker", dokument={"art": "dokument", "titel": "Geheimtitel",
                                                                   "text": "Vertraulicher Inhalt"}))
    inhalt = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "drucken" in inhalt and "Vertraulich" not in inhalt and "Geheimtitel" not in inhalt
