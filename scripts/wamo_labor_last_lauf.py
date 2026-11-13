"""Der Ablauf des Lasttests (scripts/wamo_labor_last.py): Stufen fahren, am Kern messen, Grenzen bewachen, aufräumen, auswerten.

Ein Lauf besteht aus Vorprüfung, Vorbereitung (einmal für die größte Stufe), den Stufen und dem Aufräumen. Jede Stufe startet die Gespräche der ersten N
Sitzungen, lässt je Sitzung einen Thread die Kette A1, J1, G3 sprechen (mit zufälligen Denkpausen aus festem Seed) und misst am Kern lesend alle 15 Sekunden.
Das Aufräumen steht in einem finally und läuft auch bei Fehlern, Grenzen und Strg+C.

Grenzen, bei denen der Lauf sofort endet (alles in scripts/wamo_labor_last_plan.py):
  ein 429 (im Journal des Kerns oder in einer Antwort im Browser), mehr als 2 Abbrüche, Speicher des Kerns über 3 GiB, Messung am Server dreimal in Folge
  ausgefallen, Kern nicht mehr aktiv. Eine Stufe dauert höchstens 20 Minuten (harte Grenze, auch wenn weniger gewählt ist, plus die Dauer der Wartezeit
  eines laufenden Schritts, die nie über diese Grenze hinausgeht).
"""
from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime

import wamo_labor_last_auswertung as aw
import wamo_labor_last_messung as ms
import wamo_labor_last_plan as pl
import wamo_labor_last_server as sv
import wamo_labor_last_vorbereitung as vb
import wamo_labor_faktoren_signale as sg
from wamo_labor_last_browser import TreiberFehler
from wamo_labor_last_umgebung import Einstellungen, Umgebung, Zustand, kurz, parallel

VERSION = 1
START_FRIST_S = 45.0  # so lange wartet ein Schritt auf den ersten Ton
ENDE_FRIST_S = 90.0  # so lange auf das Ende der Antwort
EINWURF_AB_S = 3.0  # der Einwurf kommt, wenn WAMO schon so lange spricht
MIN_SCHRITT_S = 20.0  # ein Schritt beginnt nur, wenn bis zur harten Grenze noch so viel bleibt
STUFEN_PAUSE_S = 15.0
GESPRAECH_VERSUCHE = 3
GESPRAECH_PAUSE_S = 20.0  # nach einem Fehlversuch (der Kern kennt ein Profil bis zu 2 Minuten aus dem Zwischenspeicher)
MESSFEHLER_MAX = 3


@dataclass
class _Stufenlauf:
    stufe: pl.Stufe
    start: float = 0.0
    soft: float = 0.0
    hard: float = 0.0
    schritte: list = field(default_factory=list)
    proben: list = field(default_factory=list)
    browser_429: int = 0
    konsole: int = 0
    sperre: threading.Lock = field(default_factory=threading.Lock)

    def eintragen(self, ergebnis: aw.SchrittErgebnis, r429: int = 0, konsole: int = 0) -> None:
        with self.sperre:
            self.schritte.append(ergebnis)
            self.browser_429 += r429
            self.konsole += konsole


