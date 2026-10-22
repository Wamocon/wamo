-- Rückweg zu 0011: entfernt die Modellwahl der Personen und alle hinterlegten (verschlüsselten) Claude-Code-Token.
-- Danach gilt für jedes Profil wieder nur die Regel aus 0010 (eigener API-Schlüssel oder nur Hausmodelle).
-- In wamo_prod nur nach Backup (alle Token-Chiffrate gehen verloren) und ausdrücklicher Freigabe:
--   docker exec -e PGOPTIONS='-c wamo.allow_drop=ja' -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 --single-transaction
do $$ begin
  if '__SCHEMA__' = 'wamo_prod' and coalesce(current_setting('wamo.allow_drop', true), '') <> 'ja' then
    raise exception 'Down in wamo_prod gesperrt. Nach Backup: PGOPTIONS="-c wamo.allow_drop=ja"';
  end if;
end $$;

set local lock_timeout = '5s';

-- Wächter zurück auf den Stand von 0010 (ohne Claude-Code-Token); die Spalten hängen nicht mehr daran
alter table __SCHEMA__.model_access
  drop constraint if exists model_access_codetoken_vollstaendig,
  drop constraint if exists model_access_nur_haus_ohne_codetoken;

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
  if tg_op = 'UPDATE' and new.claude_schluessel is null and old.claude_schluessel is not null then
    new.claude_schluessel_ende := null; new.claude_workspace := null; new.claude_geprueft_at := null;
  end if;
  if new.nur_haus and (tg_op = 'INSERT' or not old.nur_haus) then
    new.claude_schluessel := null; new.claude_schluessel_ende := null;
    new.claude_workspace := null;  new.claude_geprueft_at := null;
  end if;
  return new;
end $$;

-- Wer nur Hausmodelle gewählt hat, soll danach nicht plötzlich mit Claude bedient werden, weil die Wahl verschwindet und
-- der Schlüssel bleibt: sein API-Schlüssel wird mit gelöscht (er trägt ihn bei Bedarf neu ein). Der Wächter räumt die
-- Angaben (Ende, Workspace, geprüft) mit.
update __SCHEMA__.model_access set claude_schluessel = null
 where modell_modus = 'nur_haus' and claude_schluessel is not null;

alter table __SCHEMA__.model_access
  drop column if exists modell_modus,
  drop column if exists claude_code_token,
  drop column if exists claude_code_token_ende,
  drop column if exists claude_code_token_at;

delete from __SCHEMA__.schema_migrations where version = '0011_modellmodus_und_codetoken';
