-- WAMO 0018: Laborprofile für das Testlabor (Spalten labor_art, labor_platz, labor_aktiv) und die Löschfunktion labor_loeschen.
-- __SCHEMA__ wird ersetzt durch wamo_dev | wamo_test | wamo_prod.
-- Anwenden (je Schema, in EINER Transaktion, ein zweiter Lauf ändert nichts):
--   bash scripts/render-migration.sh <schema> supabase/migrations/0018_labor_profile.sql \
--     | docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 --single-transaction
-- Rückweg: 0018_labor_profile.down.sql. Bricht bei JEDEM Profil mit labor_art ab, auch bei gesperrten.
-- Reihenfolge der Anwendung: wamo_dev, wamo_test, dann das Schema des Büro-Kerns, erst nach database-reviewer und nach Nikos Ja.
-- Reihenfolge mit dem Kern: Diese Migration VOR dem Release des Kerns mit Laborlogik anwenden. Der neue Kern liest labor_art,
--   labor_platz und labor_aktiv bei jedem Profil; fehlt die Spalte (Kern vor der Migration oder Rückweg bei laufendem neuem Kern),
--   antwortet er für ALLE Nutzer mit 503. Das ist gewollt: lieber nichts als ein Laborprofil ohne Sperre. Der Rückweg gilt erst nach
--   der Rückstellung des Kerns auf einen Stand ohne Laborlogik.
-- Hängt ab von 0001 (profiles, sessions, turns, ...), 0002 (devices, hinweise), 0003 (dateien, teilungen), 0010 und 0017.
--
-- Was hier entsteht (Plan docs/testlabor-wamo.md, Abschnitt 3.1):
--   * profiles.labor_art    platz oder schaufenster, sonst null. Ein Profil mit labor_art ist ein Laborprofil.
--   * profiles.labor_platz  1 bis 16, nur bei platz, eindeutig. Das Schau-Profil hat keinen Platz.
--   * profiles.labor_aktiv  die Freischaltung. Nur der Kern mit Laborlogik liest sie. Standard falsch.
--   * __SCHEMA__.labor_loeschen(p_profil, p_ab, p_bis): löscht den gesamten Text eines Laborprofils (Liste siehe
--     voice-core/labor_loeschliste.py), optional nur das Zeitfenster eines Durchlaufs. Nur für wamo_admin ausführbar.
--   * __SCHEMA__.labor_zaehlen(p_profil): zählt je Tabelle die Zeilen eines Laborprofils (Nachweis "nichts mehr da").
-- Es entsteht keine neue Tabelle und keine neue Rolle.
--
-- Befund 1 (Sicherheitsreview): Ein Laborprofil bleibt DAUERHAFT aktiv = false. Ältere Kerne kennen labor_aktiv nicht und lehnen
-- jedes Profil mit aktiv = false mit 403 ab. Ein Rückweg auf einen Kern ohne Laborlogik macht ein Laborprofil deshalb nie zum
-- Mitglied mit gemeinsamem Gedächtnis. Die Freischaltung läuft allein über labor_aktiv (scripts/wamo_labor.py aktivieren).
-- Befund 2: Auch das Schau-Profil hat die Rolle mitglied. Es zählt damit nie als Admin der Sonderfreigabe (0013).
-- Befund 12: Die Spalten schreibt keine Rolle außer wamo_admin: wamo_core, anon, authenticated, service_role und PUBLIC
-- haben weder INSERT noch UPDATE darauf (Selbsttest am Ende, für beide Anweisungen). wamo_admin darf sie beim Anlegen setzen
-- und danach nur labor_aktiv ändern. labor_art und labor_platz sind nach dem Anlegen für jede Rolle unveränderlich (Trigger
-- profiles_labor_guard), damit kein Laborprofil durch Zurücksetzen von labor_art zum gewöhnlichen Profil wird.
-- Entscheidung für den database-reviewer: Spaltenrecht plus Trigger statt eines Superuser-Wegs, damit scripts/wamo_labor.py
-- ausschließlich als Verwaltungsrolle schreibt: Anmeldung als wamo_admin mit WAMO_ADMIN_DATABASE_URL, kein SET ROLE (postgres ist im
-- Supabase-Image kein Superuser und darf keine Rolle annehmen) und nie mit Superuser-Rechten.
--
-- Löschfunktion: security definer mit leerem search_path, Besitzer ist die Rolle, die die Migration ausführt. Sie wirft bei einem
-- Profil ohne labor_art (42501), bei labor_aktiv = true, in der Sperrfrist und bei einer laufenden Sitzung (jeweils 55000) und bei
-- verkehrtem Fenster (22023).
-- Sitzung: Eine Sitzung ohne ended_at, die älter als 135 Minuten ist, gilt als abgestürzt. Kopplung an den Kern: bot.py begrenzt eine
-- Sitzung auf WAMO_MAX_SITZUNG_MIN (Standard 120, per Umgebung änderbar). Wer das über den Standard hinaus erhöht, muss dieses
-- Intervall UND SITZUNG_MAX_MINUTEN in voice-core/labor_loeschliste.py anpassen (ein Test sichert Standard plus 10 Minuten ab).
-- Sperrfrist: Der Kern merkt sich ein aktives Profil bis zu 60 s (auth_gate.py, PROFILE_HIT_SECS) und prüft laufende Sitzungen
-- minütlich (bot.py, PROFILE_RECHECK_SECS). Nach labor_aktiv = false kann deshalb noch rund 2 Minuten eine Sitzung beginnen und nach
-- der Löschung neuen Text schreiben. Die Funktion lehnt darum ab, solange profiles.updated_at (profiles_touch setzt es bei jedem
-- Update, 0001) weniger als 3 Minuten zurückliegt, und prüft die Sitzungen vor dem Ergebnis noch einmal: Begann eine Sitzung
-- während der Löschung, macht der Fehler die ganze Löschung rückgängig. scripts/wamo_labor.py wartet die Sperrfrist ab (Meldung
-- "Sperrfrist, noch etwa N Sekunden") und ruft die Funktion erneut auf.
-- Die Zeile des Profils wird für die Dauer der Löschung mit for no key update gesperrt: labor_aktiv kann sich währenddessen nicht
-- ändern, Fremdschlüsselprüfungen des Kerns auf dieses Profil werden aber nicht blockiert (for update würde sie blockieren).
-- Mit p_ab oder p_bis löscht sie nur Zeilen im Zeitfenster (Zeitspalte je Tabelle) sowie alle Zeilen der Sitzungen, die im Fenster
-- begannen (bei feedback über die Turns dieser Sitzungen). Ohne Angabe löscht sie alles des Profils. Das Profil selbst bleibt
-- (Konten löscht die Verwaltung nach dem Labortag).
-- Nicht gelöscht werden Verweise auf ein Profil, die keinen Besitz bedeuten (zum Beispiel Prüfer oder Bearbeiter), und die
-- Protokolltabellen der Selbständerung; die Begründung steht je Eintrag in voice-core/labor_loeschliste.py (AUSNAHMEN).
-- Ebenfalls nicht Teil der Löschung (Spuren außerhalb der Löschfunktion, Begründung in labor_loeschliste.py: AUSSERHALB, AUSNAHMEN):
--   * auth.audit_log_entries (Anmeldeprotokoll mit IP-Adresse und E-Mail) und auth.sessions (Anmeldesitzungen mit IP-Adresse und
--     User-Agent) liegen im Schema auth von GoTrue. Sie verschwinden mit dem Löschen der Laborkonten am 30.11.2026 (Plan Q11).
--   * model_access kann einen verschlüsselten Claude-Schlüssel tragen. Für Laborprofile ist das Hinterlegen gesperrt (labor.py hat
--     weder die Client-Nachricht modellzugang noch den Pfad /wamo/api/modellzugang in der Erlaubnisliste); eine vorhandene Zeile fällt
--     mit dem Konto weg (on delete cascade, ebenfalls am 30.11.2026).
set local lock_timeout = '5s';