class Lauf:
    def __init__(self, ein: Einstellungen, umg: Umgebung):
        self.ein, self.umg, self.uhr = ein, umg, umg.uhr
        self.z = Zustand()
        self.abbruch = threading.Event()
        self.grund = ""
        self.von_aussen = False
        self.abbrueche = 0
        self.messfehler = 0
        self.seit: float | None = None
        self.murmeln: dict = {}
        self.stufen: list[dict] = []
        self.schritte: dict[str, list] = {}
        self.proben: dict[str, list] = {}
        self.aufraeumen_liste: list[dict] = []
        self.beginn = self.ende = ""
        self._sperre = threading.Lock()

    # ---- Hauptweg ----

    def ausfuehren(self) -> dict:
        self.beginn = self._zeit()
        try:
            self._lauf()
        except (KeyboardInterrupt, sg.Signal):
            self.von_aussen = True
            self._grund("Abbruch von außen (Strg+C oder Signal)")
        except vb.VorpruefFehler as e:
            self._grund(f"Vorprüfung: {kurz(e, 300)}")
        except (vb.VorbereitungsFehler, pl.EingabeFehler) as e:
            self._grund(f"Vorbereitung: {kurz(e, 300)}")
        except Exception as e:  # noqa: BLE001 (nie ein Traceback mit möglichen Werten, aber immer das Aufräumen)
            self._grund(f"unerwarteter Fehler ({type(e).__name__})")
        finally:
            self._beenden()
        return self._ergebnis()

    def _lauf(self) -> None:
        basis = vb.vorpruefen(self.ein, self.umg)
        self.seit = basis.zeit
        self._aus(f"Vorprüfung bestanden: {self.ein.sitzungen_max} Profile, Kern wamo-core@{self.ein.slot} aktiv")
        self.murmeln = vb.vorbereiten(self.ein, self.umg, self.z, basis.versatz_s)
        self._aus(f"Vorbereitung fertig: {self.ein.sitzungen_max} Browser angemeldet")
        for stufe in self.ein.stufen:
            if self.abbruch.is_set():
                break
            self._stufe(stufe)
            if stufe is not self.ein.stufen[-1] and not self.abbruch.is_set():
                self.uhr.schlafen(STUFEN_PAUSE_S)

    def _beenden(self) -> None:
        self._stoppen()
        alt = sg.ignorieren()  # während des Aufräumens zählt kein Signal, ein zweites Strg+C schneidet nichts ab
        try:
            if self.z.angefasst():
                self._aus("Aufräumen ...")
                self.aufraeumen_liste = vb.aufraeumen(self.ein, self.umg, self.z)
        finally:
            sg.zuruecksetzen(alt)
        self.ende = self._zeit()
        self.umg.speichern(self._ergebnis())

    # ---- Grenzen und Abbruch ----

    def _grund(self, text: str) -> None:
        with self._sperre:
            if not self.grund:
                self.grund = text
        self.abbruch.set()

    def _abbrechen(self, text: str) -> None:
        """Beendet den Lauf kontrolliert: Der Grund wird festgehalten, die Sitzungen hören auf, laufende Wartezeiten im Browser enden."""
        self._grund(text)
        self._aus(f"ABBRUCH: {text}")
        threading.Thread(target=self._treiber_abbrechen, daemon=True, name="abbrechen").start()

    def _treiber_abbrechen(self) -> None:
        parallel([t.abbrechen for t in list(self.z.treiber.values())], "abbrechen")

    def _stoppen(self) -> None:
        """Hält alle Sitzungen an (auch bei einem Fehler im Hauptweg) und wartet, bis ihre Threads fertig sind."""
        self.abbruch.set()
        self._treiber_abbrechen()

    def _abbruch_zaehlen(self) -> None:
        with self._sperre:
            self.abbrueche += 1
            zu_viele = self.abbrueche > pl.MAX_ABBRUECHE
        if zu_viele:
            self._abbrechen(f"mehr als {pl.MAX_ABBRUECHE} Abbrüche")

    def _grenzen(self, m: sv.Messung) -> None:
        if m.journal is not None and m.journal.r429 > 0:
            self._abbrechen("429 im Journal des Kerns")
        elif m.speicher is not None and m.speicher > pl.ABBRUCH_SPEICHER_BYTES:
            self._abbrechen(f"Speicher des Kerns über {pl.ABBRUCH_SPEICHER_BYTES / pl.GIB:.0f} GiB ({m.speicher / pl.GIB:.2f} GiB)".replace(".", ","))
        elif not m.aktiv:
            self._abbrechen(f"Der Kern wamo-core@{self.ein.slot} ist nicht mehr aktiv")

    # ---- Messung am Kern ----

    def _probe(self, st: _Stufenlauf) -> None:
        try:
            m = self.umg.server.messen(self.seit)
        except sv.ServerFehler as e:
            self.messfehler += 1
            self._aus(f"Messung am Server fehlgeschlagen ({self.messfehler}): {kurz(e, 120)}")
            if self.messfehler >= MESSFEHLER_MAX:
                self._abbrechen(f"Messung am Server ausgefallen ({MESSFEHLER_MAX} Fehler in Folge)")
            return
        self.messfehler = 0
        self.seit = m.zeit
        st.proben.append(m)
        self._grenzen(m)

    def _ueberwachen(self, st: _Stufenlauf, threads: list[threading.Thread]) -> None:
        naechste = self.uhr.jetzt() + self.ein.mess_intervall_s
        while any(t.is_alive() for t in threads) and not self.abbruch.is_set():
            self.uhr.schlafen(1.0)
            if self.uhr.jetzt() >= naechste:
                self._probe(st)
                naechste += self.ein.mess_intervall_s

    # ---- eine Stufe ----

    def _stufe(self, stufe: pl.Stufe) -> None:
        st = _Stufenlauf(stufe)
        self._aus(f"Stufe {stufe.nummer}: {stufe.name}, {stufe.minuten} Minuten")
        aktive: list[int] = []
        threads: list[threading.Thread] = []
        try:
            self._probe(st)
            aktive = self._gespraeche_starten(st)
            self._murmeln(st, aktive, True)
            st.start = self.uhr.jetzt()
            st.soft, st.hard = st.start + stufe.minuten * 60, st.start + pl.MAX_STUFE_MINUTEN * 60
            threads = [threading.Thread(target=self._sitzung, args=(st, i), daemon=True, name=f"sitzung{i}") for i in aktive]
            for t in threads:
                t.start()
            self._ueberwachen(st, threads)
        except BaseException:
            self._stoppen()
            raise
        finally:
            self._stufe_abschliessen(st, aktive, threads)

    def _stufe_abschliessen(self, st: _Stufenlauf, aktive: list[int], threads: list[threading.Thread]) -> None:
        for t in threads:
            while t.is_alive():
                t.join(0.5)
        self._probe(st)
        self._murmeln(st, aktive, False)
        self._gespraeche_beenden(aktive)
        dauer = self.uhr.jetzt() - st.start if st.start else 0.0
        self.stufen.append(aw.stufe_auswerten(st.stufe, st.schritte, st.proben, st.browser_429, st.konsole, dauer))
        key = str(st.stufe.nummer)
        self.schritte[key] = [asdict(s) for s in st.schritte]
        self.proben[key] = [_probe_dict(p) for p in st.proben]
        self.umg.speichern(self._ergebnis())

    # ---- Gespräche und Murmeln ----

    def _gespraech_starten(self, i: int) -> None:
        treiber = self.z.treiber[i]
        self.z.gespraeche.add(i)  # vor dem Versuch: Das Aufräumen beendet auch ein halb gestartetes Gespräch
        for versuch in range(1, GESPRAECH_VERSUCHE + 1):
            try:
                treiber.gespraech_starten()
                return
            except TreiberFehler:
                if versuch == GESPRAECH_VERSUCHE or self.abbruch.is_set():
                    raise
                self.uhr.schlafen(GESPRAECH_PAUSE_S)

    def _gespraeche_starten(self, st: _Stufenlauf) -> list[int]:
        n = st.stufe.sitzungen
        fehler = parallel([lambda i=i: self._gespraech_starten(i) for i in range(n)], "gespraech")
        aktive = []
        for i, f in enumerate(fehler):
            if f is None:
                aktive.append(i)
                continue
            text = f"Gespräch nicht gestartet: {kurz(f, 60)}"
            st.eintragen(aw.SchrittErgebnis(sitzung=i, kennung=self.ein.profile[i], karte="-", beginn_s=0.0, erster_ton_ms=None, abbruch=text[:80]))
            self._abbruch_zaehlen()
        return aktive

    def _murmeln(self, st: _Stufenlauf, aktive: list[int], an: bool) -> None:
        if st.stufe.snr_db is None or not aktive:
            return
        m = self.murmeln[st.stufe.snr_db]
        fehler = parallel([(lambda i=i: self.z.treiber[i].murmeln(m.datei, m.gain)) if an else (lambda i=i: self.z.treiber[i].murmeln(None, 0.0))
                           for i in aktive], "murmeln")
        if an and any(f is not None for f in fehler):
            self._abbrechen("Das Murmeln ließ sich nicht starten")

    def _gespraeche_beenden(self, aktive: list[int]) -> None:
        def beenden(i: int) -> None:
            self.z.treiber[i].gespraech_beenden()
            self.z.gespraeche.discard(i)

        parallel([lambda i=i: beenden(i) for i in aktive], "beenden")  # Fehler zählt das Aufräumen, hier genügt der Versuch

    # ---- eine Sitzung ----

    def _sitzung(self, st: _Stufenlauf, i: int) -> None:
        folge = pl.schritt_folge(self.ein.seed, st.stufe.nummer, i)
        try:
            while not self.abbruch.is_set():
                jetzt = self.uhr.jetzt()
                if jetzt >= st.soft or st.hard - jetzt < MIN_SCHRITT_S:
                    return
                plan = next(folge)
                if not self._schritt(st, i, plan, jetzt):
                    return
                self._pause(min(plan.pause_s, st.soft - self.uhr.jetzt()))
        except Exception as e:  # noqa: BLE001 (ein Fehler im Thread beendet den Lauf sichtbar und nie still)
            self._abbrechen(f"Fehler in Sitzung {i + 1} ({type(e).__name__})")

    def _pause(self, sekunden: float) -> None:
        ende = self.uhr.jetzt() + sekunden
        while not self.abbruch.is_set() and self.uhr.jetzt() < ende:
            self.uhr.schlafen(min(0.5, ende - self.uhr.jetzt()))

    def _schritt(self, st: _Stufenlauf, i: int, plan: pl.SchrittPlan, jetzt: float) -> bool:
        """Ein Schritt. Gibt wahr zurück, wenn die Sitzung weiterlaufen kann."""
        kennung, beginn = self.ein.profile[i], jetzt - st.start
        rest = st.hard - jetzt
        start_frist = min(START_FRIST_S, max(5.0, rest - 10.0))
        ende_frist = min(ENDE_FRIST_S, max(5.0, rest - start_frist))
        try:
            roh = self.z.treiber[i].schritt(plan, start_frist, ende_frist, EINWURF_AB_S, self.ein.freigabe)
            erg = ms.schritt_auswerten(roh, plan, i, kennung, beginn)
        except (TreiberFehler, pl.EingabeFehler) as e:
            return self._schritt_gescheitert(st, i, kennung, plan, beginn, e)
        r429, konsole = ms.browser_zaehler([roh])
        st.eintragen(erg, r429, konsole)
        if r429:
            self._abbrechen(f"429 im Browser (Sitzung {i + 1})")
        if erg.abbruch:
            if not (self.abbruch.is_set() and erg.abbruch == "abgebrochen"):
                self._abbruch_zaehlen()
            return False
        return True

    def _schritt_gescheitert(self, st: _Stufenlauf, i: int, kennung: str, plan: pl.SchrittPlan, beginn: float, e: Exception) -> bool:
        if self.abbruch.is_set():
            return False
        text = f"Browser: {kurz(e, 60)}"
        st.eintragen(aw.SchrittErgebnis(sitzung=i, kennung=kennung, karte=plan.karte, beginn_s=beginn, erster_ton_ms=None, abbruch=text[:80]))
        self._abbruch_zaehlen()
        return False

    # ---- Ergebnis ----

    def _zeit(self) -> str:
        return datetime.fromtimestamp(self.uhr.jetzt()).astimezone().isoformat(timespec="seconds")

    def _aus(self, text: str) -> None:
        self.umg.ausgabe(text)

    def _kosten(self) -> dict:
        k = self.ein.kosten or pl.kosten_schaetzen(self.ein.stufen, self.ein.annahmen)
        return asdict(k)

    def _ergebnis(self) -> dict:
        kriterien = aw.kriterien_pruefen(self.stufen, self.grund, self.ein.ratenlimit_anfragen, self.ein.ratenlimit_tokens)
        return {
            "lauf": self.ein.lauf, "version": VERSION, "ausfuehrung": "echt", "seed": self.ein.seed, "schema": self.ein.schema, "slot": self.ein.slot,
            "beginn": self.beginn, "ende": self.ende, "abbruchgrund": self.grund, "von_aussen": self.von_aussen, "stufen": self.stufen,
            "kriterien": kriterien, "gesamt": aw.gesamturteil(kriterien, self.grund), "annahmen": asdict(self.ein.annahmen),
            "kosten_schaetzung": self._kosten(), "aufraeumen": self.aufraeumen_liste, "schritte": self.schritte, "proben": self.proben,
        }


def _probe_dict(m: sv.Messung) -> dict:
    return {"zeit": m.zeit, "cpu_usec": m.cpu_usec, "speicher": m.speicher, "aufgaben": m.aufgaben, "versatz_s": round(m.versatz_s, 2)}
