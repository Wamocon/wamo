-- Test für 0017_vorgaenge. Läuft als postgres in EINER Transaktion, die am Ende immer zurückgerollt wird (Testkonten, Profile, Sitzungen und
-- Vorgänge verschwinden wieder). Nur für wamo_test oder wamo_dev. Wird von Hand ausgeführt, nicht von der CI:
--   sed "s/__SCHEMA__/wamo_test/g" supabase/tests/0017_vorgaenge_test.sql \
--     | ssh wamo-sokrates2 "docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 -q"
-- Voraussetzung: 0001 und 0017 sind im Schema angewendet.
-- Der Nachlauf der Migration (zweimal anwenden, der zweite Lauf ändert nichts) ist NICHT Teil dieses Tests: Wer ihn prüfen will, wendet
-- 0017_vorgaenge.sql vorher zweimal nacheinander an (Aufruf im Kopf der Migration); der zweite Lauf muss ohne Fehler durchgehen.
-- Erfolg: letzte Zeile "0017 OK", Exit-Code 0. Jeder Fehlschlag bricht mit einer Meldung ab.
-- Die Anweisungen des Kerns (Upsert und die beiden Aufräum-Updates aus voice-core/vorgaenge.py) stehen hier wörtlich; der Test
-- voice-core/test_migration_0017.py prüft, dass sie mit denen des Kerns übereinstimmen. Aufräumen wirkt auf das ganze Schema, die
-- Ergebnisse werden deshalb nur an eigenen Kennungen geprüft; die Transaktion wird zurückgerollt, echte Zeilen bleiben unberührt.
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

-- Testkonten mit Profilen (user_id verweist auf profiles) und zwei Sitzungen
insert into auth.users (id, email) values
  ('00000000-0000-4000-8000-0000000017a1', 'vorgang-eins@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000017a2', 'vorgang-zwei@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000017a3', 'vorgang-drei@wamo-test.invalid');
insert into __SCHEMA__.profiles (user_id, display_name) values
  ('00000000-0000-4000-8000-0000000017a1', 'Vorgang Eins'),
  ('00000000-0000-4000-8000-0000000017a2', 'Vorgang Zwei'),
  ('00000000-0000-4000-8000-0000000017a3', 'Vorgang Drei');
insert into __SCHEMA__.sessions (id, user_id) values
  ('00000000-0000-4000-8000-0000000017b1', '00000000-0000-4000-8000-0000000017a1'),
  ('00000000-0000-4000-8000-0000000017b2', '00000000-0000-4000-8000-0000000017a3');

-- 1. Migration eingetragen, Tabelle mit Zeilensicherheit und Policy core_all, Index für die Sitzung partiell, Fremdschlüssel und Prüfungen
do $$ begin
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.schema_migrations where version = '0017_vorgaenge'), 1::bigint, 'Migration eingetragen');
  perform wamo_testhilfe.gleich((select c.relrowsecurity from pg_class c where c.oid = '__SCHEMA__.vorgaenge'::regclass), true, 'RLS an (relrowsecurity)');
  perform wamo_testhilfe.gleich((select count(*) from pg_policies where schemaname = '__SCHEMA__' and tablename = 'vorgaenge' and policyname = 'core_all'), 1::bigint, 'Policy core_all');
  perform wamo_testhilfe.gleich((select count(*) from pg_indexes where schemaname = '__SCHEMA__' and tablename = 'vorgaenge'
                                   and indexname = 'vorgaenge_session_idx' and indexdef like '%WHERE (session_id IS NOT NULL)%'), 1::bigint, 'Index partiell');
  perform wamo_testhilfe.gleich((select count(*) from pg_constraint where conrelid = '__SCHEMA__.vorgaenge'::regclass
                                   and contype = 'f' and confrelid = '__SCHEMA__.profiles'::regclass), 1::bigint, 'Fremdschlüssel user_id auf profiles');
  perform wamo_testhilfe.gleich((select count(*) from pg_constraint where conrelid = '__SCHEMA__.vorgaenge'::regclass
                                   and confrelid = 'auth.users'::regclass), 0::bigint, 'kein Fremdschlüssel auf auth.users');
  perform wamo_testhilfe.gleich((select count(*) from pg_constraint where conrelid = '__SCHEMA__.vorgaenge'::regclass
                                   and contype = 'c' and conname in ('vorgaenge_ende_chk', 'vorgaenge_zeit_chk')), 2::bigint, 'Prüfungen für Ende und Zeit');
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
-- nicht erlaubt
select wamo_testhilfe.fehler($s$delete from __SCHEMA__.vorgaenge$s$, '42501');
select wamo_testhilfe.fehler($s$truncate __SCHEMA__.vorgaenge$s$, '42501');
select wamo_testhilfe.fehler($s$update __SCHEMA__.vorgaenge set user_id = '00000000-0000-4000-8000-0000000017a2'$s$, '42501');
select wamo_testhilfe.fehler($s$update __SCHEMA__.vorgaenge set art = 'geraet'$s$, '42501');
select wamo_testhilfe.fehler($s$update __SCHEMA__.vorgaenge set id = gen_random_uuid()$s$, '42501');
select wamo_testhilfe.fehler($s$update __SCHEMA__.vorgaenge set gestartet_at = now()$s$, '42501');
reset role;

