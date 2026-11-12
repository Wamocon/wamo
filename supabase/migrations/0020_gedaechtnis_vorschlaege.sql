-- WAMO 0020: Gemeinsame Gedächtnisvorschläge in der Oberfläche freigeben oder ablehnen. Bis jetzt ging das nur mit voice-core/wamo_admin.py
-- (Verbindung als wamo_admin), und die Oberfläche schrieb trotzdem "wartet auf Freigabe". Neu: die Funktion gedaechtnis_vorschlag_entscheiden,
-- die der Kern für den angemeldeten Admin aufruft.
-- __SCHEMA__ wird ersetzt durch wamo_dev | wamo_test | wamo_prod.
-- Anwenden (je Schema, in EINER Transaktion, zweimal hintereinander fehlerfrei):
--   bash scripts/render-migration.sh <schema> supabase/migrations/0020_gedaechtnis_vorschlaege.sql \
--     | docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 --single-transaction
-- Rückweg: 0020_gedaechtnis_vorschlaege.down.sql (entfernt nur die Funktion, an den Einträgen ändert sich nichts).
-- Hängt ab von 0001 (memories, profiles, memories_guard). Reihenfolge: ZUERST diese Migration, DANN der Kern mit gedaechtnis_api.py, dann die
-- Oberfläche. Ein Kern ohne die Funktion antwortet auf einen Klick ruhig mit 503 und ändert nichts.
-- Reihenfolge der Anwendung: wamo_dev, wamo_test, dann das Schema des Büro-Kerns, erst nach database-reviewer und nach Nikos Ja.
--
-- Was die Funktion tut (und was nicht):
--   * Nur ein aktiver Admin (p_admin) entscheidet; der Kern reicht die geprüfte Identität aus dem Token weiter. Die Funktion prüft das
--     Profil in der Datenbank selbst (42501 sonst), der Trigger memories_guard prüft es beim Freigeben noch einmal.
--   * Sie ändert genau eine Zeile: bereich 'gemeinsam', status 'vorgeschlagen', is_active. Alles andere ergibt 'nicht_offen' und ändert
--     nichts (persönliche Einträge, schon entschiedene, vergessene). Freigeben setzt status 'aktiv', Ablehnen 'abgelehnt', dazu
--     reviewed_by und reviewed_at.
--   * Sie ändert nie den Inhalt, nie den Autor, nie is_active. wamo_core bekommt kein UPDATE auf status: der Weg führt nur über diese Funktion.
--   * SECURITY DEFINER wie die Funktionen aus 0003 (der Kern braucht keine Schreibrechte auf memories.status), search_path leer, EXECUTE nur
--     für wamo_core. Der Kern kann damit nichts, was er nicht schon könnte: Ein Admin-Eintrag "für alle" wird schon heute sofort aktiv.
set local lock_timeout = '5s';

do $$ begin
  if to_regclass('__SCHEMA__.memories') is null or to_regclass('__SCHEMA__.profiles') is null
     or to_regprocedure('__SCHEMA__.memories_guard()') is null then
    raise exception '0020: 0001 ist in __SCHEMA__ nicht angewendet (memories, profiles, memories_guard fehlen)';
  end if;
end $$;

create or replace function __SCHEMA__.gedaechtnis_vorschlag_entscheiden(p_id uuid, p_admin uuid, p_entscheidung text)
returns text
language plpgsql security definer set search_path = '' as $$
declare
  v_neu text;
begin
  if p_id is null or p_admin is null or p_entscheidung is null then
    raise exception 'gedaechtnis_vorschlag_entscheiden: id, admin und entscheidung sind Pflicht' using errcode = '22004';
  end if;
  if p_entscheidung not in ('freigeben', 'ablehnen') then
    raise exception 'gedaechtnis_vorschlag_entscheiden: entscheidung muss freigeben oder ablehnen sein' using errcode = '22023';
  end if;
  if not exists (select 1 from __SCHEMA__.profiles p where p.user_id = p_admin and p.aktiv and p.rolle = 'admin') then
    raise exception 'gedaechtnis_vorschlag_entscheiden: nur ein aktiver Admin entscheidet über gemeinsame Vorschläge' using errcode = '42501';
  end if;
  v_neu := case p_entscheidung when 'freigeben' then 'aktiv' else 'abgelehnt' end;
  update __SCHEMA__.memories m
     set status = v_neu::__SCHEMA__.memory_status, reviewed_by = p_admin, reviewed_at = now()
   where m.id = p_id and m.bereich = 'gemeinsam' and m.status = 'vorgeschlagen' and m.is_active;
  if not found then
    return 'nicht_offen';
  end if;
  return case p_entscheidung when 'freigeben' then 'freigegeben' else 'abgelehnt' end;
end $$;

revoke all on function __SCHEMA__.gedaechtnis_vorschlag_entscheiden(uuid, uuid, text)
  from public, anon, authenticated, service_role, wamo_admin;
grant execute on function __SCHEMA__.gedaechtnis_vorschlag_entscheiden(uuid, uuid, text) to wamo_core;

-- Selbsttest: bricht die Transaktion bei Rechtedrift ab.
do $$
declare
  v_fn regprocedure := '__SCHEMA__.gedaechtnis_vorschlag_entscheiden(uuid, uuid, text)'::regprocedure;
begin
  if not (select p.prosecdef from pg_proc p where p.oid = v_fn)
     or not exists (select 1 from pg_proc p, unnest(p.proconfig) c where p.oid = v_fn and c like 'search_path=%')
     or not has_function_privilege('wamo_core', v_fn, 'EXECUTE')
     or has_function_privilege('wamo_admin', v_fn, 'EXECUTE')
     or has_function_privilege('anon', v_fn, 'EXECUTE')
     or has_function_privilege('authenticated', v_fn, 'EXECUTE')
     or has_function_privilege('service_role', v_fn, 'EXECUTE')
     or has_column_privilege('wamo_core', '__SCHEMA__.memories', 'status', 'UPDATE')
     or has_column_privilege('wamo_core', '__SCHEMA__.memories', 'reviewed_by', 'UPDATE')
     or has_table_privilege('wamo_core', '__SCHEMA__.memories', 'DELETE')
  then
    raise exception 'gedaechtnis_vorschlag_entscheiden: Funktion oder Rechte nicht im erwarteten Stand (0001 prüfen)';
  end if;
end $$;

insert into __SCHEMA__.schema_migrations (version) values ('0020_gedaechtnis_vorschlaege') on conflict do nothing;
