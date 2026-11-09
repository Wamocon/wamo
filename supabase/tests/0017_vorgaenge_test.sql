-- Test für 0017_vorgaenge. Läuft als postgres in EINER Transaktion, die am Ende immer zurückgerollt wird (Testkonten, Sitzungen und
-- Vorgänge verschwinden wieder). Nur für wamo_test oder wamo_dev. Wird von Hand ausgeführt, nicht von der CI:
--   sed "s/__SCHEMA__/wamo_test/g" supabase/tests/0017_vorgaenge_test.sql \
--     | ssh wamo-sokrates2 "docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 -q"
-- Voraussetzung: 0001 und 0017 sind im Schema angewendet.
-- Erfolg: letzte Zeile "0017 OK", Exit-Code 0. Jeder Fehlschlag bricht mit einer Meldung ab.
\set ON_ERROR_STOP 1
begin;
do $$ begin
  if '__SCHEMA__' = 'wamo_prod' then raise exception 'Test nie in wamo_prod'; end if;
end $$;

grant wamo_core, wamo_admin to postgres;
create schema wamo_testhilfe;
grant usage on schema wamo_testhilfe to wamo_core, wamo_admin, anon, authenticated, service_role;
-- erwartet einen Fehler mit genau diesem SQLSTATE
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
-- anon, authenticated und service_role haben auf dem Schema kein usage (0001): hier vorübergehend, damit die Sperre
-- wirklich an den Tabellenrechten scheitert und nicht schon am Schema
grant usage on schema __SCHEMA__ to anon, authenticated, service_role;

-- Testkonten und eine Sitzung
insert into auth.users (id, email) values
  ('00000000-0000-4000-8000-0000000017a1', 'vorgang-eins@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000017a2', 'vorgang-zwei@wamo-test.invalid');
insert into __SCHEMA__.sessions (id, user_id) values
  ('00000000-0000-4000-8000-0000000017b1', '00000000-0000-4000-8000-0000000017a1');

-- 1. Migration eingetragen, Tabelle mit Zeilensicherheit und Policy core_all, Index für die Sitzung partiell
do $$ begin
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.schema_migrations where version = '0017_vorgaenge'), 1::bigint, 'Migration eingetragen');
  perform wamo_testhilfe.gleich((select c.relrowsecurity from pg_class c where c.oid = '__SCHEMA__.vorgaenge'::regclass), true, 'RLS an (relrowsecurity)');
  perform wamo_testhilfe.gleich((select count(*) from pg_policies where schemaname = '__SCHEMA__' and tablename = 'vorgaenge' and policyname = 'core_all'), 1::bigint, 'Policy core_all');
  perform wamo_testhilfe.gleich((select count(*) from pg_indexes where schemaname = '__SCHEMA__' and tablename = 'vorgaenge'
                                   and indexname = 'vorgaenge_session_idx' and indexdef like '%WHERE (session_id IS NOT NULL)%'), 1::bigint, 'Index partiell');
end $$;

-- 2. wamo_core: anlegen, lesen, Stand fortschreiben; Beginn, Profil, Art und Kennung bleiben; kein delete, kein truncate
set role wamo_core;
insert into __SCHEMA__.vorgaenge (id, user_id, session_id, art, titel, status, gesamt, daten, gestartet_at) values
  ('00000000-0000-4000-8000-0000000017c1', '00000000-0000-4000-8000-0000000017a1', '00000000-0000-4000-8000-0000000017b1',
   'entwicklung', 'Umsetzung: rezepte-app', 'laeuft', 0.25, '{"schritte": [{"nr": 1, "titel": "Tests", "status": "fertig"}]}',
   now() - interval '1 minute');
-- ohne Kennung vergibt die Datenbank selbst eine
insert into __SCHEMA__.vorgaenge (user_id, art, titel, status) values
  ('00000000-0000-4000-8000-0000000017a2', 'aufgabe', 'Code: Beispiel', 'geplant');
select wamo_testhilfe.gleich((select count(*) from __SCHEMA__.vorgaenge where user_id in
  ('00000000-0000-4000-8000-0000000017a1', '00000000-0000-4000-8000-0000000017a2')), 2::bigint, 'zwei Zeilen');