do $$ begin
  if (select count(*) from __SCHEMA__.schema_migrations
       where version in ('0001_wamo_init', '0002_wamo_geraete', '0003_wamo_teilen', '0010_modellzugang', '0017_vorgaenge')) <> 5 then
    raise exception '0018 braucht 0001, 0002, 0003, 0010 und 0017';
  end if;
end $$;

-- ---------- Spalten ----------
alter table __SCHEMA__.profiles
  add column if not exists labor_art   text,
  add column if not exists labor_platz smallint,
  add column if not exists labor_aktiv boolean not null default false;
comment on column __SCHEMA__.profiles.labor_art is
  'Laborprofil (Testlabor): platz oder schaufenster, sonst null. Nach dem Anlegen unveränderlich (Trigger profiles_labor_guard).';
comment on column __SCHEMA__.profiles.labor_platz is 'Platz 1 bis 16 eines Laborprofils der Art platz, eindeutig. Unveränderlich.';
comment on column __SCHEMA__.profiles.labor_aktiv is
  'Freischaltung eines Laborprofils. Nur der Kern mit Laborlogik liest sie; aktiv bleibt bei Laborprofilen dauerhaft falsch.';

-- ---------- Prüfungen (drop davor: ein zweiter Lauf bricht nicht ab) ----------
alter table __SCHEMA__.profiles drop constraint if exists profiles_labor_art_chk;
alter table __SCHEMA__.profiles add constraint profiles_labor_art_chk
  check (labor_art is null or labor_art in ('platz', 'schaufenster'));
