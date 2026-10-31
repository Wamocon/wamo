-- WAMO 0015: Protokoll und Belegung der Fernsitzungen (Remote Desktop der Sparks, docs/remote-desktop.md, Stufe 3).
-- __SCHEMA__ wird ersetzt durch wamo_dev | wamo_test | wamo_prod.
-- Anwenden (je Schema, in EINER Transaktion, zweimal hintereinander fehlerfrei):
--   bash scripts/render-migration.sh <schema> supabase/migrations/0015_rd_sitzungen.sql \
--     | docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 --single-transaction
-- Rückweg: 0015_rd_sitzungen.down.sql. Hängt nur von 0001 ab (profiles, schema_migrations).
-- wamo_prod nur nach ausdrücklicher Freigabe und nach dem Agenten database-reviewer.
--
-- Was hier entsteht: Eine Tabelle rd_sitzungen mit einer Zeile je Fernsitzung (wer, welches Ziel, Start, Höchstende, Ende,
-- Grund) und zwei Funktionen für den Kern (voice-core/rd_api.py). Das Protokoll enthält ausdrücklich NUR wer, wann, welcher
-- Spark, wie lange und warum beendet. Keine Bildschirminhalte, keine Eingaben, keine Adressen, keine Zugangsdaten.
--
-- Belegung ("ein Zuschauer je Ziel") hält die Datenbank selbst fest, nicht der Speicher des Kerns: Der teilweise eindeutige
-- Index rd_sitzungen_ziel_offen_idx erlaubt je Ziel höchstens eine Zeile ohne ende_at. Ein Neustart des Kerns, zwei Kerne oder
-- ein Fehler im Ablauf können deshalb nie zwei Zuschauer auf demselben Ziel erzeugen.
--
--   rd_sitzung_beginnen(...)   prüft aktiver Admin und zweiter Faktor höchstens 120 Sekunden alt (wie 0014), sperrt per
--                              Advisory-Lock, schließt abgelaufene Sitzungen (Höchstende + 2 Minuten), prüft Starts je Stunde
--                              und gleichzeitige Sitzungen, löst eine eigene offene Sitzung auf demselben Ziel ab (nur vom
--                              selben Profil, Grund abgeloest) und legt die neue Zeile an. Ergebnis: ok | belegt | rate | voll.
--   rd_sitzung_beenden(...)    setzt ende_at und grund genau einmal; vom Benutzer nur für die eigene Sitzung (p_profil).
-- Beide laufen mit den Rechten des Aufrufers (SECURITY INVOKER), also nur mit dem, was wamo_core auf die Tabelle darf: lesen,
-- eine Zeile anlegen (vier Spalten), ende_at und grund setzen. Kein delete, kein Update anderer Spalten; ein Trigger lässt ein
-- einmal gesetztes Ende nicht mehr ändern (das Protokoll ist nach dem Schreiben unveränderlich).
-- Grenze der Frische: p_aal2_zeit stammt vom Kern (wie bei 0014). Die Prüfung schützt vor einem ehrlichen Kern mit einem alten
-- Token, nicht vor einem übernommenen Kern; dagegen hilft der Verlauf in dieser Tabelle.
set local lock_timeout = '5s';

do $$ begin
  if not exists (select 1 from __SCHEMA__.schema_migrations where version = '0001_wamo_init') then
    raise exception '0015 braucht 0001';
  end if;
end $$;

-- ---------- Tabelle ----------
create table if not exists __SCHEMA__.rd_sitzungen (
  id        uuid primary key default gen_random_uuid(),
  profil_id uuid not null references __SCHEMA__.profiles(user_id) on delete restrict,  -- Protokoll bleibt, Profile nur sperren
  ziel      text not null,
  start_at  timestamptz not null default now(),
  bis_at    timestamptz not null,   -- hartes Höchstende (Start + Dauer), das der Kern und das Gateway durchsetzen
  aal2_at   timestamptz not null,   -- Zeitpunkt des Codes des zweiten Faktors, mit dem die Sitzung gestartet wurde
  ende_at   timestamptz,
  grund     text
);

