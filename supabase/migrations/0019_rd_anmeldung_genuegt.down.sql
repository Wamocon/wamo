-- Rückweg zu 0019 (Fernzugriff startet mit dem Code der Anmeldung): stellt die Funktion rd_sitzung_beginnen aus 0015 wieder her
-- (Code höchstens 120 Sekunden alt, ein Code gilt für genau einen Start, kein Hinweis an die Admins). Die Funktion steht hier
-- wortgleich wie in 0015. Es gehen keine Daten verloren: Tabelle, Sitzungen und schon geschriebene Hinweise bleiben, auch die
-- Rechte bleiben. Wiederholbar. Reihenfolge: ZUERST den Kern auf einen Stand mit frischem Code zurückstellen (rueckweg-kern),
-- sonst bekommt der Kern von der alten Funktion für jeden Start mit altem Code die Antwort 23514 und fragt den Code ab; das ist
-- sicher, aber unbequem. Spätere Migrationen vorher zurücknehmen.
-- Anwenden: bash scripts/render-migration.sh <schema> supabase/migrations/0019_rd_anmeldung_genuegt.down.sql \
--     | docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 --single-transaction
set local lock_timeout = '5s';

do $$ begin
  if exists (select 1 from __SCHEMA__.schema_migrations where version > '0019_rd_anmeldung_genuegt')
     and coalesce(current_setting('wamo.down_trotz_folgemigration', true), '') <> 'ja' then
    raise exception '0019 down: erst die späteren Migrationen zurücknehmen';
  end if;
  if to_regprocedure('__SCHEMA__.rd_sitzung_beginnen(uuid, text, integer, timestamptz, integer, integer)') is null then
    raise exception '0019 down: rd_sitzung_beginnen fehlt (0015 nicht angewendet)';
  end if;
end $$;

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
  if clock_timestamp() - p_aal2_zeit > interval '120 seconds' then
    raise exception 'rd_sitzung_beginnen: der zweite Faktor ist älter als 120 Sekunden' using errcode = '23514';
  end if;

  -- alle Starts und Ablösungen nacheinander (der Lock endet mit der Transaktion)
  perform pg_advisory_xact_lock(hashtextextended('wamo.rd_sitzungen', 0));

  -- vergessene Sitzungen schließen: Höchstende plus 2 Minuten Nachfrist (Kern und Gateway beenden sie vorher selbst)
  update __SCHEMA__.rd_sitzungen s set ende_at = now(), grund = 'zeitlimit'
   where s.ende_at is null and s.bis_at < now() - interval '2 minutes';

  -- Der Code gilt für genau einen Start: Hat dieses Profil schon mit demselben oder einem jüngeren Code gestartet, ist er
  -- verbraucht. Ein Start, der am Gateway scheiterte (grund 'fehler'), zählt nicht, damit ein Wiederholen möglich bleibt.
  if exists (select 1 from __SCHEMA__.rd_sitzungen s
              where s.profil_id = p_profil and s.aal2_at >= p_aal2_zeit and coalesce(s.grund, '') <> 'fehler') then
    raise exception 'rd_sitzung_beginnen: der zweite Faktor wurde schon für einen Start verwendet' using errcode = '23514';
  end if;

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
  return query select 'ok'::text, v_neu, v_abgel, null::text, null::timestamptz, v_bis;
end $$;

-- Rechte wie in 0015 (create or replace behält sie, hier nur festgehalten)
revoke all on function __SCHEMA__.rd_sitzung_beginnen(uuid, text, integer, timestamptz, integer, integer)
  from public, anon, authenticated, service_role, wamo_admin;
grant execute on function __SCHEMA__.rd_sitzung_beginnen(uuid, text, integer, timestamptz, integer, integer) to wamo_core;

-- Selbsttest: die Funktion hat wieder den Stand von 0015 und die Rechte stimmen.
do $$
declare
  v_rumpf text;
begin
  select p.prosrc into v_rumpf from pg_proc p
   where p.oid = '__SCHEMA__.rd_sitzung_beginnen(uuid, text, integer, timestamptz, integer, integer)'::regprocedure;
  if v_rumpf not like '%120 seconds%' or v_rumpf not like '%schon für einen Start verwendet%' or v_rumpf like '%31 days%'
     or not has_function_privilege('wamo_core', '__SCHEMA__.rd_sitzung_beginnen(uuid, text, integer, timestamptz, integer, integer)', 'EXECUTE')
     or has_function_privilege('anon', '__SCHEMA__.rd_sitzung_beginnen(uuid, text, integer, timestamptz, integer, integer)', 'EXECUTE')
     or has_function_privilege('authenticated', '__SCHEMA__.rd_sitzung_beginnen(uuid, text, integer, timestamptz, integer, integer)', 'EXECUTE')
     or has_function_privilege('service_role', '__SCHEMA__.rd_sitzung_beginnen(uuid, text, integer, timestamptz, integer, integer)', 'EXECUTE')
     or has_function_privilege('wamo_admin', '__SCHEMA__.rd_sitzung_beginnen(uuid, text, integer, timestamptz, integer, integer)', 'EXECUTE')
  then
    raise exception '0019 down: rd_sitzung_beginnen nicht im Stand von 0015' using errcode = '42501';
  end if;
end $$;

delete from __SCHEMA__.schema_migrations where version = '0019_rd_anmeldung_genuegt';
