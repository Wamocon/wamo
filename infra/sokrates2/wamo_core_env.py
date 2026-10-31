#!/usr/bin/python3 -I
"""Geheimnisse und Einstellungen des WAMO-Kerns auf Sokrates-2: /etc/wamo/core.env (root:wamo-core 0640).

Installiert als /usr/local/sbin/wamo-core-env (root, 0755) durch wamo-core-einrichten.sh. Aufruf nur mit sudo.

  wamo-core-env setzen        liest KEY=VALUE-Zeilen von stdin und übernimmt sie (nur erlaubte Schlüssel)
  wamo-core-env loeschen K..  entfernt Schlüssel
  wamo-core-env liste         zeigt Schlüsselnamen und ob ein Wert gesetzt ist, nie Werte
  wamo-core-env pruefen       Exit 0, wenn alle Pflichtschlüssel gesetzt sind

Werte kommen nur über stdin (vom PC: scripts/wamo_kern_sokrates2.py per ssh), nie über die Befehlszeile, und werden
nie ausgegeben. Die Datei wird atomar ersetzt (neue Datei im selben Ordner, fsync, rename). Werte stehen in
einfachen Anführungszeichen; systemd liest sie dann wörtlich (EnvironmentFile, ohne Escape-Regeln). Einfache
Anführungszeichen, Zeilenumbrüche und Steuerzeichen in Werten werden abgelehnt.

Was die Unit wamo-core.service selbst festlegt (Betriebsart, Socket-Pfade, SUPABASE_URL, CA-Datei, erlaubte
Herkunft, Entwicklung aus, Log-Stufe), lässt sich hier nicht setzen: EnvironmentFile hätte Vorrang vor Environment=.
"""

from __future__ import annotations

import grp
import os
import re
import sys
import tempfile

DATEI = "/etc/wamo/core.env"
GRUPPE = "wamo-core"
MAX_WERT = 4096
MAX_ZEILEN = 200

PFLICHT = ("SONIOX_API_KEY", "SUPABASE_PUBLISHABLE_KEY", "WAMO_DATABASE_URL")
ERLAUBT = set(PFLICHT) | {
    # Geheimnisse (WAMO_PC_HOST_FREIGABE: zweites Geheimnis für Freigaben kritischer PC-Aktionen)
    "ANTHROPIC_WORKSPACE_ID", "WAMO_PC_HOST_TOKEN", "WAMO_PC_HOST_FREIGABE", "LITELLM_API_KEY",
    # Modellzugang je Profil (voice-core/modellzugang.py): ANTHROPIC_API_KEY ist der Schlüssel einer Person und gilt
    # nur für das Profil in WAMO_CLAUDE_SCHLUESSEL_PROFIL, sonst gar nicht; WAMO_SCHLUESSEL_KEK verschlüsselt die
    # eigenen Schlüssel der Profile in der Datenbank. Ohne beide laufen alle Profile nur mit Hausmodellen.
    "ANTHROPIC_API_KEY", "WAMO_CLAUDE_SCHLUESSEL_PROFIL", "WAMO_SCHLUESSEL_KEK", "WAMO_SCHLUESSEL_KEK_ALT",
    # Bindung des PCs am Tunnel (genau eine user_id)
    "WAMO_PC_HOST_PROFILE",
    # Einstellungen wie in der .env des PCs
    "WAMO_DB_SCHEMA", "SONIOX_STT_URL", "SONIOX_TTS_URL", "SONIOX_VOICE", "SONIOX_TERMS", "CLAUDE_MODEL_FAST",
    "VISION_MODEL", "MIN_INTERRUPT_WORDS", "WAMO_CITY", "WAMO_TIMEZONE", "WAMO_GREETING", "WAMO_MAX_SITZUNG_MIN",
    "LEARN_EVERY_USER_TURNS", "SMART_TURN_STOP_SECS", "TURN_INCOMPLETE_SHORT_SECS", "TURN_INCOMPLETE_LONG_SECS",
    "SOFT_INTERRUPT_MAX_WORDS", "SOFT_INTERRUPT_LAG_SECS", "SUMMARY_TRIGGER_TOKENS", "SUMMARY_TARGET_TOKENS",
    # Latenz und Unterbrechungen (voice-core/latenz.py, soniox_tts.py, soft_interrupt.py): Log je Zug ohne Inhalte,
    # Stille für das Ende des Redebeitrags, Startschwelle der Sprachausgabe, Echo-Filter, Prompt-Cache vorwärmen
    "WAMO_LATENZ_LOG", "WAMO_VAD_STOP_SECS", "WAMO_TTS_START_ZEICHEN", "WAMO_ECHO_FILTER", "WAMO_CACHE_VORWAERMEN",
    # Sprachverhalten (voice-core/soft_interrupt.py, wartesatz.py): Stopp an der Wortgrenze, Dämpfen, Rückmeldelaute, Wartesatz
    "WAMO_STOPP_GRENZE", "WAMO_DUCKEN", "WAMO_RUECKMELDUNG", "WAMO_WARTESATZ", "WAMO_WARTESATZ_SECS",
    # Sprachpakete A und B (voice-core/soniox_tts.py, persona.py, bot.py, learning.py, soft_interrupt.py, ja_waechter.py):
    # Tempo und Pausen der Stimme, Leerzeichen am Satzende, Persona, Pausen-Texte, Lernen, Rückmeldelaut bei Werkzeug, Ja-Wächter
    "SONIOX_SPEED", "SONIOX_REDUCE_SILENCE", "WAMO_TTS_SATZLEERZEICHEN", "WAMO_PERSONA_RUECKFRAGEN", "WAMO_PERSONA_BROWSER", "WAMO_PERSONA_PC_ORDNER",
    "WAMO_PAUSEN_PROMPT", "WAMO_LERNEN_OHNE_MOMENT", "WAMO_RUECKMELDUNG_WERKZEUG", "WAMO_JA_WAECHTER", "WAMO_ECHO_MODUS",
    # Fehlerbehebungen aus der Sitzung vom 31.10.2026 (voice-core/werkzeug_wache.py), Standard an
    "WAMO_ABBRUCH_HINWEIS", "WAMO_WERKZEUG_DOPPELT", "WAMO_LERNEN_HAUS_SECS", "WAMO_MARKER_FALLBACK",
    "LITELLM_BASE_URL", "WAMO_SUCHE", "WAMO_SEARXNG_URL", "WAMO_LITELLM_EXTRA",
    # Eine Sitzung je Profil und Höchstalter der Anmeldung (bot.py, auth_gate.py)
    "WAMO_SITZUNG_EINZELN", "WAMO_ANMELDUNG_MAX_STUNDEN",
}
ERLAUBT_PRAEFIX = ("WAMO_LLM_",)  # Anbieter-Fabrik (Phase P4)
SCHLUESSEL_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
# einfache Anführungszeichen, Backslash und Steuerzeichen (Tab erlaubt). systemd liest '...' laut systemd.exec(5)
# wörtlich; der Backslash ist trotzdem gesperrt, damit kein Wert je ein Zeilenende maskieren kann.
UNGUELTIG = re.compile(r"['\\\x00-\x08\x0a-\x1f\x7f]")


