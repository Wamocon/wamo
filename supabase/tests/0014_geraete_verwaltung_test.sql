-- Test fuer 0014_geraete_verwaltung. Laeuft als postgres in EINER Transaktion, die am Ende immer zurueckgerollt wird
-- (Testkonten in auth.users, Profile, Geraete und Hinweise verschwinden wieder). Nur fuer wamo_test oder wamo_dev.
--   sed "s/__SCHEMA__/wamo_test/g" supabase/tests/0014_geraete_verwaltung_test.sql \
--     | ssh wamo-sokrates2 "docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 -q"
-- Voraussetzung: 0002 und 0014 sind im Schema angewendet.
-- Erfolg: letzte Zeile "0014 OK", Exit-Code 0. Jeder Fehlschlag bricht mit einer Meldung ab.
\set ON_ERROR_STOP 1
begin;
do $$ begin
  if '__SCHEMA__' = 'wamo_prod' then raise exception 'Test nie in wamo_prod'; end if;
end $$;

grant wamo_core, wamo_admin to postgres;
create schema wamo_testhilfe;
grant usage on schema wamo_testhilfe to wamo_core, wamo_admin;
-- erwartet einen Fehler mit genau diesem SQLSTATE (42501 = keine Rechte, 23514 = Pruefung, 22023 = ungueltig, ...)
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

-- Testkonten: zwei Admins, drei Mitglieder
insert into auth.users (id, email) values
  ('00000000-0000-4000-8000-0000000000a1', 'admin1@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000000a2', 'admin2@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000000b1', 'mitglied1@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000000b2', 'mitglied2@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000000b4', 'inaktiv@wamo-test.invalid');
insert into __SCHEMA__.profiles (user_id, display_name, rolle, aktiv) values
  ('00000000-0000-4000-8000-0000000000a1', 'Admin Eins', 'admin', true),
  ('00000000-0000-4000-8000-0000000000a2', 'Admin Zwei', 'admin', true),
  ('00000000-0000-4000-8000-0000000000b1', 'Anna', 'mitglied', true),
  ('00000000-0000-4000-8000-0000000000b2', 'Bernd', 'mitglied', true),
  ('00000000-0000-4000-8000-0000000000b4', 'Dora', 'mitglied', false);

