-- Test fuer 0003_wamo_teilen. Laeuft als postgres in EINER Transaktion, die am Ende immer zurueckgerollt wird. Alle
-- Zaehlungen beziehen sich nur auf die Testkonten, damit der Test auch in einem Schema mit Bestandsdaten laeuft.
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
create temp table u as select unnest(array['00000000-0000-4000-8000-0000000000a1','00000000-0000-4000-8000-0000000000b1',
  '00000000-0000-4000-8000-0000000000b2','00000000-0000-4000-8000-0000000000b3',
  '00000000-0000-4000-8000-0000000000b4']::uuid[]) as id;
grant select on u to wamo_core, wamo_admin;
-- Canvas: Annas Notiz, ein Auftragsergebnis des Admins (Projekt rezepte-app), Bernds Tabelle
insert into __SCHEMA__.canvas_items (id, user_id, type, title, data) values
  ('00000000-0000-4000-8000-0000000000c1', '00000000-0000-4000-8000-0000000000b1', 'document', 'Notiz Angebot',
   '{"kind":"dokument","title":"Notiz Angebot","text":"Entwurf"}'),
  ('00000000-0000-4000-8000-0000000000c2', '00000000-0000-4000-8000-0000000000a1', 'document', 'Entwicklung: rezepte-app',
   '{"kind":"dokument","title":"Entwicklung: rezepte-app","text":"fertig","auftrag":{"job":"j1","projekt":"rezepte-app"}}'),
  ('00000000-0000-4000-8000-0000000000c3', '00000000-0000-4000-8000-0000000000b2', 'table', 'Bernds Liste',
   '{"kind":"tabelle","title":"Bernds Liste","columns":["a"],"rows":[["1"]]}');
-- zu langer Projektname im Auftragsergebnis wird abgewiesen (Index)
select wamo_testhilfe.fehler($$insert into __SCHEMA__.canvas_items (user_id, type, title, data) values
  ('00000000-0000-4000-8000-0000000000a1', 'document', 'x',
   jsonb_build_object('auftrag', jsonb_build_object('projekt', repeat('a', 101))))$$, '23514');

-- 1. Dateien als wamo_core
set role wamo_core;
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
select wamo_testhilfe.fehler($$update __SCHEMA__.dateien set geloescht_at = now()$$, '42501');
select wamo_testhilfe.fehler($$delete from __SCHEMA__.dateien$$, '42501');
reset role;
create temp table d as select id from __SCHEMA__.dateien where name = 'Angebot.pdf'
  and user_id = '00000000-0000-4000-8000-0000000000b1';
grant select on d to wamo_core, wamo_admin;
do $$ begin
  perform wamo_testhilfe.gleich((select groesse from __SCHEMA__.dateien where id = (select id from d)),
    octet_length(convert_to('%PDF-1.4 Testinhalt', 'UTF8')), 'Groesse berechnet');
  perform wamo_testhilfe.gleich((select sha256 from __SCHEMA__.dateien where id = (select id from d)),
    encode(sha256(convert_to('%PDF-1.4 Testinhalt', 'UTF8')), 'hex'), 'Pruefsumme berechnet');
  perform wamo_testhilfe.gleich((select attstorage from pg_attribute where attrelid = '__SCHEMA__.dateien'::regclass
    and attname = 'inhalt'), 'e'::"char", 'Inhalt ohne Kompression');
end $$;

-- 2. Teilen als wamo_core
set role wamo_core;
insert into __SCHEMA__.teilungen (besitzer, empfaenger, art, canvas_id, recht, hinweis)
  values ('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000000b2', 'canvas',
          '00000000-0000-4000-8000-0000000000c1', 'lesen', 'Bitte gegenlesen');
-- fremdes Objekt, sich selbst, inaktives Profil, doppelt, Projekt ohne Ergebnisse, Projekt oder Ergebnis bearbeiten
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
select wamo_testhilfe.fehler($$insert into __SCHEMA__.teilungen (besitzer, empfaenger, art, canvas_id, recht)
  values ('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000000b3', 'canvas',
          '00000000-0000-4000-8000-0000000000c2', 'bearbeiten')$$, '42501');
insert into __SCHEMA__.teilungen (besitzer, empfaenger, art, projekt)
  values ('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000000b2', 'auftrag', 'rezepte-app');
