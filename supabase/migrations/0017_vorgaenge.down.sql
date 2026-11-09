-- Rückweg zu 0017 (Vorgänge): entfernt die Tabelle vorgaenge samt allen gespeicherten Ständen. Wiederholbar.
-- Bricht in wamo_prod ohne ausdrückliche Freigabe ab und solange spätere Migrationen angewendet sind (erst diese zurücknehmen).
-- Die Stände gehen verloren; wer sie behalten will, sichert die Tabelle vorher (pg_dump -t <schema>.vorgaenge).
-- In wamo_prod nur nach ausdrücklicher Freigabe:
--   docker exec -e PGOPTIONS='-c wamo.allow_drop=ja' -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 --single-transaction
do $$ begin
  if '__SCHEMA__' = 'wamo_prod' and coalesce(current_setting('wamo.allow_drop', true), '') <> 'ja' then
    raise exception 'Down in wamo_prod gesperrt. Nach Backup: PGOPTIONS="-c wamo.allow_drop=ja"';
  end if;
  if exists (select 1 from __SCHEMA__.schema_migrations where version > '0017_vorgaenge')
     and coalesce(current_setting('wamo.down_trotz_folgemigration', true), '') <> 'ja' then
    raise exception '0017 down: erst die späteren Migrationen zurücknehmen';
  end if;
end $$;
set local lock_timeout = '5s';

drop table if exists __SCHEMA__.vorgaenge;
delete from __SCHEMA__.schema_migrations where version = '0017_vorgaenge';
