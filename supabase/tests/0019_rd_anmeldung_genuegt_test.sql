-- Test für 0019_rd_anmeldung_genuegt. Läuft als postgres in EINER Transaktion, die am Ende immer zurückgerollt wird
-- (Testkonten in auth.users, Profile, Sitzungen und Hinweise verschwinden wieder). Nur für wamo_test oder wamo_dev.
--   sed "s/__SCHEMA__/wamo_test/g" supabase/tests/0019_rd_anmeldung_genuegt_test.sql \
--     | ssh wamo-sokrates2 "docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 -q"
-- Voraussetzung: 0001, 0002, 0015 und 0019 sind im Schema angewendet. Ohne 0019 bricht der Test gleich ab.
-- Erfolg: letzte Zeile "0019 OK", Exit-Code 0. Jeder Fehlschlag bricht mit einer Meldung ab.
-- Der Test 0015_rd_sitzungen_test.sql prüft das, was 0019 nicht ändert (Rechte, Belegung, Raten), und kennt beide Stände.
\set ON_ERROR_STOP 1
begin;
do $$ begin
  if '__SCHEMA__' = 'wamo_prod' then raise exception 'Test nie in wamo_prod'; end if;
  if not exists (select 1 from __SCHEMA__.schema_migrations where version = '0019_rd_anmeldung_genuegt') then
    raise exception '0019 ist in __SCHEMA__ nicht angewendet';
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

-- Vorbedingung: keine echten offenen Fernsitzungen (sie würden Ergebnisse verfälschen)
do $$ begin
  if exists (select 1 from __SCHEMA__.rd_sitzungen where ende_at is null) then
    raise exception 'Es laufen echte Fernsitzungen in __SCHEMA__, Test abgebrochen';
  end if;
end $$;

-- 1. Rechte: unverändert gegenüber 0015, dazu hinweise nur anlegen, nie löschen oder quittieren
set role wamo_core;
select wamo_testhilfe.fehler($$delete from __SCHEMA__.hinweise$$, '42501');
select wamo_testhilfe.fehler($$update __SCHEMA__.hinweise set gelesen_at = now()$$, '42501');
select wamo_testhilfe.fehler($$select __SCHEMA__.hinweis_an_admins('system', 'x', 'y', null, '{}'::jsonb)$$, '42501');
select wamo_testhilfe.fehler($$delete from __SCHEMA__.rd_sitzungen$$, '42501');
reset role;
set role wamo_admin;
select wamo_testhilfe.fehler($$select * from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', 'sokrates1', 3600, clock_timestamp())$$, '42501');
reset role;

-- 2. Eingaben und Akteur (wie 0015)
set role wamo_core;
select wamo_testhilfe.fehler($$select * from __SCHEMA__.rd_sitzung_beginnen(null, 'sokrates1', 3600, clock_timestamp())$$, '22004');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', null, 3600, clock_timestamp())$$, '22004');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', 'sokrates1', 3600, null)$$, '22004');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', 'Sokrates1', 3600, clock_timestamp())$$, '22023');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', 'sokrates1', 30, clock_timestamp())$$, '22023');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000b1', 'sokrates1', 3600, clock_timestamp())$$, '42501');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000b4', 'sokrates1', 3600, clock_timestamp())$$, '42501');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000ff', 'sokrates1', 3600, clock_timestamp())$$, '42501');
-- Zeit des Codes: Zukunft und mehr als 31 Tage sind abgelehnt, alles dazwischen ist erlaubt (siehe Abschnitt 3)
select wamo_testhilfe.fehler($$select * from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', 'sokrates1', 3600, clock_timestamp() + interval '30 seconds')$$, '23514');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', 'sokrates1', 3600, clock_timestamp() - interval '31 days' - interval '1 second')$$, '23514');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', 'sokrates1', 3600, clock_timestamp() - interval '90 days')$$, '23514');
reset role;
do $$ begin
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.rd_sitzungen where profil_id in (
    '00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000000a2', '00000000-0000-4000-8000-0000000000b1',
    '00000000-0000-4000-8000-0000000000b4')), 0::bigint, 'abgelehnte Starts legen nichts an');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.hinweise where empfaenger in (
    '00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000000a2', '00000000-0000-4000-8000-0000000000b1',
    '00000000-0000-4000-8000-0000000000b4')), 0::bigint, 'abgelehnte Starts melden nichts');
