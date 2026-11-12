# WAMO Labor-Dienst (Test-Jira des Testlabors). Das Image wird auf dem Server aus diesen Quellen gebaut.
#
# D11: Das Basisimage hat keinen Standardwert (kein wanderndes Tag): Es wird beim Bau mit festem Digest übergeben, sonst bricht der Bau ab:
#   docker build --build-arg BASIS=python:3.11-slim@sha256:<Digest> -t wamo-labor-dienst:<Stand> .
# Laufzeit (Empfehlung, D11): --read-only, --cap-drop ALL, --security-opt no-new-privileges, --memory 256m, --cpus 0.5,
# ein beschreibbares Volume nur auf /data, die Tokendateien als Secrets (Pfade in WAMO_LABOR_DIENST_*_TOKEN_DATEI), kein Weg nach außen (D2).
ARG BASIS
FROM ${BASIS}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

COPY wamo_labor_dienst ./wamo_labor_dienst
# Bytecode beim Bau erzeugen (zur Laufzeit ist das Dateisystem schreibgeschützt), Nutzer ohne root, einziger beschreibbarer Ort ist /data
RUN python -m compileall -q wamo_labor_dienst \
    && groupadd --system --gid 10001 wamo-labor \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin wamo-labor \
    && mkdir /data \
    && chown 10001:10001 /data

ENV WAMO_LABOR_DIENST_DATEN=/data \
    WAMO_LABOR_DIENST_HOST=0.0.0.0 \
    WAMO_LABOR_DIENST_PORT=8080

USER 10001:10001
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 CMD ["python", "-m", "wamo_labor_dienst", "gesundheit"]
CMD ["python", "-m", "wamo_labor_dienst"]