create temp table k (n int primary key, pub text, fp text);
insert into k values
  (1, 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIXzWuY9bt9yoPnQ/6MxsjlyjVT1qdY93du6PLlNt3D6', 'SHA256:vxe+7I/Wv6VHjKAOQpvJtkZDcHvINq3CgURh2MotlRg'),
  (2, 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE50ay97gzBDSQ+duRL7WDF1VC0I08U9H8q6CvFUwK6U', 'SHA256:BmBgNXdW7B5NcukfRB7DYajWW7BWUMU8gPAM2xl6nhc'),
  (3, 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE7Hvy6Ke+WhqfWeI9ypwRu7cwpcFCHyJMVT9M2Bj2Q4', 'SHA256:0dGl3Yc/sHpzRWWdxHeNTdVD7tt/pgpXk3C2t5jGOy0'),
  (4, 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILLfM5W/LniWF3heFFhtw/MHfcZUVhdGb9KRga2ItB+K', 'SHA256:nueEGbN5iBak9ymwzJL10GYmC2mBFEG3DEFAqoKtqxA'),
  (5, 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGF8OHN2EjoktKZR6PXhkyigEBFZKC+hwzEUXf+2YOsc', 'SHA256:etWFSEGdt0X7ya9IarVaKYPlXtEMTiFofoxS6g3EpHg'),
  (6, 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAsJJFq/PImdV0+/NKtyo8OqGCuZEV3BbdWtKof19auL', 'SHA256:k5u2i4fBhTunO0LceOpVNUWsiWwUUY6tb5inWZIPhvs');
grant select on k to wamo_core, wamo_admin;

-- 1. Vorbereitung ueber die echte Bindung (als wamo_core): Admin1 beantragt, Anna ist aktiv und beantragt einen Wechsel,
--    Bernd ist aktiv und beantragt ein zweites Geraet
set role wamo_core;
do $$ declare r record; begin
  select * into r from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000a1', (select pub from k where n = 1), 'ADMIN1-PC', 'Admin Laptop');
  perform wamo_testhilfe.gleich(r.ergebnis, 'neu_beantragt', 'Admin1 beantragt');
  select * into r from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b1', (select pub from k where n = 2), 'ANNA-PC', 'Annas Laptop');
  perform wamo_testhilfe.gleich(r.ergebnis, 'neu_aktiv', 'Anna aktiv');
  select * into r from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b1', (select pub from k where n = 3), 'ANNA-NEU', 'Annas neuer Laptop');
  perform wamo_testhilfe.gleich(r.ergebnis, 'neu_beantragt', 'Anna Wechsel beantragt');
  select * into r from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b2', (select pub from k where n = 4), 'BERND-PC', 'Bernds Laptop');
  perform wamo_testhilfe.gleich(r.ergebnis, 'neu_aktiv', 'Bernd aktiv');
  select * into r from __SCHEMA__.geraet_binden('00000000-0000-4000-8000-0000000000b2', (select pub from k where n = 5), 'BERND-ZWEI', null);
  perform wamo_testhilfe.gleich(r.ergebnis, 'neu_beantragt', 'Bernd zweites Geraet beantragt');
end $$;

-- 2. Rechte von wamo_core: kein direktes Schreiben, die Verlaufstabelle bleibt unlesbar
select wamo_testhilfe.fehler($$update __SCHEMA__.devices set status = 'aktiv'$$, '42501');
select wamo_testhilfe.fehler($$update __SCHEMA__.devices set status = 'gesperrt', geaendert_von = '00000000-0000-4000-8000-0000000000a1'$$, '42501');
select wamo_testhilfe.fehler($$update __SCHEMA__.devices set freigegeben_von = '00000000-0000-4000-8000-0000000000a1'$$, '42501');
select wamo_testhilfe.fehler($$update __SCHEMA__.devices set gesperrt_grund = 'x'$$, '42501');
select wamo_testhilfe.fehler($$delete from __SCHEMA__.devices$$, '42501');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.device_events$$, '42501');

-- 3. Eingaben und Akteur
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraet_entscheiden(null, 'pc-admin1-pc', 'freigeben', null, clock_timestamp())$$, '22004');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a1', 'pc-admin1-pc', 'freigeben', null, null)$$, '22004');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a1', 'pc-admin1-pc', 'loeschen', null, clock_timestamp())$$, '22023');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a1', 'PC-ADMIN1-PC', 'freigeben', null, clock_timestamp())$$, '22023');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a1', 'drucker-x', 'freigeben', null, clock_timestamp())$$, '22023');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a1', 'pc-admin1-pc''; drop table x;--', 'freigeben', null, clock_timestamp())$$, '22023');
-- Grund ist Pflicht bei Sperren und Widerruf (mindestens 3 Zeichen), hoechstens 200
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a1', 'pc-anna-neu', 'widerrufen', null, clock_timestamp())$$, '22023');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a1', 'pc-anna-pc', 'sperren', '  ', clock_timestamp())$$, '22023');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a1', 'pc-anna-pc', 'sperren', 'ab', clock_timestamp())$$, '22023');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a1', 'pc-anna-pc', 'sperren', repeat('x', 201), clock_timestamp())$$, '22023');
-- Akteur: Mitglied, inaktives Profil, unbekanntes Profil
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000b1', 'pc-admin1-pc', 'freigeben', null, clock_timestamp())$$, '42501');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000b4', 'pc-admin1-pc', 'freigeben', null, clock_timestamp())$$, '42501');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000ff', 'pc-admin1-pc', 'freigeben', null, clock_timestamp())$$, '42501');
-- zweiter Faktor: zu alt (121 s), Grenze (110 s geht), Zukunft
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a1', 'pc-admin1-pc', 'freigeben', null, clock_timestamp() - interval '121 seconds')$$, '23514');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a1', 'pc-admin1-pc', 'freigeben', null, clock_timestamp() + interval '30 seconds')$$, '23514');
reset role;

