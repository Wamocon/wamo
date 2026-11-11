-- Test fuer 0015_rd_sitzungen. Laeuft als postgres in EINER Transaktion, die am Ende immer zurueckgerollt wird
-- (Testkonten in auth.users, Profile und Sitzungen verschwinden wieder). Nur fuer wamo_test oder wamo_dev.
--   sed "s/__SCHEMA__/wamo_test/g" supabase/tests/0015_rd_sitzungen_test.sql \
--     | ssh wamo-sokrates2 "docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 -q"
-- Voraussetzung: 0001 und 0015 sind im Schema angewendet.
-- Erfolg: letzte Zeile "0015 OK", Exit-Code 0. Jeder Fehlschlag bricht mit einer Meldung ab.
\set ON_ERROR_STOP 1
begin;
do $$ begin
  if '__SCHEMA__' = 'wamo_prod' then raise exception 'Test nie in wamo_prod'; end if;
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

-- Vorbedingung: die Tabelle ist leer genug fuer den Test (offene echte Sitzungen wuerden Ergebnisse verfaelschen)
do $$ begin
  if exists (select 1 from __SCHEMA__.rd_sitzungen where ende_at is null) then
    raise exception 'Es laufen echte Fernsitzungen in __SCHEMA__, Test abgebrochen';
  end if;
end $$;

-- Stand der Funktion: Ab Migration 0019 gelten zwei Sperren dieses Tests nicht mehr (Code hoechstens 120 Sekunden alt, ein Code
-- fuer genau einen Start). Der Test kennt beide Staende und prueft die Sperren, solange 0019 nicht angewendet ist; das neue Verhalten
-- prueft 0019_rd_anmeldung_genuegt_test.sql. Der Merker gilt nur in dieser Transaktion.
select set_config('wamo_test.neu19', case when exists (select 1 from __SCHEMA__.schema_migrations
                                                        where version = '0019_rd_anmeldung_genuegt') then 'ja' else 'nein' end, true);

-- 1. Rechte von wamo_core: nur lesen, vier Spalten anlegen, ende_at und grund setzen
set role wamo_core;
select wamo_testhilfe.fehler($$delete from __SCHEMA__.rd_sitzungen$$, '42501');
select wamo_testhilfe.fehler($$truncate __SCHEMA__.rd_sitzungen$$, '42501');
select wamo_testhilfe.fehler($$insert into __SCHEMA__.rd_sitzungen (profil_id, ziel, bis_at, aal2_at, ende_at, grund) values ('00000000-0000-4000-8000-0000000000a1', 'sokrates1', now() + interval '1 hour', now(), now(), 'beendet')$$, '42501');
select wamo_testhilfe.fehler($$insert into __SCHEMA__.rd_sitzungen (id, profil_id, ziel, bis_at, aal2_at) values (gen_random_uuid(), '00000000-0000-4000-8000-0000000000a1', 'sokrates1', now() + interval '1 hour', now())$$, '42501');
select wamo_testhilfe.fehler($$update __SCHEMA__.rd_sitzungen set ziel = 'caesar'$$, '42501');
select wamo_testhilfe.fehler($$update __SCHEMA__.rd_sitzungen set bis_at = now()$$, '42501');
select wamo_testhilfe.fehler($$update __SCHEMA__.rd_sitzungen set profil_id = '00000000-0000-4000-8000-0000000000a2'$$, '42501');
reset role;
set role wamo_admin;
select wamo_testhilfe.fehler($$select * from __SCHEMA__.rd_sitzungen$$, '42501');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', 'sokrates1', 3600, clock_timestamp())$$, '42501');
reset role;

