-- Test fuer 0003_wamo_teilen. Laeuft als postgres in EINER Transaktion, die am Ende immer zurueckgerollt wird.
--   bash scripts/render-migration.sh wamo_test supabase/tests/0003_wamo_teilen_test.sql \
--     | ssh sokrates2 "docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 -q"
-- Erfolg: letzte Zeile "0003 OK", Exit-Code 0.
\set ON_ERROR_STOP 1
begin;
do $$ begin
  if '__SCHEMA__' = 'wamo_prod' then raise exception 'Test nie in wamo_prod'; end if;
end $$;
-- postgres ist bei Supabase kein Superuser: Rollenwechsel nur ueber eine Mitgliedschaft (wird mit zurueckgerollt)
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

insert into auth.users (id, email) values
  ('00000000-0000-4000-8000-0000000000a1', 'admin1@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000000b1', 'anna@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000000b2', 'bernd@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000000b3', 'clara@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000000b4', 'dora@wamo-test.invalid');
insert into __SCHEMA__.profiles (user_id, display_name, rolle, aktiv) values
  ('00000000-0000-4000-8000-0000000000a1', 'Admin Eins', 'admin', true),
  ('00000000-0000-4000-8000-0000000000b1', 'Anna', 'mitglied', true),
  ('00000000-0000-4000-8000-0000000000b2', 'Bernd', 'mitglied', true),
  ('00000000-0000-4000-8000-0000000000b3', 'Clara', 'mitglied', true),
  ('00000000-0000-4000-8000-0000000000b4', 'Dora', 'mitglied', false);
-- Canvas: Annas Notiz, ein Auftragsergebnis des Admins (Projekt rezepte-app), Bernds Tabelle
insert into __SCHEMA__.canvas_items (id, user_id, type, title, data) values
  ('00000000-0000-4000-8000-0000000000c1', '00000000-0000-4000-8000-0000000000b1', 'document', 'Notiz Angebot',
   '{"kind":"dokument","title":"Notiz Angebot","text":"Entwurf"}'),
  ('00000000-0000-4000-8000-0000000000c2', '00000000-0000-4000-8000-0000000000a1', 'document', 'Entwicklung: rezepte-app',
   '{"kind":"dokument","title":"Entwicklung: rezepte-app","text":"fertig","auftrag":{"job":"j1","projekt":"rezepte-app"}}'),
  ('00000000-0000-4000-8000-0000000000c3', '00000000-0000-4000-8000-0000000000b2', 'table', 'Bernds Liste',
   '{"kind":"tabelle","title":"Bernds Liste","columns":["a"],"rows":[["1"]]}');

-- 1. Dateien als wamo_core
set role wamo_core;
-- id ist nicht schreibbar (kein Spaltenrecht)
select wamo_testhilfe.fehler($$insert into __SCHEMA__.dateien (id, user_id, name, mime, groesse, sha256, inhalt)
  values (gen_random_uuid(), '00000000-0000-4000-8000-0000000000b1', 'x.pdf', 'application/pdf', 1, repeat('0', 64),
  '\x00')$$, '42501');
insert into __SCHEMA__.dateien (user_id, name, mime, groesse, sha256, herkunft, inhalt)
  values ('00000000-0000-4000-8000-0000000000b1', 'Angebot.pdf', 'application/pdf', 1, repeat('0', 64), 'upload',
          convert_to('%PDF-1.4 Testinhalt', 'UTF8'));
select wamo_testhilfe.fehler($$insert into __SCHEMA__.dateien (user_id, name, mime, groesse, sha256, inhalt)
  values ('00000000-0000-4000-8000-0000000000b1', 'x.html', 'text/html', 1, repeat('0', 64), '\x3c')$$, '23514');
select wamo_testhilfe.fehler($$insert into __SCHEMA__.dateien (user_id, name, mime, groesse, sha256, inhalt)
  values ('00000000-0000-4000-8000-0000000000b1', '../x.pdf', 'application/pdf', 1, repeat('0', 64), '\x25')$$, '23514');
select wamo_testhilfe.fehler($$insert into __SCHEMA__.dateien (user_id, name, mime, groesse, sha256, herkunft, inhalt)
  values ('00000000-0000-4000-8000-0000000000b1', 'x.pdf', 'application/pdf', 1, repeat('0', 64), 'version', '\x25')$$,
  '23514');
select wamo_testhilfe.fehler($$insert into __SCHEMA__.dateien (user_id, name, mime, groesse, sha256, inhalt)
  values ('00000000-0000-4000-8000-0000000000b4', 'x.pdf', 'application/pdf', 1, repeat('0', 64), '\x25')$$, '42501');
