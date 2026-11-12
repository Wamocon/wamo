-- Test für 0020_gedaechtnis_vorschlaege. Läuft als postgres in EINER Transaktion, die am Ende immer zurückgerollt wird
-- (Testkonten in auth.users, Profile und Einträge in memories verschwinden wieder). Nur für wamo_test oder wamo_dev.
--   sed "s/__SCHEMA__/wamo_test/g" supabase/tests/0020_gedaechtnis_vorschlaege_test.sql \
--     | ssh wamo-sokrates2 "docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 -q"
-- Voraussetzung: 0001 und 0020 sind im Schema angewendet. Ohne 0020 bricht der Test gleich ab.
-- Erfolg: letzte Zeile "0020 OK", Exit-Code 0. Jeder Fehlschlag bricht mit einer Meldung ab.
\set ON_ERROR_STOP 1
begin;
do $$ begin
  if '__SCHEMA__' = 'wamo_prod' then raise exception 'Test nie in wamo_prod'; end if;
  if not exists (select 1 from __SCHEMA__.schema_migrations where version = '0020_gedaechtnis_vorschlaege') then
    raise exception '0020 ist in __SCHEMA__ nicht angewendet';
  end if;
end $$;

grant wamo_core, wamo_admin to postgres;
create schema wamo_testhilfe;
grant usage on schema wamo_testhilfe to wamo_core, wamo_admin;
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
grant execute on all functions in schema wamo_testhilfe to wamo_core, wamo_admin;

-- Testkonten: zwei Admins, ein Mitglied, ein inaktiver Admin
insert into auth.users (id, email) values
  ('00000000-0000-4000-8000-0000000000a1', 'admin1@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000000a2', 'admin2@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000000b1', 'mitglied1@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000000b4', 'inaktiv@wamo-test.invalid');
insert into __SCHEMA__.profiles (user_id, display_name, rolle, aktiv) values
  ('00000000-0000-4000-8000-0000000000a1', 'Admin Eins', 'admin', true),
  ('00000000-0000-4000-8000-0000000000a2', 'Admin Zwei', 'admin', true),
  ('00000000-0000-4000-8000-0000000000b1', 'Anna', 'mitglied', true),
  ('00000000-0000-4000-8000-0000000000b4', 'Dora', 'admin', false);

-- Einträge: c1 bis c3 offene Vorschläge, c4 persönlich, c5 vergessen, c6 schon abgelehnt, c7 Vorschlag eines Admins
insert into __SCHEMA__.memories (id, user_id, bereich, status, kind, content, is_active) values
  ('00000000-0000-4000-8000-0000000000c1', '00000000-0000-4000-8000-0000000000b1', 'gemeinsam', 'vorgeschlagen', 'fact', 'Vorschlag von Anna', true),
  ('00000000-0000-4000-8000-0000000000c2', '00000000-0000-4000-8000-0000000000b1', 'gemeinsam', 'vorgeschlagen', 'lesson', 'Zweiter Vorschlag', true),
  ('00000000-0000-4000-8000-0000000000c3', '00000000-0000-4000-8000-0000000000b1', 'gemeinsam', 'vorgeschlagen', 'fact', 'Dritter Vorschlag', true),
  ('00000000-0000-4000-8000-0000000000c4', '00000000-0000-4000-8000-0000000000b1', 'persoenlich', 'aktiv', 'fact', 'Persönlicher Eintrag', true),
  ('00000000-0000-4000-8000-0000000000c5', '00000000-0000-4000-8000-0000000000b1', 'gemeinsam', 'vorgeschlagen', 'fact', 'Vergessener Vorschlag', false),
  ('00000000-0000-4000-8000-0000000000c6', '00000000-0000-4000-8000-0000000000b1', 'gemeinsam', 'abgelehnt', 'fact', 'Schon abgelehnt', true),
  ('00000000-0000-4000-8000-0000000000c7', '00000000-0000-4000-8000-0000000000a1', 'gemeinsam', 'vorgeschlagen', 'fact', 'Vorschlag eines Admins', true);

-- 1. Rechte: nur wamo_core ruft die Funktion auf, und direkt ändern darf wamo_core den Status weiter nicht
set role wamo_core;
select wamo_testhilfe.fehler($$update __SCHEMA__.memories set status = 'aktiv' where id = '00000000-0000-4000-8000-0000000000c3'$$, '42501');
select wamo_testhilfe.fehler($$update __SCHEMA__.memories set reviewed_by = '00000000-0000-4000-8000-0000000000a1', reviewed_at = now() where id = '00000000-0000-4000-8000-0000000000c3'$$, '42501');
select wamo_testhilfe.fehler($$delete from __SCHEMA__.memories where id = '00000000-0000-4000-8000-0000000000c3'$$, '42501');
reset role;
set role wamo_admin;
select wamo_testhilfe.fehler($$select __SCHEMA__.gedaechtnis_vorschlag_entscheiden('00000000-0000-4000-8000-0000000000c3', '00000000-0000-4000-8000-0000000000a1', 'freigeben')$$, '42501');
reset role;

