-- Rueckweg zu 0003 (Arbeit teilen), wiederholbar. Bricht ab, solange Teilungen oder Dateien existieren (sonst gingen
-- geteilte Arbeit, Dateien und das Protokoll verloren). Fassungen geteilter Canvas-Dokumente bleiben als Canvas-Elemente.
-- Spaetere Migrationen muessen vorher zurueck; haengen sie nachweislich nicht von 0003 ab (z. B. 0010), geht es mit
--   PGOPTIONS="-c wamo.down_trotz_folgemigration=ja"
do $$
declare
  t text;
  belegt boolean;
begin
  if '__SCHEMA__' = 'wamo_prod' and coalesce(current_setting('wamo.allow_drop', true), '') <> 'ja' then
    raise exception 'Down in wamo_prod gesperrt. Nach Backup: PGOPTIONS="-c wamo.allow_drop=ja"';
  end if;
  if exists (select 1 from __SCHEMA__.schema_migrations where version > '0003_wamo_teilen')
     and coalesce(current_setting('wamo.down_trotz_folgemigration', true), '') <> 'ja' then
    raise exception '0003 down: erst die spaeteren Migrationen zuruecknehmen';
  end if;
  -- dynamisch: beim zweiten Lauf gibt es die Tabellen nicht mehr (statisches SQL scheiterte schon beim Planen)
  foreach t in array array['teilungen', 'dateien'] loop
    if to_regclass('__SCHEMA__.' || t) is not null then
      execute format('select exists (select 1 from __SCHEMA__.%I)', t) into belegt;
      if belegt then
        raise exception '0003 down: % enthaelt Zeilen; erst bewusst entfernen', t;
      end if;
    end if;
  end loop;
end $$;
drop function if exists __SCHEMA__.dateien_endgueltig_loeschen();
drop function if exists __SCHEMA__.datei_loeschen(uuid, uuid);
drop function if exists __SCHEMA__.datei_fassung_anlegen(uuid, uuid, text, text, bytea);
drop function if exists __SCHEMA__.canvas_fassung_anlegen(uuid, uuid, jsonb, integer);
drop function if exists __SCHEMA__.canvas_fassung_anlegen(uuid, uuid, text, jsonb);   -- erster Entwurf (nur wamo_test)
drop function if exists __SCHEMA__.teilung_abgerufen(uuid, uuid);
drop function if exists __SCHEMA__.teilung_beenden(uuid, uuid);
drop function if exists __SCHEMA__.teilung_recht_setzen(uuid, uuid, text);
-- Tabellen samt ihrer Trigger (keine Abhaengigkeiten ausserhalb von 0003, sonst bricht drop ohne cascade ab)
drop table if exists __SCHEMA__.teilung_events;
drop table if exists __SCHEMA__.teilungen;
drop table if exists __SCHEMA__.dateien;
drop function if exists __SCHEMA__.teilungen_ereignis();
drop function if exists __SCHEMA__.teilungen_guard();
drop function if exists __SCHEMA__.dateien_guard();
drop function if exists __SCHEMA__.bearbeitung_melden(uuid, uuid, text, jsonb);
drop function if exists __SCHEMA__.datei_recht(uuid, uuid);
drop function if exists __SCHEMA__.canvas_recht(uuid, uuid);
drop function if exists __SCHEMA__.teilung_objekt_titel(text, uuid, uuid, text);
drop index if exists __SCHEMA__.canvas_fassung_idx, __SCHEMA__.canvas_auftrag_idx, __SCHEMA__.canvas_bearbeiter_idx;
alter table __SCHEMA__.canvas_items drop constraint if exists canvas_auftrag_projekt_chk;
alter table __SCHEMA__.canvas_items drop column if exists bearbeitet_von;
delete from __SCHEMA__.schema_migrations where version = '0003_wamo_teilen';
