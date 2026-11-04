"""Live-Bild des Laptops: Entscheidung im Host, ob ein Einzelbild entstehen darf (Betriebsart Büro).

Das Live-Bild zeigt alles, was am Laptop offen ist. Deshalb entscheidet der HOST selbst (nicht der Kern, nicht die
Oberfläche), und zwar bei jedem einzelnen Bild:

  * Schalter am Laptop: Die Desktop-App der Person am Laptop schreibt %LOCALAPPDATA%\\WAMO\\live-bild.json (neben
    geraet.json), per Tray-Menü oder auf eine Fernanfrage des Besitzers (live-bild-anfrage.json, siehe anfrage_stellen; die App
    prüft sie selbst und schaltet nur bei vorhandener Einwilligung ein). Standard ist AUS. Kein Werkzeug des Hosts schreibt
    diese Datei, PowerShell-Befehle, die sie nennen,
    sind gesperrt (wamo_host.BLOCKED_PATTERNS), und die Fenster der Desktop-App bedient der Host nie. Ein verschleierter
    Befehl, den ein Admin auf einer Freigabekarte bestätigt, könnte die Datei trotzdem schreiben (Restrisiko). Darum
    zählt der Host zusätzlich selbst die Hinweisfenster der App (windows_control.hinweisfenster_zahl).
  * Der Schalter gilt nur, wenn Kennung und Besitzer in der Datei zu geraet.json passen und die Stufe dort "admin" ist
    (ein Mitglieder-Laptop liefert nie ein Bild), die Einwilligung erteilt ist, "bis" nicht abgelaufen ist (höchstens
    eine Stunde je Einschalten) und der Hinweis am Laptop lebt: Die App erneuert alle 2 s einen Herzschlag und meldet,
    ob der rote Rahmen sichtbar ist. Ist der Hinweis ausgefallen, verdeckt oder weggeblendet (Vollbild), gibt es kein Bild.
  * Höchstens ein Zuschauer je Laptop, höchstens MIN_ABSTAND_SECS zwischen zwei Bildern und BILDER_JE_MINUTE je Minute.
  * Das Bild lebt nur im Arbeitsspeicher: nichts davon geht auf die Platte, in das Audit-Log oder in ein Log. Das Audit
    kennt nur Zustände und Zähler.

Dieses Modul entscheidet nur; aufgenommen wird in wamo_host.bild_holen (windows_control.screenshot_live).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

DATEI_NAME = "live-bild.json"
# Fernanfrage (Entscheidung Niko 04.11.2026): Der Admin bittet vom Handy aus, mit frischem Code, das Live-Bild am eigenen Laptop
# einzuschalten. Der Host schreibt dafür NUR diese Anfragedatei (neben geraet.json); den Schalter live-bild.json schreibt weiterhin
# allein die Desktop-App, und zwar erst nach eigener Prüfung (Einwilligung am Laptop liegt vor, Besitzer, Alter, roter Rahmen).
ANFRAGE_DATEI_NAME = "live-bild-anfrage.json"
ANFRAGE_ABSTAND_SECS = 15.0  # höchstens eine Anfrage je Abstand
DATEI_MAX_BYTES = 4096
EINWILLIGUNG_VERSION = 1
HERZSCHLAG_MAX_SECS = 6.0  # so alt darf der Herzschlag der Desktop-App höchstens sein
ZUKUNFT_TOLERANZ_SECS = 2.0
EINSCHALTEN_MAX_SECS = 3600  # "bis" liegt höchstens so weit in der Zukunft
MIN_ABSTAND_SECS = 0.35  # höchstens etwa 2,8 Bilder je Sekunde
BILDER_JE_MINUTE = 150
ZUSCHAUER_FREI_SECS = 12.0  # so lange blockiert ein Zuschauer, der nicht mehr abruft, den Platz
ZUSCHAUER_RE = re.compile(r"[0-9a-f]{16,32}")
BILD_KANTE_MAX = 1280
BILD_BYTES_MAX = 110_000

# Gründe (stabile Codes für Kern und Oberfläche) und ihre Meldungen für Menschen
GRUENDE = {
    "nicht_eingerichtet": "Dieser Laptop ist nicht für das Live-Bild eingerichtet.",
    "stufe": "Dieser Laptop gehört keinem Admin. Ein Live-Bild gibt es hier nie.",
    "schalter_aus": "Am Laptop ist das Live-Bild aus. Die Person am Laptop schaltet es im Tray-Menü von WAMO ein.",
    "keine_einwilligung": "Die Einwilligung am Laptop fehlt noch. Die Person am Laptop muss sie erst geben.",
    "abgelaufen": "Die Zeit des Live-Bilds am Laptop ist abgelaufen. Die Person am Laptop schaltet es erneut ein.",
    "hinweis_fehlt": "Der Hinweis am Laptop ist nicht sichtbar. Ohne ihn gibt es kein Bild.",
}


class Live:
    """Zustand und Entscheidungen des Live-Bilds in einem Host-Prozess. Zeit und Geräte-Bindung sind austauschbar."""

    def __init__(self, datei: Path | None, bindung: object | None, *, uhr: Callable[[], float] = time.time,
                 takt: Callable[[], float] = time.monotonic):
        self._datei = datei
        self._bindung = bindung  # host_http.GeraetBindung (werte()) oder None
        self._uhr = uhr
        self._takt = takt
        self._zuschauer: str | None = None
        self._zuletzt_zuschauer = -1e9
        self._letztes_bild = -1e9
        self._bilder: deque[float] = deque()
        self._zaehler = 0
        self._letzte_anfrage = -1e9

    # ---------- Fernanfrage ----------
    def anfrage_stellen(self) -> dict:
        """Bittet die Desktop-App am Laptop, das Live-Bild einzuschalten. Schreibt nur live-bild-anfrage.json mit Kennung und
        Besitzer des gebundenen Laptops und der Zeit. Gibt {"ok": bool, "grund": code | None} zurück; die App entscheidet danach
        selbst (ohne Einwilligung am Laptop, bei anderem Besitzer oder älter als 60 s tut sie nichts)."""
        if self._datei is None or self._bindung is None:
            return {"ok": False, "grund": "nicht_eingerichtet"}
        gerat = self._bindung.werte()
        if gerat is None:
            return {"ok": False, "grund": "nicht_eingerichtet"}
        kennung, besitzer, stufe = gerat
        if stufe != "admin":
            return {"ok": False, "grund": "stufe"}
        if self.schalter()["bereit"]:
            return {"ok": True, "grund": "schon_an"}
        jetzt = self._takt()
        if jetzt - self._letzte_anfrage < ANFRAGE_ABSTAND_SECS:
            return {"ok": False, "grund": "zu_schnell"}
        pfad = self._datei.with_name(ANFRAGE_DATEI_NAME)
        tmp = pfad.with_name(ANFRAGE_DATEI_NAME + ".neu")
        inhalt = {"v": 1, "id": secrets.token_hex(8), "ts": self._uhr(), "kennung": kennung, "besitzer": besitzer.lower()}
        try:
            tmp.write_text(json.dumps(inhalt), encoding="utf-8")
            os.replace(tmp, pfad)
        except OSError:
            return {"ok": False, "grund": "schreibfehler"}
        self._letzte_anfrage = jetzt
        return {"ok": True, "grund": None}

    # ---------- Schalter ----------
    def _datei_lesen(self) -> dict | None:
        if self._datei is None:
            return None
        try:
            with self._datei.open("rb") as f:
                roh = f.read(DATEI_MAX_BYTES + 1)
            if len(roh) > DATEI_MAX_BYTES:
                return None
            daten = json.loads(roh.decode("utf-8"))
        except (OSError, ValueError, UnicodeError):
            return None
        return daten if isinstance(daten, dict) else None

    def schalter(self) -> dict:
        """{"bereit": bool, "grund": code | None, "rest_s": int}. Bereit heißt: JETZT darf ein Bild entstehen
        (Schalter, Einwilligung, Gerät, Hinweis). Alles Unklare ist nicht bereit."""
        if self._datei is None or self._bindung is None:
            return self._nein("nicht_eingerichtet")
        gerat = self._bindung.werte()
        if gerat is None:
            return self._nein("nicht_eingerichtet")
        kennung, besitzer, stufe = gerat
        if stufe != "admin":
            return self._nein("stufe")
        d = self._datei_lesen()
        if d is None or d.get("v") != 1 or d.get("an") is not True:
            return self._nein("schalter_aus")
        if d.get("kennung") != kennung or str(d.get("besitzer", "")).lower() != besitzer.lower():
            return self._nein("schalter_aus")  # Schalter einer früheren Bindung gilt nie
        if type(d.get("einwilligung")) is not int or d["einwilligung"] != EINWILLIGUNG_VERSION:
            return self._nein("keine_einwilligung")
        jetzt = self._uhr()
        bis = d.get("bis")
        if not _zahl(bis) or bis <= jetzt or bis - jetzt > EINSCHALTEN_MAX_SECS + ZUKUNFT_TOLERANZ_SECS:
            return self._nein("abgelaufen")
        herz = d.get("herzschlag")
        if not _zahl(herz) or jetzt - herz > HERZSCHLAG_MAX_SECS or herz - jetzt > ZUKUNFT_TOLERANZ_SECS \
                or d.get("sichtbar") is not True:
            return self._nein("hinweis_fehlt")
        return {"bereit": True, "grund": None, "rest_s": max(0, int(bis - jetzt))}

    @staticmethod
    def _nein(grund: str) -> dict:
        return {"bereit": False, "grund": grund, "rest_s": 0}

    # ---------- Zuschauer und Takt ----------
    def zuschauer(self, kennung: object) -> bool:
        """Höchstens ein Zuschauer: ein zweiter bekommt nichts, solange der erste in den letzten Sekunden abrief."""
        if not isinstance(kennung, str) or not ZUSCHAUER_RE.fullmatch(kennung):
            return False
        jetzt = self._takt()
        if self._zuschauer not in (None, kennung) and jetzt - self._zuletzt_zuschauer < ZUSCHAUER_FREI_SECS:
            return False
        if self._zuschauer != kennung:
            self._zaehler = 0
        self._zuschauer, self._zuletzt_zuschauer = kennung, jetzt
        return True

    def takt(self) -> float:
        """0.0 und das Bild zählt, sonst die Wartezeit in Sekunden (Abstand oder Minutengrenze)."""
        jetzt = self._takt()
        while self._bilder and jetzt - self._bilder[0] >= 60.0:
            self._bilder.popleft()
        warte = MIN_ABSTAND_SECS - (jetzt - self._letztes_bild)
        if warte > 0:
            return warte
        if len(self._bilder) >= BILDER_JE_MINUTE:
            return max(0.5, 60.0 - (jetzt - self._bilder[0]))
        self._letztes_bild = jetzt
        self._bilder.append(jetzt)
        self._zaehler += 1
        return 0.0

    @property
    def bilder_im_lauf(self) -> int:
        return self._zaehler

    def beenden(self, kennung: object = None) -> None:
        """Zuschauerplatz freigeben (Kern meldet das Ende)."""
        if kennung is None or kennung == self._zuschauer:
            self._zuschauer = None
            self._zaehler = 0


def _zahl(wert: object) -> bool:
    return isinstance(wert, (int, float)) and not isinstance(wert, bool) and wert == wert


def datei_neben(geraet_datei: str | None) -> Path | None:
    """live-bild.json liegt neben geraet.json; ohne Geräte-Datei (lokaler Betrieb über stdio) gibt es kein Live-Bild."""
    if not geraet_datei or not geraet_datei.strip():
        return None
    return Path(geraet_datei.strip()).with_name(DATEI_NAME)