-- 2. Eingaben und Akteur: nichts ändert sich
set role wamo_core;
select wamo_testhilfe.fehler($$select __SCHEMA__.gedaechtnis_vorschlag_entscheiden(null, '00000000-0000-4000-8000-0000000000a1', 'freigeben')$$, '22004');
select wamo_testhilfe.fehler($$select __SCHEMA__.gedaechtnis_vorschlag_entscheiden('00000000-0000-4000-8000-0000000000c3', null, 'freigeben')$$, '22004');
select wamo_testhilfe.fehler($$select __SCHEMA__.gedaechtnis_vorschlag_entscheiden('00000000-0000-4000-8000-0000000000c3', '00000000-0000-4000-8000-0000000000a1', null)$$, '22004');
select wamo_testhilfe.fehler($$select __SCHEMA__.gedaechtnis_vorschlag_entscheiden('00000000-0000-4000-8000-0000000000c3', '00000000-0000-4000-8000-0000000000a1', 'aktiv')$$, '22023');
select wamo_testhilfe.fehler($$select __SCHEMA__.gedaechtnis_vorschlag_entscheiden('00000000-0000-4000-8000-0000000000c3', '00000000-0000-4000-8000-0000000000a1', 'Freigeben')$$, '22023');
select wamo_testhilfe.fehler($$select __SCHEMA__.gedaechtnis_vorschlag_entscheiden('00000000-0000-4000-8000-0000000000c3', '00000000-0000-4000-8000-0000000000b1', 'freigeben')$$, '42501'); -- Mitglied
select wamo_testhilfe.fehler($$select __SCHEMA__.gedaechtnis_vorschlag_entscheiden('00000000-0000-4000-8000-0000000000c3', '00000000-0000-4000-8000-0000000000b4', 'freigeben')$$, '42501'); -- inaktiver Admin
select wamo_testhilfe.fehler($$select __SCHEMA__.gedaechtnis_vorschlag_entscheiden('00000000-0000-4000-8000-0000000000c3', '00000000-0000-4000-8000-0000000000ff', 'ablehnen')$$, '42501'); -- unbekanntes Profil
select wamo_testhilfe.gleich((select status::text from __SCHEMA__.memories where id = '00000000-0000-4000-8000-0000000000c3'), 'vorgeschlagen', 'c3 nach Fehlern unverändert');
select wamo_testhilfe.gleich((select reviewed_by is null from __SCHEMA__.memories where id = '00000000-0000-4000-8000-0000000000c3'), true, 'c3 ohne Prüfer');
reset role;

-- 3. Freigeben und Ablehnen durch aktive Admins
set role wamo_core;
select wamo_testhilfe.gleich(__SCHEMA__.gedaechtnis_vorschlag_entscheiden('00000000-0000-4000-8000-0000000000c1', '00000000-0000-4000-8000-0000000000a1', 'freigeben'), 'freigegeben', 'c1 freigeben');
select wamo_testhilfe.gleich(__SCHEMA__.gedaechtnis_vorschlag_entscheiden('00000000-0000-4000-8000-0000000000c2', '00000000-0000-4000-8000-0000000000a2', 'ablehnen'), 'abgelehnt', 'c2 ablehnen');
select wamo_testhilfe.gleich(__SCHEMA__.gedaechtnis_vorschlag_entscheiden('00000000-0000-4000-8000-0000000000c7', '00000000-0000-4000-8000-0000000000a1', 'freigeben'), 'freigegeben', 'Admin gibt den eigenen Vorschlag frei');
select wamo_testhilfe.gleich((select status::text from __SCHEMA__.memories where id = '00000000-0000-4000-8000-0000000000c1'), 'aktiv', 'c1 aktiv');
select wamo_testhilfe.gleich((select reviewed_by::text from __SCHEMA__.memories where id = '00000000-0000-4000-8000-0000000000c1'), '00000000-0000-4000-8000-0000000000a1', 'c1 geprüft von Admin Eins');
select wamo_testhilfe.gleich((select reviewed_at is not null from __SCHEMA__.memories where id = '00000000-0000-4000-8000-0000000000c1'), true, 'c1 mit Zeit');
select wamo_testhilfe.gleich((select status::text from __SCHEMA__.memories where id = '00000000-0000-4000-8000-0000000000c2'), 'abgelehnt', 'c2 abgelehnt');
select wamo_testhilfe.gleich((select reviewed_by::text from __SCHEMA__.memories where id = '00000000-0000-4000-8000-0000000000c2'), '00000000-0000-4000-8000-0000000000a2', 'c2 geprüft von Admin Zwei');
-- Inhalt, Autor, Bereich und Aktivität bleiben
select wamo_testhilfe.gleich((select content from __SCHEMA__.memories where id = '00000000-0000-4000-8000-0000000000c1'), 'Vorschlag von Anna', 'Inhalt unverändert');
select wamo_testhilfe.gleich((select user_id::text from __SCHEMA__.memories where id = '00000000-0000-4000-8000-0000000000c1'), '00000000-0000-4000-8000-0000000000b1', 'Autor unverändert');
select wamo_testhilfe.gleich((select bereich::text from __SCHEMA__.memories where id = '00000000-0000-4000-8000-0000000000c1'), 'gemeinsam', 'Bereich unverändert');
select wamo_testhilfe.gleich((select is_active from __SCHEMA__.memories where id = '00000000-0000-4000-8000-0000000000c1'), true, 'bleibt aktiv im Sinn von is_active');
reset role;

