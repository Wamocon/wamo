-- Test fuer 0002_wamo_geraete. Laeuft als postgres in EINER Transaktion, die am Ende immer zurueckgerollt wird
-- (Testkonten in auth.users, Profile, Geraete und Hinweise verschwinden wieder). Nur fuer wamo_test oder wamo_dev;
-- die Zaehlungen betrachten nur die Testkonten, vorhandene Geraete stoeren nicht.
--   sed "s/__SCHEMA__/wamo_test/g" supabase/tests/0002_wamo_geraete_test.sql \
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

-- Testkonten: zwei Admins, vier Mitglieder, ein inaktives Mitglied
insert into auth.users (id, email) values
  ('00000000-0000-4000-8000-0000000000a1', 'admin1@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000000a2', 'admin2@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000000b1', 'mitglied1@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000000b2', 'mitglied2@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000000b3', 'mitglied3@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000000b4', 'inaktiv@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000000b5', 'mitglied5@wamo-test.invalid');
insert into __SCHEMA__.profiles (user_id, display_name, rolle, aktiv) values
  ('00000000-0000-4000-8000-0000000000a1', 'Admin Eins', 'admin', true),
  ('00000000-0000-4000-8000-0000000000a2', 'Admin Zwei', 'admin', true),
  ('00000000-0000-4000-8000-0000000000b1', 'Anna', 'mitglied', true),
  ('00000000-0000-4000-8000-0000000000b2', 'Bernd', 'mitglied', true),
  ('00000000-0000-4000-8000-0000000000b3', 'Clara', 'mitglied', true),
  ('00000000-0000-4000-8000-0000000000b4', 'Dora', 'mitglied', false),
  ('00000000-0000-4000-8000-0000000000b5', 'Emil', 'mitglied', true);

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
-- Ergebnisse zum Vergleichen (sie leben bis zum Ende der Transaktion)
create temp table erg (name text primary key, slot smallint, kennung text);
grant select, insert on erg to wamo_core;
-- kleinster nie benutzter Platz vor dem Test (in einer leeren Datenbank 1)
create temp table basis as
  select (select min(s) from generate_series(1, 64) s
          where not exists (select 1 from __SCHEMA__.devices d where d.tunnel_slot = s))::smallint as frei;
grant select on basis to wamo_core;

-- 1. Fingerabdruck wie ssh-keygen
do $$ declare r record; begin
  for r in select * from k loop
    perform wamo_testhilfe.gleich(__SCHEMA__.ssh_fingerabdruck(r.pub), r.fp, 'Fingerabdruck ' || r.n);
  end loop;
end $$;

