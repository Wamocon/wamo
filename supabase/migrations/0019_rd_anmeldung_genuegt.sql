-- WAMO 0019: Fernzugriff startet mit dem Code der Anmeldung. Die Funktion rd_sitzung_beginnen verliert die beiden Sperren aus 0015
-- (Code höchstens 120 Sekunden alt, ein Code gilt für genau einen Start) und meldet jeden Start allen Admins.
-- __SCHEMA__ wird ersetzt durch wamo_dev | wamo_test | wamo_prod.
-- Anwenden (je Schema, in EINER Transaktion, zweimal hintereinander fehlerfrei):
--   bash scripts/render-migration.sh <schema> supabase/migrations/0019_rd_anmeldung_genuegt.sql \
--     | docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 --single-transaction
-- Rückweg: 0019_rd_anmeldung_genuegt.down.sql (stellt die Funktion aus 0015 wieder her, keine Daten gehen verloren).
-- Hängt ab von 0001, 0002 (hinweise) und 0015 (rd_sitzungen, rd_sitzung_beginnen).
-- Reihenfolge der Anwendung: wamo_dev, wamo_test, dann das Schema des Büro-Kerns, erst nach database-reviewer und nach Nikos Ja.
-- Reihenfolge mit dem Kern: Diese Migration kommt VOR dem Kern, der keinen frischen Code mehr verlangt (rd_api.py, Paket KP-2).
--   * Alter Kern, neue Funktion: Der alte Kern verlangt weiter einen frischen Code und gibt dessen Zeit an die Funktion. Das geht
--     ohne Fehler durch, nur Hinweis und die Sperren sind anders.
--   * Neuer Kern, alte Funktion (Kern vor der Migration): Die alte Funktion lehnt die Zeit des Codes der Anmeldung mit 23514 ab, der
--     Kern antwortet mit zweiter_faktor_zu_alt, und die Oberfläche fragt den Code ab. Es geht nichts auf, es fehlt nur der Komfort.
--
-- Was sich ändert (nur die Funktion, keine Tabelle, keine Rolle, keine Rechte):
--   * Entfällt: "der zweite Faktor ist älter als 120 Sekunden". Der Code der Anmeldung genügt. Die Frische des Codes und das
--     Höchstalter der Anmeldung (WAMO_RD_ANMELDUNG_MAX_MIN, WAMO_ANMELDUNG_MAX_STUNDEN) setzt allein der Kern durch, denn die
--     Datenbank sieht kein Token.
--   * Entfällt: "der zweite Faktor wurde schon für einen Start verwendet". Ohne diese Sperre scheitert sonst der zweite Start
--     nach derselben Anmeldung. Das Zeitfenster der Anmeldung bleibt gleich, und der Code kommt mehrfach vor.
--   * Neu: Die Zeit des Codes darf höchstens 31 Tage zurückliegen (23514). Das ist die letzte Schranke gegen einen Kern mit falscher
--     Uhr oder falschem Token: Die Zugangskontrolle lässt höchstens 720 Stunden (30 Tage) zu.
--   * Bleibt: aktiver Admin (42501), Zeit nicht aus der Zukunft (5 Sekunden Toleranz), Eingaben, Advisory-Lock, Schließen
--     abgelaufener Sitzungen, Starts je Stunde (rate), ein Zuschauer je Ziel (belegt), Ablösung der eigenen Sitzung, gleichzeitige
--     Sitzungen (voll).
--   * Neu (KP-2b): Jeder erfolgreiche Start schreibt einen Hinweis (art system) an ALLE aktiven Admins, auch an den Startenden.
--     Ein gestohlenes Token startet Sitzungen, bis die Anmeldung abläuft. Der Hinweis macht einen fremden Start sichtbar, ohne
--     dass der Kern sie melden muss: Er entsteht in derselben Transaktion wie die Sitzungszeile. Der Text nennt die Person, den
--     Kurznamen des Ziels, das Alter des Codes und die Höchstdauer. Keine Adresse, kein Konto, kein Passwort, keine Zugangsdaten.
--     Die Funktion läuft weiter mit den Rechten des Aufrufers (security invoker): wamo_core darf hinweise anlegen (0002, Trigger
--     hinweise_guard erlaubt dafür nur scan_fertig und system), hinweis_an_admins ist für wamo_core nicht ausführbar und wird
--     nicht genutzt. Das Recht EXECUTE bleibt wie in 0015: nur wamo_core.
-- Grenze (wie in 0014 und 0015): p_aal2_zeit stammt vom Kern. Die Prüfung schützt vor einem ehrlichen Kern mit einem alten Token,
-- nicht vor einem übernommenen Kern. Dagegen helfen der Hinweis an alle Admins und das Protokoll in rd_sitzungen.
set local lock_timeout = '5s';

do $$ begin
  if not exists (select 1 from __SCHEMA__.schema_migrations where version = '0001_wamo_init')
     or not exists (select 1 from __SCHEMA__.schema_migrations where version = '0002_wamo_geraete')
     or not exists (select 1 from __SCHEMA__.schema_migrations where version = '0015_rd_sitzungen') then
    raise exception '0019 braucht 0001, 0002 und 0015';
  end if;