-- 2b. Das Schreiben des Kerns: der Upsert aus voice-core/vorgaenge.py (_UPSERT) wörtlich, die Platzhalter $n durch Werte ersetzt.
-- Gleiche Kennung und gleiches Profil schreibt fort, die Sitzung wird nur gesetzt, solange sie leer ist (coalesce), ein anderes
-- Profil lässt die Zeile unberührt, und ein Endstand wird von keinem späteren Stand überschrieben.
set role wamo_core;
-- (1) anlegen: laeuft, noch ohne Ende und ohne Sitzung
insert into __SCHEMA__.vorgaenge (id, user_id, session_id, art, titel, status, gesamt, daten, gestartet_at, beendet_at)
values ('00000000-0000-4000-8000-0000000017c2'::uuid, '00000000-0000-4000-8000-0000000017a3'::uuid, null::uuid, 'entwicklung',
        'Upsert angelegt', 'laeuft', 0.25::real, '{"schritte": []}'::text::jsonb,
        to_timestamp((extract(epoch from now()) - 120)::double precision), to_timestamp(null::double precision))
on conflict (id) do update set session_id = coalesce(__SCHEMA__.vorgaenge.session_id, excluded.session_id), titel = excluded.titel,
  status = excluded.status, gesamt = excluded.gesamt, daten = excluded.daten, beendet_at = excluded.beendet_at
  where __SCHEMA__.vorgaenge.user_id = excluded.user_id and __SCHEMA__.vorgaenge.status not in ('fertig', 'fehler', 'abgebrochen');
select wamo_testhilfe.gleich((select status from __SCHEMA__.vorgaenge where id = '00000000-0000-4000-8000-0000000017c2'), 'laeuft', 'Upsert legt an');
select wamo_testhilfe.gleich((select beendet_at is null and session_id is null from __SCHEMA__.vorgaenge where id = '00000000-0000-4000-8000-0000000017c2'), true, 'Upsert: noch ohne Ende und Sitzung');
-- (2) Zwischenstand mit Sitzung: gleiche Kennung und gleiches Profil schreibt fort
insert into __SCHEMA__.vorgaenge (id, user_id, session_id, art, titel, status, gesamt, daten, gestartet_at, beendet_at)
values ('00000000-0000-4000-8000-0000000017c2'::uuid, '00000000-0000-4000-8000-0000000017a3'::uuid, '00000000-0000-4000-8000-0000000017b2'::uuid, 'entwicklung',
        'Upsert Zwischenstand', 'wartet_freigabe', 0.5::real, '{"schritte": []}'::text::jsonb,
        to_timestamp((extract(epoch from now()) - 120)::double precision), to_timestamp(null::double precision))
