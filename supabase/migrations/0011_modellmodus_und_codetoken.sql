-- WAMO 0011: Modellwahl je Profil (Automatisch oder Nur Hausmodelle) und zweiter Schlüsseltyp (Claude-Code-Token).
-- __SCHEMA__ wird ersetzt durch wamo_dev | wamo_test | wamo_prod. Anwenden (genau einmal je Schema, in EINER Transaktion):
--   bash scripts/render-migration.sh <schema> supabase/migrations/0011_modellmodus_und_codetoken.sql \
--     | docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 --single-transaction
-- Voraussetzung: 0010 (Tabelle model_access). Ein zweiter Lauf ändert nichts (alles mit if not exists bzw. drop ... if exists).
--
-- modell_modus   Die Wahl der Person selbst: automatisch (der Boss wählt unter allen erlaubten Modellen) oder nur_haus.
--                Sie gilt ab der nächsten Sitzung und lässt einen hinterlegten Schlüssel unberührt. Der Zwang eines
--                Admins (nur_haus) bleibt stärker: Der Kern prüft zuerst nur_haus, dann diese Wahl.
-- claude_code_*  Der Claude-Code-Token der Person (aus `claude setup-token`, Max-Tarif). Er gilt nur für Entwicklungs-
--                aufträge, nie für das Gespräch (die Messages-API nimmt ihn nicht). Verschlüsselt wie der API-Schlüssel
--                (AES-256-GCM, Profil-ID und Zweck als zusätzliche Daten, Hauptschlüssel nur beim Kern). wamo_admin hat
--                auf diese Spalten kein Recht; ein Admin leert den Token nur, indem er das Profil auf nur_haus setzt.

do $$ begin
  if to_regclass('__SCHEMA__.model_access') is null then
    raise exception '0011 braucht 0010 (Tabelle __SCHEMA__.model_access fehlt)';
  end if;
end $$;

alter table __SCHEMA__.model_access
  add column if not exists modell_modus text not null default 'automatisch'
    check (modell_modus in ('automatisch', 'nur_haus')),
  add column if not exists claude_code_token bytea
    check (claude_code_token is null or octet_length(claude_code_token) between 40 and 1024),
  add column if not exists claude_code_token_ende text
    check (claude_code_token_ende is null or claude_code_token_ende ~ '^[A-Za-z0-9_-]{4}$'),
  add column if not exists claude_code_token_at timestamptz;

alter table __SCHEMA__.model_access
  drop constraint if exists model_access_codetoken_vollstaendig,
  drop constraint if exists model_access_nur_haus_ohne_codetoken,
  add constraint model_access_codetoken_vollstaendig
    check ((claude_code_token is null) = (claude_code_token_ende is null)
           and (claude_code_token is null) = (claude_code_token_at is null)),
  add constraint model_access_nur_haus_ohne_codetoken
    check (not (nur_haus and claude_code_token is not null));

comment on column __SCHEMA__.model_access.claude_code_token is
  'Claude-Code-Token der Person (claude setup-token), AES-256-GCM (Kopf v1, Kennung des Hauptschlüssels, Nonce), '
  'Profil-ID und Zweck als AAD; Klartext nie in der Datenbank, nur für Entwicklungsaufträge';

-- Wächter: wie in 0010, dazu der Claude-Code-Token. Die Verwaltung darf ihn nie setzen oder ändern; wird er geleert,
-- gehen seine Angaben mit; der Wechsel auf nur_haus räumt API-Schlüssel und Token.
create or replace function __SCHEMA__.model_access_guard() returns trigger
language plpgsql security invoker set search_path = '' as $$
declare
  verwaltung boolean :=
    (pg_has_role(current_user, 'wamo_admin', 'USAGE') and not pg_has_role(current_user, 'wamo_core', 'USAGE'))
    or (pg_has_role(session_user, 'wamo_admin', 'USAGE') and not pg_has_role(session_user, 'wamo_core', 'USAGE'));