-- nichts davon hat etwas veraendert
do $$ begin
  perform wamo_testhilfe.gleich((select status::text from __SCHEMA__.devices where kennung = 'pc-admin1-pc'), 'beantragt', 'Admin1-Geraet unveraendert');
  perform wamo_testhilfe.gleich((select status::text from __SCHEMA__.devices where kennung = 'pc-anna-pc'), 'aktiv', 'Annas Geraet unveraendert');
end $$;

-- 4. Liste fuer Admins
set role wamo_core;
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraete_verwaltung_liste('00000000-0000-4000-8000-0000000000b1')$$, '42501');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraete_verwaltung_liste('00000000-0000-4000-8000-0000000000b4')$$, '42501');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraete_verwaltung_liste(null)$$, '42501');
do $$ declare r record; anz int; begin
  select count(*) into anz from __SCHEMA__.geraete_verwaltung_liste('00000000-0000-4000-8000-0000000000a1')
   where kennung in ('pc-admin1-pc', 'pc-anna-pc', 'pc-anna-neu', 'pc-bernd-pc', 'pc-bernd-zwei');
  perform wamo_testhilfe.gleich(anz, 5, 'Liste enthaelt alle fuenf Testgeraete');
  select * into r from __SCHEMA__.geraete_verwaltung_liste('00000000-0000-4000-8000-0000000000a1') where kennung = 'pc-anna-pc';
  perform wamo_testhilfe.gleich(r.profil_name, 'Anna', 'Profilname');
  perform wamo_testhilfe.gleich(r.status::text, 'aktiv', 'Status');
  perform wamo_testhilfe.gleich(r.bindung::text, 'automatisch', 'Bindung');
  perform wamo_testhilfe.gleich(r.fingerabdruck, (select fp from k where n = 2), 'Fingerabdruck');
  perform wamo_testhilfe.gleich(jsonb_array_length(r.ereignisse), 1, 'ein Ereignis (Bindung)');
  perform wamo_testhilfe.gleich(r.ereignisse -> 0 ->> 'nach', 'aktiv', 'Ereignis nach');
end $$;
reset role;
-- keine Schluesselmaterial-Spalte in der Antwort
do $$ declare s text; begin
  s := pg_get_function_result('__SCHEMA__.geraete_verwaltung_liste(uuid)'::regprocedure);
  perform wamo_testhilfe.gleich(position('pubkey' in s) = 0, true, 'kein Schluesselmaterial in der Liste');
  perform wamo_testhilfe.gleich(position('user_id' in s) = 0, true, 'kein user_id-Feld (nur profil_id)');
end $$;
-- Reihenfolge: wartende Geraete zuerst
do $$ declare erst text; begin
  select l.status::text into erst from __SCHEMA__.geraete_verwaltung_liste('00000000-0000-4000-8000-0000000000a1') l limit 1;
  perform wamo_testhilfe.gleich(erst, 'beantragt', 'wartende Geraete stehen oben');
end $$;