update __SCHEMA__.vorgaenge set status = 'wartet_freigabe', gesamt = 0.5, daten = '{"freigabe_id": "abcdef0123456789"}', titel = 'Umsetzung: neu'
  where id = '00000000-0000-4000-8000-0000000017c1';
update __SCHEMA__.vorgaenge set status = 'fertig', gesamt = 1, beendet_at = now() where id = '00000000-0000-4000-8000-0000000017c1';
select wamo_testhilfe.gleich((select status from __SCHEMA__.vorgaenge where id = '00000000-0000-4000-8000-0000000017c1'), 'fertig', 'Status fortgeschrieben');
-- Das Schreiben des Kerns ist ein Upsert: gleiche Kennung und gleiches Profil aktualisiert, ein anderes Profil lässt die Zeile unberührt
insert into __SCHEMA__.vorgaenge (id, user_id, art, titel, status, gesamt) values
  ('00000000-0000-4000-8000-0000000017c1', '00000000-0000-4000-8000-0000000017a1', 'entwicklung', 'Upsert eigenes Profil', 'fertig', 1)
  on conflict (id) do update set titel = excluded.titel, status = excluded.status, gesamt = excluded.gesamt
  where __SCHEMA__.vorgaenge.user_id = excluded.user_id;
insert into __SCHEMA__.vorgaenge (id, user_id, art, titel, status, gesamt) values
  ('00000000-0000-4000-8000-0000000017c1', '00000000-0000-4000-8000-0000000017a2', 'entwicklung', 'Upsert fremdes Profil', 'fehler', 0)
  on conflict (id) do update set titel = excluded.titel, status = excluded.status, gesamt = excluded.gesamt
  where __SCHEMA__.vorgaenge.user_id = excluded.user_id;
select wamo_testhilfe.gleich((select titel from __SCHEMA__.vorgaenge where id = '00000000-0000-4000-8000-0000000017c1'), 'Upsert eigenes Profil', 'Upsert gilt für das eigene Profil');
-- nicht erlaubt
select wamo_testhilfe.fehler($s$delete from __SCHEMA__.vorgaenge$s$, '42501');
select wamo_testhilfe.fehler($s$truncate __SCHEMA__.vorgaenge$s$, '42501');
select wamo_testhilfe.fehler($s$update __SCHEMA__.vorgaenge set user_id = '00000000-0000-4000-8000-0000000017a2'$s$, '42501');
select wamo_testhilfe.fehler($s$update __SCHEMA__.vorgaenge set art = 'geraet'$s$, '42501');
select wamo_testhilfe.fehler($s$update __SCHEMA__.vorgaenge set id = gen_random_uuid()$s$, '42501');
select wamo_testhilfe.fehler($s$update __SCHEMA__.vorgaenge set gestartet_at = now()$s$, '42501');
reset role;

-- 3. Prüfungen der Werte: Art, Status, Gesamtfortschritt, Titel, Stand, Ende nach Beginn
set role wamo_core;
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.vorgaenge (user_id, art, titel, status) values ('00000000-0000-4000-8000-0000000017a1', 'unbekannt', 'T', 'geplant')$s$, '23514');
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.vorgaenge (user_id, art, titel, status) values ('00000000-0000-4000-8000-0000000017a1', 'aufgabe', 'T', 'kaputt')$s$, '23514');
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.vorgaenge (user_id, art, titel, status, gesamt) values ('00000000-0000-4000-8000-0000000017a1', 'aufgabe', 'T', 'laeuft', 1.5)$s$, '23514');
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.vorgaenge (user_id, art, titel, status, gesamt) values ('00000000-0000-4000-8000-0000000017a1', 'aufgabe', 'T', 'laeuft', -0.1)$s$, '23514');
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.vorgaenge (user_id, art, titel, status) values ('00000000-0000-4000-8000-0000000017a1', 'aufgabe', '', 'laeuft')$s$, '23514');
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.vorgaenge (user_id, art, titel, status) values ('00000000-0000-4000-8000-0000000017a1', 'aufgabe', repeat('x', 301), 'laeuft')$s$, '23514');
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.vorgaenge (user_id, art, titel, status, daten) values ('00000000-0000-4000-8000-0000000017a1', 'aufgabe', 'T', 'laeuft', '[1]')$s$, '23514');
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.vorgaenge (user_id, art, titel, status, gestartet_at, beendet_at) values ('00000000-0000-4000-8000-0000000017a1', 'aufgabe', 'T', 'fertig', now(), now() - interval '1 hour')$s$, '23514');
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.vorgaenge (user_id, art, titel, status) values (gen_random_uuid(), 'aufgabe', 'T', 'laeuft')$s$, '23503');
reset role;