end $$;

-- 3. Der Code der Anmeldung genuegt: alt geht, derselbe Code startet mehrmals, jeder Start meldet sich bei allen Admins
set role wamo_core;
do $$ declare r record; a uuid; b uuid; c uuid; d uuid; t0 timestamptz := clock_timestamp() - interval '3 days'; n_admins bigint; begin
  select count(*) into n_admins from __SCHEMA__.profiles where aktiv and rolle = 'admin';
  -- drei Tage alter Code
  select * into r from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', 'sokrates1', 3600, t0);
  perform wamo_testhilfe.gleich(r.ergebnis, 'ok', 'drei Tage alter Code startet');
  a := r.sitzung_id;
  perform wamo_testhilfe.gleich(r.abgeloest_id is null, true, 'beim ersten Start nichts abgeloest');
  -- derselbe Code, zweiter Start (Ein Code, ein Start entfällt): geht und löst die erste Sitzung ab
  select * into r from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', 'sokrates1', 3600, t0);
  perform wamo_testhilfe.gleich(r.ergebnis, 'ok', 'zweiter Start mit demselben Code');
  b := r.sitzung_id;
  perform wamo_testhilfe.gleich(r.abgeloest_id, a, 'erste Sitzung abgeloest');
  perform wamo_testhilfe.gleich((select grund from __SCHEMA__.rd_sitzungen where id = a), 'abgeloest', 'Grund abgeloest');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.rd_sitzungen where ende_at is null and ziel = 'sokrates1'), 1::bigint, 'genau eine offene Sitzung je Ziel');
  -- zehn Minuten alter Code und ein Code kurz vor der Grenze von 31 Tagen (anderer Admin, anderes Ziel)
  select * into r from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a2', 'caesar', 3600, clock_timestamp() - interval '10 minutes');
  perform wamo_testhilfe.gleich(r.ergebnis, 'ok', 'zehn Minuten alter Code startet');
  c := r.sitzung_id;
  select * into r from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a2', 'sokrates2', 3600, clock_timestamp() - interval '30 days 23 hours');
  perform wamo_testhilfe.gleich(r.ergebnis, 'ok', 'Code kurz vor 31 Tagen startet');
  d := r.sitzung_id;
  -- Hinweis an alle aktiven Admins, nicht an Mitglieder und nicht an inaktive Profile
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.hinweise where bezug = a), n_admins, 'Hinweis je aktivem Admin (erster Start)');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.hinweise where bezug = a and empfaenger in (
    '00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000000a2')), 2::bigint, 'beide Test-Admins bekommen ihn, auch der Startende');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.hinweise where bezug in (a, b, c, d) and empfaenger in (
    '00000000-0000-4000-8000-0000000000b1', '00000000-0000-4000-8000-0000000000b4')), 0::bigint, 'Mitglied und inaktiver Admin bekommen keinen');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.hinweise where bezug in (a, b, c, d)), n_admins * 4, 'vier Starts, vier Hinweise je Admin');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.hinweise where bezug in (a, b, c, d) and art = 'system' and gelesen_at is null), n_admins * 4, 'art system, ungelesen');
  -- Inhalt: Person, Kurzname des Ziels, Alter des Codes, Höchstdauer; Titel mit dem Ziel
  perform wamo_testhilfe.gleich((select titel from __SCHEMA__.hinweise where bezug = a and empfaenger = '00000000-0000-4000-8000-0000000000a2'), 'Fernzugriff gestartet: sokrates1', 'Titel');
  perform wamo_testhilfe.gleich((select h.text like 'Admin Eins hat eine Fernsitzung auf sokrates1 gestartet.%' from __SCHEMA__.hinweise h where h.bezug = a and h.empfaenger = '00000000-0000-4000-8000-0000000000a2'), true, 'Text nennt Person und Ziel');
  perform wamo_testhilfe.gleich((select h.text like '%wurde vor 3 Tagen eingegeben.%' from __SCHEMA__.hinweise h where h.bezug = a and h.empfaenger = '00000000-0000-4000-8000-0000000000a1'), true, 'Alter des Codes in Tagen (drei)');
  perform wamo_testhilfe.gleich((select h.text like '%wurde vor 30 Tagen eingegeben.%' from __SCHEMA__.hinweise h where h.bezug = d and h.empfaenger = '00000000-0000-4000-8000-0000000000a1'), true, 'Alter des Codes in Tagen (dreissig)');
  perform wamo_testhilfe.gleich((select h.text like '%wurde vor 10 Minuten eingegeben.%' from __SCHEMA__.hinweise h where h.bezug = c and h.empfaenger = '00000000-0000-4000-8000-0000000000a1'), true, 'Alter des Codes in Minuten');
  perform wamo_testhilfe.gleich((select h.text like '%nach höchstens 60 Minuten.%' from __SCHEMA__.hinweise h where h.bezug = a and h.empfaenger = '00000000-0000-4000-8000-0000000000a1'), true, 'Hoechstdauer');
  -- nichts Vertrauliches im Hinweis: keine Adresse, kein Konto, kein Passwort, keine Zugangsdaten
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.hinweise h where h.bezug in (a, b, c, d)
    and (h.text ~* '(passwort|password|schluessel|schlüssel|token|konto|wamo-rd|hostname|json|[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)'
         or h.titel ~* '(passwort|password|token|[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)')), 0::bigint, 'keine Zugangsdaten und keine Adressen im Text');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.hinweise h where h.bezug in (a, b, c, d) and (length(h.titel) > 200 or length(h.text) > 1000)), 0::bigint, 'Laengen');
  -- daten: genau vier feste Schlüssel
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.hinweise h, jsonb_object_keys(h.daten) k
    where h.bezug = a and k not in ('quelle', 'sitzung', 'ziel', 'profil')), 0::bigint, 'nur feste Schluessel in daten');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.hinweise h, jsonb_object_keys(h.daten) k where h.bezug = a and h.empfaenger = '00000000-0000-4000-8000-0000000000a1'), 4::bigint, 'vier Schluessel in daten');
  perform wamo_testhilfe.gleich((select h.daten->>'quelle' from __SCHEMA__.hinweise h where h.bezug = a and h.empfaenger = '00000000-0000-4000-8000-0000000000a1'), 'fernzugriff', 'daten.quelle');
  perform wamo_testhilfe.gleich((select (h.daten->>'sitzung')::uuid from __SCHEMA__.hinweise h where h.bezug = a and h.empfaenger = '00000000-0000-4000-8000-0000000000a1'), a, 'daten.sitzung');
  perform wamo_testhilfe.gleich((select h.daten->>'ziel' from __SCHEMA__.hinweise h where h.bezug = a and h.empfaenger = '00000000-0000-4000-8000-0000000000a1'), 'sokrates1', 'daten.ziel');
  perform wamo_testhilfe.gleich((select (h.daten->>'profil')::uuid from __SCHEMA__.hinweise h where h.bezug = a and h.empfaenger = '00000000-0000-4000-8000-0000000000a1'), '00000000-0000-4000-8000-0000000000a1'::uuid, 'daten.profil');
