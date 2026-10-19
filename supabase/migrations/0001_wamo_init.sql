-- WAMO: Grundschema mit Mehrprofil-Gedaechtnis. __SCHEMA__ wird ersetzt durch
--   wamo_dev | wamo_test | wamo_prod   (eigener WAMO-Supabase auf Sokrates-2, Projekt supabase-wamo)
-- Anwenden (genau einmal je Schema, immer in EINER Transaktion; ECC database-migrations):
--   bash scripts/render-migration.sh <schema> | docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 --single-transaction
--
-- Grundsaetze (ECC database-reviewer, 19.10.2026)
-- * Eigener Stack mit geschlossener Registrierung: Konten legt nur der Admin an (scripts/wamo_nutzer.py).
-- * Die Oberflaeche liest KEINE Tabellen. authenticated, anon und service_role haben deshalb keinerlei
--   Rechte auf dieses Schema. Kommt spaeter ein UI-Zugriff, folgt er als eigene Migration mit RLS,
--   aal2-Pruefung und Profil-Gate.
-- * Zwei Backend-Rollen mit minimalen Rechten:
--     wamo_core   Voice-Core (LLM-nah): lesen, einfuegen und wenige gezielte Updates, kein delete,
--                 keine Profile anlegen, keine Rollen aendern, nichts freigeben.
--     wamo_admin  Verwaltung (voice-core/wamo_admin.py): Profile anlegen und pflegen, gemeinsame
--                 Vorschlaege freigeben oder ablehnen.
--   Login und Passwort setzt scripts/wamo_db_rolle.py, nie die Migration.
-- * Gedaechtnis: bereich 'persoenlich' gehoert genau einem Profil, bereich 'gemeinsam' gilt fuer alle.
--   Gemeinsame Eintraege wirken nur mit status 'aktiv'; das erzwingt der Trigger memories_guard in der
--   Datenbank (nur ein aktiver Admin als Autor oder Pruefer macht sie wirksam).

create extension if not exists vector with schema extensions;

-- ---------- Rollen ----------
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'wamo_core') then
    create role wamo_core nologin nosuperuser nocreatedb nocreaterole nobypassrls;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'wamo_admin') then
    create role wamo_admin nologin nosuperuser nocreatedb nocreaterole nobypassrls;
  end if;
end $$;
alter role wamo_core set statement_timeout = '10s';
alter role wamo_core set idle_in_transaction_session_timeout = '15s';
alter role wamo_admin set statement_timeout = '30s';

create schema if not exists __SCHEMA__;
grant usage on schema __SCHEMA__ to wamo_core, wamo_admin;
grant usage on schema extensions to wamo_core;

-- ---------- Typen ----------
do $$ begin create type __SCHEMA__.turn_role as enum ('user','assistant','system','tool');
  exception when duplicate_object then null; end $$;
do $$ begin create type __SCHEMA__.memory_kind as enum ('fact','preference','behavior','lesson','skill','episode');
  exception when duplicate_object then null; end $$;
do $$ begin create type __SCHEMA__.memory_scope as enum ('persoenlich','gemeinsam');
  exception when duplicate_object then null; end $$;
do $$ begin create type __SCHEMA__.memory_status as enum ('aktiv','vorgeschlagen','abgelehnt');
  exception when duplicate_object then null; end $$;
do $$ begin create type __SCHEMA__.profile_role as enum ('admin','mitglied');
  exception when duplicate_object then null; end $$;
do $$ begin create type __SCHEMA__.feedback_kind as enum ('correction','praise','thumbs_down','thumbs_up','missed_intent');
  exception when duplicate_object then null; end $$;
do $$ begin create type __SCHEMA__.risk_class as enum ('read','change','critical','blocked');
  exception when duplicate_object then null; end $$;
do $$ begin create type __SCHEMA__.action_status as enum ('pending','approved','rejected','running','done','failed','blocked');
  exception when duplicate_object then null; end $$;
do $$ begin create type __SCHEMA__.canvas_type as enum ('table','document','bpmn','mermaid','flow','pipeline','whiteboard','code','chart');
  exception when duplicate_object then null; end $$;

-- ---------- Versionstabelle ----------
create table if not exists __SCHEMA__.schema_migrations (
  version    text primary key,
  applied_at timestamptz not null default now()
);

-- ---------- Hilfsfunktion ----------
create or replace function __SCHEMA__.touch_updated_at() returns trigger
language plpgsql set search_path = '' as $$
begin new.updated_at = now(); return new; end $$;

