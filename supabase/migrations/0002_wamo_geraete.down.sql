-- Rueckweg zu 0002 (Geraeteregister und Hinweise). Bricht ab, solange Geraete eingetragen sind (das Audit in actions
-- und sessions haengt daran). Hinweise gehen verloren.
do $$ begin
  if '__SCHEMA__' = 'wamo_prod' and coalesce(current_setting('wamo.allow_drop', true), '') <> 'ja' then
    raise exception 'Down in wamo_prod gesperrt. Nach Backup: PGOPTIONS="-c wamo.allow_drop=ja"';
  end if;
  if exists (select 1 from __SCHEMA__.schema_migrations where version > '0002_wamo_geraete') then
    raise exception '0002 down: erst die spaeteren Migrationen zuruecknehmen';
  end if;
  if exists (select 1 from __SCHEMA__.devices) then
    raise exception '0002 down: devices enthaelt Zeilen; erst bewusst entfernen';
  end if;
end $$;
drop function if exists __SCHEMA__.geraet_binden(uuid, text, text, text);
drop function if exists __SCHEMA__.pc_abgleich_liste();
drop function if exists __SCHEMA__.geraet_gesehen(text);
drop function if exists __SCHEMA__.hinweise_alle_quittieren(uuid);
drop function if exists __SCHEMA__.hinweis_quittieren(uuid, uuid);
drop trigger if exists hinweise_guard on __SCHEMA__.hinweise;
drop function if exists __SCHEMA__.hinweise_guard();
drop trigger if exists profiles_geraete on __SCHEMA__.profiles;
drop function if exists __SCHEMA__.devices_profil_deaktiviert();
drop trigger if exists devices_ereignis on __SCHEMA__.devices;
drop trigger if exists devices_guard on __SCHEMA__.devices;
drop function if exists __SCHEMA__.devices_ereignis();
drop function if exists __SCHEMA__.devices_guard();
drop function if exists __SCHEMA__.hinweis_an_admins(text, text, text, uuid, jsonb);
drop table if exists __SCHEMA__.hinweise;
drop table if exists __SCHEMA__.device_events;
drop policy if exists admin_all on __SCHEMA__.devices;
revoke all on __SCHEMA__.devices from wamo_admin;
drop index if exists __SCHEMA__.hinweise_bezug_idx;
drop index if exists __SCHEMA__.devices_hostname_idx, __SCHEMA__.devices_slot_idx, __SCHEMA__.devices_ein_aktives_idx,
  __SCHEMA__.devices_fp_idx, __SCHEMA__.devices_kennung_idx, __SCHEMA__.devices_aenderer_idx,
  __SCHEMA__.devices_freigeber_idx, __SCHEMA__.devices_user_idx;
alter table __SCHEMA__.devices
  drop constraint if exists devices_besitzer_fk, drop constraint if exists devices_freigeber_fk,
  drop constraint if exists devices_aenderer_fk, drop constraint if exists devices_art_chk,
  drop constraint if exists devices_persoenlich_chk, drop constraint if exists devices_gemeinsam_chk,
  drop constraint if exists devices_zugang_chk, drop constraint if exists devices_kennung_chk,
  drop constraint if exists devices_slot_chk, drop constraint if exists devices_pubkey_chk,
  drop constraint if exists devices_fp_chk, drop constraint if exists devices_hostname_chk,
  drop constraint if exists devices_text_chk, drop constraint if exists devices_sperre_chk,
  drop constraint if exists devices_bindung_chk, drop constraint if exists devices_praefix_chk;
alter table __SCHEMA__.devices
  drop column if exists art, drop column if exists klasse, drop column if exists zugang, drop column if exists status,
  drop column if exists bindung, drop column if exists kennung, drop column if exists tunnel_slot,
  drop column if exists ssh_pubkey, drop column if exists ssh_fingerprint, drop column if exists beschreibung,
  drop column if exists freigegeben_von, drop column if exists freigegeben_at, drop column if exists geaendert_von,
  drop column if exists gesperrt_at, drop column if exists gesperrt_grund, drop column if exists updated_at,
  add column if not exists tunnel_port integer unique check (tunnel_port between 47000 and 47999),
  add column if not exists kind text not null default 'windows-pc' check (kind in ('windows-pc','desktop-app','browser'));
alter table __SCHEMA__.devices alter column user_id set not null;
alter table __SCHEMA__.devices drop constraint if exists devices_user_id_fkey,
  add constraint devices_user_id_fkey foreign key (user_id) references auth.users(id) on delete cascade;
create index if not exists devices_user_idx on __SCHEMA__.devices (user_id);
drop function if exists __SCHEMA__.ssh_fingerabdruck(text);
drop type if exists __SCHEMA__.geraet_bindung, __SCHEMA__.geraet_status, __SCHEMA__.geraet_klasse, __SCHEMA__.geraet_art;
delete from __SCHEMA__.schema_migrations where version = '0002_wamo_geraete';