insert into __SCHEMA__.teilungen (besitzer, empfaenger, art, canvas_id)
  values ('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000000b3', 'canvas',
          '00000000-0000-4000-8000-0000000000c2');
insert into __SCHEMA__.teilungen (besitzer, empfaenger, art, datei_id, recht)
  values ('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000000b3', 'datei', (select id from d),
          'bearbeiten');
-- wamo_core aendert Teilungen nur ueber Funktionen
select wamo_testhilfe.fehler($$update __SCHEMA__.teilungen set recht = 'bearbeiten'$$, '42501');
select wamo_testhilfe.fehler($$update __SCHEMA__.teilungen set widerrufen_at = now(),
  widerrufen_von = '00000000-0000-4000-8000-0000000000b1'$$, '42501');
select wamo_testhilfe.fehler($$delete from __SCHEMA__.teilungen$$, '42501');
select wamo_testhilfe.fehler($$insert into __SCHEMA__.teilung_events (art, ereignis) values ('canvas', 'geteilt')$$, '42501');
reset role;
create temp table t as select id, art, empfaenger, besitzer, canvas_id from __SCHEMA__.teilungen
  where besitzer in (select id from u);
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
    '00000000-0000-4000-8000-0000000000c2'), 'lesen', 'Clara liest Auftragsergebnis');
  perform wamo_testhilfe.gleich(__SCHEMA__.datei_recht('00000000-0000-4000-8000-0000000000b3', (select id from d)),
    'bearbeiten', 'Clara bearbeitet Datei');
  perform wamo_testhilfe.gleich(__SCHEMA__.datei_recht('00000000-0000-4000-8000-0000000000b2', (select id from d)),
    null::text, 'Bernd nicht Datei');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.hinweise where art = 'teilung_erhalten'
    and empfaenger in (select id from u)), 4::bigint, 'Hinweise teilung_erhalten');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.teilung_events where ereignis = 'geteilt'
    and besitzer in (select id from u)), 4::bigint, 'Protokoll geteilt');
end $$;

-- 4. Recht aendern nur ueber die Funktion und nur durch den Besitzer; Auftragsergebnis nie bearbeiten
set role wamo_core;
do $$ begin
  perform wamo_testhilfe.gleich(__SCHEMA__.teilung_recht_setzen('00000000-0000-4000-8000-0000000000b2',
    (select id from t where art = 'canvas' and besitzer = '00000000-0000-4000-8000-0000000000b1'), 'bearbeiten'),
    false, 'Empfaenger aendert kein Recht');
  perform wamo_testhilfe.gleich(__SCHEMA__.teilung_recht_setzen('00000000-0000-4000-8000-0000000000b1',
    (select id from t where art = 'canvas' and besitzer = '00000000-0000-4000-8000-0000000000b1'), 'bearbeiten'),
    true, 'Besitzer aendert Recht');
end $$;
select wamo_testhilfe.fehler($$select __SCHEMA__.teilung_recht_setzen('00000000-0000-4000-8000-0000000000a1',
  (select id from t where canvas_id = '00000000-0000-4000-8000-0000000000c2'), 'bearbeiten')$$, '42501');

-- 5. Bearbeiten: nur mit Recht, Titel der Wurzel bleibt, Basisfassung, Hinweise gebuendelt, Fassungsgrenzen
select wamo_testhilfe.fehler($$select * from __SCHEMA__.canvas_fassung_anlegen('00000000-0000-4000-8000-0000000000b3',
  '00000000-0000-4000-8000-0000000000c2', '{"kind":"dokument","title":"x","text":"y"}')$$, '42501');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.canvas_fassung_anlegen('00000000-0000-4000-8000-0000000000b2',
  '00000000-0000-4000-8000-0000000000c1', '{"kind":"dokument","title":"x","text":"y"}', 7)$$, 'P0409');
reset role;
create temp table f (id uuid, version int);
grant select, insert on f to wamo_core;
set role wamo_core;
insert into f select * from __SCHEMA__.canvas_fassung_anlegen('00000000-0000-4000-8000-0000000000b2',
  '00000000-0000-4000-8000-0000000000c1',
  '{"kind":"dokument","title":"Irrefuehrender Titel","text":"ergänzt","auftrag":{"projekt":"rezepte-app"}}', 1);
