-- Test für 0018_labor_profile. Läuft als postgres in EINER Transaktion, die am Ende immer zurückgerollt wird (Testkonten, Profile und
-- alle Zeilen verschwinden wieder). Nur für wamo_test oder wamo_dev. Wird von Hand ausgeführt, nicht von der CI:
--   sed "s/__SCHEMA__/wamo_test/g" supabase/tests/0018_labor_profile_test.sql \
--     | ssh wamo-sokrates2 "docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 -q"
-- Voraussetzung: 0001 bis 0017 und 0018 sind im Schema angewendet. Der Nachlauf der Migration (zweimal anwenden, der zweite Lauf
-- ändert nichts) ist NICHT Teil dieses Tests: 0018_labor_profile.sql vorher zweimal nacheinander anwenden.
-- Erfolg: letzte Zeile "0018 OK", Exit-Code 0. Jeder Fehlschlag bricht mit einer Meldung ab.
-- Was geprüft wird (Tests T7, T10, T12 des Plans docs/testlabor-wamo.md):
--   1. Prüfungen und Trigger der Laborspalten, Platz eindeutig, Rolle mitglied auch für das Schau-Profil
--   2. Rechte für INSERT und UPDATE je Rolle (wamo_core, anon, authenticated, service_role, wamo_admin), Befund 12
--   3. Das Schau-Profil zählt nicht als Admin der Sonderfreigabe (release_anzahl_admins, release_ist_admin)
--   4. Der Prüfblock des Rückwegs bricht bei einem Laborprofil ab (er steht wörtlich aus 0018_labor_profile.down.sql hier)
--   5. Löschfunktion: Zugangsprüfungen, Zeitfenster, Vollständigkeit aus dem Katalog, "danach ist nichts mehr da" (T10)
-- Die Fixture füllt jede Tabelle der Löschliste (voice-core/labor_loeschliste.py). Tabellen ohne einfachen Einfügeweg (Geräte, Dateien,
-- Teilungen, Hinweise) werden als Superuser mit session_replication_role = replica gefüllt; die Löschung selbst läuft im Normalbetrieb.
\set ON_ERROR_STOP 1
begin;
do $$ begin
  if '__SCHEMA__' = 'wamo_prod' then raise exception 'Test nie in wamo_prod'; end if;
end $$;

grant wamo_core, wamo_admin to postgres;
create schema wamo_testhilfe;
grant usage on schema wamo_testhilfe to wamo_core, wamo_admin, anon, authenticated, service_role;
-- erwartet einen Fehler mit genau diesem SQLSTATE und, wenn angegeben, einem Meldungstext, der p_text enthält
create function wamo_testhilfe.fehler(p_sql text, p_code text, p_text text default null) returns void language plpgsql as $f$
begin
  begin
    execute p_sql;
  exception when others then
    if sqlstate <> p_code then
      raise exception 'erwartet %, bekam % (%) bei: %', p_code, sqlstate, sqlerrm, p_sql;
    end if;
    if p_text is not null and position(p_text in sqlerrm) = 0 then
      raise exception 'erwartet eine Meldung mit "%", bekam "%" bei: %', p_text, sqlerrm, p_sql;
    end if;
    return;
  end;
  raise exception 'erwartet Fehler %, aber gelungen: %', p_code, p_sql;
end $f$;
create function wamo_testhilfe.gleich(p_ist anyelement, p_soll anyelement, p_was text) returns void language plpgsql as $f$
begin
  if p_ist is distinct from p_soll then raise exception '%: ist %, soll %', p_was, p_ist, p_soll; end if;
end $f$;
-- Zeilen je Tabelle und Besitzerspalte, dynamisch aus dem Katalog: jede Spalte, die auf profiles(user_id) oder auf auth.users(id) verweist
create function wamo_testhilfe.zeilen(p_user uuid) returns jsonb language plpgsql as $f$
declare
  r record;
  n bigint;
  ergebnis jsonb := '{}'::jsonb;
begin
  for r in
    select distinct (select t.relname from pg_class t where t.oid = c.conrelid) as tabelle, a.attname as spalte
      from pg_constraint c
      join pg_attribute a on a.attrelid = c.conrelid and a.attnum = any(c.conkey)
     where c.contype = 'f' and c.connamespace = '__SCHEMA__'::regnamespace
       and (c.confrelid = '__SCHEMA__.profiles'::regclass or c.confrelid = 'auth.users'::regclass)
  loop
    execute format('select count(*) from __SCHEMA__.%I where %I = $1', r.tabelle, r.spalte) into n using p_user;
    ergebnis := ergebnis || jsonb_build_object(r.tabelle || '.' || r.spalte, n);
  end loop;
  return ergebnis;
