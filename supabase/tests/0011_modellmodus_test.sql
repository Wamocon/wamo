-- Test für 0011_modellmodus_und_codetoken. Läuft als postgres in EINER Transaktion, die am Ende immer zurückgerollt
-- wird (Testkonten, Profile und vorübergehende Rechte verschwinden wieder). Nur für wamo_test oder wamo_dev.
--   sed "s/__SCHEMA__/wamo_test/g" supabase/tests/0011_modellmodus_test.sql \
--     | ssh sokrates2 "docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 -q"
-- Erfolg: letzte Zeile "0011 OK", Exit-Code 0. Jeder Fehlschlag bricht mit einer Meldung ab.
\set ON_ERROR_STOP 1
begin;
do $$ begin
  if '__SCHEMA__' = 'wamo_prod' then raise exception 'Test nie in wamo_prod'; end if;
end $$;

grant wamo_core, wamo_admin to postgres;
create schema wamo_testhilfe;
grant usage on schema wamo_testhilfe to wamo_core, wamo_admin, anon, authenticated, service_role;
-- erwartet einen Fehler mit genau diesem SQLSTATE und (wenn angegeben) einem Muster im Meldungstext
create function wamo_testhilfe.fehler(p_sql text, p_code text, p_muster text default null) returns void
language plpgsql as $f$
begin
  begin
    execute p_sql;
  exception when others then
    if sqlstate <> p_code then
      raise exception 'erwartet %, bekam % (%) bei: %', p_code, sqlstate, sqlerrm, p_sql;
    end if;
    if p_muster is not null and sqlerrm not like p_muster then
      raise exception 'Meldung "%" passt nicht zu "%" bei: %', sqlerrm, p_muster, p_sql;
    end if;
    return;
  end;
  raise exception 'erwartet Fehler %, aber gelungen: %', p_code, p_sql;
end $f$;
create function wamo_testhilfe.gleich(p_ist anyelement, p_soll anyelement, p_was text) returns void language plpgsql as $f$
begin
  if p_ist is distinct from p_soll then raise exception '%: ist %, soll %', p_was, p_ist, p_soll; end if;
end $f$;
grant execute on all functions in schema wamo_testhilfe to wamo_core, wamo_admin, anon, authenticated, service_role;
-- anon, authenticated und service_role haben auf dem Schema kein usage (0001): hier vorübergehend, damit die Sperre
-- wirklich an den Spaltenrechten scheitert und nicht schon am Schema
grant usage on schema __SCHEMA__ to anon, authenticated, service_role;

insert into auth.users (id, email) values
  ('00000000-0000-4000-8000-0000000011a1', 'modus1@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000011a2', 'modus2@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000011a3', 'modus3@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000011a4', 'modus4@wamo-test.invalid');
insert into __SCHEMA__.profiles (user_id, display_name, rolle, aktiv) values
  ('00000000-0000-4000-8000-0000000011a1', 'Modus Eins', 'mitglied', true),
  ('00000000-0000-4000-8000-0000000011a2', 'Modus Zwei', 'mitglied', true),
  ('00000000-0000-4000-8000-0000000011a3', 'Modus Drei', 'mitglied', true),
  ('00000000-0000-4000-8000-0000000011a4', 'Modus Vier', 'mitglied', true);

-- 1. Migration eingetragen
do $$ begin
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.schema_migrations
                                  where version = '0011_modellmodus_und_codetoken'), 1::bigint, 'Migration eingetragen');
end $$;

