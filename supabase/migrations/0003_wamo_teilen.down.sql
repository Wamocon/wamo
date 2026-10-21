-- Rueckweg zu 0003 (Arbeit teilen). Bricht ab, solange Teilungen oder Dateien existieren (sonst gingen geteilte
-- Arbeit, Dateien und das Protokoll verloren). Fassungen geteilter Canvas-Dokumente bleiben als Canvas-Elemente.
do $$ begin
  if '__SCHEMA__' = 'wamo_prod' and coalesce(current_setting('wamo.allow_drop', true), '') <> 'ja' then
    raise exception 'Down in wamo_prod gesperrt. Nach Backup: PGOPTIONS="-c wamo.allow_drop=ja"';
  end if;
  if exists (select 1 from __SCHEMA__.schema_migrations where version > '0003_wamo_teilen') then
    raise exception '0003 down: erst die spaeteren Migrationen zuruecknehmen';
  end if;
  if exists (select 1 from __SCHEMA__.teilungen) or exists (select 1 from __SCHEMA__.dateien) then
    raise exception '0003 down: Teilungen oder Dateien vorhanden; erst bewusst entfernen';
  end if;
end $$;
drop function if exists __SCHEMA__.datei_loeschen(uuid, uuid);
drop function if exists __SCHEMA__.datei_fassung_anlegen(uuid, uuid, text, text, bytea);
drop function if exists __SCHEMA__.canvas_fassung_anlegen(uuid, uuid, text, jsonb);
drop function if exists __SCHEMA__.teilung_abgerufen(uuid, uuid);
drop trigger if exists teilungen_ereignis on __SCHEMA__.teilungen;
drop trigger if exists teilungen_guard on __SCHEMA__.teilungen;
drop function if exists __SCHEMA__.teilungen_ereignis();
drop function if exists __SCHEMA__.teilungen_guard();
drop table if exists __SCHEMA__.teilung_events;
drop table if exists __SCHEMA__.teilungen;
drop function if exists __SCHEMA__.datei_recht(uuid, uuid);
drop function if exists __SCHEMA__.canvas_recht(uuid, uuid);
drop function if exists __SCHEMA__.teilung_objekt_titel(text, uuid, uuid, text);
drop trigger if exists dateien_guard on __SCHEMA__.dateien;
drop function if exists __SCHEMA__.dateien_guard();
drop table if exists __SCHEMA__.dateien;
drop index if exists __SCHEMA__.canvas_auftrag_idx, __SCHEMA__.canvas_bearbeiter_idx;
alter table __SCHEMA__.canvas_items drop column if exists bearbeitet_von;
delete from __SCHEMA__.schema_migrations where version = '0003_wamo_teilen';
