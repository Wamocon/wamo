-- Rueckweg zu 0001 (ECC database-migrations: jede Migration hat UP und DOWN).
-- ACHTUNG: loescht alle WAMO-Daten im Schema. In wamo_prod nur nach Backup und ausdruecklicher Freigabe:
--   PGOPTIONS="-c wamo.allow_drop=ja" ... psql ...
-- Die Rollen wamo_core und wamo_admin sowie die Erweiterung vector bleiben bestehen, solange andere
-- wamo_*-Schemas sie nutzen.
do $$ begin
  if '__SCHEMA__' = 'wamo_prod' and coalesce(current_setting('wamo.allow_drop', true), '') <> 'ja' then
    raise exception 'Down in wamo_prod gesperrt. Nach Backup: PGOPTIONS="-c wamo.allow_drop=ja"';
  end if;
end $$;
drop schema if exists __SCHEMA__ cascade;