on conflict (id) do update set session_id = coalesce(__SCHEMA__.vorgaenge.session_id, excluded.session_id), titel = excluded.titel,
  status = excluded.status, gesamt = excluded.gesamt, daten = excluded.daten, beendet_at = excluded.beendet_at
  where __SCHEMA__.vorgaenge.user_id = excluded.user_id and __SCHEMA__.vorgaenge.status not in ('fertig', 'fehler', 'abgebrochen');
select wamo_testhilfe.gleich((select titel || '/' || status from __SCHEMA__.vorgaenge where id = '00000000-0000-4000-8000-0000000017c2'), 'Upsert Zwischenstand/wartet_freigabe', 'Upsert schreibt den Zwischenstand fort');
select wamo_testhilfe.gleich((select session_id from __SCHEMA__.vorgaenge where id = '00000000-0000-4000-8000-0000000017c2'), '00000000-0000-4000-8000-0000000017b2'::uuid, 'Sitzung gesetzt');
-- (3) Ende, mit einer anderen Sitzung: die erste bleibt (coalesce), der Endstand gilt mit beendet_at
insert into __SCHEMA__.vorgaenge (id, user_id, session_id, art, titel, status, gesamt, daten, gestartet_at, beendet_at)
values ('00000000-0000-4000-8000-0000000017c2'::uuid, '00000000-0000-4000-8000-0000000017a3'::uuid, '00000000-0000-4000-8000-0000000017b1'::uuid, 'entwicklung',
        'Upsert fertig', 'fertig', 1::real, '{"schritte": []}'::text::jsonb,
        to_timestamp((extract(epoch from now()) - 120)::double precision), to_timestamp((extract(epoch from now()) - 60)::double precision))
on conflict (id) do update set session_id = coalesce(__SCHEMA__.vorgaenge.session_id, excluded.session_id), titel = excluded.titel,
  status = excluded.status, gesamt = excluded.gesamt, daten = excluded.daten, beendet_at = excluded.beendet_at
  where __SCHEMA__.vorgaenge.user_id = excluded.user_id and __SCHEMA__.vorgaenge.status not in ('fertig', 'fehler', 'abgebrochen');
select wamo_testhilfe.gleich((select titel || '/' || status from __SCHEMA__.vorgaenge where id = '00000000-0000-4000-8000-0000000017c2'), 'Upsert fertig/fertig', 'Upsert schreibt das Ende');
select wamo_testhilfe.gleich((select beendet_at is not null and session_id = '00000000-0000-4000-8000-0000000017b2'::uuid
                                from __SCHEMA__.vorgaenge where id = '00000000-0000-4000-8000-0000000017c2'), true, 'Ende gesetzt, erste Sitzung bleibt');
-- (4) ein verspäteter Zwischenstand überschreibt den Endstand nicht
insert into __SCHEMA__.vorgaenge (id, user_id, session_id, art, titel, status, gesamt, daten, gestartet_at, beendet_at)
values ('00000000-0000-4000-8000-0000000017c2'::uuid, '00000000-0000-4000-8000-0000000017a3'::uuid, null::uuid, 'entwicklung',
        'Zu spät', 'pruefen', 0.5::real, '{"schritte": []}'::text::jsonb,
        to_timestamp((extract(epoch from now()) - 120)::double precision), to_timestamp(null::double precision))
on conflict (id) do update set session_id = coalesce(__SCHEMA__.vorgaenge.session_id, excluded.session_id), titel = excluded.titel,
  status = excluded.status, gesamt = excluded.gesamt, daten = excluded.daten, beendet_at = excluded.beendet_at
  where __SCHEMA__.vorgaenge.user_id = excluded.user_id and __SCHEMA__.vorgaenge.status not in ('fertig', 'fehler', 'abgebrochen');
