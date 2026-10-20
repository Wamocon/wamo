-- Test fuer 0002_wamo_geraete. Laeuft als postgres in EINER Transaktion, die am Ende immer zurueckgerollt wird
-- (Testkonten in auth.users, Profile, Geraete und Hinweise verschwinden wieder). Nur fuer wamo_test oder wamo_dev.
--   bash scripts/render-migration.sh wamo_test supabase/tests/0002_wamo_geraete_test.sql \
--     | ssh sokrates2 "docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 -q"
-- Erfolg: letzte Zeile "0002 OK", Exit-Code 0. Jeder Fehlschlag bricht mit einer Meldung ab.
\set ON_ERROR_STOP 1
begin;
do $$ begin
  if '__SCHEMA__' = 'wamo_prod' then raise exception 'Test nie in wamo_prod'; end if;
end $$;

-- postgres ist bei Supabase kein Superuser: Rollenwechsel nur ueber eine Mitgliedschaft (wird mit zurueckgerollt)
grant wamo_core, wamo_admin to postgres;
create schema wamo_testhilfe;
grant usage on schema wamo_testhilfe to wamo_core, wamo_admin;
-- erwartet einen Fehler mit genau diesem SQLSTATE (42501 = keine Rechte, 23514 = Pruefung, 23505 = eindeutig, ...)
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

-- Testkonten: zwei Admins, drei Mitglieder, ein inaktives Mitglied
insert into auth.users (id, email) values
  ('00000000-0000-4000-8000-0000000000a1', 'admin1@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000000a2', 'admin2@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000000b1', 'mitglied1@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000000b2', 'mitglied2@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000000b3', 'mitglied3@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000000b4', 'inaktiv@wamo-test.invalid');
insert into __SCHEMA__.profiles (user_id, display_name, rolle, aktiv) values
  ('00000000-0000-4000-8000-0000000000a1', 'Admin Eins', 'admin', true),
  ('00000000-0000-4000-8000-0000000000a2', 'Admin Zwei', 'admin', true),
  ('00000000-0000-4000-8000-0000000000b1', 'Anna', 'mitglied', true),
  ('00000000-0000-4000-8000-0000000000b2', 'Bernd', 'mitglied', true),
  ('00000000-0000-4000-8000-0000000000b3', 'Clara', 'mitglied', true),
  ('00000000-0000-4000-8000-0000000000b4', 'Dora', 'mitglied', false);