end $$;

-- ---------- Sitzung beginnen ----------
create or replace function __SCHEMA__.rd_sitzung_beginnen(p_profil uuid, p_ziel text, p_dauer_secs integer,
                                                          p_aal2_zeit timestamptz,
                                                          p_max_gleichzeitig integer default 3,
                                                          p_max_je_stunde integer default 6)
returns table (ergebnis text, sitzung_id uuid, abgeloest_id uuid, belegt_von text, belegt_seit timestamptz,
               bis_at timestamptz)
language plpgsql set search_path = '' as $$
declare
  v_alt   __SCHEMA__.rd_sitzungen%rowtype;
  v_neu   uuid;
  v_bis   timestamptz;
  v_abgel uuid;
  v_alter interval;
  v_wann  text;
begin
  if p_profil is null or p_ziel is null or p_dauer_secs is null or p_aal2_zeit is null then
    raise exception 'rd_sitzung_beginnen: Angaben fehlen' using errcode = '22004';
  end if;
  if p_ziel !~ '^[a-z0-9]{1,16}$' or p_dauer_secs not between 60 and 14400
     or p_max_gleichzeitig not between 1 and 10 or p_max_je_stunde not between 1 and 60 then
    raise exception 'rd_sitzung_beginnen: ungültige Angaben' using errcode = '22023';
  end if;
  if not exists (select 1 from __SCHEMA__.profiles p where p.user_id = p_profil and p.aktiv and p.rolle = 'admin') then
    raise exception 'rd_sitzung_beginnen: nur ein aktiver Admin darf eine Fernsitzung starten' using errcode = '42501';
  end if;
  if p_aal2_zeit > clock_timestamp() + interval '5 seconds' then
    raise exception 'rd_sitzung_beginnen: aal2_zeit liegt in der Zukunft' using errcode = '23514';
  end if;
  if clock_timestamp() - p_aal2_zeit > interval '31 days' then
    raise exception 'rd_sitzung_beginnen: der zweite Faktor liegt mehr als 31 Tage zurück' using errcode = '23514';
  end if;

  -- alle Starts und Ablösungen nacheinander (der Lock endet mit der Transaktion)
  perform pg_advisory_xact_lock(hashtextextended('wamo.rd_sitzungen', 0));

  -- vergessene Sitzungen schließen: Höchstende plus 2 Minuten Nachfrist (Kern und Gateway beenden sie vorher selbst)
  update __SCHEMA__.rd_sitzungen s set ende_at = now(), grund = 'zeitlimit'
   where s.ende_at is null and s.bis_at < now() - interval '2 minutes';

  if (select count(*) from __SCHEMA__.rd_sitzungen s where s.profil_id = p_profil and s.start_at > now() - interval '1 hour')
       >= p_max_je_stunde then
    return query select 'rate'::text, null::uuid, null::uuid, null::text, null::timestamptz, null::timestamptz;
    return;
  end if;

  select * into v_alt from __SCHEMA__.rd_sitzungen s where s.ziel = p_ziel and s.ende_at is null for update;
  if found then
    if v_alt.profil_id <> p_profil then
      return query select 'belegt'::text, null::uuid, null::uuid,
                          (select p.display_name from __SCHEMA__.profiles p where p.user_id = v_alt.profil_id),
                          v_alt.start_at, null::timestamptz;
      return;
    end if;
    -- dieselbe Person startet erneut (zum Beispiel nach einem Tabwechsel): die alte Sitzung endet, die neue beginnt
    update __SCHEMA__.rd_sitzungen s set ende_at = now(), grund = 'abgeloest' where s.id = v_alt.id;
    v_abgel := v_alt.id;
  end if;

  if (select count(*) from __SCHEMA__.rd_sitzungen s where s.ende_at is null) >= p_max_gleichzeitig then
    return query select 'voll'::text, null::uuid, null::uuid, null::text, null::timestamptz, null::timestamptz;
    return;
  end if;

  v_bis := now() + make_interval(secs => p_dauer_secs);
  insert into __SCHEMA__.rd_sitzungen (profil_id, ziel, bis_at, aal2_at) values (p_profil, p_ziel, v_bis, p_aal2_zeit)
    returning id into v_neu;

  -- Hinweis an alle aktiven Admins (KP-2b), in derselben Transaktion wie die Sitzungszeile: ohne Hinweis kein Start
  v_alter := clock_timestamp() - p_aal2_zeit;
  v_wann := case when v_alter < interval '3 minutes' then 'gerade eben'
                 when v_alter < interval '1 hour' then 'vor ' || floor(extract(epoch from v_alter) / 60)::integer::text || ' Minuten'
                 else 'vor ' || floor(extract(epoch from v_alter) / 3600)::integer::text || ' Stunden' end;
  insert into __SCHEMA__.hinweise (empfaenger, art, titel, text, bezug, daten)
  select a.user_id, 'system', 'Fernzugriff gestartet: ' || p_ziel,
         left(coalesce((select left(p.display_name, 60) from __SCHEMA__.profiles p where p.user_id = p_profil), 'Ein Admin')
              || ' hat eine Fernsitzung auf ' || p_ziel || ' gestartet. Der Code der Anmeldung wurde ' || v_wann
              || ' eingegeben. Die Sitzung endet nach höchstens ' || (p_dauer_secs / 60)::text || ' Minuten. '
              || 'Warst du das nicht, melde dich an allen Geräten ab und sag es den anderen Admins.', 1000),
         v_neu, jsonb_build_object('quelle', 'fernzugriff', 'sitzung', v_neu, 'ziel', p_ziel, 'profil', p_profil)
    from __SCHEMA__.profiles a
   where a.aktiv and a.rolle = 'admin';

  return query select 'ok'::text, v_neu, v_abgel, null::text, null::timestamptz, v_bis;