-- Tabelle aus einem früheren Lauf: Fremdschlüssel auf restrict umstellen (das Protokoll bleibt, wenn ein Profil verschwindet)
alter table __SCHEMA__.rd_sitzungen drop constraint if exists rd_sitzungen_profil_id_fkey;
alter table __SCHEMA__.rd_sitzungen add constraint rd_sitzungen_profil_id_fkey
  foreign key (profil_id) references __SCHEMA__.profiles(user_id) on delete restrict;

alter table __SCHEMA__.rd_sitzungen drop constraint if exists rd_sitzungen_ziel_form;
alter table __SCHEMA__.rd_sitzungen add constraint rd_sitzungen_ziel_form check (ziel ~ '^[a-z0-9]{1,16}$');
alter table __SCHEMA__.rd_sitzungen drop constraint if exists rd_sitzungen_dauer;
alter table __SCHEMA__.rd_sitzungen add constraint rd_sitzungen_dauer
  check (bis_at > start_at and bis_at <= start_at + interval '4 hours' and aal2_at <= start_at + interval '5 seconds');
alter table __SCHEMA__.rd_sitzungen drop constraint if exists rd_sitzungen_ende;
alter table __SCHEMA__.rd_sitzungen add constraint rd_sitzungen_ende
  check ((ende_at is null) = (grund is null) and (ende_at is null or ende_at >= start_at));
alter table __SCHEMA__.rd_sitzungen drop constraint if exists rd_sitzungen_grund;
alter table __SCHEMA__.rd_sitzungen add constraint rd_sitzungen_grund
  check (grund is null or grund in ('beendet', 'abgeloest', 'zeitlimit', 'getrennt', 'nicht_geoeffnet', 'profil_inaktiv',
                                    'kern_neustart', 'fehler'));
comment on table __SCHEMA__.rd_sitzungen is
  'Fernsitzungen (Remote Desktop): wer, welches Ziel, Start, Höchstende, Ende, Grund. Keine Inhalte, keine Zugangsdaten. Kein delete.';

-- Ein Zuschauer je Ziel: höchstens eine offene Zeile je Ziel
create unique index if not exists rd_sitzungen_ziel_offen_idx on __SCHEMA__.rd_sitzungen (ziel) where ende_at is null;
-- Fremdschlüssel profil_id (Starts je Stunde, Verlauf je Profil)
create index if not exists rd_sitzungen_profil_idx on __SCHEMA__.rd_sitzungen (profil_id, start_at desc);

-- ---------- Das Ende ist endgültig ----------
create or replace function __SCHEMA__.rd_sitzungen_guard() returns trigger
language plpgsql set search_path = '' as $$
begin
  if old.ende_at is not null then
    raise exception 'rd_sitzungen: ein beendetes Protokoll ist unveränderlich' using errcode = '23514';
  end if;
  if new.id <> old.id or new.profil_id <> old.profil_id or new.ziel <> old.ziel or new.start_at <> old.start_at
     or new.bis_at <> old.bis_at or new.aal2_at <> old.aal2_at then
    raise exception 'rd_sitzungen: nur ende_at und grund dürfen sich ändern' using errcode = '23514';
  end if;
  return new;
end $$;
drop trigger if exists rd_sitzungen_guard on __SCHEMA__.rd_sitzungen;
create trigger rd_sitzungen_guard before update on __SCHEMA__.rd_sitzungen
  for each row execute function __SCHEMA__.rd_sitzungen_guard();

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

-- ---------- Sitzung beenden ----------
-- p_profil gesetzt: der Benutzer beendet seine eigene Sitzung (fremde: 'fremd'). p_profil null: der Kern beendet (Zeitlimit,
-- getrennt, Profil nicht mehr aktiv). Ergebnis: ok | schon (war schon beendet) | unbekannt | fremd.
create or replace function __SCHEMA__.rd_sitzung_beenden(p_sitzung uuid, p_grund text, p_profil uuid default null)
returns text
language plpgsql set search_path = '' as $$
declare
  v __SCHEMA__.rd_sitzungen%rowtype;