-- 2. Eingaben und Akteur
set role wamo_core;
select wamo_testhilfe.fehler($$select * from __SCHEMA__.rd_sitzung_beginnen(null, 'sokrates1', 3600, clock_timestamp())$$, '22004');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', null, 3600, clock_timestamp())$$, '22004');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', 'sokrates1', 3600, null)$$, '22004');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', 'Sokrates1', 3600, clock_timestamp())$$, '22023');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', 'x; drop table y', 3600, clock_timestamp())$$, '22023');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', 'sokrates1', 30, clock_timestamp())$$, '22023');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', 'sokrates1', 20000, clock_timestamp())$$, '22023');
-- Mitglied, inaktiver Admin, unbekanntes Profil
select wamo_testhilfe.fehler($$select * from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000b1', 'sokrates1', 3600, clock_timestamp())$$, '42501');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000b4', 'sokrates1', 3600, clock_timestamp())$$, '42501');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000ff', 'sokrates1', 3600, clock_timestamp())$$, '42501');
-- zweiter Faktor: zu alt (121 s, nur bis 0019), Zukunft (immer)
do $$ begin
  if current_setting('wamo_test.neu19') = 'nein' then
    perform wamo_testhilfe.fehler($f$select * from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', 'sokrates1', 3600, clock_timestamp() - interval '121 seconds')$f$, '23514');
  end if;
end $$;
select wamo_testhilfe.fehler($$select * from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', 'sokrates1', 3600, clock_timestamp() + interval '30 seconds')$$, '23514');
reset role;
do $$ begin
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.rd_sitzungen), 0::bigint, 'bisher nichts angelegt');
end $$;

-- 3. Start, Belegung, Ablösung
set role wamo_core;
do $$ declare r record; a uuid; b uuid; begin
  -- Grenze: 110 Sekunden alter Code geht
  select * into r from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', 'sokrates1', 3600, clock_timestamp() - interval '110 seconds');
  perform wamo_testhilfe.gleich(r.ergebnis, 'ok', 'Admin1 startet sokrates1');
  a := r.sitzung_id;
  perform wamo_testhilfe.gleich(r.abgeloest_id is null, true, 'nichts abgeloest');
  perform wamo_testhilfe.gleich(r.bis_at > now() + interval '59 minutes' and r.bis_at <= now() + interval '61 minutes', true, 'Hoechstende in 60 Minuten');
  -- anderes Ziel, anderer Admin: geht
  select * into r from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a2', 'caesar', 3600, clock_timestamp());
  perform wamo_testhilfe.gleich(r.ergebnis, 'ok', 'Admin2 startet caesar');
  b := r.sitzung_id;
  -- dasselbe Ziel, anderer Admin: belegt, mit Name und Zeit, ohne neue Zeile
  select * into r from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a2', 'sokrates1', 3600, clock_timestamp());
  perform wamo_testhilfe.gleich(r.ergebnis, 'belegt', 'sokrates1 ist belegt');
  perform wamo_testhilfe.gleich(r.belegt_von, 'Admin Eins', 'Name des Belegers');
  perform wamo_testhilfe.gleich(r.belegt_seit is not null and r.sitzung_id is null, true, 'keine Sitzung bei belegt');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.rd_sitzungen where ende_at is null), 2::bigint, 'zwei offene Sitzungen');
  -- derselbe Admin startet sokrates1 erneut: die alte endet mit Grund abgeloest
  select * into r from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', 'sokrates1', 3600, clock_timestamp());
  perform wamo_testhilfe.gleich(r.ergebnis, 'ok', 'Neustart durch denselben Admin');
  perform wamo_testhilfe.gleich(r.abgeloest_id, a, 'alte Sitzung abgeloest');
  perform wamo_testhilfe.gleich((select grund from __SCHEMA__.rd_sitzungen where id = a), 'abgeloest', 'Grund abgeloest');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.rd_sitzungen where ende_at is null and ziel = 'sokrates1'), 1::bigint, 'genau eine offene Sitzung je Ziel');
  -- ein Code gilt für genau einen Start (nur bis 0019): ein älterer Code als der des letzten Starts ist verbraucht
  if current_setting('wamo_test.neu19') = 'nein' then
    perform wamo_testhilfe.fehler($f$select * from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', 'sokrates1', 3600, clock_timestamp() - interval '100 seconds')$f$, '23514');
    perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.rd_sitzungen where ende_at is null and ziel = 'sokrates1'), 1::bigint, 'verbrauchter Code lässt die laufende Sitzung unberührt');
  end if;
end $$;
reset role;

-- 4. Der teilweise eindeutige Index haelt auch an der Funktion vorbei: eine zweite offene Zeile je Ziel ist unmoeglich
do $$ begin
  perform wamo_testhilfe.fehler($f$insert into __SCHEMA__.rd_sitzungen (profil_id, ziel, bis_at, aal2_at) values ('00000000-0000-4000-8000-0000000000a2', 'caesar', now() + interval '1 hour', now())$f$, '23505');
end $$;

