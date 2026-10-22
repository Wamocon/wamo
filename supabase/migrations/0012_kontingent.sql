-- WAMO 0012: Kontingent des Max-Abos je Profil. __SCHEMA__ wird ersetzt durch wamo_dev | wamo_test | wamo_prod.
-- Anwenden (genau einmal je Schema, in EINER Transaktion):
--   bash scripts/render-migration.sh <schema> supabase/migrations/0012_kontingent.sql \
--     | docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 --single-transaction
-- Hängt nur von 0010 ab (Tabelle model_access); 0011 gehört dem Strang "Modellzugang" und ist unabhängig davon.
-- Reihenfolge verbindlich: 0010, 0011, danach 0012. Jeder erneute Lauf von 0010 oder 0011 mit einem "revoke all" auf der
-- Tabelle nimmt die Spaltenrechte unten stillschweigend zurück (ihre Selbsttests kennen sie nicht): danach immer 0012
-- erneut anwenden (sie ist wiederholbar). Zur Probe nach dem Anwenden (muss true liefern):
--   select has_column_privilege('wamo_core', '<schema>.model_access', 'kontingent', 'SELECT,INSERT,UPDATE');
-- Rückweg in umgekehrter Reihenfolge: erst 0012.down.sql, dann 0010.down.sql.
set local lock_timeout = '5s';  -- ALTER TABLE wartet nicht ewig hinter einer langen Transaktion des Kerns
--
-- Was Claude Code beim Lauf mit einem Abo-Zugang (CLAUDE_CODE_OAUTH_TOKEN) über die Limits ausgibt (Ereignis
-- rate_limit_event: Sitzungslimit, Wochenlimit, Modell-Wochenlimit), merkt sich der Kern je Profil, damit die
-- Oberfläche den Stand auch ohne laufenden Auftrag zeigen kann (voice-core/kontingent.py):
--   kontingent        jsonb, eine Liste von höchstens 8 Limits {"art", "prozent", "endet", "status"}; null oder [] = unbekannt
--   kontingent_stand  Zeitpunkt der letzten Speicherung; steht genau dann, wenn kontingent steht
-- Es sind keine Geheimnisse, nur Prozentwerte und Zeitpunkte. Nur der Kern schreibt und liest sie (wamo_core erhält auf
-- diese beiden Spalten select, insert und update); wamo_admin, anon, authenticated und service_role haben kein Recht darauf.

alter table __SCHEMA__.model_access add column if not exists kontingent jsonb;
alter table __SCHEMA__.model_access add column if not exists kontingent_stand timestamptz;

alter table __SCHEMA__.model_access drop constraint if exists model_access_kontingent_form;
alter table __SCHEMA__.model_access add constraint model_access_kontingent_form
  check (kontingent is null
         or case when jsonb_typeof(kontingent) = 'array'
                 then jsonb_array_length(kontingent) <= 8 and length(kontingent::text) <= 4096
                 else false end);
alter table __SCHEMA__.model_access drop constraint if exists model_access_kontingent_paar;
alter table __SCHEMA__.model_access add constraint model_access_kontingent_paar
  check ((kontingent is null) = (kontingent_stand is null));
comment on column __SCHEMA__.model_access.kontingent is
  'Limits des Max-Abos aus rate_limit_event von Claude Code: Liste {art: sitzung|woche|opus|sonnet|fable, prozent, endet, status}; null oder [] = unbekannt';
comment on column __SCHEMA__.model_access.kontingent_stand is
  'Zeitpunkt, zu dem der Kern das Kontingent zuletzt gespeichert hat';

-- ---------- Rechte (geringste Rechte, nur die beiden neuen Spalten) ----------
revoke all (kontingent, kontingent_stand) on __SCHEMA__.model_access
  from public, anon, authenticated, service_role, wamo_core, wamo_admin;
-- Voice-Core: liest den gespeicherten Stand, legt die Zeile bei Bedarf an (insert ... on conflict do update) und
-- aktualisiert den Stand. Kein delete (das Tabellenrecht fehlt weiterhin), keine Rechte auf die Schlüsselspalten.
grant select (kontingent, kontingent_stand) on __SCHEMA__.model_access to wamo_core;
grant insert (kontingent, kontingent_stand) on __SCHEMA__.model_access to wamo_core;
grant update (kontingent, kontingent_stand) on __SCHEMA__.model_access to wamo_core;

-- ---------- Row Level Security ----------
-- Die Tabelle hat RLS und die Policy core_all seit 0010; hier wird nur geprüft, dass es dabei bleibt.
alter table __SCHEMA__.model_access enable row level security;

-- Selbsttest: bricht die Transaktion bei Rechtedrift ab.
do $$ begin
  if not (has_column_privilege('wamo_core', '__SCHEMA__.model_access', 'kontingent', 'SELECT')
          and has_column_privilege('wamo_core', '__SCHEMA__.model_access', 'kontingent', 'INSERT')
          and has_column_privilege('wamo_core', '__SCHEMA__.model_access', 'kontingent', 'UPDATE')
          and has_column_privilege('wamo_core', '__SCHEMA__.model_access', 'kontingent_stand', 'SELECT')
          and has_column_privilege('wamo_core', '__SCHEMA__.model_access', 'kontingent_stand', 'INSERT')
          and has_column_privilege('wamo_core', '__SCHEMA__.model_access', 'kontingent_stand', 'UPDATE'))
     or has_table_privilege('wamo_core', '__SCHEMA__.model_access', 'DELETE,TRUNCATE')
     or has_column_privilege('wamo_core', '__SCHEMA__.model_access', 'nur_haus', 'INSERT,UPDATE')
     or has_column_privilege('wamo_admin', '__SCHEMA__.model_access', 'kontingent', 'SELECT,INSERT,UPDATE')
     or has_column_privilege('wamo_admin', '__SCHEMA__.model_access', 'kontingent_stand', 'SELECT,INSERT,UPDATE')
     or has_column_privilege('wamo_admin', '__SCHEMA__.model_access', 'claude_schluessel', 'SELECT,INSERT,UPDATE')
     or has_any_column_privilege('anon', '__SCHEMA__.model_access', 'SELECT,INSERT,UPDATE')
     or has_any_column_privilege('authenticated', '__SCHEMA__.model_access', 'SELECT,INSERT,UPDATE')
     or has_any_column_privilege('service_role', '__SCHEMA__.model_access', 'SELECT,INSERT,UPDATE')
     or not (select c.relrowsecurity from pg_class c where c.oid = '__SCHEMA__.model_access'::regclass)
     or not exists (select 1 from pg_policies where schemaname = '__SCHEMA__' and tablename = 'model_access'
                      and policyname = 'core_all')
  then
    raise exception 'model_access (Kontingent): unzulässige Rechte oder RLS fehlt (Rechte, Policy core_all prüfen)'
      using errcode = '42501';
  end if;
end $$;

insert into __SCHEMA__.schema_migrations (version) values ('0012_kontingent') on conflict do nothing;