end $$;
-- ein frischer Code (weniger als drei Minuten) heißt im Text "gerade eben"
do $$ declare r record; begin
  select * into r from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', 'ziel4', 3600, clock_timestamp(), 10, 60);
  perform wamo_testhilfe.gleich(r.ergebnis, 'ok', 'frischer Code startet');
  perform wamo_testhilfe.gleich((select h.text like '%wurde gerade eben eingegeben.%' from __SCHEMA__.hinweise h where h.bezug = r.sitzung_id and h.empfaenger = '00000000-0000-4000-8000-0000000000a2'), true, 'frischer Code: gerade eben');
  -- fünf Stunden alter Code: Stunden bis 48 Stunden, danach Tage
  select * into r from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', 'ziel7', 3600, clock_timestamp() - interval '5 hours', 10, 60);
  perform wamo_testhilfe.gleich(r.ergebnis, 'ok', 'fünf Stunden alter Code startet');
  perform wamo_testhilfe.gleich((select h.text like '%wurde vor 5 Stunden eingegeben.%' from __SCHEMA__.hinweise h where h.bezug = r.sitzung_id and h.empfaenger = '00000000-0000-4000-8000-0000000000a2'), true, 'Alter des Codes in Stunden');
  -- eine Minute Höchstdauer heißt "1 Minute", nicht "1 Minuten"
  select * into r from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', 'ziel8', 60, clock_timestamp(), 10, 60);
  perform wamo_testhilfe.gleich((select h.text like '%nach höchstens 1 Minute.%' from __SCHEMA__.hinweise h where h.bezug = r.sitzung_id and h.empfaenger = '00000000-0000-4000-8000-0000000000a2'), true, 'eine Minute im Singular');
