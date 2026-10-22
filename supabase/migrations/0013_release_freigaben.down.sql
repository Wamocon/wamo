-- Rückweg zu 0013: entfernt Freigaben, Ereignisse, Anfragen und Stand der Selbstüberarbeitung samt Funktionen, Triggern und
-- der Prüfsummenspalte in schema_migrations. Die Rollen wamo_release und wamo_anker bleiben bestehen (Rollen gelten je Cluster,
-- die anderen Schemas nutzen sie weiter); wer sie ganz entfernen will, tut das von Hand, wenn kein Schema sie mehr braucht.
-- Achtung: Die Ereignisse sind der Prüfpfad der Releases und die Spalte sha256 hält die Prüfsummen der Migrationen. Beides
-- geht verloren. Reihenfolge: erst die Anker-Dienste stoppen, dann diese Datei.
-- In wamo_prod nur nach ausdrücklicher Freigabe:
--   docker exec -e PGOPTIONS='-c wamo.allow_drop=ja' -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 --single-transaction
do $$ begin
  if '__SCHEMA__' = 'wamo_prod' and coalesce(current_setting('wamo.allow_drop', true), '') <> 'ja' then
    raise exception 'Down in wamo_prod gesperrt. Nach Freigabe: PGOPTIONS="-c wamo.allow_drop=ja"';
  end if;
end $$;
set local lock_timeout = '5s';
-- Nie bei einer offenen Anfrage: erst abbrechen oder abschließen lassen (auch außerhalb von wamo_prod)
do $$
declare offen bigint;
begin
  if to_regclass('__SCHEMA__.release_anfragen') is not null then
    execute 'select count(*) from __SCHEMA__.release_anfragen where status not in (''bewaehrt'', ''zurueckgenommen'', ''abgebrochen'')'
      into offen;
    if offen > 0 then
      raise exception 'Down gesperrt: % offene Release-Anfragen (erst abbrechen oder abschließen)', offen;
    end if;
  end if;
end $$;

-- Tabellen in umgekehrter Reihenfolge der Abhängigkeiten (Trigger fallen mit den Tabellen)
drop table if exists __SCHEMA__.release_stand;
drop table if exists __SCHEMA__.release_ereignisse;
drop table if exists __SCHEMA__.release_freigaben;
drop table if exists __SCHEMA__.release_anfragen;

drop function if exists __SCHEMA__.release_kette_pruefen();
drop function if exists __SCHEMA__.release_ereignis_hash(text, bigint, uuid, timestamptz, uuid, text, jsonb);
drop function if exists __SCHEMA__.release_ereignisse_kette();
drop function if exists __SCHEMA__.release_freigaben_guard();
drop function if exists __SCHEMA__.release_anfragen_guard();
drop function if exists __SCHEMA__.release_nur_anfuegen();
drop function if exists __SCHEMA__.release_ist_admin(uuid);
drop function if exists __SCHEMA__.release_anzahl_admins();

-- Prüfsumme in schema_migrations
drop trigger if exists schema_migrations_guard on __SCHEMA__.schema_migrations;
drop function if exists __SCHEMA__.schema_migrations_guard();
alter table if exists __SCHEMA__.schema_migrations drop constraint if exists schema_migrations_sha256_form;
alter table if exists __SCHEMA__.schema_migrations drop column if exists sha256;

-- Zugriff auf das Schema wieder entziehen (die Rollen selbst bleiben)
do $$ begin
  if exists (select 1 from pg_roles where rolname = 'wamo_release') then
    execute 'revoke usage on schema __SCHEMA__ from wamo_release';
  end if;
  if exists (select 1 from pg_roles where rolname = 'wamo_anker') then
    execute 'revoke usage on schema __SCHEMA__ from wamo_anker';
  end if;
end $$;

delete from __SCHEMA__.schema_migrations where version = '0013_release_freigaben';