select wamo_testhilfe.fehler($$update __SCHEMA__.dateien set name = 'neu.pdf'$$, '42501');
select wamo_testhilfe.fehler($$delete from __SCHEMA__.dateien$$, '42501');
reset role;
create temp table d as select id from __SCHEMA__.dateien where name = 'Angebot.pdf';
grant select on d to wamo_core, wamo_admin;
do $$ begin
  perform wamo_testhilfe.gleich((select groesse from __SCHEMA__.dateien where id = (select id from d)),
    octet_length(convert_to('%PDF-1.4 Testinhalt', 'UTF8')), 'Groesse berechnet');
  perform wamo_testhilfe.gleich((select sha256 from __SCHEMA__.dateien where id = (select id from d)),
    encode(sha256(convert_to('%PDF-1.4 Testinhalt', 'UTF8')), 'hex'), 'Pruefsumme berechnet');
end $$;

-- 2. Teilen als wamo_core
set role wamo_core;
insert into __SCHEMA__.teilungen (besitzer, empfaenger, art, canvas_id, recht, hinweis)
  values ('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000000b2', 'canvas',
          '00000000-0000-4000-8000-0000000000c1', 'lesen', 'Bitte gegenlesen');
-- fremdes Objekt, sich selbst, inaktives Profil, doppelt, Projekt ohne Ergebnisse, Projekt mit bearbeiten
select wamo_testhilfe.fehler($$insert into __SCHEMA__.teilungen (besitzer, empfaenger, art, canvas_id)
  values ('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000000b3', 'canvas',
          '00000000-0000-4000-8000-0000000000c3')$$, '42501');
select wamo_testhilfe.fehler($$insert into __SCHEMA__.teilungen (besitzer, empfaenger, art, canvas_id)
  values ('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000000b1', 'canvas',
          '00000000-0000-4000-8000-0000000000c1')$$, '23514');
select wamo_testhilfe.fehler($$insert into __SCHEMA__.teilungen (besitzer, empfaenger, art, canvas_id)
  values ('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000000b4', 'canvas',
          '00000000-0000-4000-8000-0000000000c1')$$, '42501');
select wamo_testhilfe.fehler($$insert into __SCHEMA__.teilungen (besitzer, empfaenger, art, canvas_id)
  values ('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000000b2', 'canvas',
          '00000000-0000-4000-8000-0000000000c1')$$, '23505');
select wamo_testhilfe.fehler($$insert into __SCHEMA__.teilungen (besitzer, empfaenger, art, projekt)
  values ('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000000b2', 'auftrag', 'gibt-es-nicht')$$,
  '42501');
select wamo_testhilfe.fehler($$insert into __SCHEMA__.teilungen (besitzer, empfaenger, art, projekt, recht)
  values ('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000000b2', 'auftrag', 'rezepte-app',
          'bearbeiten')$$, '23514');
insert into __SCHEMA__.teilungen (besitzer, empfaenger, art, projekt)
  values ('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000000b2', 'auftrag', 'rezepte-app');
insert into __SCHEMA__.teilungen (besitzer, empfaenger, art, datei_id, recht)
  values ('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000000b3', 'datei', (select id from d),
          'bearbeiten');
-- Rechte von wamo_core
select wamo_testhilfe.fehler($$update __SCHEMA__.teilungen set besitzer = '00000000-0000-4000-8000-0000000000b3'$$, '42501');
select wamo_testhilfe.fehler($$delete from __SCHEMA__.teilungen$$, '42501');
select wamo_testhilfe.fehler($$insert into __SCHEMA__.teilung_events (art, ereignis) values ('canvas', 'geteilt')$$, '42501');
reset role;
create temp table t as select id, art, empfaenger from __SCHEMA__.teilungen;
grant select on t to wamo_core, wamo_admin;

-- 3. Rechte
do $$ begin
  perform wamo_testhilfe.gleich(__SCHEMA__.canvas_recht('00000000-0000-4000-8000-0000000000b1',
    '00000000-0000-4000-8000-0000000000c1'), 'besitzer', 'Anna besitzt c1');
  perform wamo_testhilfe.gleich(__SCHEMA__.canvas_recht('00000000-0000-4000-8000-0000000000b2',
    '00000000-0000-4000-8000-0000000000c1'), 'lesen', 'Bernd liest c1');
  perform wamo_testhilfe.gleich(__SCHEMA__.canvas_recht('00000000-0000-4000-8000-0000000000b3',
    '00000000-0000-4000-8000-0000000000c1'), null::text, 'Clara nicht c1');
  perform wamo_testhilfe.gleich(__SCHEMA__.canvas_recht('00000000-0000-4000-8000-0000000000b2',
    '00000000-0000-4000-8000-0000000000c2'), 'lesen', 'Bernd liest Projekt');
  perform wamo_testhilfe.gleich(__SCHEMA__.canvas_recht('00000000-0000-4000-8000-0000000000b3',
    '00000000-0000-4000-8000-0000000000c2'), null::text, 'Clara nicht Projekt');
  perform wamo_testhilfe.gleich(__SCHEMA__.datei_recht('00000000-0000-4000-8000-0000000000b3', (select id from d)),
    'bearbeiten', 'Clara bearbeitet Datei');
  perform wamo_testhilfe.gleich(__SCHEMA__.datei_recht('00000000-0000-4000-8000-0000000000b2', (select id from d)),
    null::text, 'Bernd nicht Datei');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.hinweise where art = 'teilung_erhalten'), 3::bigint,
    'Hinweise teilung_erhalten');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.teilung_events where ereignis = 'geteilt'), 3::bigint,
    'Protokoll geteilt');