-- Wegwerfschluessel (nur oeffentliche Teile, Fingerabdruecke von ssh-keygen -l)
create temp table k (n int primary key, pub text, fp text);
insert into k values
  (1, 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIXzWuY9bt9yoPnQ/6MxsjlyjVT1qdY93du6PLlNt3D6', 'SHA256:vxe+7I/Wv6VHjKAOQpvJtkZDcHvINq3CgURh2MotlRg'),
  (2, 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE50ay97gzBDSQ+duRL7WDF1VC0I08U9H8q6CvFUwK6U', 'SHA256:BmBgNXdW7B5NcukfRB7DYajWW7BWUMU8gPAM2xl6nhc'),
  (3, 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE7Hvy6Ke+WhqfWeI9ypwRu7cwpcFCHyJMVT9M2Bj2Q4', 'SHA256:0dGl3Yc/sHpzRWWdxHeNTdVD7tt/pgpXk3C2t5jGOy0'),
  (4, 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILLfM5W/LniWF3heFFhtw/MHfcZUVhdGb9KRga2ItB+K', 'SHA256:nueEGbN5iBak9ymwzJL10GYmC2mBFEG3DEFAqoKtqxA'),
  (5, 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGF8OHN2EjoktKZR6PXhkyigEBFZKC+hwzEUXf+2YOsc', 'SHA256:etWFSEGdt0X7ya9IarVaKYPlXtEMTiFofoxS6g3EpHg'),
  (6, 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAsJJFq/PImdV0+/NKtyo8OqGCuZEV3BbdWtKof19auL', 'SHA256:k5u2i4fBhTunO0LceOpVNUWsiWwUUY6tb5inWZIPhvs');
grant select on k to wamo_core, wamo_admin;

-- 1. Fingerabdruck wie ssh-keygen
do $$ declare r record; begin
  for r in select * from k loop
    perform wamo_testhilfe.gleich(__SCHEMA__.ssh_fingerabdruck(r.pub), r.fp, 'Fingerabdruck ' || r.n);
  end loop;
end $$;

-- 2. Erste Bindung als wamo_core
set role wamo_core;
do $$ declare r record; begin
  -- Anna, neuer Schluessel, freier Rechnername: automatisch aktiv
  select * into r from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b1',
    (select pub from k where n = 1), 'WMC-L-01', 'Annas Laptop');
  perform wamo_testhilfe.gleich(r.ergebnis, 'neu_aktiv', 'Anna K1');
  perform wamo_testhilfe.gleich(r.status::text, 'aktiv', 'Anna K1 Status');
  perform wamo_testhilfe.gleich(r.kennung, 'wmc-l-01', 'Anna K1 Kennung');
  perform wamo_testhilfe.gleich(r.tunnel_slot, 1::smallint, 'Anna K1 Platz');
  -- derselbe Schluessel noch einmal: bekannt, gleiche Kennung
  select * into r from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b1',
    (select pub from k where n = 1), 'WMC-L-01', null);
  perform wamo_testhilfe.gleich(r.ergebnis, 'bekannt', 'Anna K1 erneut');
  perform wamo_testhilfe.gleich(r.kennung, 'wmc-l-01', 'Anna K1 erneut Kennung');
  -- derselbe Schluessel fuer Bernd: fremd, ohne Kennung
  select * into r from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b2',
    (select pub from k where n = 1), 'WMC-L-01', null);
  perform wamo_testhilfe.gleich(r.ergebnis, 'fremd', 'Bernd mit Annas Schluessel');
  perform wamo_testhilfe.gleich(r.kennung, null::text, 'fremd verraet keine Kennung');
  -- Anna mit zweitem Schluessel: beantragt (Geraetewechsel)
  select * into r from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b1',
    (select pub from k where n = 2), 'WMC-L-09', null);
  perform wamo_testhilfe.gleich(r.ergebnis, 'neu_beantragt', 'Anna K2');
  perform wamo_testhilfe.gleich(r.tunnel_slot, 2::smallint, 'Anna K2 Platz');
  -- Bernd mit Annas Rechnernamen (andere Schreibweise): beantragt
  select * into r from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b2',
    (select pub from k where n = 3), 'wmc-l-01', null);
  perform wamo_testhilfe.gleich(r.ergebnis, 'neu_beantragt', 'Bernd gleicher Rechnername');
  perform wamo_testhilfe.gleich(r.kennung, 'wmc-l-01-2', 'Bernd Kennung eindeutig');
  -- Clara mit eigenem Rechner, Kennung kollidiert nach Normalisierung: aktiv, Kennung mit Zaehler
  select * into r from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b3',
    (select pub from k where n = 4), 'WMC--L-01', null);
  perform wamo_testhilfe.gleich(r.ergebnis, 'neu_aktiv', 'Clara');
  perform wamo_testhilfe.gleich(r.kennung, 'wmc-l-01-3', 'Clara Kennung');
  -- Rechnername mit Ziffer vorn: Kennung beginnt mit Buchstaben
  select * into r from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000a1',
    (select pub from k where n = 5), '4711-PC', null);
  perform wamo_testhilfe.gleich(r.kennung, 'g-4711-pc', 'Kennung mit Ziffer vorn');
  perform wamo_testhilfe.gleich(r.ergebnis, 'neu_aktiv', 'Admin eigenes Geraet');
end $$;
-- ungueltige Eingaben, inaktives Profil
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b4',
  'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAsJJFq/PImdV0+/NKtyo8OqGCuZEV3BbdWtKof19auL', 'X1', null)$$, '42501');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b3',
  'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ', 'X1', null)$$, '22023');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b3',
  'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAsJJFq/PImdV0+/NKtyo8OqGCuZEV3BbdWtKof19auL', 'bad host;', null)$$, '22023');
-- Rechte von wamo_core
select wamo_testhilfe.fehler($$insert into __SCHEMA__.devices (name, art, klasse, zugang, kennung)
  values ('x', 'gemeinsam', 'drucker', 'ipp', 'drucker-x')$$, '42501');
