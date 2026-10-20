"""Vertrauen in die interne WAMO-CA für Verbindungen zum eigenen Supabase-Stack auf Sokrates-2.

WAMO_CA_FILE zeigt auf das öffentliche Root-Zertifikat der CA (im Repo infra/sokrates2/wamo-ca.crt). Es gilt
nur für Verbindungen zum WAMO-Stack: JWKS und GoTrue (auth_gate.py) sowie Postgres über Supavisor
(memory.py). Alle anderen Verbindungen (Claude, Soniox, Websuche) prüfen weiter nur gegen die üblichen
Zertifikate.

  https_context()     certifi plus WAMO-CA; None ohne WAMO_CA_FILE (dann gilt der Standard der Bibliothek).
  postgres_ssl(dsn)   Mit WAMO_CA_FILE gilt für die Datenbank immer geprüftes TLS: Ohne sslmode in der DSN wird
                      verify-full angenommen, verify-ca prüft nur die Kette, schwächere Modi (disable, allow,
                      prefer, require) sind ein Konfigurationsfehler. Der Kontext vertraut nur der WAMO-CA.
                      None ohne WAMO_CA_FILE oder bei eigenem sslrootcert in der DSN (dann wertet asyncpg die
                      DSN selbst aus).

Fehlkonfigurationen (unlesbarer WAMO_CA_FILE, zu schwacher sslmode) sind TlsConfigError, nie ein stiller Rückfall
auf unverschlüsselt oder ungeprüft. memory.py macht daraus DatabaseUnavailable (fail-closed).
"""

from __future__ import annotations

import os
import ssl
from urllib.parse import parse_qs, urlsplit

CA_ENV = "WAMO_CA_FILE"
VERIFY_MODES = ("verify-ca", "verify-full")


class TlsConfigError(ValueError):
    """TLS zum WAMO-Stack ist falsch eingestellt (Meldung ohne Geheimnisse)."""


class CaFileError(TlsConfigError):
    """WAMO_CA_FILE ist gesetzt, zeigt aber auf keine lesbare PEM-Datei mit Zertifikat."""


def ca_file() -> str | None:
    """Pfad aus WAMO_CA_FILE oder None, wenn nicht gesetzt."""
    value = (os.getenv(CA_ENV) or "").strip().strip('"').strip("'")
    return value or None


def _load_ca(ctx: ssl.SSLContext, path: str) -> None:
    try:
        ctx.load_verify_locations(cafile=path)
    except (OSError, ssl.SSLError) as e:
        raise CaFileError(f"{CA_ENV} zeigt auf keine lesbare Zertifikatsdatei ({type(e).__name__})") from e


def https_context() -> ssl.SSLContext | None:
    """SSL-Kontext für HTTPS zum WAMO-Stack: öffentliche Zertifikate (certifi) plus WAMO-CA."""
    path = ca_file()
    if path is None:
        return None
    try:
        import certifi

        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:  # certifi kommt mit httpx, fehlt praktisch nie
        ctx = ssl.create_default_context()
    _load_ca(ctx, path)
    return ctx


def sslmode(dsn: str | None) -> str | None:
    """sslmode aus der DSN, klein und mit Bindestrich (verify_full -> verify-full); letzter Wert gewinnt wie bei
    asyncpg. None, wenn die DSN keinen sslmode nennt."""
    if not dsn:
        return None
    values = parse_qs(urlsplit(dsn).query).get("sslmode")
    return values[-1].strip().lower().replace("_", "-") if values else None


def postgres_ssl(dsn: str | None) -> ssl.SSLContext | None:
    """SSL-Kontext für asyncpg, der nur der WAMO-CA vertraut; Hostname geprüft bei verify-full."""
    if not dsn:
        return None
    path = ca_file()
    if path is None:
        return None  # asyncpg wertet die DSN aus; verify-* sucht dann PGSSLROOTCERT oder ~/.postgresql/root.crt
    mode = sslmode(dsn) or "verify-full"
    if mode not in VERIFY_MODES:
        raise TlsConfigError(
            f"{CA_ENV} ist gesetzt, die Datenbank-DSN verlangt aber sslmode={mode}; "
            "erlaubt sind verify-full oder verify-ca"
        )
    if "sslrootcert" in parse_qs(urlsplit(dsn).query):
        return None  # eigener Vertrauensanker in der DSN, asyncpg prüft damit
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # Zertifikatsprüfung Pflicht, mindestens TLS 1.2
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = mode == "verify-full"
    _load_ca(ctx, path)
    return ctx