begin
  if p_sitzung is null or p_grund is null then
    raise exception 'rd_sitzung_beenden: Angaben fehlen' using errcode = '22004';
  end if;
  if p_grund not in ('beendet', 'abgeloest', 'zeitlimit', 'getrennt', 'nicht_geoeffnet', 'profil_inaktiv', 'kern_neustart',
                     'fehler') then
    raise exception 'rd_sitzung_beenden: unbekannter Grund' using errcode = '22023';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('wamo.rd_sitzungen', 0));
  select * into v from __SCHEMA__.rd_sitzungen s where s.id = p_sitzung for update;
  if not found then return 'unbekannt'; end if;
  if p_profil is not null and v.profil_id <> p_profil then return 'fremd'; end if;
  if v.ende_at is not null then return 'schon'; end if;
  update __SCHEMA__.rd_sitzungen s set ende_at = now(), grund = p_grund where s.id = p_sitzung;
  return 'ok';
end $$;

-- ---------- Rechte (geringste Rechte) ----------
revoke all on __SCHEMA__.rd_sitzungen from public, anon, authenticated, service_role, wamo_core, wamo_admin;
-- Voice-Core: lesen, eine Zeile mit vier Angaben anlegen (id und start_at füllt die Datenbank), Ende und Grund setzen.
-- Kein delete (das Protokoll bleibt), kein Update der übrigen Spalten.
grant select on __SCHEMA__.rd_sitzungen to wamo_core;
grant insert (profil_id, ziel, bis_at, aal2_at) on __SCHEMA__.rd_sitzungen to wamo_core;
grant update (ende_at, grund) on __SCHEMA__.rd_sitzungen to wamo_core;
revoke all on function __SCHEMA__.rd_sitzung_beginnen(uuid, text, integer, timestamptz, integer, integer),
                       __SCHEMA__.rd_sitzung_beenden(uuid, text, uuid), __SCHEMA__.rd_sitzungen_guard()
  from public, anon, authenticated, service_role, wamo_admin;
grant execute on function __SCHEMA__.rd_sitzung_beginnen(uuid, text, integer, timestamptz, integer, integer) to wamo_core;
grant execute on function __SCHEMA__.rd_sitzung_beenden(uuid, text, uuid) to wamo_core;

-- ---------- Row Level Security ----------
alter table __SCHEMA__.rd_sitzungen enable row level security;
drop policy if exists core_all on __SCHEMA__.rd_sitzungen;
create policy core_all on __SCHEMA__.rd_sitzungen for all to wamo_core using (true) with check (true);