-- 2. Erste Bindung als wamo_core
set role wamo_core;
do $$ declare r record; begin
  -- Anna (Mitglied), neuer Schluessel, freier Rechnername: automatisch aktiv
  select * into r from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b1',
    (select pub from k where n = 1), 'WMC-L-01', 'Annas Laptop');
  perform wamo_testhilfe.gleich(r.ergebnis, 'neu_aktiv', 'Anna K1');
  perform wamo_testhilfe.gleich(r.status::text, 'aktiv', 'Anna K1 Status');
  perform wamo_testhilfe.gleich(r.kennung, 'pc-wmc-l-01', 'Anna K1 Kennung');
  perform wamo_testhilfe.gleich(r.tunnel_slot, (select frei from basis), 'Anna K1 Platz');
  insert into erg values ('anna', r.tunnel_slot, r.kennung);
  -- derselbe Schluessel noch einmal: bekannt, gleiche Kennung und Platz
  select * into r from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b1',
    (select pub from k where n = 1), 'WMC-L-01', null);
  perform wamo_testhilfe.gleich(r.ergebnis, 'bekannt', 'Anna K1 erneut');
  perform wamo_testhilfe.gleich(r.kennung, 'pc-wmc-l-01', 'Anna K1 erneut Kennung');
  -- derselbe Schluessel fuer Bernd: fremd, ohne Kennung
  select * into r from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b2',
    (select pub from k where n = 1), 'WMC-L-01', null);
  perform wamo_testhilfe.gleich(r.ergebnis, 'fremd', 'Bernd mit Annas Schluessel');
  perform wamo_testhilfe.gleich(r.kennung, null::text, 'fremd verraet keine Kennung');
  -- Anna mit zweitem Schluessel: beantragt (Geraetewechsel), ohne Kennung und Platz
  select * into r from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b1',
    (select pub from k where n = 2), 'WMC-L-09', null);
  perform wamo_testhilfe.gleich(r.ergebnis, 'neu_beantragt', 'Anna K2');
  perform wamo_testhilfe.gleich(r.status::text, 'beantragt', 'Anna K2 Status');
  perform wamo_testhilfe.gleich(r.kennung, null::text, 'beantragt gibt keine Kennung heraus');
  perform wamo_testhilfe.gleich(r.tunnel_slot, null::smallint, 'beantragt gibt keinen Platz heraus');
  -- bekannt, aber noch nicht aktiv: ebenfalls keine Kennung
  select * into r from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b1',
    (select pub from k where n = 2), 'WMC-L-09', null);
  perform wamo_testhilfe.gleich(r.ergebnis, 'bekannt', 'Anna K2 erneut');
  perform wamo_testhilfe.gleich(r.status::text, 'beantragt', 'Anna K2 erneut Status');
  perform wamo_testhilfe.gleich(r.kennung, null::text, 'bekannt beantragt: keine Kennung');
  -- Anna mit drittem Schluessel waehrend ein Antrag offen ist: zu_viele
  select * into r from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b1',
    (select pub from k where n = 6), 'WMC-L-10', null);
  perform wamo_testhilfe.gleich(r.ergebnis, 'zu_viele', 'Anna K6 bei offenem Antrag');
  -- Bernd mit Annas Rechnernamen (andere Schreibweise): beantragt, Kennung eindeutig vergeben
  select * into r from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b2',
    (select pub from k where n = 3), 'wmc-l-01', null);
  perform wamo_testhilfe.gleich(r.ergebnis, 'neu_beantragt', 'Bernd gleicher Rechnername');
  -- Clara mit eigenem Rechner, Kennung kollidiert nach Normalisierung: aktiv, Kennung mit Zaehler
  select * into r from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b3',
    (select pub from k where n = 4), 'WMC--L-01', null);
  perform wamo_testhilfe.gleich(r.ergebnis, 'neu_aktiv', 'Clara');
  perform wamo_testhilfe.gleich(r.kennung, 'pc-wmc-l-01-3', 'Clara Kennung');
  insert into erg values ('clara', r.tunnel_slot, r.kennung);
  -- Admin-Profile werden nie automatisch aktiv
  select * into r from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000a1',
    (select pub from k where n = 5), '4711-PC', null);
  perform wamo_testhilfe.gleich(r.ergebnis, 'neu_beantragt', 'Admin eigenes Geraet');
end $$;
-- ungueltige Eingaben, inaktives Profil
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b4',
  'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAsJJFq/PImdV0+/NKtyo8OqGCuZEV3BbdWtKof19auL', 'X1', null)$$, '42501');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b3',
  'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ', 'X1', null)$$, '22023');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b3',
  'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAsJJFq/PImdV0+/NKtyo8OqGCuZEV3BbdWtKof19auL', 'bad host;', null)$$, '22023');
-- Der 26. Zeichen darf nur A bis P sein (sonst kein gueltiger ed25519-Schluessel)
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b3',
  'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIZsJJFq/PImdV0+/NKtyo8OqGCuZEV3BbdWtKof19auL', 'X1', null)$$, '22023');
-- Rechte von wamo_core
select wamo_testhilfe.fehler($$insert into __SCHEMA__.devices (name, art, klasse, zugang, kennung)
  values ('x', 'gemeinsam', 'drucker', 'ipp', 'drucker-x')$$, '42501');
select wamo_testhilfe.fehler($$update __SCHEMA__.devices set status = 'gesperrt'$$, '42501');
select wamo_testhilfe.fehler($$update __SCHEMA__.devices set user_id = '00000000-0000-4000-8000-0000000000b2'$$, '42501');
select wamo_testhilfe.fehler($$update __SCHEMA__.devices set last_seen_at = now()$$, '42501');
select wamo_testhilfe.fehler($$delete from __SCHEMA__.devices$$, '42501');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.device_events$$, '42501');
select wamo_testhilfe.fehler($$select __SCHEMA__.hinweis_an_admins('system', 'x', 'x', null, '{}')$$, '42501');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.pc_abgleich_liste()$$, '42501');
select wamo_testhilfe.fehler($$update __SCHEMA__.hinweise set gelesen_at = now()$$, '42501');
select wamo_testhilfe.fehler($$delete from __SCHEMA__.hinweise$$, '42501');
-- Hinweise: der Kern darf nur scan_fertig und system anlegen, nicht Karten fuer Admins faelschen
select wamo_testhilfe.fehler($$insert into __SCHEMA__.hinweise (empfaenger, art, titel)
  values ('00000000-0000-4000-8000-0000000000a1', 'geraet_gebunden', 'gefaelscht')$$, '42501');
