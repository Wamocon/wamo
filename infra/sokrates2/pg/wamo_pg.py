"""Verbindung zur WAMO-Datenbank für die Dienste auf Sokrates-2, die nicht der Kern sind (Release-Dienst, Freigabe-Dienst des Ankers).

Gemeinsame Grundlage, ohne Wissen über Release oder Freigaben. Sie liest eine root-eigene Zugangsdatei, verbindet sich immer mit
TLS und geprüftem Zertifikat (sslmode=verify-full, Zertifikat der WAMO-CA) und führt Anweisungen in kurzen Transaktionen mit
Zeitgrenzen aus. Fehler kommen als PgFehler mit SQLSTATE und der Angabe, ob ein neuer Versuch sinnvoll ist, nie mit dem Text des
Servers nach außen (er kann Tabellen-, Spalten- und Werte nennen); der Text steht nur im Attribut meldung, das die Adapter für
ihre Entscheidung lesen und nicht weitergeben.

Zugangsdatei (Schlüssel=Wert, eine Zeile je Schlüssel, Kommentarzeilen mit #):
    host, port, dbname, user, password, sslrootcert
Sie muss eine reguläre Datei sein (kein Link), gehört root oder dem erwarteten Benutzer, ist für Gruppe und andere nicht
beschreibbar und für andere nicht lesbar (höchstens 0640). Das Passwort steht nie in einer Ausgabe oder einem Fehler.

Der Treiber ist psycopg 3 (Paket psycopg[binary]); das Modul importiert ihn erst beim Verbinden, damit die Prüfungen der
Zugangsdatei auch ohne Treiber laufen.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

SQLSTATE_RE = re.compile(r"^[0-9A-Z]{5}$")
SCHLUESSEL = ("host", "port", "dbname", "user", "password", "sslrootcert")
ERLAUBT_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,63}$")
VERBINDEN_SECS = 5
ANWEISUNG_MS = 5000
SPERRE_MS = 3000
MAX_ZEILEN = 10000

# Klassen und Fälle, nach denen ein neuer Versuch sinnvoll sein kann: Verbindung (08), Ressourcen (53), Abbruch durch den Betreiber
# (57), Systemfehler (58), Serialisierung und Deadlock (40), Anmeldung (28: zum Beispiel ein rotiertes Passwort), unzulässiger
# Transaktionszustand (25: zum Beispiel ein Server, der gerade nur noch liest) und 55P03 (die eigene lock_timeout-Grenze)
VORUEBERGEHEND_KLASSEN = ("08", "53", "57", "58", "40", "25", "28")
VORUEBERGEHEND_EINZEL = ("55P03",)


class PgFehler(Exception):
    """sqlstate: SQLSTATE oder "" (Verbindung gescheitert, kein Server geantwortet); vorlaeufig: neuer Versuch sinnvoll.
    meldung ist der Text des Servers und nur für Entscheidungen im Adapter gedacht, nie für Aufrufer."""

    def __init__(self, sqlstate: str, meldung: str = "", vorlaeufig: bool = False):
        super().__init__(f"{sqlstate or 'Verbindung'}")
        self.sqlstate, self.meldung, self.vorlaeufig = sqlstate, meldung, vorlaeufig


class ZugangsdateiFehler(Exception):
    """Die Zugangsdatei fehlt, hat falsche Rechte oder falschen Inhalt. Der Text nennt nie das Passwort."""


@dataclass(frozen=True)
class Ziel:
    host: str
    port: int
    dbname: str
    user: str
    password: str = field(repr=False)
    sslrootcert: str = ""

    def __repr__(self) -> str:  # nie das Passwort
        return f"Ziel({self.user}@{self.host}:{self.port}/{self.dbname})"


def zugangsdatei_lesen(pfad: str | os.PathLike, *, erwartete_uids: tuple[int, ...] = (0,)) -> Ziel:
    """Liest und prüft die Zugangsdatei. erwartete_uids: wem sie gehören darf (Vorgabe nur root)."""
    p = Path(pfad)
    # Verzeichnis: Wer dort schreiben darf, tauscht die Datei zwischen Prüfung und Lesen aus
    try:
        sv = os.stat(p.parent)
    except OSError:
        raise ZugangsdateiFehler(f"Ordner der Zugangsdatei {p.name} ist nicht lesbar") from None
    if sv.st_uid not in erwartete_uids or sv.st_mode & 0o022:
        raise ZugangsdateiFehler(f"Ordner der Zugangsdatei {p.name} gehört nicht dem erwarteten Benutzer oder ist beschreibbar")
    # Datei: einmal öffnen (ohne einem Link zu folgen) und alles am geöffneten Deskriptor prüfen und lesen
    try:
        fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        raise ZugangsdateiFehler(f"Zugangsdatei {p.name} fehlt, ist ein Link oder nicht lesbar") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ZugangsdateiFehler(f"Zugangsdatei {p.name} ist keine reguläre Datei")
        if st.st_uid not in erwartete_uids:
            raise ZugangsdateiFehler(f"Zugangsdatei {p.name} gehört nicht dem erwarteten Benutzer")
        if st.st_mode & 0o037:  # Gruppe darf lesen (0640), aber nicht schreiben; andere dürfen nichts
            raise ZugangsdateiFehler(f"Zugangsdatei {p.name} hat zu weite Rechte (erlaubt höchstens 0640)")
        if st.st_size > 16384:
            raise ZugangsdateiFehler(f"Zugangsdatei {p.name} ist unerwartet groß")
        try:
            text = os.read(fd, 16384).decode("utf-8")
        except (OSError, UnicodeDecodeError):
            raise ZugangsdateiFehler(f"Zugangsdatei {p.name} ist nicht lesbar") from None
    finally:
        os.close(fd)
    werte: dict[str, str] = {}
    for nr, zeile in enumerate(text.splitlines(), 1):
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#"):
            continue
        if "=" not in zeile:  # nie den Inhalt der Zeile nennen: Sie könnte ein Passwort ohne Schlüssel sein
            raise ZugangsdateiFehler(f"Zugangsdatei {p.name}: Zeile {nr} hat nicht die Form Schlüssel=Wert")
        k, v = zeile.split("=", 1)
        k = k.strip()
        if k not in SCHLUESSEL or k in werte:
            raise ZugangsdateiFehler(f"Zugangsdatei {p.name}: Zeile {nr} hat einen unbekannten oder doppelten Schlüssel")
        werte[k] = v.strip()
    fehlt = [k for k in SCHLUESSEL if not werte.get(k)]
    if fehlt:
        raise ZugangsdateiFehler(f"Zugangsdatei {p.name}: es fehlt " + ", ".join(fehlt))
    if not ERLAUBT_NAME.fullmatch(werte["user"]) or not ERLAUBT_NAME.fullmatch(werte["dbname"]):
        raise ZugangsdateiFehler(f"Zugangsdatei {p.name}: user oder dbname hat eine unzulässige Form")
    if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", werte["host"]) or not werte["port"].isdigit() or not 1 <= int(werte["port"]) <= 65535:
        raise ZugangsdateiFehler(f"Zugangsdatei {p.name}: host oder port ist ungültig")
    if len(werte["password"]) < 16 or any(c.isspace() for c in werte["password"]):
        raise ZugangsdateiFehler(f"Zugangsdatei {p.name}: das Passwort ist zu kurz oder enthält Leerzeichen")
    _ca_pruefen(werte["sslrootcert"], erwartete_uids)
    return Ziel(werte["host"], int(werte["port"]), werte["dbname"], werte["user"], werte["password"], werte["sslrootcert"])


def _ca_pruefen(pfad: str, erwartete_uids: tuple[int, ...]) -> None:
    """Das Zertifikat der CA ist ein absoluter Pfad zu einer regulären Datei der erwarteten Besitzer, die niemand sonst ändern darf.
    Der Wert "system" (libpq: alle öffentlichen CAs) und relative Pfade sind nicht erlaubt: verify-full gälte sonst für jede CA."""
    p = Path(pfad)
    if not p.is_absolute() or "\0" in pfad:
        raise ZugangsdateiFehler("sslrootcert muss ein absoluter Pfad sein")
    try:
        st = os.lstat(p)
    except OSError:
        raise ZugangsdateiFehler("die CA-Datei fehlt oder ist nicht lesbar") from None
    if not stat.S_ISREG(st.st_mode) or st.st_uid not in erwartete_uids or st.st_mode & 0o022:
        raise ZugangsdateiFehler("die CA-Datei ist kein reguläre Datei der erwarteten Besitzer oder beschreibbar")


def _vorlaeufig(e: BaseException, sqlstate: str) -> bool:
    """Ob ein neuer Versuch sinnvoll ist. Mit SQLSTATE nach Klasse und einigen Einzelfällen, ohne SQLSTATE nur bei Fehlern der
    Verbindung (OSError, Zeitüberschreitung, OperationalError des Treibers), nie bei Programmfehlern (sonst Endlosschleife)."""
    if sqlstate:
        return sqlstate.startswith(VORUEBERGEHEND_KLASSEN) or sqlstate in VORUEBERGEHEND_EINZEL
    return isinstance(e, (OSError, TimeoutError)) or any(c.__name__ == "OperationalError" for c in type(e).__mro__)


def _abbilden(e: BaseException) -> PgFehler:
    sqlstate = getattr(e, "sqlstate", None) or getattr(getattr(e, "diag", None), "sqlstate", None) or ""
    if not isinstance(sqlstate, str) or not SQLSTATE_RE.fullmatch(sqlstate):
        sqlstate = ""
    meldung = str(getattr(getattr(e, "diag", None), "message_primary", "") or "")
    return PgFehler(sqlstate, meldung, _vorlaeufig(e, sqlstate))


class Pg:
    """Kurze Transaktionen. Jede Anweisung (oder jede Gruppe in transaktion) läuft auf einer eigenen Verbindung: Die Dienste fragen
    selten, und eine fehlende Verbindung darf nie hängen bleiben."""

    def __init__(self, ziel: Ziel, *, anwendung: str, verbinden: Callable[..., Any] | None = None):
        if not ERLAUBT_NAME.fullmatch(anwendung):
            raise ValueError("anwendung hat eine unzulässige Form")
        # libpq liest PGSSLMODE, PGSSLROOTCERT, PGSERVICE, PGSSLCERT, PGOPTIONS und weitere aus der Umgebung, soweit der Aufruf sie nicht
        # selbst setzt. Die Dienste haben dort nichts zu suchen: Alle PG*-Variablen werden entfernt, damit keine die festen Werte
        # (TLS, Zertifikat, Zeitgrenzen) umgehen kann.
        for k in [k for k in os.environ if k.startswith("PG")]:
            del os.environ[k]
        self.ziel, self.anwendung, self._verbinden = ziel, anwendung, verbinden

    def _conn(self):
        try:
            if self._verbinden is not None:  # Tests hängen hier ihre eigene Verbindung ein; Fehler werden genauso abgebildet
                return self._verbinden(self.ziel)
            import psycopg  # erst hier: ohne Treiber laufen die Prüfungen der Zugangsdatei trotzdem

            z = self.ziel
            return psycopg.connect(host=z.host, port=z.port, dbname=z.dbname, user=z.user, password=z.password,
                                   sslmode="verify-full", sslrootcert=z.sslrootcert, ssl_min_protocol_version="TLSv1.2",
                                   gssencmode="disable", channel_binding="prefer", connect_timeout=VERBINDEN_SECS,
                                   keepalives=1, keepalives_idle=10, keepalives_interval=5, keepalives_count=3,
                                   tcp_user_timeout=15000, application_name=self.anwendung, autocommit=False,
                                   options=f"-c statement_timeout={ANWEISUNG_MS} -c lock_timeout={SPERRE_MS} "
                                           "-c idle_in_transaction_session_timeout=10000")
        except Exception as e:  # noqa: BLE001  auch ein fehlender Treiber (ImportError) wird zu einem neutralen Fehler
            raise _abbilden(e) from None

    def transaktion(self, arbeit: Callable[[Any], Any]) -> Any:
        """Führt arbeit(cursor) in einer Transaktion aus (Commit bei Erfolg, sonst Rollback) und gibt ihr Ergebnis zurück.

        Achtung bei Wiederholungen: Scheitert die Verbindung genau beim Commit (SQLSTATE 08xxx), kann der Server den Commit schon
        ausgeführt haben. Wer nach einem vorläufigen Fehler wiederholt, muss das vertragen (Ereignisse sind nur anfügbar und würden
        doppelt stehen; Anfragen und Freigaben sind durch Eindeutigkeit geschützt)."""
        c = self._conn()
        try:
            with c.cursor() as cur:
                ergebnis = arbeit(cur)
            c.commit()
            return ergebnis
        except PgFehler:
            raise
        except Exception as e:  # noqa: BLE001
            try:
                c.rollback()
            except Exception:  # noqa: BLE001
                pass
            raise _abbilden(e) from None
        finally:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass

    def zeilen(self, sql: str, parameter: tuple = ()) -> list[tuple]:
        def arbeit(cur):
            cur.execute(sql, parameter)
            return cur.fetchmany(MAX_ZEILEN) if cur.description else []

        return self.transaktion(arbeit)

    def eine(self, sql: str, parameter: tuple = ()) -> tuple | None:
        z = self.zeilen(sql, parameter)
        return z[0] if z else None