end $f$;
-- Eine Zeile in jeder Tabelle der Löschliste für ein Profil. p_zeuge ist das Gegenüber der Teilung (ein gewöhnliches Mitglied).
create function wamo_testhilfe.fuellen(p_user uuid, p_zeuge uuid, p_start timestamptz, p_nr int) returns void language plpgsql as $f$
declare
  v_sess   uuid;
  v_turn   uuid;
  v_canvas uuid;
  v_dev    uuid;
  v_teil   uuid;
  v_zeit   timestamptz := p_start + interval '1 minute';
begin
  insert into __SCHEMA__.sessions (user_id, started_at, ended_at, summary)
    values (p_user, p_start, p_start + interval '10 minutes', 'Gesprächstext') returning id into v_sess;
  insert into __SCHEMA__.turns (session_id, user_id, role, content, created_at)
    values (v_sess, p_user, 'user', 'Hallo WAMO', v_zeit) returning id into v_turn;
  insert into __SCHEMA__.actions (user_id, session_id, turn_id, tool, risk, created_at)
    values (p_user, v_sess, v_turn, 'labor_einwilligung', 'read', v_zeit);
  insert into __SCHEMA__.canvas_items (user_id, session_id, type, title, data, created_at)
    values (p_user, v_sess, 'document', 'Testbericht', '{}'::jsonb, v_zeit) returning id into v_canvas;
  insert into __SCHEMA__.memories (user_id, kind, content, created_at) values (p_user, 'fact', 'Merkzettel', v_zeit);
  insert into __SCHEMA__.feedback (user_id, turn_id, kind, created_at) values (p_user, v_turn, 'correction', v_zeit);
  insert into __SCHEMA__.speakers (user_id, name, enrolled_at) values (p_user, 'Gast', v_zeit);
  insert into __SCHEMA__.vorgaenge (user_id, session_id, art, titel, status, gestartet_at)
    values (p_user, v_sess, 'aufgabe', 'Aufgabe', 'geplant', v_zeit);
  -- ohne Trigger und ohne Fremdschlüsselprüfung (nur Superuser): Hinweise, Dateien, Teilungen, Geräte
  perform set_config('session_replication_role', 'replica', true);
  insert into __SCHEMA__.hinweise (empfaenger, art, titel, erstellt_at) values (p_user, 'system', 'Hinweis', v_zeit);
  insert into __SCHEMA__.dateien (user_id, name, mime, groesse, sha256, herkunft, inhalt, erstellt_at)
    values (p_user, 'a' || p_nr || '.txt', 'text/plain', 3, repeat('a', 64), 'upload', decode('616263', 'hex'), v_zeit);
  insert into __SCHEMA__.teilungen (besitzer, empfaenger, art, canvas_id, recht, erstellt_at)
    values (p_user, p_zeuge, 'canvas', v_canvas, 'lesen', v_zeit) returning id into v_teil;
  insert into __SCHEMA__.teilung_events (teilung_id, besitzer, empfaenger, durch, art, ereignis, at)
    values (v_teil, p_user, p_zeuge, p_user, 'canvas', 'geteilt', v_zeit);
  insert into __SCHEMA__.devices (user_id, name, art, klasse, zugang, status, kennung, tunnel_slot, ssh_pubkey, ssh_fingerprint, created_at)
    values (p_user, 'Gast-Laptop ' || p_nr, 'persoenlich', 'laptop', 'tunnel', 'beantragt', 'pc-lab' || p_nr, 50 + p_nr,
            'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIA' || repeat('A', 42), 'SHA256:' || repeat(chr(96 + p_nr), 43), v_zeit)
    returning id into v_dev;
  insert into __SCHEMA__.device_events (device_id, nach, durch, at) values (v_dev, 'beantragt', p_user, v_zeit);
  perform set_config('session_replication_role', 'origin', true);
end $f$;
grant execute on all functions in schema wamo_testhilfe to wamo_core, wamo_admin, anon, authenticated, service_role;
-- anon, authenticated und service_role haben auf dem Schema kein usage (0001): hier vorübergehend, damit die Sperre wirklich an den
-- Tabellen- und Funktionsrechten scheitert und nicht schon am Schema
grant usage on schema __SCHEMA__ to anon, authenticated, service_role;

-- Testkonten: ein Admin, ein gewöhnliches Mitglied C, ein Zeuge Z, drei Laborprofile (A, B, S) und Konten für die Prüflinge
insert into auth.users (id, email) values
  ('00000000-0000-4000-8000-0000000018a0', 'labor-admin@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000018c0', 'labor-mitglied@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000018d0', 'labor-zeuge@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000018a1', 'labor-a@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000018b1', 'labor-b@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000018e1', 'labor-schau@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000018f1', 'labor-p1@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000018f2', 'labor-p2@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000018f3', 'labor-p3@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000018f4', 'labor-p4@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000018f5', 'labor-p5@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000018f6', 'labor-p6@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000018f7', 'labor-p7@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000018f8', 'labor-p8@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000018f9', 'labor-p9@wamo-test.invalid');