-- 5. Aktionen
set role wamo_core;
do $$ declare r record; begin
  -- unbekannte Kennung
  select * into r from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a1', 'pc-gibt-es-nicht', 'freigeben', null, clock_timestamp());
  perform wamo_testhilfe.gleich(r.ergebnis, 'unbekannt', 'unbekannte Kennung');
  -- Admin1 gibt sein eigenes Geraet frei (erlaubt), mit einem zweiten Faktor von vor 100 Sekunden
  select * into r from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a1', 'pc-admin1-pc', 'freigeben', null, clock_timestamp() - interval '100 seconds');
  perform wamo_testhilfe.gleich(r.ergebnis, 'ok', 'eigenes Geraet freigegeben');
  perform wamo_testhilfe.gleich(r.vorher::text, 'beantragt', 'vorher');
  perform wamo_testhilfe.gleich(r.nachher::text, 'aktiv', 'nachher');
  -- doppelte Aktion: folgenlos
  select * into r from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a2', 'pc-admin1-pc', 'freigeben', null, clock_timestamp());
  perform wamo_testhilfe.gleich(r.ergebnis, 'schon', 'zweites Freigeben');
  -- Annas Wechselgeraet: Anna hat schon ein aktives Geraet
  select * into r from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a1', 'pc-anna-neu', 'freigeben', null, clock_timestamp());
  perform wamo_testhilfe.gleich(r.ergebnis, 'konflikt', 'zweites aktives Geraet des Profils');
  perform wamo_testhilfe.gleich(r.nachher::text, 'beantragt', 'bleibt beantragt');
  -- freigeben gilt nur fuer beantragte, entsperren nur fuer gesperrte, sperren nur fuer aktive
  select * into r from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a1', 'pc-anna-pc', 'entsperren', null, clock_timestamp());
  perform wamo_testhilfe.gleich(r.ergebnis, 'schon', 'entsperren eines aktiven Geraets');
  select * into r from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a1', 'pc-anna-neu', 'sperren', 'Test', clock_timestamp());
  perform wamo_testhilfe.gleich(r.ergebnis, 'nicht_moeglich', 'sperren eines beantragten Geraets');
  select * into r from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a1', 'pc-anna-neu', 'entsperren', null, clock_timestamp());
  perform wamo_testhilfe.gleich(r.ergebnis, 'nicht_moeglich', 'entsperren eines beantragten Geraets');
  -- Anna sperren, mit Grund (Steuerzeichen werden entfernt, Leerraum beschnitten)
  select * into r from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a2', 'pc-anna-pc', 'sperren', E'  Laptop verloren\n ', clock_timestamp());
  perform wamo_testhilfe.gleich(r.ergebnis, 'ok', 'Anna gesperrt');
  select * into r from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a2', 'pc-anna-pc', 'sperren', 'Laptop verloren', clock_timestamp());
  perform wamo_testhilfe.gleich(r.ergebnis, 'schon', 'zweites Sperren');
  select * into r from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a2', 'pc-anna-pc', 'freigeben', null, clock_timestamp());
  perform wamo_testhilfe.gleich(r.ergebnis, 'nicht_moeglich', 'freigeben eines gesperrten Geraets (dafuer gibt es entsperren)');
  -- Entsperren durch Admin2
  select * into r from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a2', 'pc-anna-pc', 'entsperren', null, clock_timestamp());
  perform wamo_testhilfe.gleich(r.ergebnis, 'ok', 'Anna entsperrt');
  -- Widerruf von Annas altem Geraet, danach geht das Wechselgeraet
  select * into r from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a1', 'pc-anna-pc', 'widerrufen', 'Geraetewechsel', clock_timestamp());
  perform wamo_testhilfe.gleich(r.ergebnis, 'ok', 'Annas altes Geraet widerrufen');
  select * into r from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a1', 'pc-anna-pc', 'widerrufen', 'Geraetewechsel', clock_timestamp());
  perform wamo_testhilfe.gleich(r.ergebnis, 'schon', 'zweites Widerrufen');
  select * into r from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a1', 'pc-anna-pc', 'entsperren', null, clock_timestamp());
  perform wamo_testhilfe.gleich(r.ergebnis, 'nicht_moeglich', 'widerrufen ist endgueltig (entsperren)');
  select * into r from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a1', 'pc-anna-pc', 'freigeben', null, clock_timestamp());
  perform wamo_testhilfe.gleich(r.ergebnis, 'nicht_moeglich', 'widerrufen ist endgueltig (freigeben)');
  select * into r from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a1', 'pc-anna-pc', 'sperren', 'Test', clock_timestamp());
  perform wamo_testhilfe.gleich(r.ergebnis, 'nicht_moeglich', 'widerrufen ist endgueltig (sperren)');
  select * into r from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a1', 'pc-anna-neu', 'freigeben', null, clock_timestamp());
  perform wamo_testhilfe.gleich(r.ergebnis, 'ok', 'Wechselgeraet freigegeben');
  -- Ablehnen: Bernds zweites Geraet (beantragt) widerrufen
  select * into r from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a1', 'pc-bernd-zwei', 'widerrufen', 'Nicht bekannt', clock_timestamp());
  perform wamo_testhilfe.gleich(r.ergebnis, 'ok', 'Antrag abgelehnt');
  perform wamo_testhilfe.gleich(r.nachher::text, 'widerrufen', 'abgelehnt = widerrufen');