alter table __SCHEMA__.profiles drop constraint if exists profiles_labor_platz_chk;
alter table __SCHEMA__.profiles add constraint profiles_labor_platz_chk
  check (labor_platz is null or labor_platz between 1 and 16);
-- Form: ohne labor_art weder Platz noch Freischaltung; platz verlangt einen Platz; schaufenster hat keinen.
-- Als case geschrieben, damit nie null herauskommt (ein null bestünde die Prüfung).
alter table __SCHEMA__.profiles drop constraint if exists profiles_labor_form_chk;
alter table __SCHEMA__.profiles add constraint profiles_labor_form_chk
  check (case when labor_art is null then (labor_platz is null and not labor_aktiv)
              when labor_art = 'platz' then labor_platz is not null
              else labor_platz is null end);
-- Befund 1 und 2: Laborprofil heißt immer Mitglied und dauerhaft aktiv = false.
alter table __SCHEMA__.profiles drop constraint if exists profiles_labor_streng_chk;
alter table __SCHEMA__.profiles add constraint profiles_labor_streng_chk
  check (labor_art is null or (rolle = 'mitglied' and not aktiv));

-- Ein Platz gehört höchstens einem Profil
create unique index if not exists profiles_labor_platz_uni on __SCHEMA__.profiles (labor_platz) where labor_platz is not null;

-- ---------- Trigger: labor_art und labor_platz sind nach dem Anlegen unveränderlich ----------
create or replace function __SCHEMA__.profiles_labor_guard() returns trigger
language plpgsql set search_path = '' as $$
begin
  if tg_op = 'INSERT' then
    -- ein Laborprofil entsteht nie freigeschaltet: aktivieren ist ein eigener Schritt (wamo_labor.py aktivieren)
    if new.labor_art is not null and new.labor_aktiv then
      raise exception 'profiles: ein Laborprofil entsteht mit labor_aktiv = false' using errcode = '42501';
    end if;
    return new;
  end if;
  if new.labor_art is distinct from old.labor_art or new.labor_platz is distinct from old.labor_platz then
    raise exception 'profiles: labor_art und labor_platz sind nach dem Anlegen unveränderlich' using errcode = '42501';
  end if;
  return new;