insert into __SCHEMA__.hinweise (empfaenger, art, titel)
  values ('00000000-0000-4000-8000-0000000000b1', 'scan_fertig', 'Scan fertig');
-- gesehen nur ueber die Funktion und nur fuer aktive Geraete
select __SCHEMA__.geraet_gesehen('pc-wmc-l-01');
select __SCHEMA__.geraet_gesehen('pc-wmc-l-09');
-- Quittieren nur fuer den Empfaenger
do $$ declare h uuid; begin
  select id into h from __SCHEMA__.hinweise where empfaenger = '00000000-0000-4000-8000-0000000000b1' and art = 'scan_fertig';
  perform wamo_testhilfe.gleich(__SCHEMA__.hinweis_quittieren('00000000-0000-4000-8000-0000000000b2', h), false, 'fremder Empfaenger');
  perform wamo_testhilfe.gleich(__SCHEMA__.hinweis_quittieren('00000000-0000-4000-8000-0000000000b1', h), true, 'eigener Hinweis');
  perform wamo_testhilfe.gleich(__SCHEMA__.hinweis_quittieren('00000000-0000-4000-8000-0000000000b1', h), false, 'zweites Mal');
end $$;
reset role;

do $$ begin
  perform wamo_testhilfe.gleich((select last_seen_at is not null from __SCHEMA__.devices where kennung = 'pc-wmc-l-01'),
    true, 'gesehen gesetzt');
  perform wamo_testhilfe.gleich((select last_seen_at is null from __SCHEMA__.devices where kennung = 'pc-wmc-l-09'),
    true, 'beantragtes Geraet wird nicht gesehen');
end $$;

-- 3. Hinweise an die Testadmins: je Bindung einer an jeden aktiven Admin, Konflikt einmal
do $$ begin
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.hinweise where art = 'geraet_gebunden'
    and empfaenger in ('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000000a2')), 4::bigint,
    'Hinweise geraet_gebunden (2 Bindungen x 2 Admins)');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.hinweise where art = 'geraet_beantragt'
    and empfaenger in ('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000000a2')), 6::bigint,
    'Hinweise geraet_beantragt (3 Antraege x 2 Admins)');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.hinweise where art = 'geraet_konflikt'
    and empfaenger in ('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000000a2')), 2::bigint,
    'Hinweise geraet_konflikt');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.device_events e join __SCHEMA__.devices d on d.id = e.device_id
    where d.user_id in ('00000000-0000-4000-8000-0000000000a1', '00000000-0000-4000-8000-0000000000b1',
                        '00000000-0000-4000-8000-0000000000b2', '00000000-0000-4000-8000-0000000000b3')),
    5::bigint, 'Verlauf nach Bindungen');
  perform wamo_testhilfe.gleich((select bindung::text from __SCHEMA__.devices where kennung = 'pc-wmc-l-01'), 'automatisch',
    'Bindung automatisch');
  perform wamo_testhilfe.gleich((select ssh_fingerprint from __SCHEMA__.devices where kennung = 'pc-wmc-l-01'),
    (select fp from k where n = 1), 'Fingerabdruck berechnet');
  -- Abgleichsliste: nur aktive Geraete aktiver Profile
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.pc_abgleich_liste()
    where kennung in ('pc-wmc-l-01', 'pc-wmc-l-09', 'pc-wmc-l-01-3')), 2::bigint, 'Abgleichsliste');
end $$;

-- 4. Verwaltung als wamo_admin
set role wamo_admin;
-- bindung ist nicht schreibbar, Kennung auch nicht aenderbar
select wamo_testhilfe.fehler($$insert into __SCHEMA__.devices (name, art, klasse, zugang, kennung, bindung)
  values ('x', 'gemeinsam', 'drucker', 'ipp', 'drucker-x', 'automatisch')$$, '42501');