end $$;
reset role;

-- Spuren in devices, device_events und Hinweisen
do $$ declare d record; begin
  select * into d from __SCHEMA__.devices where kennung = 'pc-admin1-pc';
  perform wamo_testhilfe.gleich(d.status::text, 'aktiv', 'Admin1-Geraet aktiv');
  perform wamo_testhilfe.gleich(d.freigegeben_von, '00000000-0000-4000-8000-0000000000a1'::uuid, 'freigegeben_von');
  perform wamo_testhilfe.gleich(d.geaendert_von, '00000000-0000-4000-8000-0000000000a1'::uuid, 'geaendert_von');
  perform wamo_testhilfe.gleich(d.freigegeben_at is not null, true, 'freigegeben_at');
  select * into d from __SCHEMA__.devices where kennung = 'pc-anna-pc';
  perform wamo_testhilfe.gleich(d.status::text, 'widerrufen', 'Annas altes Geraet widerrufen');
  perform wamo_testhilfe.gleich(d.gesperrt_grund, 'Geraetewechsel', 'Grund');
  perform wamo_testhilfe.gleich(d.gesperrt_at is not null, true, 'gesperrt_at');
  perform wamo_testhilfe.gleich(d.freigegeben_von, '00000000-0000-4000-8000-0000000000a2'::uuid, 'Entsperren durch Admin2 steht in freigegeben_von');
  -- Verlauf Annas altes Geraet: Bindung, gesperrt, aktiv, widerrufen
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.device_events e where e.device_id = d.id), 4::bigint, 'Verlauf Annas altes Geraet (keine Duplikate durch "schon")');
  perform wamo_testhilfe.gleich((select e.grund from __SCHEMA__.device_events e where e.device_id = d.id and e.nach = 'gesperrt'),
    'Laptop verloren', 'Sperrgrund bereinigt im Verlauf');
  perform wamo_testhilfe.gleich((select e.durch from __SCHEMA__.device_events e where e.device_id = d.id and e.nach = 'gesperrt'),
    '00000000-0000-4000-8000-0000000000a2'::uuid, 'Akteur im Verlauf');
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.hinweise h where h.empfaenger = '00000000-0000-4000-8000-0000000000b1'
    and h.bezug = d.id and h.art in ('geraet_gesperrt', 'geraet_entsperrt', 'geraet_widerrufen')), 3::bigint, 'Hinweise an Anna');
  perform wamo_testhilfe.gleich((select h.text from __SCHEMA__.hinweise h where h.empfaenger = '00000000-0000-4000-8000-0000000000b1'
    and h.bezug = d.id and h.art = 'geraet_gesperrt'), 'Laptop verloren', 'Anna sieht den Sperrgrund');
  select * into d from __SCHEMA__.devices where kennung = 'pc-bernd-zwei';
  perform wamo_testhilfe.gleich(d.status::text, 'widerrufen', 'abgelehnter Antrag');
  perform wamo_testhilfe.gleich(d.gesperrt_grund, 'Nicht bekannt', 'Ablehnungsgrund');
end $$;

-- 6. Besitzer deaktiviert: die Datenbank sperrt selbst, Entsperren geht nicht, Widerruf schon
set role wamo_admin;
update __SCHEMA__.profiles set aktiv = false where user_id = '00000000-0000-4000-8000-0000000000b2';
reset role;
set role wamo_core;
do $$ declare r record; begin
  perform wamo_testhilfe.gleich((select status::text from __SCHEMA__.devices where kennung = 'pc-bernd-pc'), 'gesperrt', 'Bernds Geraet von der Datenbank gesperrt');
  select * into r from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a1', 'pc-bernd-pc', 'entsperren', null, clock_timestamp());
  perform wamo_testhilfe.gleich(r.ergebnis, 'besitzer_inaktiv', 'Entsperren bei inaktivem Besitzer');
  perform wamo_testhilfe.gleich(r.nachher::text, 'gesperrt', 'bleibt gesperrt');
  select * into r from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a1', 'pc-bernd-pc', 'widerrufen', 'Konto geschlossen', clock_timestamp());
  perform wamo_testhilfe.gleich(r.ergebnis, 'ok', 'Widerruf bei inaktivem Besitzer');
