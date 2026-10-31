-- WAMO 0014: Geräteverwaltung durch Admins (freigeben, sperren, entsperren, widerrufen) über die Oberfläche.
-- __SCHEMA__ wird ersetzt durch wamo_dev | wamo_test | wamo_prod.
-- Anwenden (je Schema, in EINER Transaktion, zweimal hintereinander fehlerfrei):
--   bash scripts/render-migration.sh <schema> supabase/migrations/0014_geraete_verwaltung.sql \
--     | docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 --single-transaction
-- Rückweg: 0014_geraete_verwaltung.down.sql. Hängt nur von 0002 ab.
--
-- Was bisher fehlte: Ein neues Gerät eines Admins (und jeder Gerätewechsel) wartet im Status "beantragt" auf einen Admin,
-- aber es gab keinen Weg dorthin außer SQL. Die Datenbank erlaubt den Wechsel schon sauber (Trigger devices_guard aus 0002:
-- nur ein aktiver Admin als geaendert_von und freigegeben_von, Verlauf in device_events, Hinweis an den Besitzer).
-- Diese Migration gibt dem Kern genau diesen Weg, und nur diesen.
--
-- Entwurf: wamo_core bekommt KEIN Update-Recht auf devices (auch keine Spaltenrechte auf status, freigegeben_von,
-- geaendert_von oder gesperrt_grund). Ein Spaltenrecht auf status würde dem Kern erlauben, jeden zulässigen Statuswechsel
-- mit jedem beliebigen Admin als Akteur auszulösen, ohne Grund und ohne Prüfung des zweiten Faktors. Stattdessen:
--   geraet_entscheiden(...)           genau vier Aktionen mit festen Übergängen, Akteur muss aktiver Admin sein, Grund
--                                     Pflicht bei Sperren und Widerruf, zweiter Faktor höchstens 120 Sekunden alt,
--                                     Zeile gesperrt (for update), doppelte Aktion ist folgenlos ("schon")
--   geraete_verwaltung_liste(...)     die Liste für Admins ohne Schlüsselmaterial (ohne ssh_pubkey), samt Verlauf
-- Beide laufen als SECURITY DEFINER (Eigentümer postgres) und sind nur für wamo_core ausführbar. Die Datenbank kann den
-- Akteur nicht prüfen (der Kern arbeitet ohne JWT, wie bei geraet_binden in 0002): Anmeldung, Rolle und zweiten Faktor
-- prüft der Kern vorher; die Datenbank begrenzt, was ein kompromittierter Kern anrichten kann (nur diese Übergänge, nur
-- persönliche Geräte, nur mit einem aktiven Admin als Akteur, jede Änderung im Verlauf device_events).
-- Gemeinsame Geräte (Drucker, Scanner, ...) pflegt weiter wamo_admin.py; sie stehen nicht in dieser Verwaltung.
--
-- Regeln der Übergänge (die Trigger aus 0002 gelten zusätzlich und bleiben maßgeblich):
--   freigeben   beantragt -> aktiv          freigegeben_von = Admin
--   entsperren  gesperrt  -> aktiv          freigegeben_von = Admin
--   sperren     aktiv     -> gesperrt       Grund Pflicht (3 bis 200 Zeichen)
--   widerrufen  beantragt, aktiv, gesperrt -> widerrufen (endgültig, bei "beantragt" ist das die Ablehnung), Grund Pflicht
-- Ergebnis: ok | schon (Zielzustand war schon erreicht, nichts geändert) | nicht_moeglich (Übergang nicht erlaubt) |
--   unbekannt (Kennung gibt es nicht) | konflikt (das Profil hat schon ein anderes aktives Gerät) |
--   besitzer_inaktiv (das Besitzerprofil ist deaktiviert).
-- Grenze der Frische: p_aal2_zeit stammt vom Kern. Die Prüfung schützt vor einem ehrlichen Kern mit einem alten Token, nicht
-- vor einem übernommenen Kern (er setzt Akteur und Zeit selbst). Dagegen hilft nur der Hinweis an alle Admins bei jeder
-- Aktivierung (siehe unten) und der Verlauf in device_events. Die Frist gilt für ein Zeitfenster, nicht für einen einzelnen Code.
-- Ein Admin darf sein eigenes Gerät freigeben (sonst käme ein einzelner Admin nie an sein erstes Gerät); die Oberfläche
-- verlangt dafür dieselbe Bestätigung mit Fingerabdruck wie bei jedem anderen Gerät.
set local lock_timeout = '5s';