end $$;

-- 4. Bearbeiten: nur mit Recht; Fassung gehoert dem Besitzer, Auftragszuordnung bleibt
set role wamo_core;
select wamo_testhilfe.fehler($$select __SCHEMA__.canvas_fassung_anlegen('00000000-0000-4000-8000-0000000000b2',
  '00000000-0000-4000-8000-0000000000c1', 'x', '{"kind":"dokument","title":"x","text":"y"}')$$, '42501');
select wamo_testhilfe.fehler($$select __SCHEMA__.canvas_fassung_anlegen('00000000-0000-4000-8000-0000000000b2',
  '00000000-0000-4000-8000-0000000000c2', 'x', '{"kind":"dokument","title":"x","text":"y"}')$$, '42501');
update __SCHEMA__.teilungen set recht = 'bearbeiten' where art = 'canvas';
reset role;
create temp table f (id uuid);
grant select, insert on f to wamo_core;
set role wamo_core;
insert into f select __SCHEMA__.canvas_fassung_anlegen('00000000-0000-4000-8000-0000000000b2',
  '00000000-0000-4000-8000-0000000000c1', 'Notiz Angebot (Bernd)',
  '{"kind":"dokument","title":"Notiz Angebot (Bernd)","text":"ergänzt","auftrag":{"projekt":"rezepte-app"}}');
select wamo_testhilfe.fehler($$select __SCHEMA__.datei_fassung_anlegen('00000000-0000-4000-8000-0000000000b2',
  (select id from d), 'x.pdf', 'application/pdf', '\x25')$$, '42501');
select __SCHEMA__.datei_fassung_anlegen('00000000-0000-4000-8000-0000000000b3', (select id from d),
  'Angebot v2.pdf', 'application/pdf', convert_to('%PDF-1.4 zweite Fassung', 'UTF8'));
reset role;
do $$ declare r record; begin
  select * into r from __SCHEMA__.canvas_items where id = (select id from f);
  perform wamo_testhilfe.gleich(r.user_id, '00000000-0000-4000-8000-0000000000b1'::uuid, 'Fassung gehoert Anna');
  perform wamo_testhilfe.gleich(r.parent_id, '00000000-0000-4000-8000-0000000000c1'::uuid, 'Fassung an Wurzel');
  perform wamo_testhilfe.gleich(r.version, 2, 'Fassung 2');
  perform wamo_testhilfe.gleich(r.bearbeitet_von, '00000000-0000-4000-8000-0000000000b2'::uuid, 'bearbeitet von Bernd');
  perform wamo_testhilfe.gleich(r.data ? 'auftrag', false, 'kein untergeschobenes Projekt');
  perform wamo_testhilfe.gleich(__SCHEMA__.canvas_recht('00000000-0000-4000-8000-0000000000b2', r.id), 'bearbeiten',
    'Recht an der Fassung');
  select * into r from __SCHEMA__.dateien where wurzel_id = (select id from d);
  perform wamo_testhilfe.gleich(r.user_id, '00000000-0000-4000-8000-0000000000b1'::uuid, 'Dateifassung gehoert Anna');
  perform wamo_testhilfe.gleich(r.hochgeladen_von, '00000000-0000-4000-8000-0000000000b3'::uuid, 'hochgeladen von Clara');
  perform wamo_testhilfe.gleich(r.herkunft, 'version', 'Herkunft version');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.hinweise where art = 'teilung_geaendert'
    and empfaenger = '00000000-0000-4000-8000-0000000000b1'), 2::bigint, 'Anna erfaehrt von beiden Bearbeitungen');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.hinweise where art = 'teilung_geaendert'
    and empfaenger = '00000000-0000-4000-8000-0000000000b2'), 1::bigint, 'Bernd erfaehrt von neuem Recht');
end $$;