insert into __SCHEMA__.profiles (user_id, display_name, rolle, aktiv) values
  ('00000000-0000-4000-8000-0000000018a0', 'Labor Admin', 'admin', true),
  ('00000000-0000-4000-8000-0000000018c0', 'Labor Mitglied', 'mitglied', true),
  ('00000000-0000-4000-8000-0000000018d0', 'Labor Zeuge', 'mitglied', true);
-- Laborprofile: Platz 1, Platz 2 und das Schau-Profil (Rolle mitglied, aktiv = false, Befund 1 und 2)
insert into __SCHEMA__.profiles (user_id, display_name, rolle, aktiv, labor_art, labor_platz) values
  ('00000000-0000-4000-8000-0000000018a1', 'Gast', 'mitglied', false, 'platz', 1),
  ('00000000-0000-4000-8000-0000000018b1', 'Gast', 'mitglied', false, 'platz', 2),
  ('00000000-0000-4000-8000-0000000018e1', 'Gast', 'mitglied', false, 'schaufenster', null);

-- 1. Migration eingetragen, Spalten, Prüfungen, Index, Trigger
do $$ begin
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.schema_migrations where version = '0018_labor_profile'), 1::bigint, 'Migration eingetragen');
  perform wamo_testhilfe.gleich((select count(*) from information_schema.columns where table_schema = '__SCHEMA__' and table_name = 'profiles'
                                   and column_name in ('labor_art', 'labor_platz', 'labor_aktiv')), 3::bigint, 'drei Spalten');
  perform wamo_testhilfe.gleich((select count(*) from pg_constraint where conrelid = '__SCHEMA__.profiles'::regclass
                                   and conname in ('profiles_labor_art_chk', 'profiles_labor_platz_chk', 'profiles_labor_form_chk', 'profiles_labor_streng_chk')),
                                4::bigint, 'vier Prüfungen');
  perform wamo_testhilfe.gleich((select count(*) from pg_trigger where tgrelid = '__SCHEMA__.profiles'::regclass and tgname = 'profiles_labor_guard'),
                                1::bigint, 'Trigger profiles_labor_guard');
  perform wamo_testhilfe.gleich((select labor_aktiv from __SCHEMA__.profiles where user_id = '00000000-0000-4000-8000-0000000018a1'), false, 'Standard labor_aktiv falsch');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.profiles where user_id = '00000000-0000-4000-8000-0000000018c0' and labor_art is null
                                   and labor_platz is null and not labor_aktiv), 1::bigint, 'gewöhnliches Profil unberührt');
  perform wamo_testhilfe.gleich((select rolle::text from __SCHEMA__.profiles where user_id = '00000000-0000-4000-8000-0000000018e1'), 'mitglied', 'Schau-Profil ist Mitglied');
end $$;

-- 2. Prüfungen: jede verkehrte Form wird abgelehnt (23514), ein doppelter Platz ebenfalls (23505)
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.profiles (user_id, display_name, rolle, aktiv, labor_art, labor_platz) values ('00000000-0000-4000-8000-0000000018f1', 'x', 'admin', false, 'platz', 5)$s$, '23514');
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.profiles (user_id, display_name, rolle, aktiv, labor_art, labor_platz) values ('00000000-0000-4000-8000-0000000018f1', 'x', 'admin', false, 'schaufenster', null)$s$, '23514');
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.profiles (user_id, display_name, rolle, aktiv, labor_art, labor_platz) values ('00000000-0000-4000-8000-0000000018f2', 'x', 'mitglied', true, 'platz', 5)$s$, '23514');
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.profiles (user_id, display_name, rolle, aktiv, labor_art, labor_platz) values ('00000000-0000-4000-8000-0000000018f3', 'x', 'mitglied', false, 'platz', null)$s$, '23514');
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.profiles (user_id, display_name, rolle, aktiv, labor_art, labor_platz) values ('00000000-0000-4000-8000-0000000018f4', 'x', 'mitglied', false, 'schaufenster', 5)$s$, '23514');
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.profiles (user_id, display_name, rolle, aktiv, labor_art, labor_platz) values ('00000000-0000-4000-8000-0000000018f5', 'x', 'mitglied', true, null, 5)$s$, '23514');
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.profiles (user_id, display_name, rolle, aktiv, labor_art, labor_platz) values ('00000000-0000-4000-8000-0000000018f6', 'x', 'mitglied', false, 'platz', 17)$s$, '23514');
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.profiles (user_id, display_name, rolle, aktiv, labor_art, labor_platz) values ('00000000-0000-4000-8000-0000000018f7', 'x', 'mitglied', false, 'beobachter', 5)$s$, '23514');
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.profiles (user_id, display_name, rolle, aktiv, labor_aktiv) values ('00000000-0000-4000-8000-0000000018f8', 'x', 'mitglied', true, true)$s$, '23514');
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.profiles (user_id, display_name, rolle, aktiv, labor_art, labor_platz) values ('00000000-0000-4000-8000-0000000018f9', 'x', 'mitglied', false, 'platz', 1)$s$, '23505');
-- Ein Laborprofil entsteht nie freigeschaltet (Trigger beim Anlegen)
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.profiles (user_id, display_name, rolle, aktiv, labor_art, labor_platz, labor_aktiv) values ('00000000-0000-4000-8000-0000000018f1', 'x', 'mitglied', false, 'platz', 5, true)$s$, '42501');
-- Befund 1 und 2: ein Laborprofil wird nie Admin und nie aktiv
select wamo_testhilfe.fehler($s$update __SCHEMA__.profiles set rolle = 'admin' where user_id = '00000000-0000-4000-8000-0000000018a1'$s$, '23514');
select wamo_testhilfe.fehler($s$update __SCHEMA__.profiles set rolle = 'admin' where user_id = '00000000-0000-4000-8000-0000000018e1'$s$, '23514');
select wamo_testhilfe.fehler($s$update __SCHEMA__.profiles set aktiv = true where user_id = '00000000-0000-4000-8000-0000000018a1'$s$, '23514');
select wamo_testhilfe.fehler($s$update __SCHEMA__.profiles set labor_aktiv = true where user_id = '00000000-0000-4000-8000-0000000018c0'$s$, '23514');
-- Trigger: labor_art und labor_platz sind nach dem Anlegen unveränderlich, auch für den Superuser
select wamo_testhilfe.fehler($s$update __SCHEMA__.profiles set labor_art = null where user_id = '00000000-0000-4000-8000-0000000018a1'$s$, '42501');
select wamo_testhilfe.fehler($s$update __SCHEMA__.profiles set labor_platz = 9 where user_id = '00000000-0000-4000-8000-0000000018a1'$s$, '42501');
select wamo_testhilfe.fehler($s$update __SCHEMA__.profiles set labor_art = 'schaufenster', labor_platz = null where user_id = '00000000-0000-4000-8000-0000000018a1'$s$, '42501');
select wamo_testhilfe.fehler($s$update __SCHEMA__.profiles set labor_art = 'platz', labor_platz = 8 where user_id = '00000000-0000-4000-8000-0000000018c0'$s$, '42501');