-- 4. Keine anderen Rollen: weder lesen noch schreiben
set role wamo_admin;
select wamo_testhilfe.fehler($s$select count(*) from __SCHEMA__.vorgaenge$s$, '42501');
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.vorgaenge (user_id, art, titel, status) values ('00000000-0000-4000-8000-0000000017a1', 'aufgabe', 'T', 'laeuft')$s$, '42501');
reset role;
set role anon;
select wamo_testhilfe.fehler($s$select count(*) from __SCHEMA__.vorgaenge$s$, '42501');
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.vorgaenge (user_id, art, titel, status) values ('00000000-0000-4000-8000-0000000017a1', 'aufgabe', 'T', 'laeuft')$s$, '42501');
reset role;
set role authenticated;
select wamo_testhilfe.fehler($s$select count(*) from __SCHEMA__.vorgaenge$s$, '42501');
select wamo_testhilfe.fehler($s$update __SCHEMA__.vorgaenge set status = 'fertig'$s$, '42501');
reset role;
set role service_role;
select wamo_testhilfe.fehler($s$select count(*) from __SCHEMA__.vorgaenge$s$, '42501');
select wamo_testhilfe.fehler($s$delete from __SCHEMA__.vorgaenge$s$, '42501');
reset role;

-- 5. Fremdschlüssel: Sitzung weg setzt session_id auf null (die Zeile bleibt), Profil weg nimmt seine Vorgänge mit
delete from __SCHEMA__.sessions where id = '00000000-0000-4000-8000-0000000017b1';
do $$ begin
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.vorgaenge
                                  where id = '00000000-0000-4000-8000-0000000017c1' and session_id is null), 1::bigint, 'session_id auf null gesetzt');
end $$;
delete from auth.users where id = '00000000-0000-4000-8000-0000000017a2';
do $$ begin
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.vorgaenge where user_id = '00000000-0000-4000-8000-0000000017a2'), 0::bigint, 'Vorgänge des gelöschten Kontos entfernt');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.vorgaenge where user_id = '00000000-0000-4000-8000-0000000017a1'), 1::bigint, 'Vorgänge des anderen Kontos bleiben');
end $$;

-- 6. Rechte als Zahlen: genau die vereinbarten Spalten sind für wamo_core änderbar
do $$ begin
  perform wamo_testhilfe.gleich(has_table_privilege('wamo_core', '__SCHEMA__.vorgaenge', 'UPDATE,DELETE,TRUNCATE'), false, 'kein Tabellenrecht außer lesen und einfügen');
  perform wamo_testhilfe.gleich(has_table_privilege('wamo_core', '__SCHEMA__.vorgaenge', 'SELECT') and has_table_privilege('wamo_core', '__SCHEMA__.vorgaenge', 'INSERT'), true, 'lesen und einfügen');
  perform wamo_testhilfe.gleich((select count(*) from information_schema.columns c
                                   where c.table_schema = '__SCHEMA__' and c.table_name = 'vorgaenge'
                                     and has_column_privilege('wamo_core', '__SCHEMA__.vorgaenge', c.column_name, 'UPDATE')), 6::bigint, 'sechs änderbare Spalten');
  perform wamo_testhilfe.gleich(has_any_column_privilege('wamo_admin', '__SCHEMA__.vorgaenge', 'SELECT,INSERT,UPDATE'), false, 'wamo_admin ohne Rechte');
end $$;

rollback;
\echo 0017 OK