select wamo_testhilfe.fehler($$update __SCHEMA__.devices set status = 'gesperrt'$$, '42501');
select wamo_testhilfe.fehler($$update __SCHEMA__.devices set user_id = '00000000-0000-4000-8000-0000000000b2'$$, '42501');
select wamo_testhilfe.fehler($$delete from __SCHEMA__.devices$$, '42501');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.device_events$$, '42501');
select wamo_testhilfe.fehler($$select __SCHEMA__.hinweis_an_admins('system', 'x', 'x', null, '{}')$$, '42501');
select wamo_testhilfe.fehler($$update __SCHEMA__.hinweise set titel = 'x'$$, '42501');
select wamo_testhilfe.fehler($$delete from __SCHEMA__.hinweise$$, '42501');
update __SCHEMA__.devices set last_seen_at = now() where kennung = 'wmc-l-01';
update __SCHEMA__.hinweise set gelesen_at = now() where empfaenger = '00000000-0000-4000-8000-0000000000a1';
reset role;

-- 3. Hinweise: je Bindung einer an jeden aktiven Admin, Konflikt einmal
do $$ begin
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.hinweise where art = 'geraet_gebunden'), 6::bigint,
    'Hinweise geraet_gebunden (3 Bindungen x 2 Admins)');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.hinweise where art = 'geraet_beantragt'), 4::bigint,
    'Hinweise geraet_beantragt');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.hinweise where art = 'geraet_konflikt'), 2::bigint,
    'Hinweise geraet_konflikt');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.device_events), 5::bigint, 'Verlauf nach Bindungen');
  perform wamo_testhilfe.gleich((select bindung::text from __SCHEMA__.devices where kennung = 'wmc-l-01'), 'automatisch',
    'Bindung automatisch');
  perform wamo_testhilfe.gleich((select ssh_fingerprint from __SCHEMA__.devices where kennung = 'wmc-l-01'),
    (select fp from k where n = 1), 'Fingerabdruck berechnet');
end $$;

-- 4. Verwaltung als wamo_admin
set role wamo_admin;
-- bindung ist nicht schreibbar, Fingerabdruck auch nicht
select wamo_testhilfe.fehler($$insert into __SCHEMA__.devices (name, art, klasse, zugang, kennung, bindung)
  values ('x', 'gemeinsam', 'drucker', 'ipp', 'drucker-x', 'automatisch')$$, '42501');
select wamo_testhilfe.fehler($$update __SCHEMA__.devices set kennung = 'neu' where kennung = 'wmc-l-01'$$, '42501');
-- gemeinsames Geraet: aktiv nur mit Freigeber, ohne Schluessel
select wamo_testhilfe.fehler($$insert into __SCHEMA__.devices (name, art, klasse, zugang, kennung, status)
  values ('Drucker', 'gemeinsam', 'drucker', 'ipp', 'drucker', 'aktiv')$$, '42501');
select wamo_testhilfe.fehler($$insert into __SCHEMA__.devices (name, art, klasse, zugang, kennung, status, freigegeben_von)
  values ('Drucker', 'gemeinsam', 'drucker', 'ipp', 'drucker', 'aktiv', '00000000-0000-4000-8000-0000000000b1')$$, '42501');
select wamo_testhilfe.fehler($$insert into __SCHEMA__.devices (name, art, klasse, zugang, kennung, ssh_pubkey)
  values ('Drucker', 'gemeinsam', 'drucker', 'ipp', 'drucker', (select pub from k where n = 6))$$, '23514');
insert into __SCHEMA__.devices (name, art, klasse, zugang, kennung, status, freigegeben_von)
  values ('Drucker', 'gemeinsam', 'drucker', 'ipp', 'drucker', 'aktiv', '00000000-0000-4000-8000-0000000000a1');
-- Statusautomat
select wamo_testhilfe.fehler($$update __SCHEMA__.devices set status = 'beantragt',
  geaendert_von = '00000000-0000-4000-8000-0000000000a1' where kennung = 'wmc-l-01'$$, '23514');
select wamo_testhilfe.fehler($$update __SCHEMA__.devices set status = 'gesperrt' where kennung = 'wmc-l-01'$$, '42501');
select wamo_testhilfe.fehler($$update __SCHEMA__.devices set status = 'gesperrt',
  geaendert_von = '00000000-0000-4000-8000-0000000000b1' where kennung = 'wmc-l-01'$$, '42501');
update __SCHEMA__.devices set status = 'gesperrt', geaendert_von = '00000000-0000-4000-8000-0000000000a1',
  gesperrt_grund = 'Test' where kennung = 'wmc-l-01';
