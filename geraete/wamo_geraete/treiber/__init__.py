"""Treiber für die Bürogeräte. Jeder Treiber spricht genau ein Protokoll mit festen Pfaden und Aktionen; freie
Durchreichung (beliebige SOAP-Aktion, beliebiger IPP-Pfad, Shell) gibt es nicht."""

from __future__ import annotations


class NichtEingerichtet(Exception):
    """Gerät ist nicht vollständig eingerichtet (Adresse, Zugangsdaten oder Kopplung fehlen)."""


class GeraetFehler(Exception):
    """Gerät antwortet nicht oder nicht wie erwartet (Text für Menschen, ohne Gerätedaten)."""


class Abgelehnt(Exception):
    """Die Anfrage verletzt eine Regel (Grenze, unbekannte Aktion, zu großes Dokument)."""