select wamo_testhilfe.fehler($$update __SCHEMA__.devices set kennung = 'neu' where kennung = 'pc-wmc-l-01'$$, '42501');
-- gemeinsames Geraet: aktiv nur mit Freigeber, ohne Schluessel, Kennung nie mit Praefix pc-
select wamo_testhilfe.fehler($$insert into __SCHEMA__.devices (name, art, klasse, zugang, kennung, status)
  values ('Drucker', 'gemeinsam', 'drucker', 'ipp', 'drucker-t', 'aktiv')$$, '42501');
select wamo_testhilfe.fehler($$insert into __SCHEMA__.devices (name, art, klasse, zugang, kennung, status, freigegeben_von)
  values ('Drucker', 'gemeinsam', 'drucker', 'ipp', 'drucker-t', 'aktiv', '00000000-0000-4000-8000-0000000000b1')$$, '42501');
select wamo_testhilfe.fehler($$insert into __SCHEMA__.devices (name, art, klasse, zugang, kennung, ssh_pubkey)
  values ('Drucker', 'gemeinsam', 'drucker', 'ipp', 'drucker-t', (select pub from k where n = 6))$$, '23514');
select wamo_testhilfe.fehler($$insert into __SCHEMA__.devices (name, art, klasse, zugang, kennung, status, freigegeben_von)
  values ('Drucker', 'gemeinsam', 'drucker', 'ipp', 'pc-drucker', 'aktiv', '00000000-0000-4000-8000-0000000000a1')$$, '23514');
insert into __SCHEMA__.devices (name, art, klasse, zugang, kennung, status, freigegeben_von)
  values ('Drucker', 'gemeinsam', 'drucker', 'ipp', 'drucker-t', 'aktiv', '00000000-0000-4000-8000-0000000000a1');
-- Statusautomat
select wamo_testhilfe.fehler($$update __SCHEMA__.devices set status = 'beantragt',
  geaendert_von = '00000000-0000-4000-8000-0000000000a1' where kennung = 'pc-wmc-l-01'$$, '23514');
select wamo_testhilfe.fehler($$update __SCHEMA__.devices set status = 'gesperrt' where kennung = 'pc-wmc-l-01'$$, '42501');
select wamo_testhilfe.fehler($$update __SCHEMA__.devices set status = 'gesperrt',
  geaendert_von = '00000000-0000-4000-8000-0000000000b1' where kennung = 'pc-wmc-l-01'$$, '42501');
update __SCHEMA__.devices set status = 'gesperrt', geaendert_von = '00000000-0000-4000-8000-0000000000a1',
  gesperrt_grund = 'Test' where kennung = 'pc-wmc-l-01';
-- entsperren braucht einen Freigeber
select wamo_testhilfe.fehler($$update __SCHEMA__.devices set status = 'aktiv',
  geaendert_von = '00000000-0000-4000-8000-0000000000a1' where kennung = 'pc-wmc-l-01'$$, '42501');
update __SCHEMA__.devices set status = 'aktiv', geaendert_von = '00000000-0000-4000-8000-0000000000a1',
  freigegeben_von = '00000000-0000-4000-8000-0000000000a1' where kennung = 'pc-wmc-l-01';
-- zweites aktives Geraet fuer Anna scheitert, nach dem Widerruf des ersten geht es
select wamo_testhilfe.fehler($$update __SCHEMA__.devices set status = 'aktiv',
  geaendert_von = '00000000-0000-4000-8000-0000000000a1', freigegeben_von = '00000000-0000-4000-8000-0000000000a1'
  where kennung = 'pc-wmc-l-09'$$, '23505');
update __SCHEMA__.devices set status = 'widerrufen', geaendert_von = '00000000-0000-4000-8000-0000000000a2',
  gesperrt_grund = 'Geraetewechsel' where kennung = 'pc-wmc-l-01';
update __SCHEMA__.devices set status = 'aktiv', geaendert_von = '00000000-0000-4000-8000-0000000000a1',
  freigegeben_von = '00000000-0000-4000-8000-0000000000a1' where kennung = 'pc-wmc-l-09';
-- widerrufen ist endgueltig
select wamo_testhilfe.fehler($$update __SCHEMA__.devices set status = 'aktiv',
  geaendert_von = '00000000-0000-4000-8000-0000000000a1', freigegeben_von = '00000000-0000-4000-8000-0000000000a1'
  where kennung = 'pc-wmc-l-01'$$, '42501');
select wamo_testhilfe.fehler($$update __SCHEMA__.devices set name = 'neu' where kennung = 'pc-wmc-l-01'$$, '42501');
select wamo_testhilfe.fehler($$update __SCHEMA__.devices set freigegeben_von = '00000000-0000-4000-8000-0000000000a2'
  where kennung = 'pc-wmc-l-09'$$, '42501');
