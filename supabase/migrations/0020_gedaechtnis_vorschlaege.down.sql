-- Rückweg zu 0020 (Gedächtnisvorschläge in der Oberfläche entscheiden): entfernt die Funktion gedaechtnis_vorschlag_entscheiden und den Eintrag
-- in schema_migrations. An den Einträgen in memories ändert sich nichts: schon freigegebene oder abgelehnte Vorschläge bleiben so. Danach geht
-- das Entscheiden wieder nur mit voice-core/wamo_admin.py. Reihenfolge: ZUERST Oberfläche und Kern auf einen Stand ohne gedaechtnis_api.py
-- zurückstellen (der Kern antwortet sonst auf einen Klick ruhig mit 503), dann diese Datei. Wiederholbar. Spätere Migrationen vorher zurücknehmen.
-- Anwenden: bash scripts/render-migration.sh <schema> supabase/migrations/0020_gedaechtnis_vorschlaege.down.sql \
--     | docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 --single-transaction
set local lock_timeout = '5s';

do $$ begin
  if exists (select 1 from __SCHEMA__.schema_migrations where version > '0020_gedaechtnis_vorschlaege')
     and coalesce(current_setting('wamo.down_trotz_folgemigration', true), '') <> 'ja' then
    raise exception '0020 down: erst die späteren Migrationen zurücknehmen';
  end if;
end $$;

drop function if exists __SCHEMA__.gedaechtnis_vorschlag_entscheiden(uuid, uuid, text);

delete from __SCHEMA__.schema_migrations where version = '0020_gedaechtnis_vorschlaege';