-- 2. Der Kern setzt die Wahl, der Standard ist automatisch, ungültige Werte gehen nicht; Upsert auf vorhandene Zeile
set role wamo_core;
do $$ begin
  insert into __SCHEMA__.model_access (user_id) values ('00000000-0000-4000-8000-0000000011a1');
  perform wamo_testhilfe.gleich((select modell_modus from __SCHEMA__.model_access
                                  where user_id = '00000000-0000-4000-8000-0000000011a1'), 'automatisch', 'Standard');
  insert into __SCHEMA__.model_access (user_id, modell_modus) values ('00000000-0000-4000-8000-0000000011a2', 'nur_haus')
    on conflict (user_id) do update set modell_modus = excluded.modell_modus;
  perform wamo_testhilfe.gleich((select modell_modus from __SCHEMA__.model_access
                                  where user_id = '00000000-0000-4000-8000-0000000011a2'), 'nur_haus', 'Wahl nur_haus');
  -- derselbe Upsert auf eine vorhandene Zeile (Konfliktpfad: BEFORE INSERT, dann BEFORE UPDATE)
  insert into __SCHEMA__.model_access (user_id, modell_modus) values ('00000000-0000-4000-8000-0000000011a2', 'automatisch')
    on conflict (user_id) do update set modell_modus = excluded.modell_modus;
  perform wamo_testhilfe.gleich((select modell_modus from __SCHEMA__.model_access
                                  where user_id = '00000000-0000-4000-8000-0000000011a2'), 'automatisch', 'Upsert vorhanden');
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.model_access set modell_modus = 'egal'
    where user_id = '00000000-0000-4000-8000-0000000011a1'$s$, '23514');
  -- der Kern darf die Festlegung des Admins nicht anfassen
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.model_access set nur_haus = true
    where user_id = '00000000-0000-4000-8000-0000000011a1'$s$, '42501');
end $$;

-- 3. Claude-Code-Token: nur vollständig (Chiffrat, Ende, Zeitpunkt zusammen), Chiffrat zu kurz geht nicht
do $$ begin
  update __SCHEMA__.model_access set claude_code_token = decode(repeat('ab', 60), 'hex'),
         claude_code_token_ende = 'wxyz', claude_code_token_at = now()
   where user_id = '00000000-0000-4000-8000-0000000011a1';
  perform wamo_testhilfe.gleich((select claude_code_token_ende from __SCHEMA__.model_access
                                  where user_id = '00000000-0000-4000-8000-0000000011a1'), 'wxyz', 'Token Ende');
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.model_access set claude_code_token_ende = null
    where user_id = '00000000-0000-4000-8000-0000000011a1'$s$, '23514');
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.model_access set claude_code_token = decode('abcd', 'hex'),
    claude_code_token_ende = 'wxyz', claude_code_token_at = now()
    where user_id = '00000000-0000-4000-8000-0000000011a1'$s$, '23514');
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.model_access set claude_code_token_ende = 'zu lang!'
    where user_id = '00000000-0000-4000-8000-0000000011a1'$s$, '23514');
  -- Token und API-Schlüssel sind unabhängig: der Token allein macht kein Profil mit Claude-Gespräch
  perform wamo_testhilfe.gleich((select claude_schluessel is null from __SCHEMA__.model_access
                                  where user_id = '00000000-0000-4000-8000-0000000011a1'), true, 'kein API-Schlüssel');
  -- Löschen: Ende und Zeitpunkt gehen mit (Wächter)
  update __SCHEMA__.model_access set claude_code_token = null where user_id = '00000000-0000-4000-8000-0000000011a1';
  perform wamo_testhilfe.gleich((select claude_code_token_ende from __SCHEMA__.model_access
                                  where user_id = '00000000-0000-4000-8000-0000000011a1'), null::text, 'Ende weg');
  perform wamo_testhilfe.gleich((select claude_code_token_at from __SCHEMA__.model_access
                                  where user_id = '00000000-0000-4000-8000-0000000011a1'), null::timestamptz, 'Zeitpunkt weg');
end $$;