def erlaubt(name: str) -> bool:
    return bool(SCHLUESSEL_RE.fullmatch(name)) and (name in ERLAUBT or name.startswith(ERLAUBT_PRAEFIX))


def lesen() -> dict[str, str]:
    werte: dict[str, str] = {}
    try:
        with open(DATEI, encoding="utf-8") as f:
            for zeile in f:
                zeile = zeile.rstrip("\n")
                if not zeile or zeile.startswith("#") or "=" not in zeile:
                    continue
                name, wert = zeile.split("=", 1)
                if len(wert) >= 2 and wert[0] == "'" and wert[-1] == "'":
                    wert = wert[1:-1]
                werte[name] = wert
    except FileNotFoundError:
        pass
    return werte


def schreiben(werte: dict[str, str]) -> None:
    gid = grp.getgrnam(GRUPPE).gr_gid
    ordner = os.path.dirname(DATEI)
    fd, tmp = tempfile.mkstemp(prefix=".core.env.", dir=ordner)
    try:
        os.fchown(fd, 0, gid)
        os.fchmod(fd, 0o640)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("# WAMO-Kern (wamo-core.service). Nur über /usr/local/sbin/wamo-core-env ändern, nie Werte ausgeben.\n")
            for name in sorted(werte):
                f.write(f"{name}='{werte[name]}'\n")
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp, DATEI)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def setzen() -> int:
    eingabe = sys.stdin.read(MAX_ZEILEN * (MAX_WERT + 80))
    neu: dict[str, str] = {}
    for nummer, zeile in enumerate(eingabe.splitlines(), 1):
        if nummer > MAX_ZEILEN:
            print("Zu viele Zeilen", file=sys.stderr)
            return 2
        if not zeile.strip() or zeile.lstrip().startswith("#"):
            continue
        if "=" not in zeile:
            print(f"Zeile {nummer}: kein KEY=VALUE", file=sys.stderr)
            return 2
        name, wert = zeile.split("=", 1)
        name = name.strip()
        if not erlaubt(name):
            print(f"Zeile {nummer}: Schlüssel {name[:64]!r} ist nicht erlaubt", file=sys.stderr)
            return 2
        if len(wert) > MAX_WERT or UNGUELTIG.search(wert):
            print(f"Zeile {nummer}: Wert für {name} ist zu lang oder enthält unzulässige Zeichen", file=sys.stderr)
            return 2
        neu[name] = wert
    if not neu:
        print("Keine Werte erhalten", file=sys.stderr)
        return 2
    werte = lesen()
    werte.update(neu)
    schreiben(werte)
    print("Übernommen: " + ", ".join(sorted(neu)) + " (Werte nicht angezeigt)")
    return 0


def loeschen(namen: list[str]) -> int:
    werte = lesen()
    weg = [n for n in namen if n in werte]
    for n in weg:
        del werte[n]
    schreiben(werte)
    print("Entfernt: " + (", ".join(weg) or "nichts"))
    return 0


def liste() -> int:
    werte = lesen()
    for name in sorted(werte):
        print(f"{name}: {'gesetzt' if werte[name] else 'leer'}")
    fehlend = [n for n in PFLICHT if not werte.get(n)]
    if fehlend:
        print("Pflicht, fehlt: " + ", ".join(fehlend))
    return 0


def pruefen() -> int:
    werte = lesen()
    fehlend = [n for n in PFLICHT if not werte.get(n)]
    if fehlend:
        print("Fehlt: " + ", ".join(fehlend), file=sys.stderr)
        return 1
    return 0


def main(argv: list[str]) -> int:
    if os.geteuid() != 0:
        print("Bitte mit sudo ausführen.", file=sys.stderr)
        return 1
    befehl = argv[1] if len(argv) > 1 else ""
    if befehl == "setzen" and len(argv) == 2:
        return setzen()
    if befehl == "loeschen" and len(argv) > 2:
        return loeschen(argv[2:])
    if befehl == "liste" and len(argv) == 2:
        return liste()
    if befehl == "pruefen" and len(argv) == 2:
        return pruefen()
    print("Aufruf: wamo-core-env setzen | loeschen KEY... | liste | pruefen", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
