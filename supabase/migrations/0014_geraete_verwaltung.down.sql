-- Rückweg zu 0014 (Geräteverwaltung): entfernt die beiden Funktionen. Der Stand der Geräte und der Verlauf bleiben
-- unberührt; ab dann gibt es wieder nur den Weg über wamo_admin (SQL). Die Oberfläche zeigt die Karte Geräteverwaltung
-- dann als nicht erreichbar.
-- Reihenfolge: erst diese Datei, dann (falls gewünscht) 0002_wamo_geraete.down.sql.
-- In wamo_prod nur nach ausdrücklicher Freigabe:
--   docker exec -e PGOPTIONS='-c wamo.allow_drop=ja' -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 --single-transaction
do $$ begin
  if '__SCHEMA__' = 'wamo_prod' and coalesce(current_setting('wamo.allow_drop', true), '') <> 'ja' then
    raise exception 'Down in wamo_prod gesperrt. Nach Freigabe: PGOPTIONS="-c wamo.allow_drop=ja"';
  end if;
end $$;
drop function if exists __SCHEMA__.geraet_entscheiden(uuid, text, text, text, timestamptz);
drop function if exists __SCHEMA__.geraete_verwaltung_liste(uuid);
delete from __SCHEMA__.schema_migrations where version = '0014_geraete_verwaltung';