-- 3. Rechte für INSERT und UPDATE je Rolle (Befund 12): wamo_core, anon, authenticated, service_role schreiben die Laborspalten nie
set role wamo_core;
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.profiles (user_id, display_name, labor_art, labor_platz, rolle, aktiv) values ('00000000-0000-4000-8000-0000000018f1', 'x', 'platz', 5, 'mitglied', false)$s$, '42501');
select wamo_testhilfe.fehler($s$update __SCHEMA__.profiles set labor_aktiv = true where user_id = '00000000-0000-4000-8000-0000000018a1'$s$, '42501');
select wamo_testhilfe.fehler($s$update __SCHEMA__.profiles set labor_art = null where user_id = '00000000-0000-4000-8000-0000000018a1'$s$, '42501');
select wamo_testhilfe.gleich((select labor_art from __SCHEMA__.profiles where user_id = '00000000-0000-4000-8000-0000000018a1'), 'platz', 'wamo_core liest labor_art');
select wamo_testhilfe.gleich((select labor_aktiv from __SCHEMA__.profiles where user_id = '00000000-0000-4000-8000-0000000018a1'), false, 'wamo_core liest labor_aktiv');
reset role;
set role anon;
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.profiles (user_id, display_name, labor_art, labor_platz, rolle, aktiv) values ('00000000-0000-4000-8000-0000000018f1', 'x', 'platz', 5, 'mitglied', false)$s$, '42501');
select wamo_testhilfe.fehler($s$update __SCHEMA__.profiles set labor_aktiv = true where user_id = '00000000-0000-4000-8000-0000000018a1'$s$, '42501');
reset role;
set role authenticated;
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.profiles (user_id, display_name, labor_art, labor_platz, rolle, aktiv) values ('00000000-0000-4000-8000-0000000018f1', 'x', 'platz', 5, 'mitglied', false)$s$, '42501');
select wamo_testhilfe.fehler($s$update __SCHEMA__.profiles set labor_aktiv = true where user_id = '00000000-0000-4000-8000-0000000018a1'$s$, '42501');
reset role;
set role service_role;
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.profiles (user_id, display_name, labor_art, labor_platz, rolle, aktiv) values ('00000000-0000-4000-8000-0000000018f1', 'x', 'platz', 5, 'mitglied', false)$s$, '42501');
select wamo_testhilfe.fehler($s$update __SCHEMA__.profiles set labor_aktiv = true where user_id = '00000000-0000-4000-8000-0000000018a1'$s$, '42501');
reset role;
-- wamo_admin: legt Laborprofile an und ändert danach nur labor_aktiv
set role wamo_admin;
insert into __SCHEMA__.profiles (user_id, display_name, rolle, aktiv, labor_art, labor_platz) values
  ('00000000-0000-4000-8000-0000000018f1', 'Gast', 'mitglied', false, 'platz', 5);
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.profiles (user_id, display_name, rolle, aktiv, labor_art, labor_platz, labor_aktiv) values ('00000000-0000-4000-8000-0000000018f2', 'x', 'mitglied', false, 'platz', 6, true)$s$, '42501');
select wamo_testhilfe.fehler($s$update __SCHEMA__.profiles set labor_art = null where user_id = '00000000-0000-4000-8000-0000000018f1'$s$, '42501');
select wamo_testhilfe.fehler($s$update __SCHEMA__.profiles set labor_platz = 9 where user_id = '00000000-0000-4000-8000-0000000018f1'$s$, '42501');
update __SCHEMA__.profiles set labor_aktiv = true where user_id = '00000000-0000-4000-8000-0000000018f1';
select wamo_testhilfe.gleich((select labor_aktiv from __SCHEMA__.profiles where user_id = '00000000-0000-4000-8000-0000000018f1'), true, 'wamo_admin aktiviert');
update __SCHEMA__.profiles set labor_aktiv = false where user_id = '00000000-0000-4000-8000-0000000018f1';
select wamo_testhilfe.gleich((select labor_aktiv from __SCHEMA__.profiles where user_id = '00000000-0000-4000-8000-0000000018f1'), false, 'wamo_admin sperrt');
reset role;
do $$ begin
  perform wamo_testhilfe.gleich(has_column_privilege('wamo_core', '__SCHEMA__.profiles', 'labor_aktiv', 'UPDATE'), false, 'wamo_core ohne UPDATE');
  perform wamo_testhilfe.gleich(has_column_privilege('wamo_admin', '__SCHEMA__.profiles', 'labor_art', 'UPDATE'), false, 'wamo_admin ohne UPDATE auf labor_art');
  perform wamo_testhilfe.gleich(has_column_privilege('wamo_admin', '__SCHEMA__.profiles', 'labor_aktiv', 'UPDATE'), true, 'wamo_admin UPDATE auf labor_aktiv');
  perform wamo_testhilfe.gleich(has_column_privilege('wamo_admin', '__SCHEMA__.profiles', 'labor_art', 'INSERT'), true, 'wamo_admin INSERT auf labor_art');
