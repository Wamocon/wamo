-- Rückweg zu 0016: stellt den Trigger release_anfragen_guard in der Fassung von 0013 wieder her (direkte Abfragen mit FOR SHARE)
-- und entfernt die beiden Funktionen. Folge: Der Release-Dienst kann danach wieder nicht auf veroeffentlicht schalten.
do $$ begin
  if '__SCHEMA__' = 'wamo_prod' and coalesce(current_setting('wamo.allow_drop', true), '') <> 'ja' then
    raise exception 'Down in wamo_prod gesperrt. Nach Freigabe: PGOPTIONS="-c wamo.allow_drop=ja"';
  end if;
end $$;
set local lock_timeout = '5s';

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
       and not exists (
         select 1 from __SCHEMA__.release_freigaben f
          where f.anfrage_id = new.id and f.widerrufen_at is null
            and f.commit_id = new.v_commit and f.tree_id = new.tree_id
            and f.erstellt_at >= now() - case when new.art = 'sonder' then interval '24 hours' else interval '60 minutes' end
            and __SCHEMA__.release_ist_admin(f.freigeber)
            and (new.art <> 'sonder'
                 or (f.freigeber <> new.angefordert_von and __SCHEMA__.release_ist_admin(new.angefordert_von)
                     and __SCHEMA__.release_anzahl_admins() >= 2))
            for share) then
      raise exception 'release_anfragen: freigegeben nur mit einer gültigen, frischen Freigabe zu Commit-ID und Tree-ID'
        using errcode = '23514';
    end if;
    -- Ein Widerruf wirkt auch danach: ohne nicht widerrufene Freigabe geht es nicht weiter (Rückweg und Abbruch bleiben frei)
    if new.status in ('veroeffentlicht', 'bereitgestellt', 'umgeschaltet')
       and not exists (select 1 from __SCHEMA__.release_freigaben f
                        where f.anfrage_id = new.id and f.widerrufen_at is null
                          and f.commit_id = new.v_commit and f.tree_id = new.tree_id for share) then
      raise exception 'release_anfragen: % nur mit einer nicht widerrufenen Freigabe', new.status using errcode = '23514';
    end if;
  end if;
  new.aktualisiert_at := now();
  return new;
end $$;

drop trigger if exists release_anfragen_guard on __SCHEMA__.release_anfragen;
create trigger release_anfragen_guard before insert or update on __SCHEMA__.release_anfragen
  for each row execute function __SCHEMA__.release_anfragen_guard();

drop function if exists __SCHEMA__.release_freigabe_gilt(uuid, text, text, text, uuid);
drop function if exists __SCHEMA__.release_freigabe_nicht_widerrufen(uuid, text, text);
delete from __SCHEMA__.schema_migrations where version = '0016_release_freigaben_lesen';