end $$;
reset role;

-- 4. Kein Hinweis ohne Start: belegt, rate, voll und jeder Fehler melden nichts
set role wamo_core;
do $$ declare r record; vorher bigint; begin
  select count(*) into vorher from __SCHEMA__.hinweise where daten->>'quelle' = 'fernzugriff';
  select * into r from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a2', 'sokrates1', 3600, clock_timestamp());
  perform wamo_testhilfe.gleich(r.ergebnis, 'belegt', 'sokrates1 ist belegt');
  perform wamo_testhilfe.gleich(r.belegt_von, 'Admin Eins', 'Name des Belegers');
  select * into r from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', 'ziel5', 3600, clock_timestamp(), 10, 1);
  perform wamo_testhilfe.gleich(r.ergebnis, 'rate', 'zu viele Starts je Stunde');
  select * into r from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a2', 'ziel6', 3600, clock_timestamp(), 1, 60);
  perform wamo_testhilfe.gleich(r.ergebnis, 'voll', 'zu viele gleichzeitige Sitzungen');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.hinweise where daten->>'quelle' = 'fernzugriff'), vorher, 'belegt, rate und voll melden nichts');
end $$;
reset role;

-- 4b. Ohne Hinweis kein Start: darf wamo_core nicht in hinweise schreiben, scheitert der Start ganz (42501) und legt keine Sitzung an
revoke insert on __SCHEMA__.hinweise from wamo_core;
do $$ declare vorher bigint; begin
  select count(*) into vorher from __SCHEMA__.rd_sitzungen;
  set local role wamo_core;
  perform wamo_testhilfe.fehler($f$select * from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a2', 'ziel10', 3600, clock_timestamp(), 10, 60)$f$, '42501');
  reset role;
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.rd_sitzungen), vorher, 'ohne Recht auf hinweise entsteht keine Sitzung');
end $$;
reset role;
grant insert on __SCHEMA__.hinweise to wamo_core;

-- 5. Sitzung beenden bleibt wie in 0015 (Kurzprobe), Abgelaufene Sitzungen schließt der nächste Start
set role wamo_core;
do $$ declare s uuid; begin
  select id into s from __SCHEMA__.rd_sitzungen where ziel = 'sokrates1' and ende_at is null;
  perform wamo_testhilfe.gleich(__SCHEMA__.rd_sitzung_beenden(s, 'beendet', '00000000-0000-4000-8000-0000000000a2'), 'fremd', 'fremde Sitzung beenden');
  perform wamo_testhilfe.gleich(__SCHEMA__.rd_sitzung_beenden(s, 'beendet', '00000000-0000-4000-8000-0000000000a1'), 'ok', 'eigene Sitzung beenden');
  perform wamo_testhilfe.gleich(__SCHEMA__.rd_sitzung_beenden(s, 'beendet', '00000000-0000-4000-8000-0000000000a1'), 'schon', 'doppelt beenden');
end $$;
reset role;
do $$ declare s uuid; r record; begin
  alter table __SCHEMA__.rd_sitzungen disable trigger rd_sitzungen_guard;
  insert into __SCHEMA__.rd_sitzungen (profil_id, ziel, start_at, bis_at, aal2_at)
    values ('00000000-0000-4000-8000-0000000000a1', 'ziel9', now() - interval '3 hours', now() - interval '2 hours', now() - interval '3 hours')
    returning id into s;
  alter table __SCHEMA__.rd_sitzungen enable trigger rd_sitzungen_guard;
  set local role wamo_core;
  select * into r from __SCHEMA__.rd_sitzung_beginnen('00000000-0000-4000-8000-0000000000a1', 'ziel9', 3600, clock_timestamp() - interval '2 days', 10, 60);
  reset role;
  perform wamo_testhilfe.gleich(r.ergebnis, 'ok', 'ziel9 ist frei, weil die alte Sitzung abgelaufen war');
  perform wamo_testhilfe.gleich((select grund from __SCHEMA__.rd_sitzungen where id = s), 'zeitlimit', 'abgelaufene Sitzung mit Grund zeitlimit geschlossen');