-- 5. Abruf protokollieren (einmal je 5 Minuten), nur durch Empfaenger
set role wamo_core;
do $$ begin
  perform wamo_testhilfe.gleich(__SCHEMA__.teilung_abgerufen('00000000-0000-4000-8000-0000000000b2',
    (select id from t where art = 'canvas')), true, 'Abruf Bernd');
  perform wamo_testhilfe.gleich(__SCHEMA__.teilung_abgerufen('00000000-0000-4000-8000-0000000000b2',
    (select id from t where art = 'canvas')), true, 'Abruf Bernd erneut');
  perform wamo_testhilfe.gleich(__SCHEMA__.teilung_abgerufen('00000000-0000-4000-8000-0000000000b3',
    (select id from t where art = 'canvas')), false, 'Abruf Clara abgelehnt');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.teilung_events where ereignis = 'abgerufen'), 1::bigint,
    'ein Abruf protokolliert');
end $$;

-- 6. Widerruf: nur Beteiligte, endgueltig, wirkt sofort; Rueckgabe durch den Empfaenger
select wamo_testhilfe.fehler($$update __SCHEMA__.teilungen set widerrufen_at = now(),
  widerrufen_von = '00000000-0000-4000-8000-0000000000b3' where art = 'canvas'$$, '42501');
update __SCHEMA__.teilungen set widerrufen_at = now(), widerrufen_von = '00000000-0000-4000-8000-0000000000b1'
  where art = 'canvas';
select wamo_testhilfe.fehler($$update __SCHEMA__.teilungen set recht = 'lesen' where art = 'canvas'$$, '42501');
select wamo_testhilfe.fehler($$update __SCHEMA__.teilungen set widerrufen_at = null, widerrufen_von = null
  where art = 'canvas'$$, '42501');
update __SCHEMA__.teilungen set widerrufen_at = now(), widerrufen_von = '00000000-0000-4000-8000-0000000000b2'
  where art = 'auftrag';
do $$ begin
  perform wamo_testhilfe.gleich(__SCHEMA__.canvas_recht('00000000-0000-4000-8000-0000000000b2',
    '00000000-0000-4000-8000-0000000000c1'), null::text, 'nach Widerruf kein Recht');
  perform wamo_testhilfe.gleich(__SCHEMA__.canvas_recht('00000000-0000-4000-8000-0000000000b2', (select id from f)),
    null::text, 'nach Widerruf kein Recht an der Fassung');
  perform wamo_testhilfe.gleich(__SCHEMA__.canvas_recht('00000000-0000-4000-8000-0000000000b2',
    '00000000-0000-4000-8000-0000000000c2'), null::text, 'nach Rueckgabe kein Projekt');
  perform wamo_testhilfe.gleich(__SCHEMA__.teilung_abgerufen('00000000-0000-4000-8000-0000000000b2',
    (select id from t where art = 'canvas')), false, 'kein Abruf nach Widerruf');
end $$;
-- neue Teilung nach dem Widerruf ist erlaubt
insert into __SCHEMA__.teilungen (besitzer, empfaenger, art, canvas_id)
  values ('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000000b2', 'canvas',
          '00000000-0000-4000-8000-0000000000c1');

-- 7. Datei loeschen: nur der Besitzer, nur die erste Fassung; laufende Teilungen werden widerrufen
do $$ begin
  perform wamo_testhilfe.gleich(__SCHEMA__.datei_loeschen('00000000-0000-4000-8000-0000000000b3', (select id from d)),
    false, 'Clara loescht nicht');
  perform wamo_testhilfe.gleich(__SCHEMA__.datei_loeschen('00000000-0000-4000-8000-0000000000b1', (select id from d)),
    true, 'Anna loescht');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.dateien), 0::bigint, 'Datei samt Fassung weg');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.teilungen where art = 'datei'), 0::bigint,
    'Teilung der Datei weg');
end $$;
reset role;
do $$ begin
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.hinweise where art = 'teilung_widerrufen'), 3::bigint,
    'Hinweise Widerruf (Bernd, Admin bei Rueckgabe, Clara bei Loeschung)');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.teilung_events where ereignis = 'geloescht'), 1::bigint,
    'Loeschung protokolliert');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.teilung_events where ereignis in ('widerrufen','zurueckgegeben')),
    3::bigint, 'Widerrufe protokolliert');
end $$;

-- 8. Verwaltung sieht Metadaten, keine Dateien
set role wamo_admin;
select count(*) from __SCHEMA__.teilungen;
select count(*) from __SCHEMA__.teilung_events;
select wamo_testhilfe.fehler($$select * from __SCHEMA__.dateien$$, '42501');
reset role;

-- 9. Konto loeschen: Teilungen an das Konto verschwinden, das Protokoll bleibt (ohne Person)
delete from auth.users where id = '00000000-0000-4000-8000-0000000000b2';
do $$ begin
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.teilungen
    where empfaenger = '00000000-0000-4000-8000-0000000000b2'), 0::bigint, 'Teilungen an Bernd weg');
  perform wamo_testhilfe.gleich((select count(*) > 0 from __SCHEMA__.teilung_events where empfaenger is null), true,
    'Protokoll bleibt');
end $$;

select '0003 OK';
rollback;
