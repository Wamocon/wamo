# check=skip=SecretsUsedInArgOrEnv
# Attrappe der WAMO-Oberfläche für den Rauchtest (infra/sokrates2/wamo_ui_rauchtest.py), nie für den Betrieb.
# Sie wird im Test über WAMO_UI_BAUDATEI an Stelle von wamo-ui/Dockerfile gebaut. Der Kontext enthält server.js, static/
# (die Chunks der Fassung) und package.json (Version). Gleiche Build-Argumente wie das echte Image.
ARG NODE_IMAGE=node:22-bookworm-slim
FROM ${NODE_IMAGE}
ARG NEXT_PUBLIC_SUPABASE_URL=""
ARG NEXT_PUBLIC_WAMO_KERN_BUERO=""
ARG WAMO_COMMIT=unbekannt
ARG WAMO_BAUZEIT=unbekannt
ARG WAMO_SAUBER=nein
ARG ATTRAPPE_MODUS=""
ARG ATTRAPPE_NACH_S="0"
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0 \
    NEXT_PUBLIC_SUPABASE_URL=${NEXT_PUBLIC_SUPABASE_URL} NEXT_PUBLIC_WAMO_KERN_BUERO=${NEXT_PUBLIC_WAMO_KERN_BUERO} \
    WAMO_COMMIT=${WAMO_COMMIT} WAMO_BAUZEIT=${WAMO_BAUZEIT} WAMO_SAUBER=${WAMO_SAUBER} \
    ATTRAPPE_MODUS=${ATTRAPPE_MODUS} ATTRAPPE_NACH_S=${ATTRAPPE_NACH_S}
WORKDIR /app
COPY server.js package.json ./
COPY static ./static
LABEL wamo.commit=${WAMO_COMMIT}
USER node
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:3000/version.json').then(r=>process.exit(r.ok?0:1),()=>process.exit(1))"]
CMD ["node", "server.js"]