end $$;

-- 4. T12: Laborprofile (auch das Schau-Profil) zählen nicht als Admin der Sonderfreigabe (0013)
do $$ begin
  perform wamo_testhilfe.gleich(__SCHEMA__.release_ist_admin('00000000-0000-4000-8000-0000000018e1'), false, 'Schau-Profil ist kein Admin');
  perform wamo_testhilfe.gleich(__SCHEMA__.release_ist_admin('00000000-0000-4000-8000-0000000018a1'), false, 'Platz-Profil ist kein Admin');
  perform wamo_testhilfe.gleich(__SCHEMA__.release_ist_admin('00000000-0000-4000-8000-0000000018a0'), true, 'der echte Admin bleibt Admin');
  perform wamo_testhilfe.gleich(__SCHEMA__.release_anzahl_admins(),
    (select count(*)::integer from __SCHEMA__.profiles where aktiv and rolle = 'admin' and labor_art is null), 'Zähler der Admins ohne Laborprofile');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.profiles where labor_art is not null and (aktiv or rolle = 'admin')), 0::bigint,
    'kein Laborprofil ist aktiv oder Admin');
end $$;

-- 5. Rückweg: der gesamte do-Block aus 0018_labor_profile.down.sql (wörtlich, Prüfung und Abbau in einer Anweisung) bricht bei einem
-- Laborprofil in der Prüfung ab, bevor etwas verändert wird. Danach sind Spalten und Funktionen noch da.
-- Spätere Migrationen im Schema (ab 0019) würden die Prüfung davor abbrechen: für diesen Test wird sie übersprungen, damit die Meldung
-- der Laborprofile geprüft wird (die Einstellung gilt nur in dieser Transaktion).
select set_config('wamo.down_trotz_folgemigration', 'ja', true);
select wamo_testhilfe.fehler($d$do $$
declare
  v_n bigint := 0;
begin
  if '__SCHEMA__' = 'wamo_prod' and coalesce(current_setting('wamo.allow_drop', true), '') <> 'ja' then
    raise exception 'Down in wamo_prod gesperrt. Nach Backup: PGOPTIONS="-c wamo.allow_drop=ja"' using errcode = '55000';
  end if;
  if exists (select 1 from __SCHEMA__.schema_migrations where version > '0018_labor_profile')
     and coalesce(current_setting('wamo.down_trotz_folgemigration', true), '') <> 'ja' then
    raise exception '0018 down: erst die späteren Migrationen zurücknehmen' using errcode = '55000';
  end if;
  -- dynamisch, damit ein zweiter Lauf nach dem Entfernen der Spalte nicht an der fehlenden Spalte scheitert
  if exists (select 1 from information_schema.columns where table_schema = '__SCHEMA__'
              and table_name = 'profiles' and column_name = 'labor_art') then
    execute 'select count(*) from __SCHEMA__.profiles where labor_art is not null' into v_n;
  end if;
  if v_n > 0 then
    raise exception '0018 down: es gibt noch % Laborprofile (auch gesperrte zählen). Erst labor_loeschen, dann die Profile löschen', v_n
      using errcode = '55000';
  end if;

  -- Ab hier wird verändert: nur nach bestandener Prüfung, mit begrenzter Wartezeit auf Sperren
  perform set_config('lock_timeout', '5s', true);
  drop trigger if exists profiles_labor_guard on __SCHEMA__.profiles;
  drop function if exists __SCHEMA__.profiles_labor_guard();
  drop function if exists __SCHEMA__.labor_loeschen(uuid, timestamptz, timestamptz);
  drop function if exists __SCHEMA__.labor_zaehlen(uuid);
  drop index if exists __SCHEMA__.profiles_labor_platz_uni;
  alter table __SCHEMA__.profiles drop constraint if exists profiles_labor_streng_chk;
  alter table __SCHEMA__.profiles drop constraint if exists profiles_labor_form_chk;
  alter table __SCHEMA__.profiles drop constraint if exists profiles_labor_platz_chk;
  alter table __SCHEMA__.profiles drop constraint if exists profiles_labor_art_chk;
  alter table __SCHEMA__.profiles drop column if exists labor_aktiv;
  alter table __SCHEMA__.profiles drop column if exists labor_platz;
  alter table __SCHEMA__.profiles drop column if exists labor_art;
  delete from __SCHEMA__.schema_migrations where version = '0018_labor_profile';
end $$;$d$, '55000', 'Laborprofile');
do $$ begin
  perform wamo_testhilfe.gleich((select count(*) from information_schema.columns where table_schema = '__SCHEMA__' and table_name = 'profiles'
                                   and column_name in ('labor_art', 'labor_platz', 'labor_aktiv')), 3::bigint, 'der Rückweg hat nichts verändert (Spalten)');
  perform wamo_testhilfe.gleich((select count(*) from pg_proc where pronamespace = '__SCHEMA__'::regnamespace
                                   and proname in ('labor_loeschen', 'labor_zaehlen', 'profiles_labor_guard')), 3::bigint, 'der Rückweg hat nichts verändert (Funktionen)');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.schema_migrations where version = '0018_labor_profile'), 1::bigint,
                                'der Rückweg hat die Migration nicht ausgetragen');
