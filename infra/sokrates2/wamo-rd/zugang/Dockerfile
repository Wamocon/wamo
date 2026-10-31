# Zugangsdienst des Remote-Desktop-Gateways (docs/remote-desktop.md, Stufe 3). Nur Standardbibliothek, keine Pakete.
# Gebaut von wamo-rd-einrichten.sh (Schritt bilder) zu wamo-rd-zugang:1; nie aus dem Netz gezogen (pull_policy: never).
# Basis über den Digest des Multi-Arch-Index festgehalten (arm64 enthalten, geprüft am 31.10.2026 mit docker buildx imagetools inspect).
# Aktualisieren: neuen Digest holen, hier eintragen, wamo-rd-einrichten.sh bilder start, Rauchtest; Rückweg ist der alte Digest.
FROM python:3.13-alpine@sha256:7984cf56ceaddebe4fefeb75d64000b9f45d8b77b11a09b24524da6cdc3af0c8
COPY wamo_rd_zugang.py /app/wamo_rd_zugang.py
# Benutzer, Dateisystem und Rechte setzt die Compose-Datei (user, read_only, cap_drop)
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-I", "/app/wamo_rd_zugang.py"]