insert into f select * from __SCHEMA__.canvas_fassung_anlegen('00000000-0000-4000-8000-0000000000b2',
  '00000000-0000-4000-8000-0000000000c1', '{"kind":"dokument","title":"x","text":"zweite"}', 2);
-- veraltete Basis: Konflikt, nichts wird ueberschrieben
select wamo_testhilfe.fehler($$select * from __SCHEMA__.canvas_fassung_anlegen('00000000-0000-4000-8000-0000000000b2',
  '00000000-0000-4000-8000-0000000000c1', '{"kind":"dokument","title":"x","text":"alt"}', 2)$$, 'P0409');
-- Besitzer bearbeitet selbst: kein Protokoll, kein Hinweis
insert into f select * from __SCHEMA__.canvas_fassung_anlegen('00000000-0000-4000-8000-0000000000b1',
  '00000000-0000-4000-8000-0000000000c1', '{"kind":"dokument","title":"x","text":"Anna"}');
select wamo_testhilfe.fehler($$select __SCHEMA__.datei_fassung_anlegen('00000000-0000-4000-8000-0000000000b2',
  (select id from d), 'x.pdf', 'application/pdf', '\x25')$$, '42501');
select wamo_testhilfe.fehler($$select __SCHEMA__.datei_fassung_anlegen('00000000-0000-4000-8000-0000000000b3',
  (select id from d), 'x.png', 'image/png', '\x89')$$, '22023');
select __SCHEMA__.datei_fassung_anlegen('00000000-0000-4000-8000-0000000000b3', (select id from d),
  'Angebot v2.PDF', 'application/pdf', convert_to('%PDF-1.4 zweite Fassung', 'UTF8'));
reset role;
do $$ declare r record; begin
  select * into r from __SCHEMA__.canvas_items where id = (select id from f where version = 2);
  perform wamo_testhilfe.gleich(r.user_id, '00000000-0000-4000-8000-0000000000b1'::uuid, 'Fassung gehoert Anna');
  perform wamo_testhilfe.gleich(r.parent_id, '00000000-0000-4000-8000-0000000000c1'::uuid, 'Fassung an Wurzel');
  perform wamo_testhilfe.gleich(r.title, 'Notiz Angebot', 'Titel der Wurzel');
  perform wamo_testhilfe.gleich(r.data ->> 'title', 'Notiz Angebot', 'Titel in den Daten');
  perform wamo_testhilfe.gleich(r.bearbeitet_von, '00000000-0000-4000-8000-0000000000b2'::uuid, 'bearbeitet von Bernd');
  perform wamo_testhilfe.gleich(r.data ? 'auftrag', false, 'kein untergeschobenes Projekt');
  perform wamo_testhilfe.gleich((select max(version) from f), 4, 'Fassungen 2 bis 4');
  perform wamo_testhilfe.gleich(__SCHEMA__.canvas_recht('00000000-0000-4000-8000-0000000000b2', r.id), 'bearbeiten',
    'Recht an der Fassung');
  select * into r from __SCHEMA__.dateien where wurzel_id = (select id from d);
  perform wamo_testhilfe.gleich(r.user_id, '00000000-0000-4000-8000-0000000000b1'::uuid, 'Dateifassung gehoert Anna');
  perform wamo_testhilfe.gleich(r.hochgeladen_von, '00000000-0000-4000-8000-0000000000b3'::uuid,
    'Fassung zaehlt bei Clara');
  perform wamo_testhilfe.gleich(__SCHEMA__.teilung_objekt_titel('datei', null, (select id from d), null), 'Angebot v2.PDF',
    'Titel ist der Name der neuesten Fassung');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.hinweise where art = 'teilung_geaendert'
    and empfaenger = '00000000-0000-4000-8000-0000000000b1'), 2::bigint,
    'Anna: ein gebuendelter Hinweis je Objekt (Notiz, Datei)');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.teilung_events where ereignis = 'bearbeitet'
    and besitzer in (select id from u)), 3::bigint, 'bearbeitet nur mit Teilung (2 Bernd, 1 Clara)');
end $$;
-- hoechstens 20 Fassungen je Datei (Besitzerin legt bis zur Grenze an)
do $$ begin
  for i in 2..20 loop
    perform __SCHEMA__.datei_fassung_anlegen('00000000-0000-4000-8000-0000000000b1', (select id from d),
      'Angebot.pdf', 'application/pdf', convert_to('%PDF-' || i, 'UTF8'));
  end loop;