do $$ begin
  if not exists (select 1 from __SCHEMA__.schema_migrations where version = '0002_wamo_geraete') then
    raise exception '0014 braucht 0002';
  end if;
end $$;

-- ---------- Liste für die Verwaltung ----------
create or replace function __SCHEMA__.geraete_verwaltung_liste(p_admin uuid)
returns table (kennung text, name text, hostname text, profil_id uuid, profil_name text, profil_aktiv boolean,
               status __SCHEMA__.geraet_status, bindung __SCHEMA__.geraet_bindung, fingerabdruck text,
               erstellt_at timestamptz, freigegeben_at timestamptz, gesperrt_at timestamptz, gesperrt_grund text,
               zuletzt_gesehen timestamptz, ereignisse jsonb)
language plpgsql stable security definer set search_path = '' as $$
begin
  if p_admin is null or not exists (select 1 from __SCHEMA__.profiles p
                                    where p.user_id = p_admin and p.aktiv and p.rolle = 'admin') then
    raise exception 'geraete_verwaltung_liste: nur für einen aktiven Admin' using errcode = '42501';
  end if;
  return query
    select d.kennung, d.name, d.hostname, d.user_id, o.display_name, coalesce(o.aktiv, false), d.status, d.bindung,
           d.ssh_fingerprint, d.created_at, d.freigegeben_at, d.gesperrt_at, d.gesperrt_grund, d.last_seen_at,
           coalesce((select jsonb_agg(jsonb_build_object('von', e.von, 'nach', e.nach, 'durch', a.display_name,
                                                          'grund', e.grund, 'at', e.at) order by e.at desc, e.id desc)
                       from (select x.* from __SCHEMA__.device_events x where x.device_id = d.id
                              order by x.at desc, x.id desc limit 10) e
                       left join __SCHEMA__.profiles a on a.user_id = e.durch), '[]'::jsonb)
      from __SCHEMA__.devices d
      left join __SCHEMA__.profiles o on o.user_id = d.user_id
     where d.art = 'persoenlich'
       and (d.status <> 'widerrufen' or d.gesperrt_at > now() - interval '90 days')
     order by (d.status = 'beantragt') desc, d.created_at desc
     limit 200;
end $$;

-- ---------- Aktion auf ein Gerät ----------
create or replace function __SCHEMA__.geraet_entscheiden(p_admin uuid, p_kennung text, p_aktion text, p_grund text,
                                                          p_aal2_zeit timestamptz)
returns table (kennung text, vorher __SCHEMA__.geraet_status, nachher __SCHEMA__.geraet_status, ergebnis text)
language plpgsql security definer set search_path = '' as $$
declare
  v_dev   __SCHEMA__.devices%rowtype;
  v_grund text;
  v_ziel  __SCHEMA__.geraet_status;
