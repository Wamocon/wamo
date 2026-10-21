-- WAMO 0010: Modellzugang je Profil. __SCHEMA__ wird ersetzt durch wamo_dev | wamo_test | wamo_prod.
-- Anwenden (genau einmal je Schema, in EINER Transaktion):
--   bash scripts/render-migration.sh <schema> supabase/migrations/0010_modellzugang.sql \
--     | docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 --single-transaction
-- Die Nummern 0002 bis 0009 gehören anderen Strängen (Geräte, Teilen); 0010 hängt nur von 0001 ab.
--
-- Zwei Arten je Profil (voice-core/modellzugang.py):
--   eigener Claude-Schlüssel  Die Person hinterlegt ihren Anthropic-Schlüssel; Standardmodell Haiku.
--   nur Hausmodelle           Alles über LiteLLM und SearXNG, kein Aufruf an Anthropic. Gilt immer, wenn kein
--                             Schlüssel hinterlegt ist oder ein Admin das Profil festgelegt hat (nur_haus).
-- Der Schlüssel liegt nur verschlüsselt in claude_schluessel (AES-256-GCM, Profil-ID als zusätzliche
-- Daten, Hauptschlüssel WAMO_SCHLUESSEL_KEK nur in der Umgebung des Kerns). Die Datenbank sieht nie den
-- Klartext. wamo_admin darf die verschlüsselte Spalte weder lesen noch befüllen, nur leeren.
-- stt und tts bereiten Phase P5 vor (Spracherkennung und Sprachausgabe im Haus, Strang "Stimme").

create table if not exists __SCHEMA__.model_access (
  user_id                uuid primary key references __SCHEMA__.profiles(user_id) on delete cascade,
  nur_haus               boolean not null default false,
  claude_schluessel      bytea check (claude_schluessel is null or octet_length(claude_schluessel) between 40 and 1024),
  claude_schluessel_ende text check (claude_schluessel_ende is null or claude_schluessel_ende ~ '^[A-Za-z0-9_-]{4}$'),
  claude_workspace       text check (claude_workspace is null or claude_workspace ~ '^wrkspc_[A-Za-z0-9]{6,60}$'),
  claude_modell          text check (claude_modell is null or claude_modell ~ '^claude-[a-z0-9-]{3,60}$'),
  claude_geprueft_at     timestamptz,
  stt                    text not null default 'soniox' check (stt in ('soniox', 'haus')),
  tts                    text not null default 'soniox' check (tts in ('soniox', 'haus')),
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),
  constraint model_access_schluessel_vollstaendig
    check ((claude_schluessel is null) = (claude_schluessel_ende is null)),
  constraint model_access_nur_haus_ohne_schluessel
    check (not (nur_haus and claude_schluessel is not null)),
  constraint model_access_metadaten_nur_mit_schluessel
    check (claude_schluessel is not null or (claude_workspace is null and claude_geprueft_at is null))
);
comment on column __SCHEMA__.model_access.claude_schluessel is
  'Anthropic-Schlüssel der Person, AES-256-GCM (Kopf v1, Kennung des Hauptschlüssels, Nonce), Profil-ID als AAD; '
  'Klartext nie in der Datenbank, Hauptschlüssel WAMO_SCHLUESSEL_KEK nur beim Kern';

drop trigger if exists model_access_touch on __SCHEMA__.model_access;
create trigger model_access_touch before update on __SCHEMA__.model_access
  for each row execute function __SCHEMA__.touch_updated_at();