end $$;

-- ---------- Rechte (unverändert gegenüber 0015, hier nur festgehalten) ----------
revoke all on function __SCHEMA__.rd_sitzung_beginnen(uuid, text, integer, timestamptz, integer, integer)
  from public, anon, authenticated, service_role, wamo_admin;
grant execute on function __SCHEMA__.rd_sitzung_beginnen(uuid, text, integer, timestamptz, integer, integer) to wamo_core;

-- Selbsttest: bricht die Transaktion bei Rechtedrift oder bei einer Funktion mit altem Stand ab.
do $$
declare
  v_rumpf text;
begin
  select p.prosrc into v_rumpf from pg_proc p
   where p.oid = '__SCHEMA__.rd_sitzung_beginnen(uuid, text, integer, timestamptz, integer, integer)'::regprocedure;
  if v_rumpf like '%120 seconds%' or v_rumpf like '%schon für einen Start verwendet%' or v_rumpf not like '%31 days%'
     or v_rumpf not like '%__SCHEMA__.hinweise%'
     or (select p.prosecdef from pg_proc p
          where p.oid = '__SCHEMA__.rd_sitzung_beginnen(uuid, text, integer, timestamptz, integer, integer)'::regprocedure)
     or not exists (select 1 from pg_proc p, unnest(p.proconfig) c
                     where p.oid = '__SCHEMA__.rd_sitzung_beginnen(uuid, text, integer, timestamptz, integer, integer)'::regprocedure
                       and c like 'search_path=%')
     or not has_function_privilege('wamo_core', '__SCHEMA__.rd_sitzung_beginnen(uuid, text, integer, timestamptz, integer, integer)', 'EXECUTE')
     or has_function_privilege('wamo_admin', '__SCHEMA__.rd_sitzung_beginnen(uuid, text, integer, timestamptz, integer, integer)', 'EXECUTE')
     or has_function_privilege('anon', '__SCHEMA__.rd_sitzung_beginnen(uuid, text, integer, timestamptz, integer, integer)', 'EXECUTE')
     or has_function_privilege('authenticated', '__SCHEMA__.rd_sitzung_beginnen(uuid, text, integer, timestamptz, integer, integer)', 'EXECUTE')
     or has_function_privilege('service_role', '__SCHEMA__.rd_sitzung_beginnen(uuid, text, integer, timestamptz, integer, integer)', 'EXECUTE')
     or not has_table_privilege('wamo_core', '__SCHEMA__.rd_sitzungen', 'SELECT')
     or has_table_privilege('wamo_core', '__SCHEMA__.rd_sitzungen', 'INSERT,UPDATE,DELETE,TRUNCATE')
     or not has_table_privilege('wamo_core', '__SCHEMA__.hinweise', 'INSERT')
     or has_table_privilege('wamo_core', '__SCHEMA__.hinweise', 'DELETE,TRUNCATE')
     or has_column_privilege('wamo_core', '__SCHEMA__.hinweise', 'gelesen_at', 'UPDATE')
     or has_any_column_privilege('anon', '__SCHEMA__.hinweise', 'SELECT,INSERT,UPDATE')
     or has_any_column_privilege('authenticated', '__SCHEMA__.hinweise', 'SELECT,INSERT,UPDATE')
     or has_any_column_privilege('service_role', '__SCHEMA__.hinweise', 'SELECT,INSERT,UPDATE')
     or not (select c.relrowsecurity from pg_class c where c.oid = '__SCHEMA__.hinweise'::regclass)
     or not exists (select 1 from pg_policies where schemaname = '__SCHEMA__' and tablename = 'hinweise' and policyname = 'core_all')
     or not exists (select 1 from pg_trigger t where t.tgrelid = '__SCHEMA__.hinweise'::regclass and t.tgname = 'hinweise_guard')
     or not exists (select 1 from pg_indexes where schemaname = '__SCHEMA__' and tablename = 'rd_sitzungen'
                      and indexname = 'rd_sitzungen_ziel_offen_idx')
  then
    raise exception 'rd_sitzung_beginnen: Funktion, Rechte, Hinweise oder Index nicht im erwarteten Stand (0015 und 0002 prüfen)'
      using errcode = '42501';
  end if;
end $$;

insert into __SCHEMA__.schema_migrations (version) values ('0019_rd_anmeldung_genuegt') on conflict do nothing;
