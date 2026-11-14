"""`starten`: Chrome mit zwei Fenstern, links die WAMO-Oberfläche, rechts das Board (nur Standardbibliothek, Windows).

Das Platz-Token öffnet das Board eines Platzes. Die Board-Seite liest es aus dem URL-Fragment (/board#t=...) und entfernt es sofort
aus der Adresszeile. Das Skript will es auf dem Weg dorthin nirgends ablegen. Geprüfte Wege:

  Zwischenablage  : Windows hält einen Verlauf (Win+V) und kann ihn mit dem Konto abgleichen, außerdem müsste jemand einfügen.
  Datei (Weiterleitungsseite, 10 Sekunden): Das Token läge auf der Platte (Wiederherstellung, Virenscanner, Sicherung).
  Befehlszeile (start, startfile, Verknüpfung mit Adresse): Das Token stünde in der Prozessliste, im Task-Manager (Spalte
                    Befehlszeile), in Chrome selbst (chrome://version) und in Protokollen der Prozessüberwachung.
  Gewählt: einmalige Weiterleitung auf Loopback (EinmalWeiterleitung). Das Skript nimmt das Token ohne Anzeige entgegen (getpass),
                    hält es nur im Speicher und öffnet für höchstens 10 Sekunden einen Anschluss auf 127.0.0.1. Chrome bekommt als
                    Startadresse nur http://127.0.0.1:<Port>/weiter/<Zufallspfad>, die Antwort ist eine Umleitung (302) auf
                    /board#t=<Token>. Danach ist der Anschluss zu. Das Skript schreibt es nie in eine Datei, in die Zwischenablage oder
                    eine Befehlszeile (die Aussage gilt für das Skript, nicht für Chrome, siehe Restrisiko).

Schutz der Weiterleitung: nur Loopback, ausschließliche Bindung des Ports (kein Mitbenutzen durch ein anderes Programm), ein
Zufallspfad je Start, Host-Kopf und Pfad werden zeitkonstant verglichen (gegen DNS-Rebinding und Ratetests), genau eine erfolgreiche
Auslieferung (misslingt das Senden, bleibt der Einmalwert gültig), dann Ende, Frist 10 Sekunden. Die
Chrome-Richtlinie URLAllowlist erlaubt genau diese feste Adresse (Port 55489, Pfad /weiter/). Die Adresse des Boards kommt aus der Platzdatei und
wird gegen die feste Liste der Labor-Adressen geprüft, bevor das Token überhaupt erfragt wird.

Restrisiko (ehrlich): Ein anderes Programm desselben Benutzers kann den Zufallspfad aus der Befehlszeile von Chrome lesen und die
Umleitung vor Chrome abholen (derselbe Zugriff wie auf eine Datei im Benutzerordner). Loopback gilt rechnerweit: Auch andere
gleichzeitig angemeldete Konten des Rechners erreichen den Anschluss (sie brauchen den Zufallspfad, er ist höchstens 10 Sekunden
offen). Das Token lebt außerdem im Speicher des Skripts, bis Python den Speicher freigibt, und in Chrome, bis die Seite es aus der
Adresszeile entfernt hat. Ob /board#t=... in Sitzungsdateien oder im Verlauf des Profils landet, hat das Skript nicht in der Hand: Es
hängt an der Wirkung der Richtlinie im Benutzerzweig (SavingBrowserHistoryDisabled) und an Chrome. Die Generalprobe durchsucht den
Profilordner nach dem Token (ohne es auszugeben), und `rueckweg --profil-loeschen` entfernt das Profil nach dem Labortag. Die Knöpfe
"Token kopieren" und "Link kopieren" der Betreuer-Seite legen das Token in die Zwischenablage und den Verlauf des Betreuer-Geräts:
"nie in der Zwischenablage" gilt nur für diesen Weg. Ein Platz-Token gilt nur für das Board dieses Platzes und wird mit
`neuer-durchlauf` ungültig.
"""
from __future__ import annotations

