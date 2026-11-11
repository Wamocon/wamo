-- Rückweg zu 0018 (Laborprofile): entfernt die Spalten labor_art, labor_platz, labor_aktiv, ihre Prüfungen, den Trigger und die
-- Funktionen labor_loeschen und labor_zaehlen. Wiederholbar.
-- Aufruf, für jedes Schema (wamo_dev, wamo_test, wamo_prod):
--   bash scripts/render-migration.sh <schema> supabase/migrations/0018_labor_profile.down.sql \
--     | docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 --single-transaction
-- Prüfung und Abbau stehen in EINEM do-Block, also in einer einzigen Anweisung: Bricht die Prüfung ab, nimmt der Fehler den ganzen
-- Block zurück, auch wenn psql ohne -v ON_ERROR_STOP=1 und ohne --single-transaction aufgerufen wird. Die Optionen bleiben Vorschrift.
-- Reihenfolge mit dem Kern: Der Rückweg gilt erst nach der Rückstellung des Kerns auf einen Stand ohne Laborlogik. Der neue Kern liest
--   labor_art, labor_platz und labor_aktiv bei jedem Profil; fehlen die Spalten, antwortet er für ALLE Nutzer mit 503 (gewollt).
-- Bricht ab, solange es EIN Profil mit labor_art gibt, auch ein gesperrtes oder nie benutztes: Ohne die Spalte wäre es ein
-- gewöhnliches Mitglied, und ein Rückbau darf kein Profil still zum Mitglied machen. Reihenfolge des Rückbaus:
--   1. die Laborprofile sperren und ihren Text löschen (scripts/wamo_labor.py, Funktion labor_loeschen)
--   2. die Laborprofile samt Konten löschen
--   3. dieses Skript
-- Bricht außerdem in wamo_prod ohne ausdrückliche Freigabe ab und solange spätere Migrationen angewendet sind.
-- In wamo_prod nur nach ausdrücklicher Freigabe, mit der Einstellung wamo.allow_drop:
--   docker exec -e PGOPTIONS='-c wamo.allow_drop=ja' -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 --single-transaction
do $$
declare
  v_n bigint := 0;
begin
  if '__SCHEMA__' = 'wamo_prod' and coalesce(current_setting('wamo.allow_drop', true), '') <> 'ja' then
    raise exception 'Down in wamo_prod gesperrt. Nach Backup: PGOPTIONS="-c wamo.allow_drop=ja"' using errcode = '55000';
  end if;
  if exists (select 1 from __SCHEMA__.schema_migrations where version > '0018_labor_profile')
     and coalesce(current_setting('wamo.down_trotz_folgemigration', true), '') <> 'ja' then
    raise exception '0018 down: erst die späteren Migrationen zurücknehmen' using errcode = '55000';
  end if;
  -- dynamisch, damit ein zweiter Lauf nach dem Entfernen der Spalte nicht an der fehlenden Spalte scheitert
  if exists (select 1 from information_schema.columns where table_schema = '__SCHEMA__'
              and table_name = 'profiles' and column_name = 'labor_art') then
    execute 'select count(*) from __SCHEMA__.profiles where labor_art is not null' into v_n;
  end if;
  if v_n > 0 then
    raise exception '0018 down: es gibt noch % Laborprofile (auch gesperrte zählen). Erst labor_loeschen, dann die Profile löschen', v_n
      using errcode = '55000';
  end if;

  -- Ab hier wird verändert: nur nach bestandener Prüfung, mit begrenzter Wartezeit auf Sperren
  perform set_config('lock_timeout', '5s', true);
  drop trigger if exists profiles_labor_guard on __SCHEMA__.profiles;
  drop function if exists __SCHEMA__.profiles_labor_guard();
  drop function if exists __SCHEMA__.labor_loeschen(uuid, timestamptz, timestamptz);
  drop function if exists __SCHEMA__.labor_zaehlen(uuid);
  drop index if exists __SCHEMA__.profiles_labor_platz_uni;
  alter table __SCHEMA__.profiles drop constraint if exists profiles_labor_streng_chk;
  alter table __SCHEMA__.profiles drop constraint if exists profiles_labor_form_chk;
  alter table __SCHEMA__.profiles drop constraint if exists profiles_labor_platz_chk;
  alter table __SCHEMA__.profiles drop constraint if exists profiles_labor_art_chk;
  alter table __SCHEMA__.profiles drop column if exists labor_aktiv;
  alter table __SCHEMA__.profiles drop column if exists labor_platz;
  alter table __SCHEMA__.profiles drop column if exists labor_art;
  delete from __SCHEMA__.schema_migrations where version = '0018_labor_profile';
end $$;