-- 3b. Die Zweige des Wächters für den API-Schlüssel aus 0010 (create or replace hat ihn ersetzt): Leeren räumt die
--     Angaben, der Upsert auf eine vorhandene Zeile geht, Schlüssel und Token liegen nebeneinander
do $$ begin
  update __SCHEMA__.model_access set claude_schluessel = decode(repeat('cd', 60), 'hex'), claude_schluessel_ende = 'abcd',
         claude_workspace = 'wrkspc_abcdef12', claude_geprueft_at = now(),
         claude_code_token = decode(repeat('ef', 60), 'hex'), claude_code_token_ende = 'efgh', claude_code_token_at = now()
   where user_id = '00000000-0000-4000-8000-0000000011a1';
  update __SCHEMA__.model_access set claude_schluessel = null where user_id = '00000000-0000-4000-8000-0000000011a1';
  perform wamo_testhilfe.gleich((select claude_schluessel_ende is null and claude_workspace is null
                                    and claude_geprueft_at is null from __SCHEMA__.model_access
                                  where user_id = '00000000-0000-4000-8000-0000000011a1'), true, 'Schlüssel leeren räumt die Angaben');
  perform wamo_testhilfe.gleich((select claude_code_token_ende from __SCHEMA__.model_access
                                  where user_id = '00000000-0000-4000-8000-0000000011a1'), 'efgh', 'Token bleibt beim Löschen des Schlüssels');
  -- Upsert auf vorhandene Zeile mit dem Token (wie code_token_hinterlegen im Kern)
  insert into __SCHEMA__.model_access (user_id, claude_code_token, claude_code_token_ende, claude_code_token_at)
    values ('00000000-0000-4000-8000-0000000011a1', decode(repeat('12', 60), 'hex'), 'ijkl', now())
    on conflict (user_id) do update set claude_code_token = excluded.claude_code_token,
      claude_code_token_ende = excluded.claude_code_token_ende, claude_code_token_at = excluded.claude_code_token_at;
  perform wamo_testhilfe.gleich((select claude_code_token_ende from __SCHEMA__.model_access
                                  where user_id = '00000000-0000-4000-8000-0000000011a1'), 'ijkl', 'Upsert Token');
end $$;
reset role;

-- 4. Die Verwaltung sieht und ändert weder die Wahl noch den Token; sie legt nur nur_haus fest
set role wamo_core;
insert into __SCHEMA__.model_access (user_id, claude_schluessel, claude_schluessel_ende, claude_code_token,
                                     claude_code_token_ende, claude_code_token_at)
  values ('00000000-0000-4000-8000-0000000011a3', decode(repeat('cd', 60), 'hex'), 'abcd', decode(repeat('cd', 60), 'hex'),
          'abcd', now());
reset role;
set role wamo_admin;
do $$ begin
  perform wamo_testhilfe.fehler($s$select modell_modus from __SCHEMA__.model_access$s$, '42501');
  perform wamo_testhilfe.fehler($s$select claude_code_token from __SCHEMA__.model_access$s$, '42501');
  perform wamo_testhilfe.fehler($s$select claude_code_token_ende from __SCHEMA__.model_access$s$, '42501');
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.model_access set modell_modus = 'nur_haus'$s$, '42501');
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.model_access set claude_code_token_ende = 'abcd'$s$, '42501');
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.model_access (user_id, claude_code_token)
    values ('00000000-0000-4000-8000-0000000011a2', decode(repeat('ef', 60), 'hex'))$s$, '42501');
  -- nur_haus durch den Admin räumt Schlüssel und Token (Wächter), die Verwaltung liest sie nie
  update __SCHEMA__.model_access set nur_haus = true where user_id = '00000000-0000-4000-8000-0000000011a3';