end $$;

-- 6. Löschfunktion: vollständig aus dem Katalog. Jede Tabelle mit einer Spalte, die auf profiles(user_id) oder auf auth.users(id)
-- verweist, wird von labor_loeschen gelöscht (steht im Funktionstext) oder steht in der begründeten Ausnahmeliste
-- (voice-core/labor_loeschliste.py: AUSNAHMEN und FREMDE_VERWEISE). Eine neue Tabelle macht diesen Test rot.
do $$
declare
  r record;
  def text := pg_get_functiondef('__SCHEMA__.labor_loeschen(uuid, timestamptz, timestamptz)'::regprocedure);
  ausnahmen text[] := array['profiles', 'model_access', 'release_anfragen', 'release_freigaben', 'release_ereignisse', 'rd_sitzungen'];
  fremde text[] := array['memories.reviewed_by', 'canvas_items.bearbeitet_von', 'dateien.hochgeladen_von', 'devices.freigegeben_von',
                         'devices.geaendert_von', 'teilungen.widerrufen_von'];
begin
  for r in
    select distinct (select t.relname from pg_class t where t.oid = c.conrelid) as tabelle, a.attname as spalte
      from pg_constraint c
      join pg_attribute a on a.attrelid = c.conrelid and a.attnum = any(c.conkey)
     where c.contype = 'f' and c.connamespace = '__SCHEMA__'::regnamespace
       and (c.confrelid = '__SCHEMA__.profiles'::regclass or c.confrelid = 'auth.users'::regclass)
  loop
    if r.tabelle = any(ausnahmen) or (r.tabelle || '.' || r.spalte) = any(fremde) then continue; end if;
    if position('delete from __SCHEMA__.' || r.tabelle || ' ' in def) = 0 then
      raise exception 'Tabelle % (Spalte %) verweist auf ein Profil, steht aber weder in labor_loeschen noch in der Ausnahmeliste', r.tabelle, r.spalte;
    end if;
  end loop;
  -- die Funktion löscht nichts aus den Ausnahmen
  if exists (select 1 from unnest(ausnahmen) t where position('delete from __SCHEMA__.' || t || ' ' in def) > 0) then
    raise exception 'labor_loeschen löscht eine Tabelle der Ausnahmeliste';
  end if;
end $$;