select wamo_testhilfe.gleich((select titel || '/' || status from __SCHEMA__.vorgaenge where id = '00000000-0000-4000-8000-0000000017c2'), 'Upsert fertig/fertig', 'Endstand wird vom Zwischenstand nicht überschrieben');
select wamo_testhilfe.gleich((select beendet_at is not null from __SCHEMA__.vorgaenge where id = '00000000-0000-4000-8000-0000000017c2'), true, 'Ende bleibt gesetzt');
-- (5) auch ein anderer Endstand überschreibt den Endstand nicht
insert into __SCHEMA__.vorgaenge (id, user_id, session_id, art, titel, status, gesamt, daten, gestartet_at, beendet_at)
values ('00000000-0000-4000-8000-0000000017c2'::uuid, '00000000-0000-4000-8000-0000000017a3'::uuid, null::uuid, 'entwicklung',
        'Zu spät fehler', 'fehler', 0::real, '{"schritte": []}'::text::jsonb,
        to_timestamp((extract(epoch from now()) - 120)::double precision), to_timestamp(extract(epoch from now())::double precision))
on conflict (id) do update set session_id = coalesce(__SCHEMA__.vorgaenge.session_id, excluded.session_id), titel = excluded.titel,
  status = excluded.status, gesamt = excluded.gesamt, daten = excluded.daten, beendet_at = excluded.beendet_at
  where __SCHEMA__.vorgaenge.user_id = excluded.user_id and __SCHEMA__.vorgaenge.status not in ('fertig', 'fehler', 'abgebrochen');
select wamo_testhilfe.gleich((select titel || '/' || status from __SCHEMA__.vorgaenge where id = '00000000-0000-4000-8000-0000000017c2'), 'Upsert fertig/fertig', 'ein Endstand bleibt der erste Endstand');
-- (6) ein anderes Profil lässt eine laufende Zeile unberührt
insert into __SCHEMA__.vorgaenge (id, user_id, session_id, art, titel, status, gesamt, daten, gestartet_at, beendet_at)
values ('00000000-0000-4000-8000-0000000017c8'::uuid, '00000000-0000-4000-8000-0000000017a3'::uuid, null::uuid, 'aufgabe',
        'Fremdziel', 'laeuft', 0.1::real, '{"schritte": []}'::text::jsonb,
        to_timestamp((extract(epoch from now()) - 120)::double precision), to_timestamp(null::double precision))
on conflict (id) do update set session_id = coalesce(__SCHEMA__.vorgaenge.session_id, excluded.session_id), titel = excluded.titel,
  status = excluded.status, gesamt = excluded.gesamt, daten = excluded.daten, beendet_at = excluded.beendet_at
  where __SCHEMA__.vorgaenge.user_id = excluded.user_id and __SCHEMA__.vorgaenge.status not in ('fertig', 'fehler', 'abgebrochen');
insert into __SCHEMA__.vorgaenge (id, user_id, session_id, art, titel, status, gesamt, daten, gestartet_at, beendet_at)
values ('00000000-0000-4000-8000-0000000017c8'::uuid, '00000000-0000-4000-8000-0000000017a2'::uuid, null::uuid, 'aufgabe',
        'Fremd überschrieben', 'fehler', 0::real, '{"schritte": []}'::text::jsonb,
        to_timestamp((extract(epoch from now()) - 120)::double precision), to_timestamp(extract(epoch from now())::double precision))
on conflict (id) do update set session_id = coalesce(__SCHEMA__.vorgaenge.session_id, excluded.session_id), titel = excluded.titel,
  status = excluded.status, gesamt = excluded.gesamt, daten = excluded.daten, beendet_at = excluded.beendet_at
  where __SCHEMA__.vorgaenge.user_id = excluded.user_id and __SCHEMA__.vorgaenge.status not in ('fertig', 'fehler', 'abgebrochen');
select wamo_testhilfe.gleich((select titel || '/' || status from __SCHEMA__.vorgaenge where id = '00000000-0000-4000-8000-0000000017c8'), 'Fremdziel/laeuft', 'ein anderes Profil überschreibt nichts');
reset role;