begin
  if p_admin is null or p_kennung is null or p_aktion is null or p_aal2_zeit is null then
    raise exception 'geraet_entscheiden: Angaben fehlen' using errcode = '22004';
  end if;
  if p_aktion not in ('freigeben', 'sperren', 'entsperren', 'widerrufen') then
    raise exception 'geraet_entscheiden: unbekannte Aktion' using errcode = '22023';
  end if;
  if p_kennung !~ '^pc-[a-z0-9]+(-[a-z0-9]+)*$' or length(p_kennung) > 23 then
    raise exception 'geraet_entscheiden: ungültige Kennung' using errcode = '22023';
  end if;
  -- Steuerzeichen und Zeilenumbrüche werden zu einem Leerzeichen, Leerraum wird zusammengezogen
  v_grund := nullif(btrim(regexp_replace(regexp_replace(coalesce(p_grund, ''), '[[:cntrl:]]', ' ', 'g'),
                                         '[[:space:]]+', ' ', 'g')), '');
  if length(v_grund) > 200 then
    raise exception 'geraet_entscheiden: Grund zu lang' using errcode = '22023';
  end if;
  -- ein Grund braucht mindestens drei Buchstaben oder Ziffern (nicht nur Zeichen ohne Inhalt)
  if p_aktion in ('sperren', 'widerrufen')
     and (v_grund is null or length(regexp_replace(v_grund, '[^[:alnum:]]', '', 'g')) < 3) then
    raise exception 'geraet_entscheiden: Grund fehlt' using errcode = '22023';
  end if;
  if not exists (select 1 from __SCHEMA__.profiles p where p.user_id = p_admin and p.aktiv and p.rolle = 'admin') then
    raise exception 'geraet_entscheiden: nur ein aktiver Admin darf Geräte entscheiden' using errcode = '42501';
  end if;
  -- wie bei den Freigaben des Release-Wegs (0013): der zweite Faktor stammt von eben, nicht von der Anmeldung vor Tagen
  if p_aal2_zeit > clock_timestamp() + interval '5 seconds' then
    raise exception 'geraet_entscheiden: aal2_zeit liegt in der Zukunft' using errcode = '23514';
  end if;
  if clock_timestamp() - p_aal2_zeit > interval '120 seconds' then
    raise exception 'geraet_entscheiden: der zweite Faktor ist älter als 120 Sekunden' using errcode = '23514';
  end if;

  -- Zeile sperren: zwei Admins gleichzeitig entscheiden nacheinander, der zweite sieht den neuen Stand
  select * into v_dev from __SCHEMA__.devices d where d.kennung = p_kennung and d.art = 'persoenlich' for update;
  if not found then
    return query select p_kennung, null::__SCHEMA__.geraet_status, null::__SCHEMA__.geraet_status, 'unbekannt'::text;
    return;
  end if;

  v_ziel := case p_aktion when 'freigeben' then 'aktiv' when 'entsperren' then 'aktiv'
                          when 'sperren' then 'gesperrt' else 'widerrufen' end;
  if v_dev.status = v_ziel then
    return query select v_dev.kennung, v_dev.status, v_dev.status, 'schon'::text;
    return;
  end if;
  if not ((p_aktion = 'freigeben'  and v_dev.status = 'beantragt')
       or (p_aktion = 'entsperren' and v_dev.status = 'gesperrt')
       or (p_aktion = 'sperren'    and v_dev.status = 'aktiv')
       or (p_aktion = 'widerrufen' and v_dev.status in ('beantragt', 'aktiv', 'gesperrt'))) then
    return query select v_dev.kennung, v_dev.status, v_dev.status, 'nicht_moeglich'::text;
    return;
  end if;

  -- Freigabe und Entsperren nur für ein aktives Besitzerprofil (der Wächter lehnt es sonst mit 42501 ab; dieser Fall wird
  -- hier vorab gemeldet, damit ein echter Rechtefehler des Wächters nie als "besitzer_inaktiv" erscheint)
  if v_ziel = 'aktiv' and not exists (select 1 from __SCHEMA__.profiles p where p.user_id = v_dev.user_id and p.aktiv) then
    return query select v_dev.kennung, v_dev.status, v_dev.status, 'besitzer_inaktiv'::text;
    return;
  end if;

  begin
    update __SCHEMA__.devices d
       set status = v_ziel,
           geaendert_von = p_admin,
           freigegeben_von = case when v_ziel = 'aktiv' then p_admin else d.freigegeben_von end,
           gesperrt_grund = case when v_ziel in ('gesperrt', 'widerrufen') then v_grund else d.gesperrt_grund end
     where d.id = v_dev.id;
  exception
    when unique_violation then   -- devices_ein_aktives_idx: das Profil hat schon ein anderes aktives Gerät
      return query select v_dev.kennung, v_dev.status, v_dev.status, 'konflikt'::text;
      return;
  end;
  -- Jede Aktivierung meldet sich bei allen Admins (Kennung, Fingerabdruck, Akteur): Eine Freigabe, die niemand ausgelöst
  -- hat, fällt so auf, auch wenn der Kern selbst nicht vertrauenswürdig wäre.
  if v_ziel = 'aktiv' then
    perform __SCHEMA__.hinweis_an_admins('system',
      'Gerät ' || case p_aktion when 'freigeben' then 'freigegeben' else 'entsperrt' end || ': ' || v_dev.kennung,
      'Kennung ' || v_dev.kennung || ', Fingerabdruck ' || v_dev.ssh_fingerprint || ', durch '
        || (select p.display_name from __SCHEMA__.profiles p where p.user_id = p_admin) || '.',
      v_dev.id, jsonb_build_object('kennung', v_dev.kennung, 'fingerabdruck', v_dev.ssh_fingerprint, 'admin', p_admin));
  end if;
  return query select v_dev.kennung, v_dev.status, v_ziel, 'ok'::text;
end $$;