import hmac
import secrets
import socket
import socketserver
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from wamo_labor_platz_adressen import WEITER_PFAD, token_gueltig
from wamo_labor_platz_umgebung import Umgebung
from wamo_labor_platz_zustand import ZustandFehler, platz_lesen

EXIT_OK, EXIT_FEHLER, EXIT_EINGABE = 0, 1, 2
Geom = tuple[int, int, int, int]  # x, y, Breite, Höhe


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = False  # unter Windows würde das Mitbenutzen eines belegten Ports erlauben
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:  # keine Ablaufverfolgung auf die Konsole (nichts davon braucht die Person)
        pass

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def _gleich(a: str, b: str) -> bool:
    """Zeitkonstanter Vergleich (als Bytes, damit Nicht-ASCII-Zeichen nie zu einem Fehler führen)."""
    return hmac.compare_digest(a.encode("utf-8", "replace"), b.encode("utf-8"))


class _Handler(BaseHTTPRequestHandler):
    timeout = 3  # eine zögernde oder leere Vorab-Verbindung blockiert nie die anderen
    protocol_version = "HTTP/1.0"
    server_version = "wamo"
    sys_version = ""

    def log_message(self, *_args) -> None:  # nie protokollieren (der Pfad ist ein Einmalwert)
        pass

    def _antwort(self, code: int, ort: str | None = None) -> None:
        self.send_response(code)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", "0")
        if ort is not None:
            self.send_header("Location", ort)
        self.end_headers()

    def do_GET(self) -> None:
        weiter: EinmalWeiterleitung = self.server.weiterleitung  # type: ignore[attr-defined]
        ziel = weiter._einloesen(self.headers.get("Host", ""), self.path)
        if ziel is None:
            self._antwort(404)
            return
        try:
            self._antwort(302, ziel)
        except OSError:
            weiter._zurueckgeben(ziel)  # nicht angekommen: Der Einmalwert bleibt gültig, bis eine Auslieferung gelungen ist
            raise
        weiter._abgeholt.set()


class EinmalWeiterleitung:
    """Ein Anschluss auf 127.0.0.1, der genau einmal auf einen Zufallspfad mit einer Umleitung auf `ziel` antwortet."""

    def __init__(self, port: int, ziel: str, frist_s: float) -> None:
        self._ziel: str | None = ziel
        self._nonce = secrets.token_urlsafe(18)
        self._frist = frist_s
        self._abgeholt = threading.Event()
        self._sperre = threading.Lock()
        self._server = _Server(("127.0.0.1", port), _Handler)  # OSError, wenn der Port belegt ist
        self._server.weiterleitung = self  # type: ignore[attr-defined]
        self.port: int = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    @property
    def pfad(self) -> str:
        return f"{WEITER_PFAD}{self._nonce}"

    @property
    def adresse(self) -> str:
        return f"http://127.0.0.1:{self.port}{self.pfad}"

    @property
    def abgeholt(self) -> bool:
        return self._abgeholt.is_set()

    def _einloesen(self, host: str, pfad: str) -> str | None:
        """Gibt das Ziel genau einmal heraus, nur für den richtigen Host und Pfad."""
        if not _gleich(host, f"127.0.0.1:{self.port}") or not _gleich(pfad, self.pfad):
            return None
        with self._sperre:
            ziel, self._ziel = self._ziel, None
        return ziel

    def _zurueckgeben(self, ziel: str) -> None:
        with self._sperre:
            if not self._abgeholt.is_set():
                self._ziel = ziel

    def abwarten(self) -> bool:
        return self._abgeholt.wait(self._frist)

    def __enter__(self) -> "EinmalWeiterleitung":
        self._thread.start()
        return self

    def __exit__(self, *_ausnahme) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._ziel = None


def chrome_finden(kandidaten: tuple[Path, ...]) -> Path | None:
    return next((p for p in kandidaten if p.is_file()), None)


def fenster_geometrie(bereich: Geom) -> tuple[Geom, Geom]:
    """Links und rechts je eine Hälfte des Arbeitsbereichs (ohne Taskleiste), die rechte nimmt einen Rest."""
    x, y, breite, hoehe = bereich
    links = breite // 2
    return (x, y, links, hoehe), (x + links, y, breite - links, hoehe)


