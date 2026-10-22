-- Rückweg zu 0010: entfernt den Modellzugang je Profil samt aller hinterlegten (verschlüsselten) Schlüssel.
-- Danach gilt für alle Profile "nur Hausmodelle", bis 0010 wieder angewendet und die Schlüssel neu hinterlegt sind.
-- In wamo_prod nur nach Backup (alle Chiffrate gehen verloren) und ausdrücklicher Freigabe:
--   docker exec -e PGOPTIONS='-c wamo.allow_drop=ja' -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 --single-transaction
do $$ begin
  if '__SCHEMA__' = 'wamo_prod' and coalesce(current_setting('wamo.allow_drop', true), '') <> 'ja' then
    raise exception 'Down in wamo_prod gesperrt. Nach Backup: PGOPTIONS="-c wamo.allow_drop=ja"';
  end if;
end $$;
drop table if exists __SCHEMA__.model_access;
drop function if exists __SCHEMA__.model_access_guard();
delete from __SCHEMA__.schema_migrations where version = '0010_modellzugang';