end $$;
drop trigger if exists profiles_labor_guard on __SCHEMA__.profiles;
create trigger profiles_labor_guard before insert or update on __SCHEMA__.profiles
  for each row execute function __SCHEMA__.profiles_labor_guard();

-- ---------- Zählen: Nachweis "nichts mehr da" ----------
create or replace function __SCHEMA__.labor_zaehlen(p_profil uuid) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  v_art text;
begin
  if p_profil is null then
    raise exception 'labor_zaehlen: p_profil fehlt' using errcode = '22004';
  end if;
  select p.labor_art into v_art from __SCHEMA__.profiles p where p.user_id = p_profil;
  if v_art is null then
    raise exception 'labor_zaehlen: kein Laborprofil' using errcode = '42501';
  end if;
  return jsonb_build_object(
    'feedback',       (select count(*) from __SCHEMA__.feedback x where x.user_id = p_profil),
    'memories',       (select count(*) from __SCHEMA__.memories x where x.user_id = p_profil),
    'speakers',       (select count(*) from __SCHEMA__.speakers x where x.user_id = p_profil),
    'teilung_events', (select count(*) from __SCHEMA__.teilung_events x
                        where x.besitzer = p_profil or x.empfaenger = p_profil or x.durch = p_profil),
    'teilungen',      (select count(*) from __SCHEMA__.teilungen x where x.besitzer = p_profil or x.empfaenger = p_profil),
    'hinweise',       (select count(*) from __SCHEMA__.hinweise x where x.empfaenger = p_profil),
    'canvas_items',   (select count(*) from __SCHEMA__.canvas_items x where x.user_id = p_profil),
    'actions',        (select count(*) from __SCHEMA__.actions x where x.user_id = p_profil),
    'turns',          (select count(*) from __SCHEMA__.turns x where x.user_id = p_profil),
    'vorgaenge',      (select count(*) from __SCHEMA__.vorgaenge x where x.user_id = p_profil),
    'dateien',        (select count(*) from __SCHEMA__.dateien x where x.user_id = p_profil),
    'device_events',  (select count(*) from __SCHEMA__.device_events x
                        where x.durch = p_profil
                           or x.device_id in (select d.id from __SCHEMA__.devices d where d.user_id = p_profil)),
    'devices',        (select count(*) from __SCHEMA__.devices x where x.user_id = p_profil),
    'sessions',       (select count(*) from __SCHEMA__.sessions x where x.user_id = p_profil)
  );
end $$;

-- ---------- Löschen ----------
create or replace function __SCHEMA__.labor_loeschen(
  p_profil uuid, p_ab timestamptz default null, p_bis timestamptz default null
) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  v_art       text;
  v_aktiv     boolean;
  v_geaendert timestamptz;
  v_voll      boolean := (p_ab is null and p_bis is null);
  v_ab        timestamptz := coalesce(p_ab, '-infinity'::timestamptz);
  v_bis       timestamptz := coalesce(p_bis, 'infinity'::timestamptz);
  v_sitzungen uuid[];
  v_geraete   uuid[];
  v_n         bigint;
  v_ergebnis  jsonb := '{}'::jsonb;