-- 2c. Aufräumen: die beiden Anweisungen aus voice-core/vorgaenge.py (aufraeumen) wörtlich, als wamo_core. Sie brauchen nur die Spalten
-- status, beendet_at, daten und titel (änderbar) und lesen gestartet_at und beendet_at (lesen ist erlaubt).
set role wamo_core;
insert into __SCHEMA__.vorgaenge (id, user_id, art, titel, status, daten, gestartet_at, beendet_at) values
  ('00000000-0000-4000-8000-0000000017d1', '00000000-0000-4000-8000-0000000017a3', 'aufgabe', 'Alt und offen', 'laeuft',
   '{"schritte": [{"nr": 1}]}', now() - interval '2 days', null),
  ('00000000-0000-4000-8000-0000000017d2', '00000000-0000-4000-8000-0000000017a3', 'aufgabe', 'Jung und offen', 'laeuft',
   '{"schritte": [{"nr": 1}]}', now() - interval '1 hour', null),
  ('00000000-0000-4000-8000-0000000017d3', '00000000-0000-4000-8000-0000000017a3', 'aufgabe', 'Alt und beendet', 'fertig',
   '{"schritte": [{"nr": 1}]}', now() - interval '40 days', now() - interval '31 days'),
  ('00000000-0000-4000-8000-0000000017d4', '00000000-0000-4000-8000-0000000017a3', 'aufgabe', 'Jung und beendet', 'fertig',
   '{"schritte": [{"nr": 1}]}', now() - interval '10 days', now() - interval '9 days'),
  ('00000000-0000-4000-8000-0000000017d5', '00000000-0000-4000-8000-0000000017a3', 'aufgabe', 'Wartet seit Tagen', 'wartet_freigabe',
   '{"schritte": [{"nr": 1}]}', now() - interval '3 days', null),
  ('00000000-0000-4000-8000-0000000017d6', '00000000-0000-4000-8000-0000000017a3', 'aufgabe', 'Knapp unter 30 Tagen', 'fehler',
   '{"schritte": [{"nr": 1}]}', now() - interval '31 days', now() - interval '29 days');
-- (a) alte offene Vorgänge gelten als abgebrochen (Altersgrenze, nicht alle offenen)
update __SCHEMA__.vorgaenge set status = 'abgebrochen', beendet_at = now() where beendet_at is null and gestartet_at < now() - interval '24 hours';
select wamo_testhilfe.gleich((select status from __SCHEMA__.vorgaenge where id = '00000000-0000-4000-8000-0000000017d1'), 'abgebrochen', 'alt und offen: abgebrochen');
select wamo_testhilfe.gleich((select status from __SCHEMA__.vorgaenge where id = '00000000-0000-4000-8000-0000000017d5'), 'abgebrochen', 'wartet seit Tagen: abgebrochen');
select wamo_testhilfe.gleich((select beendet_at is not null from __SCHEMA__.vorgaenge where id = '00000000-0000-4000-8000-0000000017d1'), true, 'abgebrochen hat ein Ende');
select wamo_testhilfe.gleich((select status || '/' || (beendet_at is null)::text from __SCHEMA__.vorgaenge where id = '00000000-0000-4000-8000-0000000017d2'), 'laeuft/true', 'jung und offen bleibt offen');
select wamo_testhilfe.gleich((select string_agg(status, ',' order by id) from __SCHEMA__.vorgaenge
                               where id in ('00000000-0000-4000-8000-0000000017d3', '00000000-0000-4000-8000-0000000017d4', '00000000-0000-4000-8000-0000000017d6')),
                             'fertig,fertig,fehler', 'Beendete bleiben bei (a) unverändert');
