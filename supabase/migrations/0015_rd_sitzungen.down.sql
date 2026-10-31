-- Rückweg zu 0015 (Fernsitzungen): entfernt Funktionen, Trigger und Tabelle samt Protokoll. Bricht ab, solange offene
-- Sitzungen existieren (erst beenden), und in wamo_prod ohne ausdrückliche Freigabe. Wiederholbar.
-- Das Protokoll geht damit verloren; wer es behalten will, sichert die Tabelle vorher
-- (pg_dump -t <schema>.rd_sitzungen). Reihenfolge: spätere Migrationen vorher zurücknehmen.
-- In wamo_prod nur nach ausdrücklicher Freigabe:
--   docker exec -e PGOPTIONS='-c wamo.allow_drop=ja' -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 --single-transaction
do $$ begin
  if '__SCHEMA__' = 'wamo_prod' and coalesce(current_setting('wamo.allow_drop', true), '') <> 'ja' then
    raise exception 'Down in wamo_prod gesperrt. Nach Backup: PGOPTIONS="-c wamo.allow_drop=ja"';
  end if;
  if exists (select 1 from __SCHEMA__.schema_migrations where version > '0015_rd_sitzungen')
     and coalesce(current_setting('wamo.down_trotz_folgemigration', true), '') <> 'ja' then
    raise exception '0015 down: erst die späteren Migrationen zurücknehmen';
  end if;
  if to_regclass('__SCHEMA__.rd_sitzungen') is not null then
    if exists (select 1 from __SCHEMA__.rd_sitzungen where ende_at is null) then
      raise exception '0015 down: es läuft noch eine Fernsitzung, bitte erst beenden';
    end if;
  end if;
end $$;
drop function if exists __SCHEMA__.rd_sitzung_beenden(uuid, text, uuid);
drop function if exists __SCHEMA__.rd_sitzung_beginnen(uuid, text, integer, timestamptz, integer, integer);
drop table if exists __SCHEMA__.rd_sitzungen;
drop function if exists __SCHEMA__.rd_sitzungen_guard();
delete from __SCHEMA__.schema_migrations where version = '0015_rd_sitzungen';