-- entsperren braucht einen Freigeber
select wamo_testhilfe.fehler($$update __SCHEMA__.devices set status = 'aktiv',
  geaendert_von = '00000000-0000-4000-8000-0000000000a1' where kennung = 'wmc-l-01'$$, '42501');
update __SCHEMA__.devices set status = 'aktiv', geaendert_von = '00000000-0000-4000-8000-0000000000a1',
  freigegeben_von = '00000000-0000-4000-8000-0000000000a1' where kennung = 'wmc-l-01';
-- zweites aktives Geraet fuer Anna scheitert, nach dem Widerruf des ersten geht es
select wamo_testhilfe.fehler($$update __SCHEMA__.devices set status = 'aktiv',
  geaendert_von = '00000000-0000-4000-8000-0000000000a1', freigegeben_von = '00000000-0000-4000-8000-0000000000a1'
  where kennung = 'wmc-l-09'$$, '23505');
update __SCHEMA__.devices set status = 'widerrufen', geaendert_von = '00000000-0000-4000-8000-0000000000a2',
  gesperrt_grund = 'Geraetewechsel' where kennung = 'wmc-l-01';
update __SCHEMA__.devices set status = 'aktiv', geaendert_von = '00000000-0000-4000-8000-0000000000a1',
  freigegeben_von = '00000000-0000-4000-8000-0000000000a1' where kennung = 'wmc-l-09';
-- widerrufen ist endgueltig
select wamo_testhilfe.fehler($$update __SCHEMA__.devices set status = 'aktiv',
  geaendert_von = '00000000-0000-4000-8000-0000000000a1', freigegeben_von = '00000000-0000-4000-8000-0000000000a1'
  where kennung = 'wmc-l-01'$$, '42501');
select wamo_testhilfe.fehler($$update __SCHEMA__.devices set name = 'neu' where kennung = 'wmc-l-01'$$, '42501');
select wamo_testhilfe.fehler($$update __SCHEMA__.devices set freigegeben_von = '00000000-0000-4000-8000-0000000000a2'
  where kennung = 'wmc-l-09'$$, '42501');
reset role;

-- 5. Nach dem Widerruf: Platz frei, Schluessel und Kennung nie wieder
set role wamo_core;
do $$ declare r record; begin
  select * into r from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b1',
    (select pub from k where n = 1), 'WMC-L-01', null);
  perform wamo_testhilfe.gleich(r.ergebnis, 'bekannt', 'widerrufener Schluessel bleibt bekannt');
  perform wamo_testhilfe.gleich(r.status::text, 'widerrufen', 'widerrufener Schluessel Status');
end $$;
reset role;
do $$ begin
  perform wamo_testhilfe.gleich((select min(s)::smallint from generate_series(1, 64) s where not exists
    (select 1 from __SCHEMA__.devices d where d.tunnel_slot = s and d.status <> 'widerrufen')), 1::smallint, 'Platz 1 frei');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.hinweise where empfaenger = '00000000-0000-4000-8000-0000000000b1'
    and art in ('geraet_gesperrt','geraet_entsperrt','geraet_widerrufen','geraet_gebunden')), 4::bigint,
    'Hinweise an Anna (gesperrt, entsperrt, widerrufen, freigegeben)');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.device_events d join __SCHEMA__.devices v on v.id = d.device_id
    where v.kennung = 'wmc-l-01'), 4::bigint, 'Verlauf wmc-l-01');
end $$;

-- 6. Konten loeschen: mit aktivem Geraet nicht, mit nur widerrufenem schon (Besitzer wird null)
select wamo_testhilfe.fehler($$delete from auth.users where id = '00000000-0000-4000-8000-0000000000b3'$$, '42501');
update __SCHEMA__.devices set status = 'widerrufen', geaendert_von = '00000000-0000-4000-8000-0000000000a1'
  where kennung = 'wmc-l-01-2';
-- Bernd hat jetzt nur noch ein widerrufenes Geraet
delete from auth.users where id = '00000000-0000-4000-8000-0000000000b2';
do $$ begin
  perform wamo_testhilfe.gleich((select user_id from __SCHEMA__.devices where kennung = 'wmc-l-01-2'), null::uuid,
    'Besitzer nach Kontoloeschung');
end $$;
-- ein Admin, der Statuswechsel ausgeloest hat, laesst sich loeschen (geaendert_von wird null)
delete from auth.users where id = '00000000-0000-4000-8000-0000000000a2';

select '0002 OK';
rollback;