-- 7. Fixture: Platz 1 (A), Platz 2 (B, zwei Durchläufe), ein gewöhnliches Mitglied (C), alle mit Zeilen in jeder Tabelle der Löschliste
select wamo_testhilfe.fuellen('00000000-0000-4000-8000-0000000018a1', '00000000-0000-4000-8000-0000000018d0', now() - interval '3 hours', 1);
select wamo_testhilfe.fuellen('00000000-0000-4000-8000-0000000018b1', '00000000-0000-4000-8000-0000000018d0', now() - interval '5 hours', 3);
select wamo_testhilfe.fuellen('00000000-0000-4000-8000-0000000018b1', '00000000-0000-4000-8000-0000000018d0', now() - interval '1 hour', 4);
select wamo_testhilfe.fuellen('00000000-0000-4000-8000-0000000018c0', '00000000-0000-4000-8000-0000000018d0', now() - interval '2 hours', 2);
-- A hat zusätzlich eine abgestürzte Sitzung (ohne Ende, älter als 130 Minuten): sie blockiert die Löschung nicht
insert into __SCHEMA__.sessions (user_id, started_at) values ('00000000-0000-4000-8000-0000000018a1', now() - interval '4 hours');

-- 8. Zugang: wer die Funktionen ausführen darf, wann sie wirft
set role wamo_core;
select wamo_testhilfe.fehler($s$select __SCHEMA__.labor_loeschen('00000000-0000-4000-8000-0000000018a1')$s$, '42501');
select wamo_testhilfe.fehler($s$select __SCHEMA__.labor_zaehlen('00000000-0000-4000-8000-0000000018a1')$s$, '42501');
reset role;
set role anon;
select wamo_testhilfe.fehler($s$select __SCHEMA__.labor_loeschen('00000000-0000-4000-8000-0000000018a1')$s$, '42501');
reset role;
set role authenticated;
select wamo_testhilfe.fehler($s$select __SCHEMA__.labor_zaehlen('00000000-0000-4000-8000-0000000018a1')$s$, '42501');
reset role;
set role service_role;
select wamo_testhilfe.fehler($s$select __SCHEMA__.labor_loeschen('00000000-0000-4000-8000-0000000018a1')$s$, '42501');
reset role;
set role wamo_admin;
-- ein Profil ohne labor_art (auch ein Mitglied, auch ein Admin) wird nie angefasst
select wamo_testhilfe.fehler($s$select __SCHEMA__.labor_loeschen('00000000-0000-4000-8000-0000000018c0')$s$, '42501');
select wamo_testhilfe.fehler($s$select __SCHEMA__.labor_loeschen('00000000-0000-4000-8000-0000000018a0')$s$, '42501');
select wamo_testhilfe.fehler($s$select __SCHEMA__.labor_zaehlen('00000000-0000-4000-8000-0000000018c0')$s$, '42501');
select wamo_testhilfe.fehler($s$select __SCHEMA__.labor_loeschen('00000000-0000-4000-8000-0000000018ff')$s$, '42501');
select wamo_testhilfe.fehler($s$select __SCHEMA__.labor_loeschen(null)$s$, '22004');
select wamo_testhilfe.fehler($s$select __SCHEMA__.labor_loeschen('00000000-0000-4000-8000-0000000018a1', now(), now() - interval '1 day')$s$, '22023');
-- freigeschaltet (labor_aktiv): erst sperren
update __SCHEMA__.profiles set labor_aktiv = true where user_id = '00000000-0000-4000-8000-0000000018a1';
select wamo_testhilfe.fehler($s$select __SCHEMA__.labor_loeschen('00000000-0000-4000-8000-0000000018a1')$s$, '55000');
update __SCHEMA__.profiles set labor_aktiv = false where user_id = '00000000-0000-4000-8000-0000000018a1';
reset role;
-- laufende Sitzung (ohne Ende, jünger als 130 Minuten)
insert into __SCHEMA__.sessions (id, user_id) values ('00000000-0000-4000-8000-0000000018aa', '00000000-0000-4000-8000-0000000018a1');
set role wamo_admin;
select wamo_testhilfe.fehler($s$select __SCHEMA__.labor_loeschen('00000000-0000-4000-8000-0000000018a1')$s$, '55000');
reset role;
update __SCHEMA__.sessions set ended_at = now() where id = '00000000-0000-4000-8000-0000000018aa';

-- 9. Vorher: jede Tabelle der Löschliste hat Zeilen für A (sonst wäre der Test leer)
do $$
declare
  z jsonb;
  k text;
begin
  set local role wamo_admin;
  z := __SCHEMA__.labor_zaehlen('00000000-0000-4000-8000-0000000018a1');
  reset role;
  perform wamo_testhilfe.gleich((select count(*) from jsonb_object_keys(z)), 14::bigint, 'labor_zaehlen liefert 14 Tabellen');
  for k in select jsonb_object_keys(z) loop
    if (z ->> k)::bigint < 1 then raise exception 'Fixture leer: % hat keine Zeile für A', k; end if;
  end loop;