reset role;

-- 5. Nach dem Widerruf: der Platz bleibt eine Stunde gesperrt, der Schluessel bleibt bekannt
set role wamo_core;
do $$ declare r record; begin
  select * into r from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b1',
    (select pub from k where n = 1), 'WMC-L-01', null);
  perform wamo_testhilfe.gleich(r.ergebnis, 'bekannt', 'widerrufener Schluessel bleibt bekannt');
  perform wamo_testhilfe.gleich(r.status::text, 'widerrufen', 'widerrufener Schluessel Status');
  perform wamo_testhilfe.gleich(r.kennung, null::text, 'widerrufen gibt keine Kennung heraus');
  perform wamo_testhilfe.gleich(r.tunnel_slot, null::smallint, 'widerrufen gibt keinen Platz heraus');
  -- Emil (Mitglied, nie ein Geraet): bekommt nicht den Platz des gerade widerrufenen Geraets
  select * into r from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b5',
    (select pub from k where n = 6), 'EMIL-PC', null);
  perform wamo_testhilfe.gleich(r.ergebnis, 'neu_aktiv', 'Emil');
  perform wamo_testhilfe.gleich(r.tunnel_slot is distinct from (select slot from erg where name = 'anna'), true,
    'Platz von Annas widerrufenem Geraet bleibt gesperrt');
  perform wamo_testhilfe.gleich(r.kennung, 'pc-emil-pc', 'Emil Kennung');
end $$;
reset role;
do $$ begin
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.hinweise where empfaenger = '00000000-0000-4000-8000-0000000000b1'
    and art in ('geraet_gesperrt','geraet_entsperrt','geraet_widerrufen','geraet_gebunden')), 4::bigint,
    'Hinweise an Anna (gesperrt, entsperrt, widerrufen, freigegeben)');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.device_events d join __SCHEMA__.devices v on v.id = d.device_id
    where v.kennung = 'pc-wmc-l-01'), 4::bigint, 'Verlauf pc-wmc-l-01');
end $$;

-- 6. Profil deaktivieren sperrt dessen Geraete selbst (ohne Admin), Reaktivieren entsperrt nicht
set role wamo_admin;
update __SCHEMA__.profiles set aktiv = false where user_id = '00000000-0000-4000-8000-0000000000b5';
reset role;
do $$ begin
  perform wamo_testhilfe.gleich((select status::text from __SCHEMA__.devices where kennung = 'pc-emil-pc'), 'gesperrt',
    'Gerät nach Deaktivierung gesperrt');
  perform wamo_testhilfe.gleich((select gesperrt_grund from __SCHEMA__.devices where kennung = 'pc-emil-pc'),
    'Profil deaktiviert', 'Sperrgrund');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.pc_abgleich_liste() where kennung = 'pc-emil-pc'),
    0::bigint, 'gesperrtes Gerät nicht in der Abgleichsliste');
end $$;
update __SCHEMA__.profiles set aktiv = true where user_id = '00000000-0000-4000-8000-0000000000b5';
do $$ begin
  perform wamo_testhilfe.gleich((select status::text from __SCHEMA__.devices where kennung = 'pc-emil-pc'), 'gesperrt',
    'Reaktivieren entsperrt nicht');
end $$;

-- 7. Konten loeschen: mit aktivem Geraet nicht (eigene Meldung), mit nur widerrufenem schon (Besitzer wird null)
select wamo_testhilfe.fehler($$delete from auth.users where id = '00000000-0000-4000-8000-0000000000b3'$$, '23503');
update __SCHEMA__.devices set status = 'widerrufen', geaendert_von = '00000000-0000-4000-8000-0000000000a1'
  where kennung = 'pc-wmc-l-01-2';
-- Bernd hat jetzt nur noch ein widerrufenes Geraet
delete from auth.users where id = '00000000-0000-4000-8000-0000000000b2';
do $$ begin
  perform wamo_testhilfe.gleich((select user_id from __SCHEMA__.devices where kennung = 'pc-wmc-l-01-2'), null::uuid,
    'Besitzer nach Kontoloeschung');
end $$;
-- ein Admin, der Statuswechsel ausgeloest hat, laesst sich loeschen (geaendert_von wird null)
delete from auth.users where id = '00000000-0000-4000-8000-0000000000a2';

select '0002 OK';
rollback;