-- 5. Grenzen: gleichzeitige Sitzungen (3) und Starts je Stunde (6)
set role wamo_core;
do $$ declare r record; begin
  select * into r from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a2', 'sokrates2', 3600, clock_timestamp());
  perform wamo_testhilfe.gleich(r.ergebnis, 'ok', 'dritte Sitzung gleichzeitig');
  select * into r from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a2', 'ziel4', 3600, clock_timestamp());
  perform wamo_testhilfe.gleich(r.ergebnis, 'voll', 'vierte Sitzung gleichzeitig ist voll');
  -- mit eigener Grenze 5: es geht
  select * into r from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a2', 'ziel4', 3600, clock_timestamp(), 5, 6);
  perform wamo_testhilfe.gleich(r.ergebnis, 'ok', 'mit hoeherer Grenze geht es');
end $$;
-- Starts je Stunde: Admin2 hat bisher 3 Starts, mit Grenze 3 ist der naechste zu viel
do $$ declare r record; begin
  select * into r from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a2', 'ziel5', 3600, clock_timestamp(), 10, 3);
  perform wamo_testhilfe.gleich(r.ergebnis, 'rate', 'zu viele Starts je Stunde');
end $$;
reset role;

-- 6. Beenden: fremde Sitzung, eigene, doppelt, unbekannt, Grund
set role wamo_core;
do $$ declare s uuid; begin
  select id into s from __SCHEMA__.rd_sitzungen where ziel = 'sokrates1' and ende_at is null;
  perform wamo_testhilfe.gleich(__SCHEMA__.rd_sitzung_beenden(s, 'beendet', '00000000-0000-4000-8000-0000000000a2'), 'fremd', 'fremde Sitzung beenden');
  perform wamo_testhilfe.gleich((select ende_at is null from __SCHEMA__.rd_sitzungen where id = s), true, 'fremde Sitzung läuft weiter');
  perform wamo_testhilfe.gleich(__SCHEMA__.rd_sitzung_beenden(s, 'beendet', '00000000-0000-4000-8000-0000000000a1'), 'ok', 'eigene Sitzung beenden');
  perform wamo_testhilfe.gleich(__SCHEMA__.rd_sitzung_beenden(s, 'beendet', '00000000-0000-4000-8000-0000000000a1'), 'schon', 'doppelt beenden');
  perform wamo_testhilfe.gleich(__SCHEMA__.rd_sitzung_beenden(gen_random_uuid(), 'beendet', null), 'unbekannt', 'unbekannte Sitzung');
  perform wamo_testhilfe.gleich((select grund from __SCHEMA__.rd_sitzungen where id = s), 'beendet', 'Grund gespeichert');
  -- der Kern beendet ohne Profil
  select id into s from __SCHEMA__.rd_sitzungen where ziel = 'caesar' and ende_at is null;
  perform wamo_testhilfe.gleich(__SCHEMA__.rd_sitzung_beenden(s, 'zeitlimit', null), 'ok', 'Kern beendet');
  perform wamo_testhilfe.fehler(format($f$select __SCHEMA__.rd_sitzung_beenden(%L, 'frei erfunden', null)$f$, s), '22023');
  perform wamo_testhilfe.fehler($f$select __SCHEMA__.rd_sitzung_beenden(null, 'beendet', null)$f$, '22004');
  -- ein beendetes Protokoll ist unveraenderlich (Trigger), auch fuer ende_at und grund
  perform wamo_testhilfe.fehler(format($f$update __SCHEMA__.rd_sitzungen set grund = 'fehler' where id = %L$f$, s), '23514');
end $$;
reset role;

-- 7. Abgelaufene Sitzungen schliesst der naechste Start (Hoechstende plus 2 Minuten)
do $$ declare s uuid; r record; begin
  -- eine offene, vor Stunden gestartete Sitzung auf "ziel9" (als postgres angelegt, der Test darf die Zeit verbiegen)
  alter table __SCHEMA__.rd_sitzungen disable trigger rd_sitzungen_guard;
  insert into __SCHEMA__.rd_sitzungen (profil_id, ziel, start_at, bis_at, aal2_at)
    values ('00000000-0000-4000-8000-0000000000a1', 'ziel9', now() - interval '3 hours', now() - interval '2 hours', now() - interval '3 hours')
    returning id into s;
  alter table __SCHEMA__.rd_sitzungen enable trigger rd_sitzungen_guard;
  perform wamo_testhilfe.gleich((select ende_at is null from __SCHEMA__.rd_sitzungen where id = s), true, 'vor dem Start noch offen');
  set local role wamo_core;
  select * into r from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', 'ziel9', 3600, clock_timestamp(), 10, 60);
  reset role;
  perform wamo_testhilfe.gleich(r.ergebnis, 'ok', 'ziel9 ist frei, weil die alte Sitzung abgelaufen war');
  perform wamo_testhilfe.gleich((select grund from __SCHEMA__.rd_sitzungen where id = s), 'zeitlimit', 'abgelaufene Sitzung mit Grund zeitlimit geschlossen');
