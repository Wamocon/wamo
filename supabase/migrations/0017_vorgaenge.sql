-- WAMO 0017: Vorgänge (Aufgabenleiste mit Livefortschritt): was WAMO abarbeitet, mit Status, Gesamtfortschritt und dem letzten Stand.
-- __SCHEMA__ wird ersetzt durch wamo_dev | wamo_test | wamo_prod.
-- Anwenden (je Schema, in EINER Transaktion, ein zweiter Lauf ändert nichts):
--   bash scripts/render-migration.sh <schema> supabase/migrations/0017_vorgaenge.sql \
--     | docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 --single-transaction
-- Rückweg: 0017_vorgaenge.down.sql. Hängt nur von 0001 ab (sessions, schema_migrations, auth.users).
-- wamo_prod nur nach ausdrücklicher Freigabe und nach dem Agenten database-reviewer.
--
-- Was hier entsteht: Eine Tabelle vorgaenge mit einer Zeile je Vorgang (Boss-Aufgabe oder Entwicklungsauftrag). Der Voice-Core
-- (voice-core/vorgaenge.py) schreibt eine Zeile beim Anlegen und danach nur bei einem Statuswechsel, nie bei jedem Fortschritt;
-- die Spalte daten hält den ganzen letzten Stand (Schritte, Kriterien, Runde, Ergebnis) in der Form der Servernachricht "vorgang".
-- Gelesen wird sie vom Kern für GET /wamo/api/vorgaenge (die letzten 20 des eigenen Profils, voice-core/vorgaenge_api.py).
--
-- Rechte (CLAUDE.md): nur wamo_core. Lesen und Einfügen auf der Tabelle, Update nur für die Spalten, die sich im Lauf ändern
-- (session_id kommt spät, wenn die Sitzung erst nach dem Vorgang angelegt wurde). user_id, art, id und Beginn bleiben nach dem
-- Anlegen unveränderlich. Kein delete, kein truncate. anon, authenticated, service_role und wamo_admin haben keine Rechte.
-- Die Kennung id liefert der Kern selbst (eine UUID, die auch die Oberfläche kennt), damit das Schreiben ein Upsert sein kann
-- (on conflict (id) do update ... where user_id = excluded.user_id: nie die Zeile eines anderen Profils überschreiben).
set local lock_timeout = '5s';

do $$ begin
  if not exists (select 1 from __SCHEMA__.schema_migrations where version = '0001_wamo_init') then
    raise exception '0017 braucht 0001';
  end if;
end $$;

-- ---------- Tabelle ----------
create table if not exists __SCHEMA__.vorgaenge (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references auth.users(id) on delete cascade,
  session_id   uuid references __SCHEMA__.sessions(id) on delete set null,
  art          text not null check (art in ('aufgabe', 'entwicklung', 'geraet', 'sonstiges')),
  titel        text not null check (length(titel) between 1 and 300),
  status       text not null check (status in ('geplant', 'laeuft', 'wartet_freigabe', 'pruefen', 'fertig', 'fehler', 'abgebrochen')),
  gesamt       real not null default 0 check (gesamt >= 0 and gesamt <= 1),
  daten        jsonb not null default '{}'::jsonb check (jsonb_typeof(daten) = 'object' and pg_column_size(daten) <= 262144),
  gestartet_at timestamptz not null default now(),
  beendet_at   timestamptz,
  constraint vorgaenge_zeit_chk check (beendet_at is null or beendet_at >= gestartet_at)
);
comment on table __SCHEMA__.vorgaenge is
  'Vorgänge der Aufgabenleiste: eine Zeile je Boss-Aufgabe oder Entwicklungsauftrag, geschrieben bei Statuswechsel. daten ist der letzte Stand, nie Ergebnistext. Kein delete.';

-- ---------- Indizes ----------
-- Fremdschlüssel user_id und zugleich der Verlauf je Profil (neueste zuerst)
create index if not exists vorgaenge_user_idx on __SCHEMA__.vorgaenge (user_id, gestartet_at desc);
-- Fremdschlüssel session_id (optional): partiell
create index if not exists vorgaenge_session_idx on __SCHEMA__.vorgaenge (session_id) where session_id is not null;