begin
  if verwaltung and new.claude_schluessel is not null then
    if tg_op = 'INSERT' then
      raise exception 'wamo_admin darf keinen Claude-Schlüssel setzen' using errcode = '42501';
    elsif (new.claude_schluessel, new.claude_schluessel_ende, new.claude_workspace, new.claude_geprueft_at)
          is distinct from
          (old.claude_schluessel, old.claude_schluessel_ende, old.claude_workspace, old.claude_geprueft_at) then
      raise exception 'wamo_admin darf einen Claude-Schlüssel nicht ändern' using errcode = '42501';
    end if;
  end if;
  if verwaltung and new.claude_code_token is not null then
    if tg_op = 'INSERT' then
      raise exception 'wamo_admin darf keinen Claude-Code-Token setzen' using errcode = '42501';
    elsif (new.claude_code_token, new.claude_code_token_ende, new.claude_code_token_at)
          is distinct from
          (old.claude_code_token, old.claude_code_token_ende, old.claude_code_token_at) then
      raise exception 'wamo_admin darf einen Claude-Code-Token nicht ändern' using errcode = '42501';
    end if;
  end if;
  -- Wird der Schlüssel oder der Token geleert, gehen die Angaben mit
  if tg_op = 'UPDATE' and new.claude_schluessel is null and old.claude_schluessel is not null then
    new.claude_schluessel_ende := null; new.claude_workspace := null; new.claude_geprueft_at := null;
  end if;
  if tg_op = 'UPDATE' and new.claude_code_token is null and old.claude_code_token is not null then
    new.claude_code_token_ende := null; new.claude_code_token_at := null;
  end if;
  if new.nur_haus and (tg_op = 'INSERT' or not old.nur_haus) then
    new.claude_schluessel := null; new.claude_schluessel_ende := null;
    new.claude_workspace := null;  new.claude_geprueft_at := null;
    new.claude_code_token := null; new.claude_code_token_ende := null; new.claude_code_token_at := null;
  end if;
  return new;
end $$;

-- ---------- Rechte: nur der Kern, nur diese Spalten ----------
-- Die Spaltenrechte der Verwaltung bleiben unverändert (0010); die neuen Spalten bekommt sie nicht.
revoke all (modell_modus, claude_code_token, claude_code_token_ende, claude_code_token_at)
  on __SCHEMA__.model_access from public, anon, authenticated, service_role, wamo_core, wamo_admin;
grant select (modell_modus, claude_code_token, claude_code_token_ende, claude_code_token_at)
  on __SCHEMA__.model_access to wamo_core;
grant insert (modell_modus, claude_code_token, claude_code_token_ende, claude_code_token_at)
  on __SCHEMA__.model_access to wamo_core;
grant update (modell_modus, claude_code_token, claude_code_token_ende, claude_code_token_at)
  on __SCHEMA__.model_access to wamo_core;

-- Selbsttest: bricht die Transaktion bei Rechtedrift ab.
do $$ begin
  if has_column_privilege('wamo_admin', '__SCHEMA__.model_access', 'modell_modus', 'SELECT,INSERT,UPDATE')
     or has_column_privilege('wamo_admin', '__SCHEMA__.model_access', 'claude_code_token', 'SELECT,INSERT,UPDATE')
     or has_column_privilege('wamo_admin', '__SCHEMA__.model_access', 'claude_code_token_ende', 'SELECT,INSERT,UPDATE')
     or has_column_privilege('wamo_admin', '__SCHEMA__.model_access', 'claude_code_token_at', 'SELECT,INSERT,UPDATE')
     or not has_column_privilege('wamo_core', '__SCHEMA__.model_access', 'modell_modus', 'SELECT,INSERT,UPDATE')
     or not has_column_privilege('wamo_core', '__SCHEMA__.model_access', 'claude_code_token', 'SELECT,INSERT,UPDATE')
     or has_column_privilege('wamo_core', '__SCHEMA__.model_access', 'nur_haus', 'INSERT,UPDATE')
     or has_table_privilege('wamo_core', '__SCHEMA__.model_access', 'DELETE,TRUNCATE')
     or has_column_privilege('anon', '__SCHEMA__.model_access', 'claude_code_token', 'SELECT,INSERT,UPDATE')
     or has_column_privilege('authenticated', '__SCHEMA__.model_access', 'claude_code_token', 'SELECT,INSERT,UPDATE')
     or has_column_privilege('service_role', '__SCHEMA__.model_access', 'claude_code_token', 'SELECT,INSERT,UPDATE')
     or has_column_privilege('anon', '__SCHEMA__.model_access', 'modell_modus', 'SELECT,INSERT,UPDATE')
     or has_column_privilege('authenticated', '__SCHEMA__.model_access', 'modell_modus', 'SELECT,INSERT,UPDATE')
     or has_column_privilege('service_role', '__SCHEMA__.model_access', 'modell_modus', 'SELECT,INSERT,UPDATE')
  then
    raise exception 'model_access (0011): unzulässige Rechte (Rechte der neuen Spalten prüfen)' using errcode = '42501';
  end if;
end $$;

insert into __SCHEMA__.schema_migrations (version) values ('0011_modellmodus_und_codetoken') on conflict do nothing;