end $$;

-- 6. Struktur: Signatur, Rechte, search_path, Stand der Funktion
do $$ begin
  perform wamo_testhilfe.gleich((select p.prosecdef from pg_proc p where p.oid = '__SCHEMA__.rd_sitzung_beginnen(uuid,text,integer,timestamptz,integer,integer)'::regprocedure), false, 'Funktion laeuft mit den Rechten des Aufrufers');
  perform wamo_testhilfe.gleich((select coalesce(bool_or(c like 'search_path=%'), false) from pg_proc p, unnest(p.proconfig) c
    where p.oid = '__SCHEMA__.rd_sitzung_beginnen(uuid,text,integer,timestamptz,integer,integer)'::regprocedure), true, 'search_path fest');
  perform wamo_testhilfe.gleich((select p.prosrc like '%120 seconds%' from pg_proc p where p.oid = '__SCHEMA__.rd_sitzung_beginnen(uuid,text,integer,timestamptz,integer,integer)'::regprocedure), false, 'keine Frische von 120 Sekunden mehr');
  perform wamo_testhilfe.gleich((select p.prosrc like '%schon für einen Start verwendet%' from pg_proc p where p.oid = '__SCHEMA__.rd_sitzung_beginnen(uuid,text,integer,timestamptz,integer,integer)'::regprocedure), false, 'kein Ein-Code-ein-Start mehr');
  perform wamo_testhilfe.gleich((select count(*) from pg_proc p where p.pronamespace = '__SCHEMA__'::regnamespace and p.proname = 'rd_sitzung_beginnen'), 1::bigint, 'genau eine Funktion dieses Namens (keine zweite Signatur)');
  perform wamo_testhilfe.gleich(has_function_privilege('wamo_core', '__SCHEMA__.rd_sitzung_beginnen(uuid,text,integer,timestamptz,integer,integer)', 'EXECUTE'), true, 'wamo_core darf ausfuehren');
  perform wamo_testhilfe.gleich(has_function_privilege('wamo_admin', '__SCHEMA__.rd_sitzung_beginnen(uuid,text,integer,timestamptz,integer,integer)', 'EXECUTE'), false, 'wamo_admin nicht');
  perform wamo_testhilfe.gleich(has_function_privilege('anon', '__SCHEMA__.rd_sitzung_beginnen(uuid,text,integer,timestamptz,integer,integer)', 'EXECUTE'), false, 'anon nicht');
  perform wamo_testhilfe.gleich(has_function_privilege('authenticated', '__SCHEMA__.rd_sitzung_beginnen(uuid,text,integer,timestamptz,integer,integer)', 'EXECUTE'), false, 'authenticated nicht');
  perform wamo_testhilfe.gleich(has_function_privilege('service_role', '__SCHEMA__.rd_sitzung_beginnen(uuid,text,integer,timestamptz,integer,integer)', 'EXECUTE'), false, 'service_role nicht');
  perform wamo_testhilfe.gleich(has_function_privilege('wamo_core', '__SCHEMA__.hinweis_an_admins(text,text,text,uuid,jsonb)', 'EXECUTE'), false, 'hinweis_an_admins bleibt fuer wamo_core gesperrt');
  perform wamo_testhilfe.gleich(has_table_privilege('wamo_core', '__SCHEMA__.rd_sitzungen', 'DELETE'), false, 'kein delete auf rd_sitzungen');
  perform wamo_testhilfe.gleich((select count(*) from pg_attribute where attrelid = '__SCHEMA__.rd_sitzungen'::regclass and attnum > 0 and not attisdropped), 8::bigint, 'acht Spalten, keine Inhalte');
  perform wamo_testhilfe.gleich((select count(*) from pg_policies where schemaname = '__SCHEMA__' and tablename = 'rd_sitzungen'), 1::bigint, 'eine Policy auf rd_sitzungen');
end $$;

select '0019 OK';
rollback;