end $$;

-- 8. Constraints und Struktur
do $$ begin
  perform wamo_testhilfe.fehler($f$insert into __SCHEMA__.rd_sitzungen (profil_id, ziel, bis_at, aal2_at) values ('00000000-0000-4000-8000-0000000000a1', 'Gross', now() + interval '1 hour', now())$f$, '23514');
  perform wamo_testhilfe.fehler($f$insert into __SCHEMA__.rd_sitzungen (profil_id, ziel, bis_at, aal2_at) values ('00000000-0000-4000-8000-0000000000a1', 'zielx', now() + interval '5 hours', now())$f$, '23514');
  perform wamo_testhilfe.fehler($f$insert into __SCHEMA__.rd_sitzungen (profil_id, ziel, bis_at, aal2_at, grund) values ('00000000-0000-4000-8000-0000000000a1', 'zielx', now() + interval '1 hour', now(), 'beendet')$f$, '23514');
  perform wamo_testhilfe.fehler($f$insert into __SCHEMA__.rd_sitzungen (profil_id, ziel, bis_at, aal2_at) values ('00000000-0000-4000-8000-0000000000ee', 'zielx', now() + interval '1 hour', now())$f$, '23503');
  -- FK-Index vorhanden
  perform wamo_testhilfe.gleich(exists (select 1 from pg_indexes where schemaname = '__SCHEMA__' and indexname = 'rd_sitzungen_profil_idx'), true, 'Index auf profil_id');
  perform wamo_testhilfe.gleich(exists (select 1 from pg_indexes where schemaname = '__SCHEMA__' and indexname = 'rd_sitzungen_ziel_offen_idx'), true, 'Index je Ziel (eindeutig, teilweise)');
  perform wamo_testhilfe.gleich((select count(*) from pg_attribute where attrelid = '__SCHEMA__.rd_sitzungen'::regclass and attnum > 0 and not attisdropped), 8::bigint, 'acht Spalten, keine Inhalte');
  perform wamo_testhilfe.gleich(has_table_privilege('wamo_core', '__SCHEMA__.rd_sitzungen', 'DELETE'), false, 'kein delete');
  perform wamo_testhilfe.gleich(has_function_privilege('anon', '__SCHEMA__.rd_sitzung_beginnen(uuid,text,integer,timestamptz,integer,integer)', 'EXECUTE'), false, 'anon');
  perform wamo_testhilfe.gleich(has_function_privilege('authenticated', '__SCHEMA__.rd_sitzung_beenden(uuid,text,uuid)', 'EXECUTE'), false, 'authenticated');
  perform wamo_testhilfe.gleich(has_function_privilege('service_role', '__SCHEMA__.rd_sitzung_beginnen(uuid,text,integer,timestamptz,integer,integer)', 'EXECUTE'), false, 'service_role');
  perform wamo_testhilfe.gleich(has_any_column_privilege('wamo_admin', '__SCHEMA__.rd_sitzungen', 'SELECT,INSERT,UPDATE'), false, 'wamo_admin ohne Rechte');
  perform wamo_testhilfe.gleich((select c.relrowsecurity from pg_class c where c.oid = '__SCHEMA__.rd_sitzungen'::regclass), true, 'RLS an');
  perform wamo_testhilfe.gleich((select p.prosecdef from pg_proc p where p.oid = '__SCHEMA__.rd_sitzung_beginnen(uuid,text,integer,timestamptz,integer,integer)'::regprocedure), false, 'Funktionen laufen mit den Rechten des Aufrufers');
  perform wamo_testhilfe.gleich((select coalesce(bool_or(c like 'search_path=%'), false) from pg_proc p, unnest(p.proconfig) c
    where p.oid = '__SCHEMA__.rd_sitzung_beginnen(uuid,text,integer,timestamptz,integer,integer)'::regprocedure), true, 'search_path fest');
end $$;

select '0015 OK';
rollback;