-- ---------- Rechte (geringste Rechte) ----------
revoke all on __SCHEMA__.vorgaenge from public, anon, authenticated, service_role, wamo_admin;
grant select, insert on __SCHEMA__.vorgaenge to wamo_core;
grant update (session_id, titel, status, gesamt, daten, beendet_at) on __SCHEMA__.vorgaenge to wamo_core;

-- ---------- Row Level Security ----------
alter table __SCHEMA__.vorgaenge enable row level security;
drop policy if exists core_all on __SCHEMA__.vorgaenge;
create policy core_all on __SCHEMA__.vorgaenge for all to wamo_core using (true) with check (true);

-- Selbsttest: bricht die Transaktion bei Rechtedrift ab.
do $$ begin
  if not has_table_privilege('wamo_core', '__SCHEMA__.vorgaenge', 'SELECT')
     or not has_table_privilege('wamo_core', '__SCHEMA__.vorgaenge', 'INSERT')
     or has_table_privilege('wamo_core', '__SCHEMA__.vorgaenge', 'UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
     or not has_column_privilege('wamo_core', '__SCHEMA__.vorgaenge', 'session_id', 'UPDATE')
     or not has_column_privilege('wamo_core', '__SCHEMA__.vorgaenge', 'titel', 'UPDATE')
     or not has_column_privilege('wamo_core', '__SCHEMA__.vorgaenge', 'status', 'UPDATE')
     or not has_column_privilege('wamo_core', '__SCHEMA__.vorgaenge', 'gesamt', 'UPDATE')
     or not has_column_privilege('wamo_core', '__SCHEMA__.vorgaenge', 'daten', 'UPDATE')
     or not has_column_privilege('wamo_core', '__SCHEMA__.vorgaenge', 'beendet_at', 'UPDATE')
     or has_column_privilege('wamo_core', '__SCHEMA__.vorgaenge', 'id', 'UPDATE')
     or has_column_privilege('wamo_core', '__SCHEMA__.vorgaenge', 'user_id', 'UPDATE')
     or has_column_privilege('wamo_core', '__SCHEMA__.vorgaenge', 'art', 'UPDATE')
     or has_column_privilege('wamo_core', '__SCHEMA__.vorgaenge', 'gestartet_at', 'UPDATE')
     or has_any_column_privilege('wamo_admin', '__SCHEMA__.vorgaenge', 'SELECT,INSERT,UPDATE,REFERENCES')
     or has_any_column_privilege('anon', '__SCHEMA__.vorgaenge', 'SELECT,INSERT,UPDATE,REFERENCES')
     or has_any_column_privilege('authenticated', '__SCHEMA__.vorgaenge', 'SELECT,INSERT,UPDATE,REFERENCES')
     or has_any_column_privilege('service_role', '__SCHEMA__.vorgaenge', 'SELECT,INSERT,UPDATE,REFERENCES')
     or not (select c.relrowsecurity from pg_class c where c.oid = '__SCHEMA__.vorgaenge'::regclass)
     or not exists (select 1 from pg_policies where schemaname = '__SCHEMA__' and tablename = 'vorgaenge'
                      and policyname = 'core_all')
     or (select count(*) from pg_policies where schemaname = '__SCHEMA__' and tablename = 'vorgaenge') <> 1
     or not exists (select 1 from pg_indexes where schemaname = '__SCHEMA__' and tablename = 'vorgaenge'
                      and indexname = 'vorgaenge_session_idx' and indexdef like '%WHERE (session_id IS NOT NULL)%')
  then
    raise exception 'vorgaenge: unzulässige Rechte oder RLS fehlt (Tabelle, Spalten, Policy core_all, Index prüfen)'
      using errcode = '42501';
  end if;
end $$;

insert into __SCHEMA__.schema_migrations (version) values ('0017_vorgaenge') on conflict do nothing;
