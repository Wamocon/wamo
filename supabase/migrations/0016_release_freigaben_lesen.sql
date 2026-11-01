-- WAMO 0016: Der Trigger release_anfragen_guard liest die Freigaben über zwei Funktionen mit festen Rechten (Fehlerbehebung zu 0013).
-- __SCHEMA__ wird ersetzt durch wamo_dev | wamo_test | wamo_prod. Anwenden (in EINER Transaktion, ein zweiter Lauf ändert nichts):
--   bash scripts/render-migration.sh <schema> supabase/migrations/0016_release_freigaben_lesen.sql \
--     | docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 --single-transaction
-- Voraussetzung: 0013. wamo_prod nur nach ausdrücklicher Freigabe und nach dem Agenten database-reviewer.
--
-- Fehler in 0013 (gefunden mit der echten Datenbank und den echten Rollen, nicht mit der Attrappe): Der Trigger prüft vor dem
-- Status freigegeben, veroeffentlicht, bereitgestellt und umgeschaltet, dass die Freigabe noch gilt, und liest dazu
-- release_freigaben mit FOR SHARE. FOR SHARE verlangt neben SELECT auch ein UPDATE-Recht auf der Tabelle. Die Rolle
-- wamo_release hat dort bewusst nur SELECT. Folge: Der Release-Dienst kann nach der Freigabe nie auf veroeffentlicht schalten
-- (42501 permission denied). Die Rechte zu erweitern wäre falsch (der Release-Dienst darf Freigaben nicht ändern), und der
-- Trigger kann nicht als Eigentümer laufen, weil er mit current_user die Rolle unterscheidet.
--
-- Lösung: Zwei kleine Funktionen, die als Eigentümer laufen (security definer, search_path leer), nur lesen und das FOR SHARE
-- selbst halten. Sie tun genau, was die beiden Abfragen im Trigger bisher taten; der Trigger ruft sie auf. Sonst bleibt der
-- Trigger Wort für Wort gleich. Die Funktionen dürfen nur wamo_release und wamo_anker ausführen.
set local lock_timeout = '5s';

do $$ begin
  if to_regclass('__SCHEMA__.release_freigaben') is null
     or not exists (select 1 from __SCHEMA__.schema_migrations where version = '0013_release_freigaben') then
    raise exception '0016 braucht 0013 (release_freigaben fehlt)';
  end if;
end $$;

-- Gilt die Freigabe für den Status freigegeben: nicht widerrufen, gleiche Commit-ID und Tree-ID, frisch (60 Minuten, bei
-- sonder 24 Stunden), Freigeber ist noch aktiver Admin, bei sonder ein anderer als der Beauftragte, beide aktiv, mindestens
-- zwei aktive Admins. Dieselbe Bedingung wie bisher im Trigger, mit FOR SHARE gegen einen gleichzeitigen Widerruf.
create or replace function __SCHEMA__.release_freigabe_gilt(p_anfrage uuid, p_commit text, p_tree text, p_art text,
                                                            p_beauftragter uuid)
returns boolean language sql security definer set search_path = '' as $$
  select exists (
    select 1 from __SCHEMA__.release_freigaben f
     where f.anfrage_id = p_anfrage and f.widerrufen_at is null
       and f.commit_id = p_commit and f.tree_id = p_tree
       and f.erstellt_at >= now() - case when p_art = 'sonder' then interval '24 hours' else interval '60 minutes' end
       and __SCHEMA__.release_ist_admin(f.freigeber)
       and (p_art <> 'sonder'
            or (f.freigeber <> p_beauftragter and __SCHEMA__.release_ist_admin(p_beauftragter)
                and __SCHEMA__.release_anzahl_admins() >= 2))
       for share);
$$;

-- Ist die Freigabe seit dem Status freigegeben nicht widerrufen worden (veroeffentlicht, bereitgestellt, umgeschaltet).
create or replace function __SCHEMA__.release_freigabe_nicht_widerrufen(p_anfrage uuid, p_commit text, p_tree text)
returns boolean language sql security definer set search_path = '' as $$
  select exists (
    select 1 from __SCHEMA__.release_freigaben f
     where f.anfrage_id = p_anfrage and f.widerrufen_at is null
       and f.commit_id = p_commit and f.tree_id = p_tree
       for share);
$$;

revoke all on function __SCHEMA__.release_freigabe_gilt(uuid, text, text, text, uuid),
                       __SCHEMA__.release_freigabe_nicht_widerrufen(uuid, text, text)
  from public, anon, authenticated, service_role, wamo_core, wamo_admin, wamo_release, wamo_anker;