end $$;
reset role;
set role wamo_core;
do $$ begin
  perform wamo_testhilfe.gleich((select claude_code_token is null and claude_code_token_ende is null
                                    and claude_code_token_at is null from __SCHEMA__.model_access
                                  where user_id = '00000000-0000-4000-8000-0000000011a3'), true, 'nur_haus räumt den Token');
  perform wamo_testhilfe.gleich((select claude_schluessel is null and claude_schluessel_ende is null
                                  from __SCHEMA__.model_access
                                  where user_id = '00000000-0000-4000-8000-0000000011a3'), true, 'nur_haus räumt den Schlüssel');
  perform wamo_testhilfe.gleich((select nur_haus from __SCHEMA__.model_access
                                  where user_id = '00000000-0000-4000-8000-0000000011a3'), true, 'nur_haus gesetzt');
  -- Token neben nur_haus ist verboten (auch für den Kern)
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.model_access set claude_code_token = decode(repeat('ab', 60), 'hex'),
    claude_code_token_ende = 'wxyz', claude_code_token_at = now()
    where user_id = '00000000-0000-4000-8000-0000000011a3'$s$, '23514');
end $$;
reset role;

-- 4b. Zweite Sperre: Selbst mit Rechten auf den Spalten (hier vorübergehend vergeben) darf die Verwaltung weder Schlüssel
--     noch Token setzen, ändern oder leeren; der Wächter antwortet mit seiner eigenen Meldung
grant insert (claude_schluessel, claude_schluessel_ende, claude_code_token, claude_code_token_ende, claude_code_token_at),
      update (claude_schluessel, claude_schluessel_ende, claude_code_token, claude_code_token_ende, claude_code_token_at)
  on __SCHEMA__.model_access to wamo_admin;
-- Zeile mit Schlüssel und Token für den Versuch, sie zu ändern
set role wamo_core;
insert into __SCHEMA__.model_access (user_id, claude_schluessel, claude_schluessel_ende, claude_code_token,
                                     claude_code_token_ende, claude_code_token_at)
  values ('00000000-0000-4000-8000-0000000011a4', decode(repeat('cd', 60), 'hex'), 'abcd', decode(repeat('cd', 60), 'hex'),
          'abcd', now());
reset role;
set role wamo_admin;
do $$ begin
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.model_access set claude_code_token = null, claude_code_token_ende = null,
    claude_code_token_at = null where user_id = '00000000-0000-4000-8000-0000000011a4'$s$, '42501', '%Claude-Code-Token nicht%');
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.model_access set claude_code_token = decode(repeat('ab', 60), 'hex')
    where user_id = '00000000-0000-4000-8000-0000000011a4'$s$, '42501', '%Claude-Code-Token nicht%');
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.model_access set claude_schluessel = null, claude_schluessel_ende = null
    where user_id = '00000000-0000-4000-8000-0000000011a4'$s$, '42501', '%Claude-Schlüssel nicht%');
  -- Einfügen (der Wächter läuft vor der Prüfung auf eine doppelte Zeile)
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.model_access (user_id, claude_code_token, claude_code_token_ende,
    claude_code_token_at) values ('00000000-0000-4000-8000-0000000011a2', decode(repeat('ef', 60), 'hex'), 'abcd', now())$s$,
    '42501', '%keinen Claude-Code-Token setzen%');
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.model_access (user_id, claude_schluessel, claude_schluessel_ende)
    values ('00000000-0000-4000-8000-0000000011a2', decode(repeat('ef', 60), 'hex'), 'abcd')$s$, '42501',
    '%keinen Claude-Schlüssel setzen%');
end $$;
reset role;

-- 5. Fremde Rollen sehen nichts (mit usage auf dem Schema, also an den Spaltenrechten gescheitert)
set role anon;
select wamo_testhilfe.fehler($s$select modell_modus from __SCHEMA__.model_access$s$, '42501');
reset role;
set role authenticated;
select wamo_testhilfe.fehler($s$select claude_code_token from __SCHEMA__.model_access$s$, '42501');
reset role;
set role service_role;
select wamo_testhilfe.fehler($s$select claude_code_token from __SCHEMA__.model_access$s$, '42501');
reset role;

rollback;
\echo 0011 OK
