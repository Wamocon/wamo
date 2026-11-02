"""Schutzschicht: Welche geänderten Pfade berühren, was nur ein Mensch ändern darf (Konzept E.2, Schritt 3).

Reine Logik ohne Ein- und Ausgabe. Der Anker bildet die Pfadliste selbst aus den Git-Objekten der Quelle (git diff --name-status --no-renames -z, alter und neuer Pfad
zählen, auch Löschen und Moduswechsel) und fragt dann hier, in welche Klasse die Änderung fällt:

  gesperrt  berührt den Anker selbst. Läuft nie über den Release-Weg, nur von Hand.
  sonder    berührt Wächter, Anmeldung, Schlüssel, Geräte, den Release-Weg, die Infrastruktur, die Datenbank oder Anweisungen an Agenten.
  normal    alles andere.

Pfade werden vor dem Vergleich normalisiert (Unicode NFC, Groß- und Kleinschreibung gleich). Ein Pfad mit Steuerzeichen, Rückwärtsstrich, "." oder ".." als Teil
oder ohne gültiges UTF-8 gilt als Treffer der Klasse gesperrt: Er kann in einem ehrlichen Repository nicht vorkommen.

Muster: "*" steht für beliebige Zeichen außer "/", "**" auch über "/" hinweg. Ein Muster ohne "/" gilt für den Dateinamen in jedem Ordner. Ein Muster mit "**/" am
Anfang gilt in jedem Ordner. Sonst gilt es ab der Wurzel des Repositorys. Die Liste hier ist der Boden; /etc/wamo-anker/schutzpfade.txt (root) kann sie nur erweitern.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

GESPERRT = (
    "infra/sokrates2/anker/**",
    "infra/sokrates2/anker-*",
    "infra/sokrates2/anker_*",
)

SONDER = (
    # Wächter und Aufträge
    "voice-core/dev_guard.py", "voice-core/dev_agent.py", "voice-core/werkzeugkarte.py", "infra/sokrates2/wamo_job_dispatch.py", "wamo-job*",
    "abschottung.py", "**/claude-code/**",
    # Anmeldung
    "auth_gate.py", "kern_dienst.py", "wamo_api.py", "wamo_tls.py", "memory.py", "sitzungen.py",
    # Schlüssel
    "schluessel*.py", "modellzugang*.py", "ui/src/components/ModellZugang*", "ui/src/lib/modellzugang*",
    # Geräte und PC
    "pc_*.py", "geraet*_api.py", "geraete_*.py", "host/**", "geraete/**", "desktop/**",
    # Release-Weg
    "infra/sokrates2/release/**", "ui/src/components/kommando/Release*", "ui/src/components/Freigaben.tsx", "LoginGate.tsx",
    "ui/src/lib/supabase.ts", "ui/src/lib/anmeldung.ts", "ui/next.config.ts",
    # Infrastruktur
    "infra/sokrates2/**", ".github/**", "scripts/**", "Dockerfile*",
    # Daten
    "supabase/migrations/**", "supabase/tests/**",
    # Anweisungen an Agenten und Paketskripte
    "CLAUDE.md", "AGENTS.md", ".claude/**", ".mcp.json", "package.json",
)

TREFFER_MAX = 200   # so viele Pfade kann die Datenbank halten (release_anfragen.schutz_treffer)
PFAD_MAX = 400
STATUS_RE = re.compile(r"[AMDT]")   # mit --no-renames und ohne Kopien gibt git nur diese vier aus; alles andere ist unbekannt und gilt als Treffer


def _glob_zu_regex(muster: str) -> "re.Pattern[str]":
    m = unicodedata.normalize("NFC", muster).casefold()
    anker = "" if "/" in m and not m.startswith("**/") else "(?:.*/)?"   # ohne "/" oder mit "**/": in jedem Ordner
    if m.startswith("**/"):
        m = m[3:]
    teile = []
    i = 0
    while i < len(m):
        if m.startswith("**", i):
            teile.append(".*")
            i += 2
        elif m[i] == "*":
            teile.append("[^/]*")
            i += 1
        elif m[i] == "?":
            teile.append("[^/]")
            i += 1
        else:
            teile.append(re.escape(m[i]))
            i += 1
    return re.compile(anker + "".join(teile))


def _kompiliere(muster: tuple[str, ...]) -> list["re.Pattern[str]"]:
    return [_glob_zu_regex(m) for m in muster]


_GESPERRT_RE = _kompiliere(GESPERRT)
_SONDER_RE = _kompiliere(SONDER)


def pfad_normalisieren(roh: bytes) -> str | None:
    """Der normalisierte Pfad oder None, wenn er in einem ehrlichen Repository nicht vorkommen kann."""
    if not roh or len(roh) > PFAD_MAX:
        return None
    try:
        text = roh.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if any(ord(c) < 32 or ord(c) == 127 or c == "\\" for c in text):
        return None
    teile = text.split("/")
    if any(t in ("", ".", "..") for t in teile):
        return None
    return unicodedata.normalize("NFC", text).casefold()


def aenderungen_lesen(roh: bytes) -> list[tuple[str, bytes]]:
    """Zerlegt die Ausgabe von git diff --name-status --no-renames -z in (Status, Pfad). Wirft ValueError bei unerwarteter Form."""
    teile = roh.split(b"\0")
    if teile and teile[-1] == b"":
        teile.pop()
    if len(teile) % 2:
        raise ValueError("name_status_form")
    aus = []
    for i in range(0, len(teile), 2):
        status = teile[i].decode("ascii", "replace")
        aus.append((status, teile[i + 1]))
    return aus


def klassifizieren(aenderungen: list[tuple[str, bytes]], zusatz_sonder: tuple[str, ...] = ()) -> dict:
    """aenderungen: [(Status, Pfad als Bytes)]. Ergebnis: klasse, treffer (sortierte, eindeutige Pfade, höchstens TREFFER_MAX), treffer_anzahl, treffer_sha.

    treffer_sha ist die Prüfsumme über alle Treffer (nicht nur die gekappten), damit zwei Läufe über dasselbe Diff dasselbe Ergebnis zeigen."""
    extra = _kompiliere(zusatz_sonder)
    gesperrt: set[str] = set()
    sonder: set[str] = set()
    for status, roh in aenderungen:
        pfad = pfad_normalisieren(roh)
        anzeige = roh.decode("utf-8", "replace")[:PFAD_MAX]
        if pfad is None or not STATUS_RE.fullmatch(status):
            gesperrt.add(re.sub(r"[^\x20-\x7e]", "?", anzeige))
        elif any(r.fullmatch(pfad) for r in _GESPERRT_RE):
            gesperrt.add(anzeige)
        elif any(r.fullmatch(pfad) for r in _SONDER_RE + extra):
            sonder.add(anzeige)
    klasse = "gesperrt" if gesperrt else "sonder" if sonder else "normal"
    treffer = sorted(gesperrt | sonder)
    sha = hashlib.sha256("\n".join(treffer).encode("utf-8", "replace")).hexdigest()
    return {"klasse": klasse, "treffer": treffer[:TREFFER_MAX], "treffer_anzahl": min(len(treffer), TREFFER_MAX), "treffer_sha": sha}