begin
  if p_profil is null then
    raise exception 'labor_loeschen: p_profil fehlt' using errcode = '22004';
  end if;
  if p_ab is not null and p_bis is not null and p_ab > p_bis then
    raise exception 'labor_loeschen: p_ab liegt nach p_bis' using errcode = '22023';
  end if;
  -- Zeile sperren: labor_aktiv kann sich bis zum Ende dieser Transaktion nicht ändern. for no key update reicht dafür und blockiert
  -- keine Fremdschlüsselprüfung des Kerns auf dieses Profil.
  select p.labor_art, p.labor_aktiv, p.updated_at into v_art, v_aktiv, v_geaendert
    from __SCHEMA__.profiles p where p.user_id = p_profil for no key update;
  if v_art is null then
    raise exception 'labor_loeschen: kein Laborprofil' using errcode = '42501';
  end if;
  if v_aktiv then
    raise exception 'labor_loeschen: das Laborprofil ist noch freigeschaltet (labor_aktiv), erst sperren' using errcode = '55000';
  end if;
  -- Sperrfrist: bis zu 2 Minuten nach der Änderung kann der Kern noch eine Sitzung beginnen (Zwischenspeicher, minütliche Prüfung)
  if v_geaendert > clock_timestamp() - interval '3 minutes' then
    raise exception 'labor_loeschen: Sperrfrist, noch etwa % Sekunden: das Profil wurde vor weniger als 3 Minuten geändert, der Kern kann noch eine Sitzung beginnen',
      ceil(extract(epoch from (v_geaendert + interval '3 minutes' - clock_timestamp()))) using errcode = '55000';
  end if;
  if exists (select 1 from __SCHEMA__.sessions s
              where s.user_id = p_profil and s.ended_at is null and s.started_at > clock_timestamp() - interval '135 minutes') then
    raise exception 'labor_loeschen: es läuft noch eine Sitzung dieses Profils' using errcode = '55000';
  end if;

  select coalesce(array_agg(s.id), '{}'::uuid[]) into v_sitzungen from __SCHEMA__.sessions s
   where s.user_id = p_profil and (v_voll or (s.started_at >= v_ab and s.started_at <= v_bis));
  select coalesce(array_agg(d.id), '{}'::uuid[]) into v_geraete from __SCHEMA__.devices d
   where d.user_id = p_profil and (v_voll or (d.created_at >= v_ab and d.created_at <= v_bis));

  -- feedback hat keinen Sitzungsbezug, nur turn_id: Rückmeldungen zu den Turns der Sitzungen im Fenster gehen auch dann mit,
  -- wenn sie erst nach dem Fenster entstanden
  delete from __SCHEMA__.feedback x
   where x.user_id = p_profil and (v_voll or (x.created_at >= v_ab and x.created_at <= v_bis)
                                   or x.turn_id in (select t.id from __SCHEMA__.turns t where t.session_id = any(v_sitzungen)));
  get diagnostics v_n = row_count;
  v_ergebnis := v_ergebnis || jsonb_build_object('feedback', v_n);

  delete from __SCHEMA__.memories x
   where x.user_id = p_profil and (v_voll or (x.created_at >= v_ab and x.created_at <= v_bis)
                                   or x.source_session_id = any(v_sitzungen));
  get diagnostics v_n = row_count;
  v_ergebnis := v_ergebnis || jsonb_build_object('memories', v_n);

  delete from __SCHEMA__.speakers x
   where x.user_id = p_profil and (v_voll or (x.enrolled_at >= v_ab and x.enrolled_at <= v_bis));
  get diagnostics v_n = row_count;
  v_ergebnis := v_ergebnis || jsonb_build_object('speakers', v_n);

  delete from __SCHEMA__.teilung_events x
   where (x.besitzer = p_profil or x.empfaenger = p_profil or x.durch = p_profil)
     and (v_voll or (x.at >= v_ab and x.at <= v_bis));
  get diagnostics v_n = row_count;
  v_ergebnis := v_ergebnis || jsonb_build_object('teilung_events', v_n);

  delete from __SCHEMA__.teilungen x
   where (x.besitzer = p_profil or x.empfaenger = p_profil)
     and (v_voll or (x.erstellt_at >= v_ab and x.erstellt_at <= v_bis));
  get diagnostics v_n = row_count;
  v_ergebnis := v_ergebnis || jsonb_build_object('teilungen', v_n);

  delete from __SCHEMA__.hinweise x
   where x.empfaenger = p_profil and (v_voll or (x.erstellt_at >= v_ab and x.erstellt_at <= v_bis));
  get diagnostics v_n = row_count;
  v_ergebnis := v_ergebnis || jsonb_build_object('hinweise', v_n);

  delete from __SCHEMA__.canvas_items x
   where x.user_id = p_profil and (v_voll or (x.created_at >= v_ab and x.created_at <= v_bis)
                                   or x.session_id = any(v_sitzungen));
  get diagnostics v_n = row_count;
  v_ergebnis := v_ergebnis || jsonb_build_object('canvas_items', v_n);

  delete from __SCHEMA__.actions x
   where x.user_id = p_profil and (v_voll or (x.created_at >= v_ab and x.created_at <= v_bis)
                                   or x.session_id = any(v_sitzungen));
  get diagnostics v_n = row_count;
  v_ergebnis := v_ergebnis || jsonb_build_object('actions', v_n);

  delete from __SCHEMA__.turns x
   where x.user_id = p_profil and (v_voll or (x.created_at >= v_ab and x.created_at <= v_bis)
                                   or x.session_id = any(v_sitzungen));
  get diagnostics v_n = row_count;
  v_ergebnis := v_ergebnis || jsonb_build_object('turns', v_n);

  delete from __SCHEMA__.vorgaenge x
   where x.user_id = p_profil and (v_voll or (x.gestartet_at >= v_ab and x.gestartet_at <= v_bis)
                                   or x.session_id = any(v_sitzungen));
  get diagnostics v_n = row_count;
  v_ergebnis := v_ergebnis || jsonb_build_object('vorgaenge', v_n);

  delete from __SCHEMA__.dateien x
   where x.user_id = p_profil and (v_voll or (x.erstellt_at >= v_ab and x.erstellt_at <= v_bis));
  get diagnostics v_n = row_count;
  v_ergebnis := v_ergebnis || jsonb_build_object('dateien', v_n);

  delete from __SCHEMA__.device_events x
   where (x.durch = p_profil or x.device_id = any(v_geraete))
     and (v_voll or (x.at >= v_ab and x.at <= v_bis) or x.device_id = any(v_geraete));
  get diagnostics v_n = row_count;
  v_ergebnis := v_ergebnis || jsonb_build_object('device_events', v_n);

  delete from __SCHEMA__.devices x
   where x.user_id = p_profil and (v_voll or (x.created_at >= v_ab and x.created_at <= v_bis));
  get diagnostics v_n = row_count;
  v_ergebnis := v_ergebnis || jsonb_build_object('devices', v_n);

  delete from __SCHEMA__.sessions x
   where x.user_id = p_profil and (v_voll or x.id = any(v_sitzungen));
  get diagnostics v_n = row_count;
  v_ergebnis := v_ergebnis || jsonb_build_object('sessions', v_n);

  -- Sitzungsprüfung wiederholen: Begann während der Löschung noch eine Sitzung (Kern mit zwischengespeichertem Profil), bricht der
  -- Fehler ab und die gesamte Löschung wird mit der Transaktion zurückgenommen
  if exists (select 1 from __SCHEMA__.sessions s
              where s.user_id = p_profil and s.ended_at is null and s.started_at > clock_timestamp() - interval '135 minutes') then
    raise exception 'labor_loeschen: es läuft noch eine Sitzung dieses Profils' using errcode = '55000';
  end if;

  return v_ergebnis;
