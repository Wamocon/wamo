"""Werkzeugtabelle und Ablauf eines Aufrufs im Gerätedienst (ohne Socket, damit testbar).

Ablauf je Anfrage: Form prüfen, Kontext prüfen (user_id, rolle und session_id sind Behauptungen des Kerns: Der Dienst
vertraut nur dem Prozess mit der uid von wamo-core, siehe server.py; die Rollen hier schützen gegen Fehler im Kern,
nicht gegen einen kompromittierten Kern), Werkzeug aus der festen Tabelle, Rolle, Geräteklasse, Grenze, Ausführung
mit Zeitgrenze, Audit ohne Inhalte. Jede Antwort hat status success, error, rejected, nicht_eingerichtet oder grenze.

Werkzeuge (Risiko, Rollen):
  ping, geraete, status            read     alle
  steuern                          change   alle (Fernseher, Lautsprecher, Steckdosen)
  drucken                          critical alle (der Kern holt vorher die Freigabe per Karte und prüft den Besitz)
  scannen                          change   alle
  ablage_liste, ablage_holen       read     alle, nur eigene Einträge
  ablage_loeschen                  change   alle, nur eigene Einträge
  nas_suchen, nas_abrufen          read     alle
  router_geraete                   read     nur admin (personenbezogen, Ergebnis nur an die Oberfläche)
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from . import raster
from .ablage import Ablage, AblageFehler
from .audit import Audit
from .grenzen import GrenzeErreicht, Grenzen, Zwischenspeicher
from .konfig import Geraet, Konfig
from .text import name
from .treiber import Abgelehnt, GeraetFehler, NichtEingerichtet
from .treiber import anwesenheit, escl, fernseher, ipp, lautsprecher, lg_tv, nas, server_status, shelly, upnp

logger = logging.getLogger("wamo-geraete")

ROLLEN = frozenset({"admin", "mitglied"})
STEUER_AKTIONEN = {
    "fernseher": ("an", "aus", "lautstaerke", "lauter", "leiser", "stumm", "laut", "eingang"),
    "lautsprecher": ("lautstaerke", "lauter", "leiser", "stumm", "laut"),
    "steckdose": ("an", "aus"),
}
MAX_KOPIEN = 5
STATUS_ZEIT = 6.0


@dataclass(frozen=True)
class Werkzeug:
    risiko: str
    rollen: frozenset
    grenze: str | None
    klassen: tuple[str, ...] | None  # None: ohne Gerät
    zeit: float


WERKZEUGE: dict[str, Werkzeug] = {
    "ping": Werkzeug("read", ROLLEN, None, None, 2),
    "geraete": Werkzeug("read", ROLLEN, None, None, 2),
    "status": Werkzeug("read", ROLLEN, "status", ("*",), 15),
    "steuern": Werkzeug("change", ROLLEN, "steuern", tuple(STEUER_AKTIONEN), 15),
    "drucken": Werkzeug("critical", ROLLEN, "druck_auftrag", ("drucker",), 150),
    "scannen": Werkzeug("change", ROLLEN, "scan", ("scanner",), 360),
    "ablage_liste": Werkzeug("read", ROLLEN, None, None, 5),
    "ablage_holen": Werkzeug("read", ROLLEN, None, None, 10),
    "ablage_loeschen": Werkzeug("change", ROLLEN, None, None, 5),
    "nas_suchen": Werkzeug("read", ROLLEN, "nas", ("nas",), 30),
    "nas_abrufen": Werkzeug("read", ROLLEN, "nas", ("nas",), 75),
    "router_geraete": Werkzeug("read", frozenset({"admin"}), "router", ("router",), 20),
}


class Antwort(Exception):
    """Fertige Antwort aus der Mitte des Ablaufs (abgelehnt, nicht eingerichtet, Grenze)."""

    def __init__(self, status: str, summary: str):
        super().__init__(summary)
        self.status, self.summary = status, summary


def faehigkeiten(g: Geraet) -> list[str]:
    erg = ["status"]
    if g.klasse in STEUER_AKTIONEN:
        erg.append("steuern")
    erg += {"drucker": ["drucken"], "scanner": ["scannen"], "nas": ["nas_suchen", "nas_abrufen"],
            "router": ["router_geraete"]}.get(g.klasse, [])
    return erg


@dataclass(frozen=True)
class Kontext:
    user_id: str
    rolle: str
    session_id: str | None
    name: str


def kontext_aus(roh: object) -> Kontext:
    if not isinstance(roh, dict):
        raise Antwort("rejected", "Kontext fehlt")
    try:
        uid = str(uuid.UUID(str(roh.get("user_id"))))
    except (ValueError, TypeError, AttributeError) as e:
        raise Antwort("rejected", "Kontext ohne gültiges Profil") from e
    rolle = roh.get("rolle")
    if rolle not in ROLLEN:
        raise Antwort("rejected", "Kontext ohne gültige Rolle")
    sid = roh.get("session_id")
    try:
        sid = str(uuid.UUID(str(sid))) if sid else None
    except (ValueError, TypeError, AttributeError):
        sid = None
    return Kontext(uid, rolle, sid, name(roh.get("name"), 40))


class Dienst:
    def __init__(self, konfig: Konfig, *, ablage: Ablage, audit: Audit, grenzen: Grenzen | None = None,
                 cache: Zwischenspeicher | None = None, uhr: Callable[[], float] = time.monotonic):
        self.konfig = konfig
        self.ablage = ablage
        self.audit = audit
        self.grenzen = grenzen or Grenzen()
        self.cache = cache or Zwischenspeicher()
        self._uhr = uhr
        self._scan_sperren: dict[str, asyncio.Lock] = {}
        self.selbsttest = "nicht gelaufen"

    # ---------- Einstieg ----------
    async def bearbeiten(self, anfrage: object) -> tuple[dict, bytes]:
        start = self._uhr()
        werkzeug_name, kennung, kontext = "?", None, None
        try:
            if not isinstance(anfrage, dict) or anfrage.get("v") != 1:
                raise Antwort("rejected", "Anfrage ungültig")
            werkzeug_name = anfrage.get("werkzeug") if isinstance(anfrage.get("werkzeug"), str) else "?"
            w = WERKZEUGE.get(werkzeug_name)
            if w is None:
                raise Antwort("rejected", "Unbekanntes Werkzeug")
            kontext = kontext_aus(anfrage.get("kontext"))
            if kontext.rolle not in w.rollen:
                raise Antwort("rejected", "Dieses Werkzeug ist nur für Admins")
            argumente = anfrage.get("argumente") or {}
            if not isinstance(argumente, dict):
                raise Antwort("rejected", "Argumente ungültig")
            geraet = None
            if w.klassen is not None:
                kennung = anfrage.get("geraet")
                if werkzeug_name == "status" and kennung == "alle":
                    geraet = None
                else:
                    geraet = self.konfig.geraete.get(kennung) if isinstance(kennung, str) else None
                    if geraet is None:
                        raise Antwort("rejected", "Unbekanntes Gerät")
                    if "*" not in w.klassen and geraet.klasse not in w.klassen:
                        raise Antwort("rejected", f"Das kann {geraet.name} nicht")
            if w.grenze:
                try:
                    self.grenzen.pruefen(w.grenze, kontext.user_id)
                except GrenzeErreicht as e:
                    raise Antwort("grenze", "Zu viele Anfragen in kurzer Zeit, bitte etwas warten") from e
            async with asyncio.timeout(w.zeit):
                daten, anhang = await self._ausfuehren(werkzeug_name, geraet, argumente, kontext)
            antwort = {"status": "success", **daten}
        except Antwort as a:
            antwort, anhang = {"status": a.status, "summary": a.summary}, b""
        except NichtEingerichtet as e:
            antwort, anhang = {"status": "nicht_eingerichtet", "summary": str(e)[:200]}, b""
        except Abgelehnt as e:
            antwort, anhang = {"status": "rejected", "summary": str(e)[:200]}, b""
        except GeraetFehler as e:
            antwort, anhang = {"status": "error", "summary": str(e)[:200]}, b""
        except AblageFehler as e:
            antwort, anhang = {"status": "rejected", "summary": str(e)[:200]}, b""
        except TimeoutError:
            antwort, anhang = {"status": "error", "summary": "Das Gerät hat nicht rechtzeitig geantwortet"}, b""
        except Exception as e:  # noqa: BLE001 (ein Treiberfehler darf den Dienst nie beenden)
            logger.warning("Werkzeug %s fehlgeschlagen (%s)", werkzeug_name, type(e).__name__)
            antwort, anhang = {"status": "error", "summary": "Interner Fehler im Gerätedienst"}, b""
        self.audit.schreiben({"werkzeug": werkzeug_name[:30], "geraet": kennung if isinstance(kennung, str) else None,
                              "profil": kontext.user_id if kontext else None, "rolle": kontext.rolle if kontext else None,
                              "sitzung": kontext.session_id if kontext else None,
                              "risiko": WERKZEUGE[werkzeug_name].risiko if werkzeug_name in WERKZEUGE else None,
                              "ergebnis": antwort["status"], "ms": round((self._uhr() - start) * 1000)})
        if anhang:
            antwort["anhang"] = len(anhang)
        return antwort, anhang

    # ---------- Werkzeuge ----------
    async def _ausfuehren(self, werkzeug: str, g: Geraet | None, a: dict, k: Kontext) -> tuple[dict, bytes]:
        if werkzeug == "ping":
            return {"dienst": "wamo-geraete", "selbsttest": self.selbsttest, "geraete": len(self.konfig.geraete)}, b""
        if werkzeug == "geraete":
            return {"geraete": [{"kennung": x.kennung, "name": x.name, "klasse": x.klasse,
                                 "eingerichtet": bool(x.adresse) or x.treiber in ("server-lokal", "anwesenheit"),
                                 "faehigkeiten": [f for f in faehigkeiten(x) if k.rolle == "admin" or f != "router_geraete"],
                                 **({"aktionen": list(STEUER_AKTIONEN[x.klasse])} if x.klasse in STEUER_AKTIONEN else {}),
                                 **({"eingaenge": list(x.opt("eingaenge", ("tv", "hdmi1", "hdmi2", "hdmi3", "hdmi4")))}
                                    if x.klasse == "fernseher" else {})}
                                for x in self.konfig.geraete.values()]}, b""
        if werkzeug == "status":
            if g is None:
                return {"geraete": await self._status_alle()}, b""
            return {"geraet": g.kennung, "name": g.name, "klasse": g.klasse, **await self._status(g)}, b""
        if werkzeug == "steuern":
            return await self._steuern(g, a), b""
        if werkzeug == "drucken":
            return await self._drucken(g, a, k), b""
        if werkzeug == "scannen":
            return await self._scannen(g, a, k), b""
        if werkzeug == "ablage_liste":
            return {"eintraege": [e.oeffentlich() for e in self.ablage.liste(k.user_id)]}, b""
        if werkzeug == "ablage_holen":
            eintrag, daten = self.ablage.holen(k.user_id, str(a.get("id", "")))
            return {"eintrag": eintrag.oeffentlich()}, daten
        if werkzeug == "ablage_loeschen":
            return {"geloescht": self.ablage.loeschen(k.user_id, str(a.get("id", "")))}, b""
        if werkzeug == "nas_suchen":
            freigabe = a.get("freigabe") if isinstance(a.get("freigabe"), str) else None
            return await nas.suchen(g, self.konfig, str(a.get("begriff", "")), freigabe), b""
        if werkzeug == "nas_abrufen":
            dateiname_, daten = await nas.abrufen(g, self.konfig, str(a.get("pfad", "")))
            mime = mimetypes.guess_type(dateiname_)[0] or "application/octet-stream"
            if mime not in ("application/pdf", "image/jpeg", "image/png", "text/plain"):
                mime = "application/octet-stream"
            eintrag = self.ablage.ablegen(k.user_id, "nas", dateiname_, mime, daten, g.kennung)
            return {"eintrag": eintrag.oeffentlich()}, b""
        if werkzeug == "router_geraete":
            liste = await upnp.router_geraete(g, self.konfig)
            return {"geraete": liste, "anzahl": len(liste), "aktiv": sum(1 for x in liste if x["aktiv"])}, b""
        raise Antwort("rejected", "Unbekanntes Werkzeug")

    async def _status(self, g: Geraet) -> dict:
        if (gemerkt := self.cache.holen(g.kennung)) is not None:
            return {**gemerkt, "aus_zwischenspeicher": True}
        t = g.treiber
        aufrufe: dict[str, Callable[[], Awaitable[dict]]] = {
            "server-lokal": lambda: server_status.lokal(g),
            "server-ssh": lambda: server_status.per_ssh(g),
            "ipp": lambda: ipp.status(g),
            "escl": lambda: escl.status(g),
            "upnp-igd": lambda: upnp.router_status(g),
            "upnp-renderer": lambda: lautsprecher.status(g),
            "shelly": lambda: shelly.status(g, self.konfig),
            "samsung-tv": lambda: fernseher.status(g),
            "lg-webos": lambda: lg_tv.status(g),
            "smb": lambda: nas.status(g, self.konfig),
            "anwesenheit": lambda: anwesenheit.status(g),
        }
        erg = await aufrufe[t]()
        self.cache.setzen(g.kennung, erg)
        return erg

    async def _status_alle(self) -> list[dict]:
        async def eins(g: Geraet) -> dict:
            basis = {"geraet": g.kennung, "name": g.name, "klasse": g.klasse}
            try:
                async with asyncio.timeout(STATUS_ZEIT):
                    return {**basis, "status": "success", **await self._status(g)}
            except NichtEingerichtet as e:
                return {**basis, "status": "nicht_eingerichtet", "summary": str(e)[:200]}
            except (GeraetFehler, TimeoutError) as e:
                return {**basis, "status": "error", "summary": str(e)[:200] or "keine Antwort"}
            except Exception as e:  # noqa: BLE001
                logger.warning("Status %s: %s", g.kennung, type(e).__name__)
                return {**basis, "status": "error", "summary": "Interner Fehler"}
        return list(await asyncio.gather(*(eins(g) for g in self.konfig.geraete.values())))

    async def _steuern(self, g: Geraet, a: dict) -> dict:
        aktion = a.get("aktion")
        if aktion not in STEUER_AKTIONEN.get(g.klasse, ()):
            raise Abgelehnt(f"Für {g.name} gibt es: {', '.join(STEUER_AKTIONEN.get(g.klasse, ()))}")
        wert = a.get("wert")
        wert = wert if isinstance(wert, int) and not isinstance(wert, bool) else None
        self.cache.vergessen(g.kennung)
        if g.treiber == "shelly":
            erg = await shelly.steuern(g, self.konfig, aktion)
        elif g.treiber == "upnp-renderer":
            erg = await lautsprecher.steuern(g, aktion, wert)
        elif g.treiber in ("samsung-tv", "lg-webos"):
            eingang = a.get("eingang") if isinstance(a.get("eingang"), str) else None
            modul = fernseher if g.treiber == "samsung-tv" else lg_tv
            erg = await modul.steuern(g, aktion, wert, eingang)
        else:
            raise Abgelehnt("Dieses Gerät lässt sich nicht steuern")
        return {"geraet": g.kennung, "aktion": aktion, **erg}

    async def _drucken(self, g: Geraet, a: dict, k: Kontext) -> dict:
        dok = raster.dokument_aus(a.get("dokument"))
        kopien = a.get("kopien", 1)
        if isinstance(kopien, bool) or not isinstance(kopien, int) or not 1 <= kopien <= MAX_KOPIEN:
            raise Abgelehnt(f"Kopien 1 bis {MAX_KOPIEN}")
        farbe = a.get("farbe", False) is True
        if farbe and not g.opt("farbe_erlaubt", True):
            raise Abgelehnt("Farbdruck ist für diesen Drucker aus")
        dpi = int(g.opt("aufloesung", 600))
        bilder = await asyncio.to_thread(raster.seiten, dok, dpi=dpi, farbe=farbe)
        seiten = len(bilder) * kopien
        if not self.grenzen.seiten_frei(k.user_id, seiten, self.konfig.druck_seiten_tag,
                                        self.konfig.druck_seiten_gesamt_tag):
            raise Antwort("grenze", "Das Tageslimit für Druckseiten ist erreicht")
        if g.opt("format", "pwg") == "pwg":
            daten = await asyncio.to_thread(raster.pwg_raster, bilder, dpi, farbe)
            ergebnis = await ipp.drucken(g, daten, "image/pwg-raster", titel=dok.titel, kopien=kopien, farbe=farbe)
            auftraege = [ergebnis["auftrag"]]
        else:
            auftraege = []
            for bild in bilder:
                daten = await asyncio.to_thread(raster.jpeg, bild)
                ergebnis = await ipp.drucken(g, daten, "image/jpeg", titel=dok.titel, kopien=kopien, farbe=farbe)
                auftraege.append(ergebnis["auftrag"])
        self.grenzen.seiten_buchen(k.user_id, seiten)
        self.cache.vergessen(g.kennung)
        return {"geraet": g.kennung, "seiten": len(bilder), "kopien": kopien, "farbe": farbe, "auftraege": auftraege,
                "zustand": ergebnis.get("zustand", "angenommen"), "schrift": raster.schrift_name()}

    async def _scannen(self, g: Geraet, a: dict, k: Kontext) -> dict:
        quelle = a.get("quelle", "glas")
        farbe = a.get("farbe", False) is True
        aufloesung = a.get("aufloesung", 300)
        if quelle not in escl.QUELLEN or aufloesung not in escl.AUFLOESUNGEN:
            raise Abgelehnt("Quelle glas oder einzug, Auflösung 150 oder 300")
        sperre = self._scan_sperren.setdefault(g.kennung, asyncio.Lock())
        if sperre.locked():
            raise Antwort("rejected", "Der Scanner ist gerade belegt, bitte gleich noch einmal")
        async with sperre:
            pdf, seiten = await escl.scannen(g, quelle, farbe, aufloesung)
        name_ = time.strftime("Scan %Y-%m-%d %H-%M-%S.pdf", time.localtime())
        eintrag = self.ablage.ablegen(k.user_id, "scan", name_, "application/pdf", pdf, g.kennung)
        nas_ziel = None
        for ziel in self.konfig.geraete.values():
            if ziel.klasse == "nas" and ziel.opt("scanziel"):
                try:
                    nas_ziel = await nas.scan_ablegen(ziel, self.konfig, k.name or k.user_id[:8], eintrag.name, pdf)
                except (NichtEingerichtet, GeraetFehler) as e:
                    nas_ziel = f"nicht abgelegt: {str(e)[:80]}"
                break
        return {"geraet": g.kennung, "seiten": seiten, "eintrag": eintrag.oeffentlich(),
                **({"nas": nas_ziel} if nas_ziel else {})}
