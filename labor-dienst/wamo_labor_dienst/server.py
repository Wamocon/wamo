"""Einstellungen des Webservers (uvicorn) und die Lese-Frist für Verbindungen.

uvicorn hat keine Frist für eine Anfrage, die nicht ankommt: Eine Verbindung, die nur tröpfelt oder schweigt, bliebe offen. FristH11Protocol
schließt jede Verbindung, deren Kopfzeilen nicht binnen lese_frist Sekunden vollständig sind (ab dem Verbindungsaufbau und nach jeder Antwort
für die nächste Anfrage). Für den Inhalt gilt die Frist der Anwendung (sicherheit.KOERPER_FRIST_SECS). limit_concurrency begrenzt die Zahl der
Verbindungen, darüber antwortet uvicorn 503. Die Klasse baut auf uvicorn.protocols.http.h11_impl.H11Protocol (Version in requirements.txt gepinnt).
"""
from __future__ import annotations

from uvicorn.protocols.http.h11_impl import H11Protocol

LIMIT_VERBINDUNGEN = 64  # 8 Plätze mit je wenigen Verbindungen, dazu Gesundheitsprobe und Betreuer
LESE_FRIST_SECS = 10.0


class FristH11Protocol(H11Protocol):
    lese_frist = LESE_FRIST_SECS

    def _frist_setzen(self) -> None:
        self._frist_aufheben()
        self._lese_frist_handle = self.loop.call_later(self.lese_frist, self._frist_abgelaufen)

    def _frist_aufheben(self) -> None:
        handle = getattr(self, "_lese_frist_handle", None)
        if handle is not None:
            handle.cancel()
            self._lese_frist_handle = None

    def _frist_abgelaufen(self) -> None:
        self._lese_frist_handle = None
        if not self.transport.is_closing():
            self.transport.close()

    def connection_made(self, transport) -> None:  # type: ignore[override]
        super().connection_made(transport)
        self._lese_frist_handle = None
        self._frist_setzen()

    def data_received(self, data: bytes) -> None:
        vorher = self.cycle
        super().data_received(data)
        if self.cycle is not vorher:  # die Kopfzeilen einer neuen Anfrage sind vollständig
            self._frist_aufheben()

    def on_response_complete(self) -> None:
        vorher = self.cycle
        super().on_response_complete()
        if self.cycle is vorher and not self.transport.is_closing():  # keine Anfrage in der Leitung: die nächste hat wieder die Frist
            self._frist_setzen()

    def connection_lost(self, exc) -> None:  # type: ignore[override]
        self._frist_aufheben()
        super().connection_lost(exc)


def uvicorn_einstellungen() -> dict:
    """Einstellungen des Webservers (auch die Integrationstests starten mit ihnen): h11 mit Lese-Frist, kein Zugriffsprotokoll, kein Server-Kopf,
    keine Weiterleitungsköpfe, höchstens LIMIT_VERBINDUNGEN Verbindungen. Köpfe bis 128 KB werden gelesen (der Dienst lehnt über 16 KB mit 431 und
    einer Fehlerantwort des Vertrags ab, erst darüber lehnt h11 mit 400 ab, das einen bekannten Randfall von uvicorn auslöst: eine Ausnahme im
    Ereignisschleifen-Rückruf, der Dienst läuft weiter)."""
    return {"log_level": "warning", "access_log": False, "http": FristH11Protocol, "server_header": False, "proxy_headers": False,
            "h11_max_incomplete_event_size": 128 * 1024, "timeout_keep_alive": 5, "limit_concurrency": LIMIT_VERBINDUNGEN}