-- (b) Aufbewahrung 30 Tage: der Stand beendeter Vorgänge wird geleert (die Rolle hat kein delete)
update __SCHEMA__.vorgaenge set daten = '{}'::jsonb, titel = 'abgelaufen' where beendet_at < now() - interval '30 days' and daten <> '{}'::jsonb;
select wamo_testhilfe.gleich((select titel || '/' || (daten = '{}'::jsonb)::text from __SCHEMA__.vorgaenge where id = '00000000-0000-4000-8000-0000000017d3'), 'abgelaufen/true', 'alt und beendet: Stand geleert');
select wamo_testhilfe.gleich((select string_agg(titel || '/' || (daten = '{}'::jsonb)::text, ',' order by id) from __SCHEMA__.vorgaenge
                               where id in ('00000000-0000-4000-8000-0000000017d1', '00000000-0000-4000-8000-0000000017d2', '00000000-0000-4000-8000-0000000017d4', '00000000-0000-4000-8000-0000000017d6')),
                             'Alt und offen/false,Jung und offen/false,Jung und beendet/false,Knapp unter 30 Tagen/false', 'junge Stände und das eben Abgebrochene bleiben');
-- ein zweiter Lauf beider Anweisungen ändert nichts mehr
update __SCHEMA__.vorgaenge set status = 'abgebrochen', beendet_at = now() where beendet_at is null and gestartet_at < now() - interval '24 hours';
update __SCHEMA__.vorgaenge set daten = '{}'::jsonb, titel = 'abgelaufen' where beendet_at < now() - interval '30 days' and daten <> '{}'::jsonb;
select wamo_testhilfe.gleich((select string_agg(status || '/' || titel, ',' order by id) from __SCHEMA__.vorgaenge
                               where id in ('00000000-0000-4000-8000-0000000017d1', '00000000-0000-4000-8000-0000000017d2', '00000000-0000-4000-8000-0000000017d3', '00000000-0000-4000-8000-0000000017d4')),
                             'abgebrochen/Alt und offen,laeuft/Jung und offen,fertig/abgelaufen,fertig/Jung und beendet', 'zweiter Lauf: derselbe Stand');
-- nichts wurde gelöscht
select wamo_testhilfe.gleich((select count(*) from __SCHEMA__.vorgaenge where id::text like '00000000-0000-4000-8000-0000000017d%'), 6::bigint, 'keine Zeile gelöscht');
reset role;

-- 3. Prüfungen der Werte: Art, Status, Gesamtfortschritt, Titel, Stand, Ende nach Beginn, Status und Ende gehören zusammen
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
-- Stand über 262144 Byte (pg_column_size): abgelehnt
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.vorgaenge (user_id, art, titel, status, daten) values ('00000000-0000-4000-8000-0000000017a1', 'aufgabe', 'T', 'laeuft', jsonb_build_object('x', repeat('a', 300000)))$s$, '23514');
-- 60000 Zeichen zu je 4 Byte (die Obergrenze des Kerns, 240000 Byte) passen noch
insert into __SCHEMA__.vorgaenge (id, user_id, art, titel, status, daten) values
  ('00000000-0000-4000-8000-0000000017c9', '00000000-0000-4000-8000-0000000017a3', 'aufgabe', 'Großer Stand', 'laeuft',
   jsonb_build_object('x', repeat(chr(128512), 60000)));
