# check=skip=SecretsUsedInArgOrEnv
# WAMO-Oberfläche (Next.js 16) als Container auf Sokrates-2. Gebaut von infra/sokrates2/wamo-ui-einrichten.sh:
#   docker build --platform linux/arm64 -f wamo-ui/Dockerfile --build-arg NEXT_PUBLIC_... ~/wamo-infra/ui
# Der Kontext ist der Ordner ui/ (ohne node_modules, .next und .env*; ui/.dockerignore).
#
# Die NEXT_PUBLIC_*-Werte stehen nach dem Build im JavaScript, das jeder Browser lädt. Hier gehören deshalb nur
# öffentliche Werte hinein (Adressen, Publishable Key), nie ein Geheimnis und nie der service_role-Schlüssel.
#
# Basisimage: Node 22 (Bookworm, slim) über den Digest des Multi-Arch-Index festgehalten, arm64 auf Sokrates-2.
# Aktualisieren: neuen Digest holen (docker buildx imagetools inspect node:22-bookworm-slim) und hier eintragen.
ARG NODE_IMAGE=node:22-bookworm-slim@sha256:3ed98443660ff48413b5af19ecefce3b9d50845a901a43420d47cc63a9fddbe9

# ---- 1. Abhängigkeiten (aus package-lock.json, reproduzierbar)
FROM ${NODE_IMAGE} AS abhaengigkeiten
WORKDIR /app
COPY package.json package-lock.json ./
# --ignore-scripts: keine Install-Skripte von Paketen mit Netz und root im Bau (die Werkzeuge brauchen keine)
RUN npm ci --no-audit --no-fund --ignore-scripts

# ---- 2. Bauen (next build, Standalone-Ausgabe)
FROM ${NODE_IMAGE} AS bauen
WORKDIR /app
ARG NEXT_PUBLIC_SUPABASE_URL
ARG NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
ARG NEXT_PUBLIC_WAMO_KERN_BUERO
ARG NEXT_PUBLIC_WAMO_ANMELDUNG_MAX_STUNDEN_BROWSER=""
ENV NEXT_PUBLIC_SUPABASE_URL=${NEXT_PUBLIC_SUPABASE_URL} \
    NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=${NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY} \
    NEXT_PUBLIC_WAMO_KERN_BUERO=${NEXT_PUBLIC_WAMO_KERN_BUERO} \
    NEXT_PUBLIC_WAMO_ANMELDUNG_MAX_STUNDEN_BROWSER=${NEXT_PUBLIC_WAMO_ANMELDUNG_MAX_STUNDEN_BROWSER} \
    NEXT_PUBLIC_WAMO_NUR_BUERO=ja \
    WAMO_UI_STANDALONE=1 \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=abhaengigkeiten /app/node_modules ./node_modules
COPY . .
# Ohne diese Adressen entstünde eine CSP ohne Anmeldung und Kern; lieber früh abbrechen
RUN test -n "$NEXT_PUBLIC_SUPABASE_URL" && test -n "$NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY" && test -n "$NEXT_PUBLIC_WAMO_KERN_BUERO" \
 && npm run build

# ---- 3. Laufzeit: nur der Standalone-Server, Benutzer node (1000), kein Paketmanager im Betrieb nötig
FROM ${NODE_IMAGE} AS laufzeit
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0 \
    NEXT_TELEMETRY_DISABLED=1
WORKDIR /app
COPY --from=bauen --chown=root:root /app/.next/standalone ./
COPY --from=bauen --chown=root:root /app/.next/static ./.next/static
COPY --from=bauen --chown=root:root /app/public ./public
# Der Cache-Ordner von Next.js ist die einzige Stelle, die der Server beschreiben darf (im Betrieb ein tmpfs);
# alles andere gehört root und ist für den Benutzer node nur lesbar.
# Paketmanager (npm, corepack, yarn) fliegen aus dem Laufzeit-Image: Im Betrieb werden keine Pakete installiert
RUN mkdir -p /app/.next/cache && chown node:node /app/.next/cache \
 && rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack /usr/local/bin/npm /usr/local/bin/npx \
    /usr/local/bin/corepack /opt/yarn-* /usr/local/bin/yarn /usr/local/bin/yarnpkg
USER node
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:3000/').then(r=>process.exit(r.ok?0:1),()=>process.exit(1))"]
CMD ["node", "server.js"]