grant execute on function __SCHEMA__.release_freigabe_gilt(uuid, text, text, text, uuid),
                          __SCHEMA__.release_freigabe_nicht_widerrufen(uuid, text, text)
  to wamo_release, wamo_anker;

-- Der Trigger mit den beiden Aufrufen statt der direkten Abfragen (sonst unverändert gegenüber 0013)
create or replace function __SCHEMA__.release_anfragen_guard() returns trigger
language plpgsql security invoker set search_path = '' as $$
declare
  reihenfolge constant text[] := array['angefragt', 'arbeit', 'uebernommen', 'pruefung1', 'review', 'refactor', 'pruefung2',
                                       'versionswahl', 'pruefung3', 'wartet_freigabe', 'freigegeben', 'veroeffentlicht',
                                       'bereitgestellt', 'umgeschaltet', 'bewaehrt'];
  grenze constant integer := array_position(reihenfolge, 'wartet_freigabe');
  von integer;
  nach integer;
  erlaubt boolean;
  als_release boolean := pg_has_role(current_user, 'wamo_release', 'USAGE')
                         and not pg_has_role(current_user, 'wamo_anker', 'USAGE');
  als_anker boolean := pg_has_role(current_user, 'wamo_anker', 'USAGE')
                       and not pg_has_role(current_user, 'wamo_release', 'USAGE');
begin
  if tg_op = 'INSERT' then
    if new.status <> 'angefragt' then
      raise exception 'release_anfragen: neue Anfragen beginnen im Status angefragt' using errcode = '23514';
    end if;
    if not __SCHEMA__.release_ist_admin(new.angefordert_von) then
      raise exception 'release_anfragen: nur ein aktiver Admin darf einen Release beauftragen' using errcode = '42501';
    end if;
    new.erstellt_at := now();
    new.aktualisiert_at := now();
    return new;
  end if;

  if old.status in ('zurueckgenommen', 'abgebrochen') then
    raise exception 'release_anfragen: die Anfrage ist abgeschlossen (%)', old.status using errcode = '42501';
  end if;
  if (new.id, new.angefordert_von, new.quelle, new.erstellt_at)
     is distinct from (old.id, old.angefordert_von, old.quelle, old.erstellt_at) then
    raise exception 'release_anfragen: id, angefordert_von, quelle und erstellt_at sind unveränderlich'
      using errcode = '42501';
  end if;
  if old.art = 'sonder' and new.art <> 'sonder' then
    raise exception 'release_anfragen: die Art sonder lässt sich nicht auf normal senken' using errcode = '42501';
  end if;
  if als_release and (new.art, new.schutz_treffer) is distinct from (old.art, old.schutz_treffer) then
    raise exception 'release_anfragen: Art und Schutz-Treffer klassifiziert nur der Anker' using errcode = '42501';
  end if;
  -- der Versionsvorschlag lässt sich nur erhöhen (sonst liefe der Schutz "Version nie unter den Vorschlag" ins Leere)
  if old.version_vorschlag is not null and new.version_vorschlag is distinct from old.version_vorschlag
     and not (case when new.version_vorschlag ~ '^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})$'
                   then string_to_array(new.version_vorschlag, '.')::int[] >= string_to_array(old.version_vorschlag, '.')::int[]
                   else false end) then
    raise exception 'release_anfragen: der Versionsvorschlag lässt sich nur erhöhen' using errcode = '23514';
  end if;

  von := array_position(reihenfolge, old.status);
  nach := array_position(reihenfolge, new.status);

  -- Ab wartet_freigabe ist alles eingefroren, woran die Freigabe hängt
  if von >= grenze
     and (new.art, new.basis_commit, new.c1_commit, new.r_commit, new.v_commit, new.tree_id, new.bump,
          new.version_vorschlag, new.version_final, new.schutz_treffer, new.wartet_bis)
         is distinct from
         (old.art, old.basis_commit, old.c1_commit, old.r_commit, old.v_commit, old.tree_id, old.bump,
          old.version_vorschlag, old.version_final, old.schutz_treffer, old.wartet_bis) then
    raise exception 'release_anfragen: ab wartet_freigabe sind Art, Commits, Tree-ID, Version, Treffer und Wartezeit eingefroren'
      using errcode = '42501';
  end if;

  if new.status is distinct from old.status then
    if new.status = 'abgebrochen' then
      erlaubt := von < array_position(reihenfolge, 'umgeschaltet');
    elsif new.status = 'zurueckgenommen' then
      erlaubt := old.status in ('bereitgestellt', 'umgeschaltet', 'bewaehrt');
    elsif nach <= grenze then
      erlaubt := nach > von;          -- vor der Freigabe darf übersprungen werden (Quelle main hat keinen Agenten)
    else
      erlaubt := nach = von + 1;      -- ab der Freigabe lückenlos
    end if;
    if not erlaubt then
      raise exception 'release_anfragen: Statuswechsel % nach % nicht erlaubt', old.status, new.status
        using errcode = '23514';
    end if;
    if als_release and new.status in ('freigegeben', 'bereitgestellt', 'umgeschaltet', 'bewaehrt', 'zurueckgenommen') then
      raise exception 'release_anfragen: wamo_release darf den Status % nicht setzen (nur der Anker)', new.status
        using errcode = '42501';
    end if;
    if als_anker and (nach <= grenze) then
      raise exception 'release_anfragen: wamo_anker darf den Status % nicht setzen (Status vor der Freigabe)', new.status
        using errcode = '42501';
    end if;
    if new.status = 'wartet_freigabe' then
      if new.v_commit is null or new.tree_id is null or new.version_final is null then
        raise exception 'release_anfragen: wartet_freigabe nur mit v_commit, tree_id und version_final' using errcode = '23514';
      end if;
      if new.schutz_treffer is null then
        raise exception 'release_anfragen: Art und Schutz-Treffer sind noch nicht vom Anker klassifiziert'
          using errcode = '23514';
      end if;
      if jsonb_array_length(new.schutz_treffer) > 0 and new.art <> 'sonder' then
        raise exception 'release_anfragen: Treffer der Schutzschicht verlangen die Art sonder' using errcode = '23514';
      end if;
      if new.art = 'sonder' then
        new.wartet_bis := clock_timestamp() + interval '15 minutes';    -- Zeit zum Lesen (Konzept E.2), nicht vorgebbar
      end if;
    end if;
    if new.status = 'freigegeben'
       and not __SCHEMA__.release_freigabe_gilt(new.id, new.v_commit, new.tree_id, new.art, new.angefordert_von) then
      raise exception 'release_anfragen: freigegeben nur mit einer gültigen, frischen Freigabe zu Commit-ID und Tree-ID'
        using errcode = '23514';
    end if;
    -- Ein Widerruf wirkt auch danach: ohne nicht widerrufene Freigabe geht es nicht weiter (Rückweg und Abbruch bleiben frei)
    if new.status in ('veroeffentlicht', 'bereitgestellt', 'umgeschaltet')
       and not __SCHEMA__.release_freigabe_nicht_widerrufen(new.id, new.v_commit, new.tree_id) then
      raise exception 'release_anfragen: % nur mit einer nicht widerrufenen Freigabe', new.status using errcode = '23514';
    end if;
  end if;
  new.aktualisiert_at := now();
  return new;
