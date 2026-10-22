-- Rückweg zu 0012: entfernt das gespeicherte Kontingent des Max-Abos (nur Prozentwerte, keine Geheimnisse). Der Modellzugang
-- (0010) bleibt unberührt; die Oberfläche zeigt das Kontingent danach als unbekannt, bis ein Auftrag es wieder meldet.
-- Reihenfolge: erst diese Datei, dann 0010_modellzugang.down.sql (sonst bleibt 0012 in schema_migrations stehen).
-- In wamo_prod nur nach ausdrücklicher Freigabe:
--   docker exec -e PGOPTIONS='-c wamo.allow_drop=ja' -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 --single-transaction
do $$ begin
  if '__SCHEMA__' = 'wamo_prod' and coalesce(current_setting('wamo.allow_drop', true), '') <> 'ja' then
    raise exception 'Down in wamo_prod gesperrt. Nach Freigabe: PGOPTIONS="-c wamo.allow_drop=ja"';
  end if;
end $$;
alter table if exists __SCHEMA__.model_access drop constraint if exists model_access_kontingent_paar;
alter table if exists __SCHEMA__.model_access drop constraint if exists model_access_kontingent_form;
alter table if exists __SCHEMA__.model_access drop column if exists kontingent_stand;
alter table if exists __SCHEMA__.model_access drop column if exists kontingent;
delete from __SCHEMA__.schema_migrations where version = '0012_kontingent';