end $$;

-- 10. Zeitfenster (Durchlauf): B hat zwei Durchläufe, das Fenster trifft nur den ersten (vor 5 Stunden)
create table wamo_testhilfe.merker (name text primary key, wert jsonb);
insert into wamo_testhilfe.merker values
  ('b_vorher', wamo_testhilfe.zeilen('00000000-0000-4000-8000-0000000018b1')),
  ('c_vorher', wamo_testhilfe.zeilen('00000000-0000-4000-8000-0000000018c0'));
grant select on wamo_testhilfe.merker to wamo_admin;
set role wamo_admin;
select wamo_testhilfe.gleich((select count(*) from jsonb_each_text(__SCHEMA__.labor_zaehlen('00000000-0000-4000-8000-0000000018b1')) e where e.value::bigint <> 2),
                             0::bigint, 'B hat zwei Durchläufe in jeder Tabelle');
select __SCHEMA__.labor_loeschen('00000000-0000-4000-8000-0000000018b1', now() - interval '6 hours', now() - interval '4 hours');
select wamo_testhilfe.gleich((select count(*) from jsonb_each_text(__SCHEMA__.labor_zaehlen('00000000-0000-4000-8000-0000000018b1')) e where e.value::bigint <> 1),
                             0::bigint, 'nach dem Fenster bleibt in jeder Tabelle genau der zweite Durchlauf');
reset role;
do $$ begin
  perform wamo_testhilfe.gleich(wamo_testhilfe.zeilen('00000000-0000-4000-8000-0000000018c0'),
                                (select wert from wamo_testhilfe.merker where name = 'c_vorher'), 'C unberührt nach dem Fenster');
end $$;

-- 11. T10: Löschfunktion für A, danach ist nichts mehr da; B (zweiter Durchlauf) und C bleiben
insert into wamo_testhilfe.merker values
  ('b_nach_fenster', wamo_testhilfe.zeilen('00000000-0000-4000-8000-0000000018b1')),
  ('c_nach_fenster', wamo_testhilfe.zeilen('00000000-0000-4000-8000-0000000018c0'));
set role wamo_admin;
select wamo_testhilfe.gleich((select count(*) from jsonb_each_text(__SCHEMA__.labor_loeschen('00000000-0000-4000-8000-0000000018a1')) e where e.value::bigint < 1),
                             0::bigint, 'labor_loeschen meldet je Tabelle mindestens eine gelöschte Zeile');
reset role;
do $$
declare
  z jsonb := wamo_testhilfe.zeilen('00000000-0000-4000-8000-0000000018a1');
  k text;
  ausnahmen text[] := array['profiles', 'model_access', 'release_anfragen', 'release_freigaben', 'release_ereignisse', 'rd_sitzungen'];
begin
  -- jede Besitzerspalte jeder Tabelle: null Zeilen, außer in der Ausnahmeliste (hier nur das Profil selbst)
  for k in select jsonb_object_keys(z) loop
    if split_part(k, '.', 1) = any(ausnahmen) then continue; end if;
    if (z ->> k)::bigint <> 0 then
      raise exception 'nach labor_loeschen ist noch etwas da: % hat % Zeilen', k, z ->> k;
    end if;
  end loop;
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.profiles where user_id = '00000000-0000-4000-8000-0000000018a1'), 1::bigint,
                                'das Profil selbst bleibt');
  perform wamo_testhilfe.gleich(wamo_testhilfe.zeilen('00000000-0000-4000-8000-0000000018b1'),
                                (select wert from wamo_testhilfe.merker where name = 'b_nach_fenster'), 'B unberührt');
  perform wamo_testhilfe.gleich(wamo_testhilfe.zeilen('00000000-0000-4000-8000-0000000018c0'),
                                (select wert from wamo_testhilfe.merker where name = 'c_nach_fenster'), 'C unberührt');
end $$;
set role wamo_admin;
select wamo_testhilfe.gleich((select count(*) from jsonb_each_text(__SCHEMA__.labor_zaehlen('00000000-0000-4000-8000-0000000018a1')) e where e.value::bigint <> 0),
                             0::bigint, 'labor_zaehlen meldet nach dem Löschen überall null');
-- ein zweiter Aufruf ist harmlos: nichts mehr da, nichts passiert
select wamo_testhilfe.gleich((select count(*) from jsonb_each_text(__SCHEMA__.labor_loeschen('00000000-0000-4000-8000-0000000018a1')) e where e.value::bigint <> 0),
                             0::bigint, 'zweiter Aufruf löscht nichts');
reset role;

-- 12. Das Schau-Profil: ohne Zeilen löscht die Funktion nichts und wirft nicht
set role wamo_admin;
select __SCHEMA__.labor_loeschen('00000000-0000-4000-8000-0000000018e1');
reset role;

rollback;
\echo 0018 OK