def chrome_argumente(chrome: Path, profil: Path, url: str, geom: Geom) -> list[str]:
    """Befehlszeile eines App-Fensters. Die Schalter gegen Drosselung stammen aus scripts/wamo-app.cmd (sonst kommt Mikrofon-Audio lückenhaft an)."""
    x, y, breite, hoehe = geom
    return [str(chrome), f"--user-data-dir={profil}", f"--app={url}", f"--window-position={x},{y}", f"--window-size={breite},{hoehe}",
            "--no-first-run", "--no-default-browser-check", "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows",
            "--disable-features=CalculateNativeWinOcclusion,IntensiveWakeUpThrottling"]


def _token_holen(umg: Umgebung) -> str | None:
    """Fragt das Platz-Token ab. Ein ungültiger Wert wird nie ausgegeben, auch nicht teilweise."""
    token = umg.token_eingabe().strip()
    if not token_gueltig(token):
        umg.ausgabe("Eingabe: Das Platz-Token ist leer oder hat nicht die Form von 32 Hexzeichen (Kleinbuchstaben). Es wurde nichts "
                    "gestartet. Das Token vom Betreuer neu holen (Betreuer-Seite, Platz, Token).")
        return None
    return token


def _wamo_fenster(umg: Umgebung, chrome: Path, daten: dict, links: Geom) -> None:
    umg.prozess_starten(chrome_argumente(chrome, umg.profil, daten["wamo_url"], links))


def _board_fenster(umg: Umgebung, chrome: Path, daten: dict, token: str, geometrie: tuple[Geom, Geom]) -> int:
    ziel = daten["board_url"] + "#t=" + token
    try:
        weiter = EinmalWeiterleitung(umg.weiter_port, ziel, umg.weiter_frist_s)
    except OSError:
        umg.ausgabe("Fehler: Der lokale Anschluss für die Weiterleitung ist belegt. Es wurde nichts gestartet. "
                    "Anderes Programm beenden und noch einmal versuchen.")
        return EXIT_FEHLER
    with weiter:
        _wamo_fenster(umg, chrome, daten, geometrie[0])
        umg.warten(umg.chrome_wartezeit_s)
        umg.prozess_starten(chrome_argumente(chrome, umg.profil, weiter.adresse, geometrie[1]))
        abgeholt = weiter.abwarten()
    if not abgeholt:
        umg.ausgabe("Fehler: Das Board-Fenster hat das Token nicht abgeholt. Die Weiterleitung ist geschlossen, das Token wurde "
                    "nicht übergeben. Bitte noch einmal starten.")
        return EXIT_FEHLER
    return EXIT_OK


def cmd_starten(umg: Umgebung, ohne_board: bool = False) -> int:
    try:
        daten = platz_lesen(umg)
    except ZustandFehler as e:
        umg.ausgabe(f"Fehler: {e}. Es wurde nichts gestartet.")
        return EXIT_FEHLER
    if daten is None:
        umg.ausgabe("Dieser Platz ist nicht eingerichtet: zuerst `wamo_labor_platz.py einrichten --platz N` ausführen.")
        return EXIT_EINGABE
    chrome = chrome_finden(umg.chrome_kandidaten)
    if chrome is None:
        umg.ausgabe("Fehler: Chrome wurde nicht gefunden (nur Google Chrome, nicht Edge). Es wurde nichts gestartet.")
        return EXIT_FEHLER
    geometrie = fenster_geometrie(umg.arbeitsbereich())
    if ohne_board:
        _wamo_fenster(umg, chrome, daten, geometrie[0])
        umg.ausgabe(f"Platz {daten['platz']}: W.A.I.S.-Fenster gestartet (ohne Board).")
        return EXIT_OK
    token = _token_holen(umg)
    if token is None:
        return EXIT_EINGABE
    rc = _board_fenster(umg, chrome, daten, token, geometrie)
    if rc == EXIT_OK:
        umg.ausgabe(f"Platz {daten['platz']}: W.A.I.S. links, Board rechts gestartet.")
    return rc