-- ---------- Profile & Geraete ----------
create table if not exists __SCHEMA__.profiles (
  user_id      uuid primary key references auth.users(id) on delete cascade,
  display_name text not null check (length(display_name) between 1 and 60),
  rolle        __SCHEMA__.profile_role not null default 'mitglied',
  aktiv        boolean not null default true,
  anrede       text not null default 'du' check (anrede in ('du','sie')),
  voice_id     text check (voice_id is null or length(voice_id) <= 100),   -- Stimme dieses Profils
  wake_word    text not null default 'wamo' check (length(wake_word) between 2 and 40),
  settings     jsonb not null default '{}'::jsonb check (pg_column_size(settings) <= 16384),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

create table if not exists __SCHEMA__.devices (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null references auth.users(id) on delete cascade,
  name           text not null check (length(name) between 1 and 100),
  hostname       text,
  tunnel_port    integer unique check (tunnel_port between 47000 and 47999),  -- reservierter Block fuer SSH-Ruecktunnel
  kind           text not null default 'windows-pc' check (kind in ('windows-pc','desktop-app','browser')),
  last_seen_at   timestamptz,
  created_at     timestamptz not null default now()
);

-- ---------- Gespraeche ----------
create table if not exists __SCHEMA__.sessions (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references auth.users(id) on delete cascade,
  device_id  uuid references __SCHEMA__.devices(id) on delete set null,
  channel    text not null default 'web' check (length(channel) between 1 and 20),
  summary    text,
  started_at timestamptz not null default now(),
  ended_at   timestamptz,
  constraint sessions_time_chk check (ended_at is null or ended_at >= started_at)
);

-- Stimmabdruecke sind biometrische Daten (DSGVO Art. 9): nur fuer den Profilinhaber selbst und nur mit
-- dokumentierter Einwilligung. Datenschutz-Folgenabschaetzung vor dem ersten Eintrag.
create table if not exists __SCHEMA__.speakers (
  id                   uuid primary key default gen_random_uuid(),
  user_id              uuid not null references auth.users(id) on delete cascade,  -- Besitzer des Profils
  name                 text not null check (length(name) between 1 and 60),
  is_owner             boolean not null default false,   -- true = Profilinhaber selbst
  voiceprint           extensions.vector(192),           -- Sprecher-Embedding (z. B. ECAPA-TDNN)
  consent_at           timestamptz,
  consent_text_version text,
  enrolled_at          timestamptz not null default now(),
  constraint speakers_voiceprint_consent_chk
    check (voiceprint is null or (consent_at is not null and consent_text_version is not null)),
  constraint speakers_voiceprint_owner_chk check (voiceprint is null or is_owner)
);
comment on column __SCHEMA__.speakers.voiceprint is
  'Biometrisch (DSGVO Art. 9). Nur mit dokumentierter Einwilligung des Profilinhabers.';

create table if not exists __SCHEMA__.turns (
  id            uuid primary key default gen_random_uuid(),
  session_id    uuid not null references __SCHEMA__.sessions(id) on delete cascade,
  user_id       uuid not null references auth.users(id) on delete cascade,
  role          __SCHEMA__.turn_role not null,
  content       text not null check (length(content) <= 20000),   -- bei assistant: tatsaechlich Gesprochenes
  interrupted   boolean not null default false,
  speaker_label text,                          -- Diarisierung der Spracherkennung (1, 2, ...)
  speaker_id    uuid references __SCHEMA__.speakers(id) on delete set null,
  latency_ms    integer check (latency_ms >= 0),
  meta          jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now()
);

-- ---------- Gedaechtnis: persoenlich je Profil, gemeinsam fuer alle ----------
create table if not exists __SCHEMA__.memories (
  id                uuid primary key default gen_random_uuid(),
  user_id           uuid not null references auth.users(id) on delete cascade,  -- Besitzer bzw. Autor
  bereich           __SCHEMA__.memory_scope not null default 'persoenlich',
  status            __SCHEMA__.memory_status not null default 'aktiv',
  kind              __SCHEMA__.memory_kind not null,
  content           text not null check (length(content) between 1 and 2000 and btrim(content) <> ''),
  embedding         extensions.vector(1024),
  importance        smallint not null default 3 check (importance between 1 and 5),
  confidence        real not null default 0.8 check (confidence between 0 and 1),
  is_active         boolean not null default true,
  superseded_by     uuid references __SCHEMA__.memories(id) on delete set null,
  source_session_id uuid references __SCHEMA__.sessions(id) on delete set null,
  source_turn_id    uuid references __SCHEMA__.turns(id) on delete set null,
  reviewed_by       uuid references auth.users(id) on delete set null,  -- Admin, der gemeinsam freigegeben hat
  reviewed_at       timestamptz,
  use_count         integer not null default 0 check (use_count >= 0),
  last_used_at      timestamptz,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  constraint memories_personal_active_chk check (bereich = 'gemeinsam' or status = 'aktiv'),
  constraint memories_review_pair_chk check ((reviewed_by is null) = (reviewed_at is null)),
  constraint memories_review_scope_chk check (bereich = 'gemeinsam' or reviewed_by is null)
);

create table if not exists __SCHEMA__.feedback (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  turn_id     uuid references __SCHEMA__.turns(id) on delete set null,
  action_id   uuid,
  kind        __SCHEMA__.feedback_kind not null,
  note        text check (note is null or length(note) <= 4000),
  lesson_id   uuid references __SCHEMA__.memories(id) on delete set null,
  created_at  timestamptz not null default now()
);

-- ---------- Canvas ----------
create table if not exists __SCHEMA__.canvas_items (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references auth.users(id) on delete cascade,
  session_id uuid references __SCHEMA__.sessions(id) on delete set null,
  type       __SCHEMA__.canvas_type not null,
  title      text not null,
  data       jsonb not null,
  version    integer not null default 1 check (version >= 1),
  parent_id  uuid references __SCHEMA__.canvas_items(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- ---------- PC-Aktionen (Audit + Freigaben) ----------
create table if not exists __SCHEMA__.actions (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references auth.users(id) on delete cascade,
  session_id   uuid references __SCHEMA__.sessions(id) on delete set null,
  turn_id      uuid references __SCHEMA__.turns(id) on delete set null,
  device_id    uuid references __SCHEMA__.devices(id) on delete set null,
  tool         text not null,
  params       jsonb not null default '{}'::jsonb,
  reason       text,
  risk         __SCHEMA__.risk_class not null,
  status       __SCHEMA__.action_status not null default 'pending',
  result       jsonb,
  decided_at   timestamptz,
  created_at   timestamptz not null default now(),
  finished_at  timestamptz
);

do $$ begin
  alter table __SCHEMA__.feedback add constraint feedback_action_fk
    foreign key (action_id) references __SCHEMA__.actions(id) on delete set null;
  exception when duplicate_object then null; end $$;

-- ---------- Indizes (passend zu den Abfragen in voice-core/memory.py) ----------
create index if not exists devices_user_idx       on __SCHEMA__.devices (user_id);
create index if not exists sessions_user_idx      on __SCHEMA__.sessions (user_id, started_at desc);
create index if not exists sessions_device_idx    on __SCHEMA__.sessions (device_id) where device_id is not null;
create index if not exists speakers_user_idx      on __SCHEMA__.speakers (user_id);
create unique index if not exists speakers_one_owner_idx on __SCHEMA__.speakers (user_id) where is_owner;
create index if not exists turns_session_idx      on __SCHEMA__.turns (session_id, created_at);
create index if not exists turns_user_idx         on __SCHEMA__.turns (user_id, created_at desc);
create index if not exists turns_speaker_idx      on __SCHEMA__.turns (speaker_id) where speaker_id is not null;
create index if not exists memories_user_all_idx  on __SCHEMA__.memories (user_id);
create index if not exists memories_recall_personal_idx on __SCHEMA__.memories
  (user_id, importance desc, updated_at desc) where is_active and bereich = 'persoenlich';
create index if not exists memories_recall_shared_idx on __SCHEMA__.memories
  (importance desc, updated_at desc) where is_active and bereich = 'gemeinsam' and status = 'aktiv';
create index if not exists memories_proposed_idx on __SCHEMA__.memories
  (created_at) where is_active and bereich = 'gemeinsam' and status = 'vorgeschlagen';
create index if not exists memories_super_idx     on __SCHEMA__.memories (superseded_by) where superseded_by is not null;
create index if not exists memories_session_idx   on __SCHEMA__.memories (source_session_id) where source_session_id is not null;
create index if not exists memories_turn_idx      on __SCHEMA__.memories (source_turn_id) where source_turn_id is not null;
create index if not exists memories_reviewer_idx  on __SCHEMA__.memories (reviewed_by) where reviewed_by is not null;
create index if not exists memories_embedding_idx on __SCHEMA__.memories
  using hnsw (embedding extensions.vector_cosine_ops) where is_active and embedding is not null;
create index if not exists feedback_user_idx      on __SCHEMA__.feedback (user_id);
create index if not exists feedback_turn_idx      on __SCHEMA__.feedback (turn_id) where turn_id is not null;
create index if not exists feedback_action_idx    on __SCHEMA__.feedback (action_id) where action_id is not null;
create index if not exists feedback_lesson_idx    on __SCHEMA__.feedback (lesson_id) where lesson_id is not null;
create index if not exists canvas_user_idx        on __SCHEMA__.canvas_items (user_id, updated_at desc);
create index if not exists canvas_session_idx     on __SCHEMA__.canvas_items (session_id) where session_id is not null;
create index if not exists canvas_parent_idx      on __SCHEMA__.canvas_items (parent_id) where parent_id is not null;
create index if not exists actions_user_idx       on __SCHEMA__.actions (user_id, created_at desc);
create index if not exists actions_pending_idx    on __SCHEMA__.actions (user_id) where status = 'pending';
create index if not exists actions_session_idx    on __SCHEMA__.actions (session_id) where session_id is not null;
create index if not exists actions_turn_idx       on __SCHEMA__.actions (turn_id) where turn_id is not null;
create index if not exists actions_device_idx     on __SCHEMA__.actions (device_id) where device_id is not null;

-- ---------- Trigger ----------
drop trigger if exists profiles_touch on __SCHEMA__.profiles;
create trigger profiles_touch before update on __SCHEMA__.profiles
  for each row execute function __SCHEMA__.touch_updated_at();
drop trigger if exists canvas_touch on __SCHEMA__.canvas_items;
create trigger canvas_touch before update on __SCHEMA__.canvas_items
  for each row execute function __SCHEMA__.touch_updated_at();
-- memories: updated_at nur bei inhaltlicher Aenderung (Sortierschluessel des Recalls bleibt stabil)
drop trigger if exists memories_touch on __SCHEMA__.memories;
create trigger memories_touch before update on __SCHEMA__.memories
  for each row when (old.content is distinct from new.content or old.is_active is distinct from new.is_active
                     or old.status is distinct from new.status or old.importance is distinct from new.importance)
  execute function __SCHEMA__.touch_updated_at();

-- Gemeinsames Wissen wird nur durch einen aktiven Admin (Autor oder Pruefer) wirksam.
create or replace function __SCHEMA__.memories_guard() returns trigger
language plpgsql set search_path = '' as $$
begin
  if new.bereich = 'gemeinsam' and new.status = 'aktiv' then
    if tg_op = 'UPDATE' and old.bereich = 'gemeinsam' and old.status = 'aktiv' then
      return new;   -- schon wirksam, nur andere Spalten geaendert
    end if;
    if not exists (select 1 from __SCHEMA__.profiles p
                   where p.user_id = coalesce(new.reviewed_by, new.user_id) and p.aktiv and p.rolle = 'admin') then
      raise exception 'gemeinsamer Eintrag wird nur durch einen aktiven Admin wirksam' using errcode = '42501';
    end if;
    new.reviewed_by := coalesce(new.reviewed_by, new.user_id);
    new.reviewed_at := coalesce(new.reviewed_at, now());
  end if;
  return new;
end $$;
drop trigger if exists memories_guard on __SCHEMA__.memories;
create trigger memories_guard before insert or update on __SCHEMA__.memories
  for each row execute function __SCHEMA__.memories_guard();

-- Freigaben: Inhalt einer Aktion ist unveraenderlich, Status folgt einem festen Ablauf.
create or replace function __SCHEMA__.actions_guard() returns trigger
language plpgsql set search_path = '' as $$
begin
  if new.user_id is distinct from old.user_id or new.tool is distinct from old.tool
     or new.params is distinct from old.params or new.risk is distinct from old.risk then
    raise exception 'actions: user_id, tool, params und risk sind unveraenderlich' using errcode = '42501';
  end if;
  if new.status is distinct from old.status then
    if not ((old.status = 'pending'  and new.status in ('approved', 'rejected', 'blocked'))
         or (old.status = 'approved' and new.status = 'running')
         or (old.status = 'running'  and new.status in ('done', 'failed'))) then
      raise exception 'actions: Statuswechsel % nach % nicht erlaubt', old.status, new.status using errcode = '23514';
    end if;
    if new.status = 'approved' and old.risk = 'blocked' then
      raise exception 'actions: gesperrte Aktionen sind nicht freigebbar' using errcode = '42501';
    end if;
    if new.status in ('approved', 'rejected', 'blocked') then new.decided_at := now(); end if;
    if new.status in ('done', 'failed') then new.finished_at := coalesce(new.finished_at, now()); end if;
  end if;
  return new;
end $$;
drop trigger if exists actions_guard on __SCHEMA__.actions;
create trigger actions_guard before update on __SCHEMA__.actions
  for each row execute function __SCHEMA__.actions_guard();

-- ---------- Semantische Suche: eigene persoenliche plus aktive gemeinsame Eintraege ----------
-- p_user_id ist Pflicht (Aufrufer ist der Voice-Core als wamo_core, ohne JWT und ohne Zugriff auf auth).
create or replace function __SCHEMA__.match_memories(
  query_embedding extensions.vector(1024),
  match_count int,
  p_user_id uuid
) returns table (id uuid, bereich __SCHEMA__.memory_scope, kind __SCHEMA__.memory_kind, content text, similarity real)
language plpgsql stable security invoker set search_path = '' as $$
begin
  if p_user_id is null then
    raise exception 'match_memories: p_user_id fehlt' using errcode = '22004';
  end if;
  perform set_config('hnsw.iterative_scan', 'strict_order', true);
  perform set_config('hnsw.ef_search', '100', true);
  return query
    select m.id, m.bereich, m.kind, m.content,
           (1 - (m.embedding operator(extensions.<=>) query_embedding))::real
    from __SCHEMA__.memories m
    where m.is_active
      and m.embedding is not null
      and ((m.bereich = 'persoenlich' and m.user_id = p_user_id)
           or (m.bereich = 'gemeinsam' and m.status = 'aktiv'))
    order by m.embedding operator(extensions.<=>) query_embedding
    limit greatest(1, least(coalesce(match_count, 8), 50));
end $$;

-- ---------- Rechte (geringste Rechte, kein Zugriff fuer anon, authenticated, service_role) ----------
revoke all on all tables in schema __SCHEMA__ from public, anon, authenticated, service_role;
revoke all on all functions in schema __SCHEMA__ from public, anon, authenticated, service_role;

-- Voice-Core
grant select on __SCHEMA__.profiles, __SCHEMA__.devices, __SCHEMA__.schema_migrations to wamo_core;
grant select, insert, update on __SCHEMA__.sessions to wamo_core;
grant select, insert on __SCHEMA__.turns, __SCHEMA__.canvas_items, __SCHEMA__.feedback,
                        __SCHEMA__.memories, __SCHEMA__.actions to wamo_core;
grant update (is_active, use_count, last_used_at) on __SCHEMA__.memories to wamo_core;
grant update (status, decided_at, result, finished_at) on __SCHEMA__.actions to wamo_core;
grant select, insert, update, delete on __SCHEMA__.speakers to wamo_core;   -- delete fuer DSGVO Art. 17
grant execute on function __SCHEMA__.match_memories(extensions.vector, int, uuid) to wamo_core;

-- Verwaltung
grant select, insert on __SCHEMA__.profiles to wamo_admin;
grant update (display_name, rolle, aktiv, anrede) on __SCHEMA__.profiles to wamo_admin;
grant select on __SCHEMA__.memories to wamo_admin;
grant update (status, reviewed_by, reviewed_at, is_active) on __SCHEMA__.memories to wamo_admin;
grant select, insert on __SCHEMA__.schema_migrations to wamo_admin;

-- ---------- Row Level Security ----------
do $$
declare t text;
begin
  foreach t in array array['profiles','devices','sessions','speakers','turns','memories',
                           'feedback','canvas_items','actions','schema_migrations'] loop
    execute format('alter table __SCHEMA__.%I enable row level security', t);
    execute format('drop policy if exists core_all on __SCHEMA__.%I', t);
    -- Der Voice-Core arbeitet ohne JWT; Nutzergrenzen setzt er im Code, Rechte begrenzen die Grants.
    execute format('create policy core_all on __SCHEMA__.%I for all to wamo_core using (true) with check (true)', t);
  end loop;
  foreach t in array array['profiles','memories','schema_migrations'] loop
    execute format('drop policy if exists admin_all on __SCHEMA__.%I', t);
    execute format('create policy admin_all on __SCHEMA__.%I for all to wamo_admin using (true) with check (true)', t);
  end loop;
end $$;

insert into __SCHEMA__.schema_migrations (version) values ('0001_wamo_init') on conflict do nothing;