end $$;
reset role;

-- 7. Rolle wird zurueckgenommen: Admin2 ist danach kein Akteur mehr (Datenbank prueft die Rolle bei jedem Aufruf)
set role wamo_admin;
update __SCHEMA__.profiles set rolle = 'mitglied' where user_id = '00000000-0000-4000-8000-0000000000a2';
reset role;
set role wamo_core;
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a2', 'pc-anna-neu', 'sperren', 'Test', clock_timestamp())$$, '42501');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraete_verwaltung_liste('00000000-0000-4000-8000-0000000000a2')$$, '42501');
reset role;
do $$ begin
  perform wamo_testhilfe.gleich((select status::text from __SCHEMA__.devices where kennung = 'pc-anna-neu'), 'aktiv', 'Wechselgeraet unveraendert');
end $$;

-- 8. Andere Rollen: wamo_admin hat die Funktionen nicht (er pflegt das Register direkt, wie bisher), anon und Co. auch nicht
set role wamo_admin;
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraet_entscheiden('00000000-0000-4000-8000-0000000000a1', 'pc-anna-neu', 'sperren', 'Test', clock_timestamp())$$, '42501');
select wamo_testhilfe.fehler($$select * from __SCHEMA__.geraete_verwaltung_liste('00000000-0000-4000-8000-0000000000a1')$$, '42501');
reset role;
do $$ begin
  perform wamo_testhilfe.gleich(has_function_privilege('anon', '__SCHEMA__.geraet_entscheiden(uuid,text,text,text,timestamptz)', 'EXECUTE'), false, 'anon');
  perform wamo_testhilfe.gleich(has_function_privilege('authenticated', '__SCHEMA__.geraet_entscheiden(uuid,text,text,text,timestamptz)', 'EXECUTE'), false, 'authenticated');
  perform wamo_testhilfe.gleich(has_function_privilege('service_role', '__SCHEMA__.geraet_entscheiden(uuid,text,text,text,timestamptz)', 'EXECUTE'), false, 'service_role');
  perform wamo_testhilfe.gleich(has_function_privilege('anon', '__SCHEMA__.geraete_verwaltung_liste(uuid)', 'EXECUTE'), false, 'anon Liste');
  perform wamo_testhilfe.gleich(has_function_privilege('authenticated', '__SCHEMA__.geraete_verwaltung_liste(uuid)', 'EXECUTE'), false, 'authenticated Liste');
  perform wamo_testhilfe.gleich(has_function_privilege('service_role', '__SCHEMA__.geraete_verwaltung_liste(uuid)', 'EXECUTE'), false, 'service_role Liste');
  perform wamo_testhilfe.gleich(has_function_privilege('wamo_core', '__SCHEMA__.geraet_entscheiden(uuid,text,text,text,timestamptz)', 'EXECUTE'), true, 'wamo_core');
  perform wamo_testhilfe.gleich(has_any_column_privilege('wamo_core', '__SCHEMA__.devices', 'INSERT,UPDATE'), false, 'wamo_core ohne Schreibrecht auf devices');
  perform wamo_testhilfe.gleich((select p.prosecdef from pg_proc p where p.oid = '__SCHEMA__.geraet_entscheiden(uuid,text,text,text,timestamptz)'::regprocedure), true, 'security definer');
  perform wamo_testhilfe.gleich((select coalesce(bool_or(c like 'search_path=%'), false) from pg_proc p, unnest(p.proconfig) c
    where p.oid = '__SCHEMA__.geraet_entscheiden(uuid,text,text,text,timestamptz)'::regprocedure), true, 'search_path fest');
end $$;

select '0014 OK';
rollback;