-- Selbsttest: bricht die Transaktion bei Rechtedrift ab.
do $$ begin
  if not has_table_privilege('wamo_core', '__SCHEMA__.rd_sitzungen', 'SELECT')
     or has_table_privilege('wamo_core', '__SCHEMA__.rd_sitzungen', 'INSERT,UPDATE,DELETE,TRUNCATE')
     or not has_column_privilege('wamo_core', '__SCHEMA__.rd_sitzungen', 'profil_id', 'INSERT')
     or not has_column_privilege('wamo_core', '__SCHEMA__.rd_sitzungen', 'ziel', 'INSERT')
     or not has_column_privilege('wamo_core', '__SCHEMA__.rd_sitzungen', 'bis_at', 'INSERT')
     or not has_column_privilege('wamo_core', '__SCHEMA__.rd_sitzungen', 'aal2_at', 'INSERT')
     or has_column_privilege('wamo_core', '__SCHEMA__.rd_sitzungen', 'id', 'INSERT,UPDATE')
     or has_column_privilege('wamo_core', '__SCHEMA__.rd_sitzungen', 'start_at', 'INSERT,UPDATE')
     or has_column_privilege('wamo_core', '__SCHEMA__.rd_sitzungen', 'ende_at', 'INSERT')
     or has_column_privilege('wamo_core', '__SCHEMA__.rd_sitzungen', 'grund', 'INSERT')
     or not has_column_privilege('wamo_core', '__SCHEMA__.rd_sitzungen', 'ende_at', 'UPDATE')
     or not has_column_privilege('wamo_core', '__SCHEMA__.rd_sitzungen', 'grund', 'UPDATE')
     or has_column_privilege('wamo_core', '__SCHEMA__.rd_sitzungen', 'profil_id', 'UPDATE')
     or has_column_privilege('wamo_core', '__SCHEMA__.rd_sitzungen', 'ziel', 'UPDATE')
     or has_column_privilege('wamo_core', '__SCHEMA__.rd_sitzungen', 'bis_at', 'UPDATE')
     or has_any_column_privilege('wamo_admin', '__SCHEMA__.rd_sitzungen', 'SELECT,INSERT,UPDATE')
     or has_any_column_privilege('anon', '__SCHEMA__.rd_sitzungen', 'SELECT,INSERT,UPDATE')
     or has_any_column_privilege('authenticated', '__SCHEMA__.rd_sitzungen', 'SELECT,INSERT,UPDATE')
     or has_any_column_privilege('service_role', '__SCHEMA__.rd_sitzungen', 'SELECT,INSERT,UPDATE')
     or not has_function_privilege('wamo_core', '__SCHEMA__.rd_sitzung_beginnen(uuid, text, integer, timestamptz, integer, integer)', 'EXECUTE')
     or not has_function_privilege('wamo_core', '__SCHEMA__.rd_sitzung_beenden(uuid, text, uuid)', 'EXECUTE')
     or has_function_privilege('wamo_admin', '__SCHEMA__.rd_sitzung_beginnen(uuid, text, integer, timestamptz, integer, integer)', 'EXECUTE')
     or has_function_privilege('anon', '__SCHEMA__.rd_sitzung_beginnen(uuid, text, integer, timestamptz, integer, integer)', 'EXECUTE')
     or has_function_privilege('authenticated', '__SCHEMA__.rd_sitzung_beginnen(uuid, text, integer, timestamptz, integer, integer)', 'EXECUTE')
     or has_function_privilege('service_role', '__SCHEMA__.rd_sitzung_beginnen(uuid, text, integer, timestamptz, integer, integer)', 'EXECUTE')
     or has_function_privilege('anon', '__SCHEMA__.rd_sitzung_beenden(uuid, text, uuid)', 'EXECUTE')
     or has_function_privilege('authenticated', '__SCHEMA__.rd_sitzung_beenden(uuid, text, uuid)', 'EXECUTE')
     or has_function_privilege('service_role', '__SCHEMA__.rd_sitzung_beenden(uuid, text, uuid)', 'EXECUTE')
     or not (select c.relrowsecurity from pg_class c where c.oid = '__SCHEMA__.rd_sitzungen'::regclass)
     or not exists (select 1 from pg_policies where schemaname = '__SCHEMA__' and tablename = 'rd_sitzungen'
                      and policyname = 'core_all')
     or (select count(*) from pg_policies where schemaname = '__SCHEMA__' and tablename = 'rd_sitzungen') <> 1
     or not exists (select 1 from pg_indexes where schemaname = '__SCHEMA__' and tablename = 'rd_sitzungen'
                      and indexname = 'rd_sitzungen_ziel_offen_idx')
  then
    raise exception 'rd_sitzungen: unzulässige Rechte oder RLS fehlt (Tabelle, Funktionen, Policy core_all, Index prüfen)'
      using errcode = '42501';
  end if;
end $$;

insert into __SCHEMA__.schema_migrations (version) values ('0015_rd_sitzungen') on conflict do nothing;