end $$;
select wamo_testhilfe.fehler($$select __SCHEMA__.datei_fassung_anlegen('00000000-0000-4000-8000-0000000000b1',
  (select id from d), 'Angebot.pdf', 'application/pdf', '\x255044462d')$$, '54000');

-- 6. Abruf protokollieren (einmal je 5 Minuten), nur durch Empfaenger
set role wamo_core;
do $$ begin
  perform wamo_testhilfe.gleich(__SCHEMA__.teilung_abgerufen('00000000-0000-4000-8000-0000000000b2',
    (select id from t where canvas_id = '00000000-0000-4000-8000-0000000000c1')), true, 'Abruf Bernd');
  perform wamo_testhilfe.gleich(__SCHEMA__.teilung_abgerufen('00000000-0000-4000-8000-0000000000b2',
    (select id from t where canvas_id = '00000000-0000-4000-8000-0000000000c1')), true, 'Abruf Bernd erneut');
  perform wamo_testhilfe.gleich(__SCHEMA__.teilung_abgerufen('00000000-0000-4000-8000-0000000000b3',
    (select id from t where canvas_id = '00000000-0000-4000-8000-0000000000c1')), false, 'Abruf Clara abgelehnt');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.teilung_events where ereignis = 'abgerufen'
    and besitzer in (select id from u)), 1::bigint, 'ein Abruf protokolliert');
end $$;

-- 7. Beenden: Besitzer widerruft, Empfaenger gibt zurueck, Fremde nicht; endgueltig
do $$ begin
  perform wamo_testhilfe.gleich(__SCHEMA__.teilung_beenden('00000000-0000-4000-8000-0000000000b3',
    (select id from t where canvas_id = '00000000-0000-4000-8000-0000000000c1')), null::text, 'Clara beendet nicht');
  perform wamo_testhilfe.gleich(__SCHEMA__.teilung_beenden('00000000-0000-4000-8000-0000000000b1',
    (select id from t where canvas_id = '00000000-0000-4000-8000-0000000000c1')), 'widerrufen', 'Anna widerruft');
  perform wamo_testhilfe.gleich(__SCHEMA__.teilung_beenden('00000000-0000-4000-8000-0000000000b1',
    (select id from t where canvas_id = '00000000-0000-4000-8000-0000000000c1')), null::text, 'zweimal geht nicht');
  perform wamo_testhilfe.gleich(__SCHEMA__.teilung_beenden('00000000-0000-4000-8000-0000000000b2',
    (select id from t where art = 'auftrag')), 'zurueckgegeben', 'Bernd gibt Projekt zurueck');
  perform wamo_testhilfe.gleich(__SCHEMA__.teilung_recht_setzen('00000000-0000-4000-8000-0000000000b1',
    (select id from t where canvas_id = '00000000-0000-4000-8000-0000000000c1'), 'lesen'), false,
    'kein Recht nach Widerruf');
  perform wamo_testhilfe.gleich(__SCHEMA__.canvas_recht('00000000-0000-4000-8000-0000000000b2',
    '00000000-0000-4000-8000-0000000000c1'), null::text, 'nach Widerruf kein Recht');
  perform wamo_testhilfe.gleich(__SCHEMA__.canvas_recht('00000000-0000-4000-8000-0000000000b2',
    (select id from f where version = 2)), null::text, 'nach Widerruf kein Recht an der Fassung');
  perform wamo_testhilfe.gleich(__SCHEMA__.canvas_recht('00000000-0000-4000-8000-0000000000b2',
    '00000000-0000-4000-8000-0000000000c2'), null::text, 'nach Rueckgabe kein Projekt');
  perform wamo_testhilfe.gleich(__SCHEMA__.teilung_abgerufen('00000000-0000-4000-8000-0000000000b2',
    (select id from t where canvas_id = '00000000-0000-4000-8000-0000000000c1')), false, 'kein Abruf nach Widerruf');
end $$;
-- neue Teilung nach dem Widerruf ist erlaubt
insert into __SCHEMA__.teilungen (besitzer, empfaenger, art, canvas_id)
  values ('00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000000b2', 'canvas',
          '00000000-0000-4000-8000-0000000000c1');