select wamo_testhilfe.gleich((select count(*) from __SCHEMA__.vorgaenge where id = '00000000-0000-4000-8000-0000000017c9'), 1::bigint, 'Stand mit 60000 Vier-Byte-Zeichen gespeichert');
-- Status und Ende gehören zusammen (vorgaenge_ende_chk): Endstatus ohne Ende und Ende ohne Endstatus werden abgelehnt
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.vorgaenge (user_id, art, titel, status) values ('00000000-0000-4000-8000-0000000017a1', 'aufgabe', 'T', 'fertig')$s$, '23514');
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.vorgaenge (user_id, art, titel, status) values ('00000000-0000-4000-8000-0000000017a1', 'aufgabe', 'T', 'fehler')$s$, '23514');
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.vorgaenge (user_id, art, titel, status) values ('00000000-0000-4000-8000-0000000017a1', 'aufgabe', 'T', 'abgebrochen')$s$, '23514');
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.vorgaenge (user_id, art, titel, status, beendet_at) values ('00000000-0000-4000-8000-0000000017a1', 'aufgabe', 'T', 'laeuft', now())$s$, '23514');
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.vorgaenge (user_id, art, titel, status, beendet_at) values ('00000000-0000-4000-8000-0000000017a1', 'aufgabe', 'T', 'geplant', now())$s$, '23514');
select wamo_testhilfe.fehler($s$update __SCHEMA__.vorgaenge set beendet_at = null where id = '00000000-0000-4000-8000-0000000017c1'$s$, '23514');
select wamo_testhilfe.fehler($s$update __SCHEMA__.vorgaenge set status = 'laeuft' where id = '00000000-0000-4000-8000-0000000017c1'$s$, '23514');
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
-- das Konto zu löschen nimmt über das Profil auch dessen Vorgänge mit
delete from auth.users where id = '00000000-0000-4000-8000-0000000017a2';
do $$ begin
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.vorgaenge where user_id = '00000000-0000-4000-8000-0000000017a2'), 0::bigint, 'Vorgänge des gelöschten Kontos entfernt');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.vorgaenge where user_id = '00000000-0000-4000-8000-0000000017a1'), 1::bigint, 'Vorgänge des anderen Kontos bleiben');
end $$;
-- ein Profil zu löschen nimmt seine Vorgänge mit (der Fremdschlüssel zeigt auf profiles)
do $$ begin
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.vorgaenge where user_id = '00000000-0000-4000-8000-0000000017a3') > 0, true, 'Vorgänge des dritten Profils vorhanden');
end $$;
delete from __SCHEMA__.profiles where user_id = '00000000-0000-4000-8000-0000000017a3';
do $$ begin
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.vorgaenge where user_id = '00000000-0000-4000-8000-0000000017a3'), 0::bigint, 'Vorgänge des gelöschten Profils entfernt');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.vorgaenge where user_id = '00000000-0000-4000-8000-0000000017a1'), 1::bigint, 'Vorgänge des ersten Profils bleiben');
end $$;

-- 6. Rechte als Zahlen: genau die vereinbarten Spalten sind für wamo_core änderbar
do $$ begin
  perform wamo_testhilfe.gleich(has_table_privilege('wamo_core', '__SCHEMA__.vorgaenge', 'UPDATE,DELETE,TRUNCATE'), false, 'kein Tabellenrecht außer lesen und einfügen');
  perform wamo_testhilfe.gleich(has_table_privilege('wamo_core', '__SCHEMA__.vorgaenge', 'SELECT') and has_table_privilege('wamo_core', '__SCHEMA__.vorgaenge', 'INSERT'), true, 'lesen und einfügen');
  perform wamo_testhilfe.gleich((select count(*) from information_schema.columns c
                                   where c.table_schema = '__SCHEMA__' and c.table_name = 'vorgaenge'
                                     and has_column_privilege('wamo_core', '__SCHEMA__.vorgaenge', c.column_name, 'UPDATE')), 6::bigint, 'sechs änderbare Spalten');
  perform wamo_testhilfe.gleich(has_column_privilege('wamo_core', '__SCHEMA__.vorgaenge', 'status', 'UPDATE')
                                and has_column_privilege('wamo_core', '__SCHEMA__.vorgaenge', 'beendet_at', 'UPDATE')
                                and has_column_privilege('wamo_core', '__SCHEMA__.vorgaenge', 'daten', 'UPDATE')
                                and has_column_privilege('wamo_core', '__SCHEMA__.vorgaenge', 'titel', 'UPDATE'), true, 'Spalten des Aufräumens änderbar');
  perform wamo_testhilfe.gleich(has_any_column_privilege('wamo_admin', '__SCHEMA__.vorgaenge', 'SELECT,INSERT,UPDATE'), false, 'wamo_admin ohne Rechte');
end $$;

rollback;
\echo 0017 OK