-- Die Verwaltung (Rechte von wamo_admin, auch über eine Login-Rolle als Mitglied) darf einen Schlüssel nur
-- entfernen, nie setzen, ersetzen oder seine Angaben ändern; die Spalten bleiben in der Hand des Kerns.
-- Der Wechsel auf nur_haus räumt den Schlüssel selbst, damit der Admin die Schlüsselspalten nie nennen muss.
create or replace function __SCHEMA__.model_access_guard() returns trigger
language plpgsql set search_path = '' as $$
begin
  if pg_has_role(current_user, 'wamo_admin', 'USAGE') and not pg_has_role(current_user, 'wamo_core', 'USAGE')
     and new.claude_schluessel is not null then
    if tg_op = 'INSERT' then
      raise exception 'wamo_admin darf keinen Claude-Schlüssel setzen, nur entfernen' using errcode = '42501';
    elsif (new.claude_schluessel, new.claude_schluessel_ende, new.claude_workspace, new.claude_geprueft_at)
          is distinct from
          (old.claude_schluessel, old.claude_schluessel_ende, old.claude_workspace, old.claude_geprueft_at) then
      raise exception 'wamo_admin darf einen Claude-Schlüssel nur entfernen' using errcode = '42501';
    end if;
  end if;
  if tg_op = 'INSERT' then
    if new.nur_haus then
      new.claude_schluessel := null; new.claude_schluessel_ende := null;
      new.claude_workspace := null;  new.claude_geprueft_at := null;
    end if;
  elsif new.nur_haus and not old.nur_haus then
    new.claude_schluessel := null; new.claude_schluessel_ende := null;
    new.claude_workspace := null;  new.claude_geprueft_at := null;
  end if;
  return new;
end $$;

drop trigger if exists model_access_guard on __SCHEMA__.model_access;
create trigger model_access_guard before insert or update on __SCHEMA__.model_access
  for each row execute function __SCHEMA__.model_access_guard();

-- ---------- Rechte (geringste Rechte, Spaltenrechte für das Chiffrat) ----------
-- Auch wamo_core und wamo_admin zuerst zurücksetzen: ein zweiter Lauf nimmt so entfernte Spaltenrechte zurück.
revoke all on __SCHEMA__.model_access from public, anon, authenticated, service_role, wamo_core, wamo_admin;
revoke all on function __SCHEMA__.model_access_guard() from public, anon, authenticated, service_role;

-- Voice-Core: liest den Zugang der Sitzung, hinterlegt, ersetzt und leert den eigenen Schlüssel.
-- Kein delete, keine Rechte auf nur_haus, Modell und Stimme (das legt nur ein Admin fest).
grant select (user_id, nur_haus, claude_schluessel, claude_schluessel_ende, claude_workspace, claude_modell,
              claude_geprueft_at, stt, tts, created_at, updated_at) on __SCHEMA__.model_access to wamo_core;
grant insert (user_id, claude_schluessel, claude_schluessel_ende, claude_workspace, claude_geprueft_at)
  on __SCHEMA__.model_access to wamo_core;
grant update (claude_schluessel, claude_schluessel_ende, claude_workspace, claude_geprueft_at)
  on __SCHEMA__.model_access to wamo_core;

-- Verwaltung: sieht nie das Chiffrat; legt nur_haus, Modell und Stimme fest und darf den Schlüssel leeren.
grant select (user_id, nur_haus, claude_schluessel_ende, claude_workspace, claude_modell, claude_geprueft_at,
              stt, tts, created_at, updated_at) on __SCHEMA__.model_access to wamo_admin;
grant insert (user_id, nur_haus, claude_modell, stt, tts) on __SCHEMA__.model_access to wamo_admin;
grant update (nur_haus, claude_modell, stt, tts, claude_schluessel, claude_schluessel_ende, claude_workspace,
              claude_geprueft_at) on __SCHEMA__.model_access to wamo_admin;

-- ---------- Row Level Security ----------
alter table __SCHEMA__.model_access enable row level security;
drop policy if exists core_all on __SCHEMA__.model_access;
-- Der Voice-Core arbeitet ohne JWT; die Grenze je Profil setzt er im Code (immer where user_id = Sitzung).
create policy core_all on __SCHEMA__.model_access for all to wamo_core using (true) with check (true);
drop policy if exists admin_all on __SCHEMA__.model_access;
create policy admin_all on __SCHEMA__.model_access for all to wamo_admin using (true) with check (true);

insert into __SCHEMA__.schema_migrations (version) values ('0010_modellzugang') on conflict do nothing;