-- 8. Datei loeschen: nur Besitzer, nur erste Fassung; erst weich, Teilungen widerrufen, nach 7 Tagen endgueltig
do $$ begin
  perform wamo_testhilfe.gleich(__SCHEMA__.datei_loeschen('00000000-0000-4000-8000-0000000000b3', (select id from d)),
    false, 'Clara loescht nicht');
  perform wamo_testhilfe.gleich(__SCHEMA__.datei_loeschen('00000000-0000-4000-8000-0000000000b1', (select id from d)),
    true, 'Anna loescht');
  perform wamo_testhilfe.gleich(__SCHEMA__.datei_recht('00000000-0000-4000-8000-0000000000b1', (select id from d)),
    null::text, 'geloescht: kein Zugriff mehr');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.teilungen where art = 'datei' and widerrufen_at is null
    and besitzer in (select id from u)), 0::bigint, 'Teilung der Datei widerrufen');
  perform wamo_testhilfe.gleich(__SCHEMA__.dateien_endgueltig_loeschen() >= 0, true, 'noch nichts endgueltig');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.dateien where user_id in (select id from u)), 21::bigint,
    'weich geloescht: Zeilen bleiben 7 Tage');
end $$;
reset role;
update __SCHEMA__.dateien set geloescht_at = now() - interval '8 days' where id = (select id from d) or wurzel_id = (select id from d);
set role wamo_core;
do $$ begin
  perform wamo_testhilfe.gleich(__SCHEMA__.dateien_endgueltig_loeschen() >= 1, true, 'endgueltig geloescht');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.dateien where user_id in (select id from u)), 0::bigint,
    'Datei samt Fassungen weg');
end $$;
reset role;
do $$ begin
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.hinweise where art = 'teilung_widerrufen'
    and empfaenger in (select id from u)), 3::bigint, 'Hinweise Widerruf (Bernd, Admin bei Rueckgabe, Clara bei Loeschung)');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.teilung_events where ereignis = 'geloescht'
    and besitzer in (select id from u)), 1::bigint, 'Loeschung protokolliert');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.teilung_events
    where ereignis in ('widerrufen','zurueckgegeben') and besitzer in (select id from u)), 3::bigint,
    'Widerrufe protokolliert');
end $$;

-- 9. Verwaltung sieht Metadaten, keine Dateien
set role wamo_admin;
select count(*) from __SCHEMA__.teilungen;
select count(*) from __SCHEMA__.teilung_events;
select wamo_testhilfe.fehler($$select * from __SCHEMA__.dateien$$, '42501');
reset role;

-- 10. Konten loeschen: Bearbeiter einer fremden Datei (hochgeladen_von wird null), Empfaenger (Teilungen weg,
-- Protokoll bleibt ohne Person)
set role wamo_core;
insert into __SCHEMA__.dateien (user_id, name, mime, groesse, sha256, inhalt)
  values ('00000000-0000-4000-8000-0000000000b1', 'Plan.txt', 'text/plain', 1, repeat('0', 64), convert_to('Plan', 'UTF8'));
insert into __SCHEMA__.teilungen (besitzer, empfaenger, art, datei_id, recht)
  select '00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000000b3', 'datei', id, 'bearbeiten'
  from __SCHEMA__.dateien where name = 'Plan.txt' and user_id = '00000000-0000-4000-8000-0000000000b1';
select __SCHEMA__.datei_fassung_anlegen('00000000-0000-4000-8000-0000000000b3',
  (select id from __SCHEMA__.dateien where name = 'Plan.txt' and user_id = '00000000-0000-4000-8000-0000000000b1'
   and wurzel_id is null), 'Plan.txt', 'text/plain', convert_to('Plan von Clara', 'UTF8'));
reset role;
delete from auth.users where id = '00000000-0000-4000-8000-0000000000b3';
delete from auth.users where id = '00000000-0000-4000-8000-0000000000b2';
do $$ begin
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.dateien where name = 'Plan.txt'
    and user_id = '00000000-0000-4000-8000-0000000000b1' and hochgeladen_von is null), 1::bigint,
    'Fassung bleibt, Bearbeiterin geloescht');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.teilungen
    where empfaenger = '00000000-0000-4000-8000-0000000000b2'), 0::bigint, 'Teilungen an Bernd weg');
  perform wamo_testhilfe.gleich((select count(*) > 0 from __SCHEMA__.teilung_events
    where besitzer in (select id from u) and empfaenger is null), true, 'Protokoll bleibt');
end $$;

select '0003 OK';
rollback;