-- 4. Nur ein offener Vorschlag lässt sich entscheiden: alles andere ergibt nicht_offen und ändert nichts
set role wamo_core;
select wamo_testhilfe.gleich(__SCHEMA__.gedaechtnis_vorschlag_entscheiden('00000000-0000-4000-8000-0000000000c1', '00000000-0000-4000-8000-0000000000a2', 'ablehnen'), 'nicht_offen', 'schon freigegeben');
select wamo_testhilfe.gleich(__SCHEMA__.gedaechtnis_vorschlag_entscheiden('00000000-0000-4000-8000-0000000000c2', '00000000-0000-4000-8000-0000000000a1', 'freigeben'), 'nicht_offen', 'schon abgelehnt');
select wamo_testhilfe.gleich(__SCHEMA__.gedaechtnis_vorschlag_entscheiden('00000000-0000-4000-8000-0000000000c4', '00000000-0000-4000-8000-0000000000a1', 'freigeben'), 'nicht_offen', 'persönlicher Eintrag');
select wamo_testhilfe.gleich(__SCHEMA__.gedaechtnis_vorschlag_entscheiden('00000000-0000-4000-8000-0000000000c5', '00000000-0000-4000-8000-0000000000a1', 'freigeben'), 'nicht_offen', 'vergessener Vorschlag');
select wamo_testhilfe.gleich(__SCHEMA__.gedaechtnis_vorschlag_entscheiden('00000000-0000-4000-8000-0000000000c6', '00000000-0000-4000-8000-0000000000a1', 'freigeben'), 'nicht_offen', 'früher abgelehnt');
select wamo_testhilfe.gleich(__SCHEMA__.gedaechtnis_vorschlag_entscheiden('00000000-0000-4000-8000-0000000000e0', '00000000-0000-4000-8000-0000000000a1', 'freigeben'), 'nicht_offen', 'unbekannte Kennung');
select wamo_testhilfe.gleich((select status::text from __SCHEMA__.memories where id = '00000000-0000-4000-8000-0000000000c1'), 'aktiv', 'c1 bleibt aktiv');
select wamo_testhilfe.gleich((select status::text from __SCHEMA__.memories where id = '00000000-0000-4000-8000-0000000000c2'), 'abgelehnt', 'c2 bleibt abgelehnt');
select wamo_testhilfe.gleich((select status::text from __SCHEMA__.memories where id = '00000000-0000-4000-8000-0000000000c4'), 'aktiv', 'c4 persönlich bleibt aktiv');
select wamo_testhilfe.gleich((select reviewed_by is null from __SCHEMA__.memories where id = '00000000-0000-4000-8000-0000000000c4'), true, 'c4 ohne Prüfer');
select wamo_testhilfe.gleich((select status::text from __SCHEMA__.memories where id = '00000000-0000-4000-8000-0000000000c5'), 'vorgeschlagen', 'c5 bleibt vorgeschlagen');
select wamo_testhilfe.gleich((select reviewed_by::text from __SCHEMA__.memories where id = '00000000-0000-4000-8000-0000000000c1'), '00000000-0000-4000-8000-0000000000a1', 'Prüfer von c1 bleibt');
reset role;

-- 5. Festhalten der Rechte
do $$
declare
  v_fn regprocedure := '__SCHEMA__.gedaechtnis_vorschlag_entscheiden(uuid, uuid, text)'::regprocedure;
begin
  perform wamo_testhilfe.gleich(has_function_privilege('wamo_core', v_fn, 'EXECUTE'), true, 'wamo_core darf aufrufen');
  perform wamo_testhilfe.gleich(has_function_privilege('wamo_admin', v_fn, 'EXECUTE'), false, 'wamo_admin nicht');
  perform wamo_testhilfe.gleich(has_function_privilege('anon', v_fn, 'EXECUTE'), false, 'anon nicht');
  perform wamo_testhilfe.gleich(has_function_privilege('authenticated', v_fn, 'EXECUTE'), false, 'authenticated nicht');
  perform wamo_testhilfe.gleich(has_function_privilege('service_role', v_fn, 'EXECUTE'), false, 'service_role nicht');
  perform wamo_testhilfe.gleich(has_column_privilege('wamo_core', '__SCHEMA__.memories', 'status', 'UPDATE'), false, 'kein direktes UPDATE auf status');
  perform wamo_testhilfe.gleich((select p.prosecdef from pg_proc p where p.oid = v_fn), true, 'SECURITY DEFINER');
  perform wamo_testhilfe.gleich(exists (select 1 from pg_proc p, unnest(p.proconfig) c where p.oid = v_fn and c like 'search_path=%'), true, 'search_path gesetzt');
end $$;

select '0020 OK';
rollback;
