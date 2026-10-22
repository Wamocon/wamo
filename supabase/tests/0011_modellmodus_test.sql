-- Test für 0011_modellmodus_und_codetoken. Läuft als postgres in EINER Transaktion, die am Ende immer zurückgerollt
-- wird (Testkonten und Profile verschwinden wieder). Nur für wamo_test oder wamo_dev.
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
create function wamo_testhilfe.fehler(p_sql text, p_code text) returns void language plpgsql as $f$
begin
  begin
    execute p_sql;
  exception when others then
    if sqlstate <> p_code then
      raise exception 'erwartet %, bekam % (%) bei: %', p_code, sqlstate, sqlerrm, p_sql;
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

insert into auth.users (id, email) values
  ('00000000-0000-4000-8000-0000000011a1', 'modus1@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000011a2', 'modus2@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000011a3', 'modus3@wamo-test.invalid');
insert into __SCHEMA__.profiles (user_id, display_name, rolle, aktiv) values
  ('00000000-0000-4000-8000-0000000011a1', 'Modus Eins', 'mitglied', true),
  ('00000000-0000-4000-8000-0000000011a2', 'Modus Zwei', 'mitglied', true),
  ('00000000-0000-4000-8000-0000000011a3', 'Modus Drei', 'mitglied', true);

-- 1. Migration eingetragen, Standard automatisch
do $$ begin
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.schema_migrations
                                  where version = '0011_modellmodus_und_codetoken'), 1::bigint, 'Migration eingetragen');
end $$;

-- 2. Der Kern setzt die Wahl, der Standard ist automatisch, ungültige Werte gehen nicht
set role wamo_core;
do $$ begin
  insert into __SCHEMA__.model_access (user_id) values ('00000000-0000-4000-8000-0000000011a1');
  perform wamo_testhilfe.gleich((select modell_modus from __SCHEMA__.model_access
                                  where user_id = '00000000-0000-4000-8000-0000000011a1'), 'automatisch', 'Standard');
  insert into __SCHEMA__.model_access (user_id, modell_modus) values ('00000000-0000-4000-8000-0000000011a2', 'nur_haus')
    on conflict (user_id) do update set modell_modus = excluded.modell_modus;
  perform wamo_testhilfe.gleich((select modell_modus from __SCHEMA__.model_access
                                  where user_id = '00000000-0000-4000-8000-0000000011a2'), 'nur_haus', 'Wahl nur_haus');
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
reset role;

-- 4. Die Verwaltung sieht und ändert weder die Wahl noch den Token; sie legt nur nur_haus fest
set role wamo_core;
update __SCHEMA__.model_access set claude_code_token = decode(repeat('cd', 60), 'hex'),
       claude_code_token_ende = 'abcd', claude_code_token_at = now()
 where user_id = '00000000-0000-4000-8000-0000000011a3' or user_id = '00000000-0000-4000-8000-0000000011a1';
insert into __SCHEMA__.model_access (user_id, claude_code_token, claude_code_token_ende, claude_code_token_at)
  values ('00000000-0000-4000-8000-0000000011a3', decode(repeat('cd', 60), 'hex'), 'abcd', now())
  on conflict (user_id) do update set claude_code_token = excluded.claude_code_token,
    claude_code_token_ende = excluded.claude_code_token_ende, claude_code_token_at = excluded.claude_code_token_at;
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
  -- nur_haus durch den Admin räumt den Token (Wächter), die Verwaltung liest ihn nie
  update __SCHEMA__.model_access set nur_haus = true where user_id = '00000000-0000-4000-8000-0000000011a3';
end $$;
reset role;
set role wamo_core;
do $$ begin
  perform wamo_testhilfe.gleich((select claude_code_token is null and claude_code_token_ende is null
                                    and claude_code_token_at is null from __SCHEMA__.model_access
                                  where user_id = '00000000-0000-4000-8000-0000000011a3'), true, 'nur_haus räumt den Token');
  perform wamo_testhilfe.gleich((select nur_haus from __SCHEMA__.model_access
                                  where user_id = '00000000-0000-4000-8000-0000000011a3'), true, 'nur_haus gesetzt');
  -- Token neben nur_haus ist verboten (auch für den Kern)
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.model_access set claude_code_token = decode(repeat('ab', 60), 'hex'),
    claude_code_token_ende = 'wxyz', claude_code_token_at = now()
    where user_id = '00000000-0000-4000-8000-0000000011a3'$s$, '23514');
end $$;
reset role;

-- 5. Fremde Rollen sehen nichts
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