end $$;

-- ---------- Rechte (geringste Rechte) ----------
-- Funktionen: neu angelegte Funktionen sind für PUBLIC ausführbar, das wird zuerst entzogen. Nur wamo_admin führt sie aus.
revoke all on function __SCHEMA__.profiles_labor_guard() from public, anon, authenticated, service_role, wamo_core, wamo_admin;
revoke all on function __SCHEMA__.labor_loeschen(uuid, timestamptz, timestamptz)
  from public, anon, authenticated, service_role, wamo_core, wamo_admin;
revoke all on function __SCHEMA__.labor_zaehlen(uuid) from public, anon, authenticated, service_role, wamo_core, wamo_admin;
grant execute on function __SCHEMA__.labor_loeschen(uuid, timestamptz, timestamptz) to wamo_admin;
grant execute on function __SCHEMA__.labor_zaehlen(uuid) to wamo_admin;

-- Spalten: wamo_core liest über das Tabellenrecht aus 0001 (select) und schreibt sie nie. wamo_admin legt beim Anlegen an
-- (Tabellenrecht insert aus 0001) und ändert danach nur labor_aktiv. Alles andere wird zuerst entzogen, damit ein Nachlauf nach
-- geänderten Rechten wieder zum selben Stand führt.
revoke insert (labor_art, labor_platz, labor_aktiv), update (labor_art, labor_platz, labor_aktiv)
  on __SCHEMA__.profiles from public, anon, authenticated, service_role, wamo_core;