end $$;

drop trigger if exists release_anfragen_guard on __SCHEMA__.release_anfragen;
create trigger release_anfragen_guard before insert or update on __SCHEMA__.release_anfragen
  for each row execute function __SCHEMA__.release_anfragen_guard();

-- Selbsttest: Rechte und Eigenschaften der beiden Funktionen (bricht die Transaktion bei Abweichung ab)
do $$
declare
  f text;
  rolle text;
  erwartet boolean;
begin
  foreach f in array array['__SCHEMA__.release_freigabe_gilt(uuid, text, text, text, uuid)',
                           '__SCHEMA__.release_freigabe_nicht_widerrufen(uuid, text, text)'] loop
    if not exists (select 1 from pg_proc p where p.oid = f::regprocedure and p.prosecdef
                     and p.proconfig @> array['search_path=""']) then
      raise exception 'release (0016): % ist nicht security definer mit leerem search_path', f using errcode = '42501';
    end if;
    foreach rolle in array array['wamo_release', 'wamo_anker', 'wamo_core', 'wamo_admin', 'anon', 'authenticated', 'service_role'] loop
      erwartet := rolle in ('wamo_release', 'wamo_anker');
      if has_function_privilege(rolle, f::regprocedure, 'EXECUTE') is distinct from erwartet then
        raise exception 'release (0016): EXECUTE auf % für % ist % statt %', f, rolle, not erwartet, erwartet
          using errcode = '42501';
      end if;
    end loop;
  end loop;
  if not exists (select 1 from pg_trigger t where t.tgrelid = '__SCHEMA__.release_anfragen'::regclass
                   and t.tgname = 'release_anfragen_guard' and t.tgenabled = 'O') then
    raise exception 'release (0016): der Trigger release_anfragen_guard fehlt oder ist abgeschaltet' using errcode = '42501';
  end if;
end $$;

insert into __SCHEMA__.schema_migrations (version) values ('0016_release_freigaben_lesen') on conflict do nothing;