-- ---------- Rechte (geringste Rechte: nur Ausführen, kein Tabellenrecht dazu) ----------
revoke all on function __SCHEMA__.geraete_verwaltung_liste(uuid),
                       __SCHEMA__.geraet_entscheiden(uuid, text, text, text, timestamptz)
  from public, anon, authenticated, service_role, wamo_admin;
grant execute on function __SCHEMA__.geraete_verwaltung_liste(uuid) to wamo_core;
grant execute on function __SCHEMA__.geraet_entscheiden(uuid, text, text, text, timestamptz) to wamo_core;
-- ausdrücklich KEIN Schreibrecht auf devices für wamo_core (auch nicht auf Spalten); ein revoke auf nicht vergebene
-- Rechte ist folgenlos und hält den Stand gegen Drift fest
revoke insert, update, delete, truncate on __SCHEMA__.devices from wamo_core;
revoke update (status, freigegeben_von, geaendert_von, gesperrt_grund) on __SCHEMA__.devices from wamo_core;

-- ---------- Row Level Security ----------
-- devices hat RLS und die Policy core_all seit 0002; hier wird nur geprüft (Selbsttest unten), nichts geändert.

-- Selbsttest: bricht die Transaktion bei Rechtedrift ab.
do $$ begin
  if not has_function_privilege('wamo_core', '__SCHEMA__.geraete_verwaltung_liste(uuid)', 'EXECUTE')
     or not has_function_privilege('wamo_core', '__SCHEMA__.geraet_entscheiden(uuid, text, text, text, timestamptz)', 'EXECUTE')
     or has_function_privilege('wamo_admin', '__SCHEMA__.geraet_entscheiden(uuid, text, text, text, timestamptz)', 'EXECUTE')
     or has_function_privilege('wamo_admin', '__SCHEMA__.geraete_verwaltung_liste(uuid)', 'EXECUTE')
     or has_function_privilege('anon', '__SCHEMA__.geraet_entscheiden(uuid, text, text, text, timestamptz)', 'EXECUTE')
     or has_function_privilege('authenticated', '__SCHEMA__.geraet_entscheiden(uuid, text, text, text, timestamptz)', 'EXECUTE')
     or has_function_privilege('service_role', '__SCHEMA__.geraet_entscheiden(uuid, text, text, text, timestamptz)', 'EXECUTE')
     or has_function_privilege('anon', '__SCHEMA__.geraete_verwaltung_liste(uuid)', 'EXECUTE')
     or has_function_privilege('authenticated', '__SCHEMA__.geraete_verwaltung_liste(uuid)', 'EXECUTE')
     or has_function_privilege('service_role', '__SCHEMA__.geraete_verwaltung_liste(uuid)', 'EXECUTE')
     or has_table_privilege('wamo_core', '__SCHEMA__.devices', 'INSERT,UPDATE,DELETE,TRUNCATE')
     or has_any_column_privilege('wamo_core', '__SCHEMA__.devices', 'INSERT,UPDATE')
     or not has_table_privilege('wamo_core', '__SCHEMA__.devices', 'SELECT')
     or has_table_privilege('wamo_core', '__SCHEMA__.device_events', 'SELECT')
     or has_any_column_privilege('anon', '__SCHEMA__.devices', 'SELECT,INSERT,UPDATE')
     or has_any_column_privilege('authenticated', '__SCHEMA__.devices', 'SELECT,INSERT,UPDATE')
     or has_any_column_privilege('service_role', '__SCHEMA__.devices', 'SELECT,INSERT,UPDATE')
     or not (select c.relrowsecurity from pg_class c where c.oid = '__SCHEMA__.devices'::regclass)
     or not exists (select 1 from pg_policies where schemaname = '__SCHEMA__' and tablename = 'devices'
                      and policyname = 'core_all')
     or not (select p.prosecdef from pg_proc p
              where p.oid = '__SCHEMA__.geraet_entscheiden(uuid, text, text, text, timestamptz)'::regprocedure)
     or not (select p.prosecdef from pg_proc p where p.oid = '__SCHEMA__.geraete_verwaltung_liste(uuid)'::regprocedure)
  then
    raise exception 'Geräteverwaltung: unzulässige Rechte oder RLS fehlt (Funktionen, devices, device_events, Policy core_all prüfen)'
      using errcode = '42501';
  end if;
end $$;

insert into __SCHEMA__.schema_migrations (version) values ('0014_geraete_verwaltung') on conflict do nothing;