revoke update (labor_art, labor_platz) on __SCHEMA__.profiles from wamo_admin;
grant update (labor_aktiv) on __SCHEMA__.profiles to wamo_admin;

-- Selbsttest: bricht die Transaktion bei Rechtedrift ab, für UPDATE und für INSERT (Befund 12). Geprüft werden ALLE Rollen aus
-- pg_roles außer Superusern, wamo_admin, dem Tabellenbesitzer und den vordefinierten pg_-Rollen (keine feste Liste: eine später
-- angelegte Rolle fällt ebenso auf). Meldet der Test eine Rolle, die dort nichts verloren hat, nennt die Meldung ihren Namen.
do $$
declare
  r name;
  c text;
  v_geprueft text[] := '{}';
begin
  -- 1. PUBLIC: weder Tabellenrecht noch Spaltenrecht für INSERT oder UPDATE auf den Laborspalten
  if exists (select 1 from pg_class t, aclexplode(t.relacl) x
              where t.oid = '__SCHEMA__.profiles'::regclass and x.grantee = 0 and x.privilege_type in ('INSERT', 'UPDATE'))
     or exists (select 1 from pg_attribute a, aclexplode(a.attacl) x
                 where a.attrelid = '__SCHEMA__.profiles'::regclass
                   and a.attname in ('labor_art', 'labor_platz', 'labor_aktiv')
                   and x.grantee = 0 and x.privilege_type in ('INSERT', 'UPDATE')) then
    raise exception 'profiles (0018): PUBLIC darf Laborspalten schreiben' using errcode = '42501';
  end if;
  -- 2. Alle anderen Rollen: kein INSERT und kein UPDATE auf labor_art, labor_platz, labor_aktiv
  for r in select o.rolname from pg_roles o where not o.rolsuper and o.rolname <> 'wamo_admin' and o.rolname !~ '^pg_'
              and o.oid <> (select t.relowner from pg_class t where t.oid = '__SCHEMA__.profiles'::regclass)
            order by o.rolname loop
    v_geprueft := v_geprueft || r::text;
    foreach c in array array['labor_art', 'labor_platz', 'labor_aktiv'] loop
      if has_column_privilege(r, '__SCHEMA__.profiles', c, 'INSERT') or has_column_privilege(r, '__SCHEMA__.profiles', c, 'UPDATE') then
        raise exception 'profiles (0018): % darf % schreiben', r, c using errcode = '42501';
      end if;
    end loop;
  end loop;
  -- wamo_core darf nicht durchrutschen (zum Beispiel als Tabellenbesitzer ausgenommen)
  if not ('wamo_core' = any(v_geprueft)) then
    raise exception 'profiles (0018): der Selbsttest hat wamo_core nicht geprüft' using errcode = '42501';
  end if;
  -- 3. wamo_admin: INSERT auf alle drei (Anlegen), UPDATE nur auf labor_aktiv
  foreach c in array array['labor_art', 'labor_platz', 'labor_aktiv'] loop
    if not has_column_privilege('wamo_admin', '__SCHEMA__.profiles', c, 'INSERT') then
      raise exception 'profiles (0018): wamo_admin kann % beim Anlegen nicht setzen', c using errcode = '42501';
    end if;
  end loop;
  if has_column_privilege('wamo_admin', '__SCHEMA__.profiles', 'labor_art', 'UPDATE')
     or has_column_privilege('wamo_admin', '__SCHEMA__.profiles', 'labor_platz', 'UPDATE')
     or not has_column_privilege('wamo_admin', '__SCHEMA__.profiles', 'labor_aktiv', 'UPDATE') then
    raise exception 'profiles (0018): wamo_admin darf nur labor_aktiv ändern' using errcode = '42501';
  end if;
  -- 4. wamo_core liest (Tabellenrecht aus 0001), schreibt nichts auf profiles
  if not has_table_privilege('wamo_core', '__SCHEMA__.profiles', 'SELECT')
     or has_table_privilege('wamo_core', '__SCHEMA__.profiles', 'INSERT,UPDATE,DELETE,TRUNCATE') then
    raise exception 'profiles (0018): wamo_core darf nur lesen' using errcode = '42501';
  end if;
  -- 5. Prüfungen, Index und Trigger sind da
  if (select count(*) from pg_constraint where conrelid = '__SCHEMA__.profiles'::regclass
        and conname in ('profiles_labor_art_chk', 'profiles_labor_platz_chk', 'profiles_labor_form_chk',
                        'profiles_labor_streng_chk')) <> 4
     or not exists (select 1 from pg_indexes where schemaname = '__SCHEMA__' and tablename = 'profiles'
                      and indexname = 'profiles_labor_platz_uni' and indexdef like '%WHERE (labor_platz IS NOT NULL)%')
     or not exists (select 1 from pg_trigger where tgrelid = '__SCHEMA__.profiles'::regclass
                      and tgname = 'profiles_labor_guard' and not tgisinternal) then
    raise exception 'profiles (0018): Prüfungen, Index oder Trigger fehlen' using errcode = '42501';
  end if;
  -- 6. Funktionen: keine für PUBLIC (null in proacl wäre der Standard PUBLIC), nur wamo_admin führt sie aus
  if exists (select 1 from pg_proc p
              where p.pronamespace = '__SCHEMA__'::regnamespace
                and p.proname in ('labor_loeschen', 'labor_zaehlen', 'profiles_labor_guard')
                and (p.proacl is null or exists (select 1 from aclexplode(p.proacl) x where x.grantee = 0))) then
    raise exception 'profiles (0018): eine Laborfunktion ist für PUBLIC ausführbar' using errcode = '42501';
  end if;
  for r in select o.rolname from pg_roles o where not o.rolsuper and o.rolname <> 'wamo_admin' and o.rolname !~ '^pg_'
              and o.oid <> (select t.relowner from pg_class t where t.oid = '__SCHEMA__.profiles'::regclass)
            order by o.rolname loop
    if has_function_privilege(r, '__SCHEMA__.labor_loeschen(uuid, timestamptz, timestamptz)', 'EXECUTE')
       or has_function_privilege(r, '__SCHEMA__.labor_zaehlen(uuid)', 'EXECUTE') then
      raise exception 'profiles (0018): % darf labor_loeschen oder labor_zaehlen ausführen', r using errcode = '42501';
    end if;
  end loop;
  if not has_function_privilege('wamo_admin', '__SCHEMA__.labor_loeschen(uuid, timestamptz, timestamptz)', 'EXECUTE')
     or not has_function_privilege('wamo_admin', '__SCHEMA__.labor_zaehlen(uuid)', 'EXECUTE') then
    raise exception 'profiles (0018): wamo_admin braucht labor_loeschen und labor_zaehlen' using errcode = '42501';
  end if;
  -- 7. Die Löschfunktionen laufen mit den Rechten ihres Besitzers und mit leerem search_path
  if exists (select 1 from pg_proc p
              where p.pronamespace = '__SCHEMA__'::regnamespace and p.proname in ('labor_loeschen', 'labor_zaehlen')
                and (not p.prosecdef or p.proconfig is null or not ('search_path=""' = any(p.proconfig)))) then
    raise exception 'profiles (0018): labor_loeschen und labor_zaehlen brauchen security definer und search_path leer'
      using errcode = '42501';
  end if;
end $$;

insert into __SCHEMA__.schema_migrations (version) values ('0018_labor_profile') on conflict do nothing;
