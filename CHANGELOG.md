# Änderungsprotokoll

Alle nennenswerten Änderungen an WAMO stehen in dieser Datei. Das Format folgt [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
die Versionen folgen [Semantic Versioning](https://semver.org/lang/de/) in der Schreibweise X.Y.Z (Tags heißen vX.Y.Z).
Die Abschnitte der Versionen entstehen mit `scripts/wamo_version.py` aus den Commits, der Abschnitt Unveröffentlicht bleibt leer.

## [Unveröffentlicht]

## [0.1.0] - 2026-10-22

Erstes Release: der Stand, auf dem alle Pakete (`ui`, `desktop`, `voice-core`, `host`, `geraete`) stehen.

### Hinzugefügt

- Voice-Core auf Pipecat mit Spracherkennung und Sprachausgabe über Soniox und dem Gespräch mit Claude, Unterbrechungen
  im Livegespräch, Mehrprofil-Anmeldung mit Token und zweitem Faktor.
- Oberfläche im WMC-Design (hell und dunkel) mit Live-Untertiteln, Stimmlinie, Werkzeug-Zeitleiste, Canvas und Freigabe-Karten;
  Betrieb als Desktop-App (Electron) oder im Browser als Container auf Sokrates-2.
- PC-Host als MCP-Werkzeugserver für Windows mit Risikoklassen, Audit-Log und Freigabeweg für kritische Aktionen.
- Mehrere Profile mit den Rollen admin und mitglied, persönliches und gemeinsames Gedächtnis mit Freigabe durch Admins.
- Modellzugang je Profil (eigener Claude-Schlüssel oder nur Hausmodelle), Modellwahl durch den Boss, Claude-Code-Token
  und Anzeige des Kontingents.
- Entwicklungsaufträge in einer Sandbox auf Sokrates-2 mit ECC und REA.
- Geräte und Tunnel je Laptop, Teilen von Inhalten.
- Eigener Supabase-Stack auf Sokrates-2 mit den Migrationen bis 0013 und der Prüfung der Rechte nach jedem Anwenden.
- Einstellungsseite der Oberfläche mit Konto, Modellzugang, Verbrauch, Sprache, Darstellung, Geräten, Teilen und Info;
  der Claude-Code-Token lässt sich dort auch ohne laufendes Gespräch hinterlegen, ein Schlüssel im Gesagten wird entfernt.
- Stimme und Sprachverhalten: Stopp an der hörbaren Wortgrenze, Dämpfen beim Einwurf, Erkennung von Rückmeldelauten,
  Echo-Modus und ein Messweg mit Hörproben (`scripts/wamo_stimme.py`).
- Grundlagen der Selbstüberarbeitung (Konzept in `docs/selbstueberarbeitung.md`): Notanker, Gerüst des Release-Dienstes,
  Migration 0013 für Anfragen und Freigaben mit Vier-Augen-Regel, Versionsskript sowie die Oberfläche in zwei Farben
  (blau und grün) mit Umschalten ohne Lücke und Rückweg. Notanker und Release-Dienst sind noch nicht in Betrieb.

[Unveröffentlicht]: https://github.com/Wamocon/wamo/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Wamocon/wamo/releases/tag/v0.1.0
