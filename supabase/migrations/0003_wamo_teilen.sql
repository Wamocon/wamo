-- 0003_wamo_teilen: Arbeit teilen (Canvas-Dokumente und Notizen, Ergebnisse und Projekte von Entwicklungsauftraegen,
-- Scans und ausdruecklich geteilte Dateien) mit Rechten, Widerruf, Hinweis an den Empfaenger und Protokoll.
-- Anwenden (je Schema, immer in EINER Transaktion, zweimal hintereinander fehlerfrei):
--   bash scripts/render-migration.sh <schema> supabase/migrations/0003_wamo_teilen.sql \
--     | docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 --single-transaction
-- Rueckweg: 0003_wamo_teilen.down.sql (nur ohne geteilte Arbeit und ohne Dateien).
--
-- Grundsaetze
-- * Nur der Besitzer teilt, aendert das Recht und widerruft. Empfaenger teilen nicht weiter (sie besitzen das Objekt
--   nicht); sie koennen eine Teilung fuer sich zurueckgeben. Widerruf ist endgueltig und wirkt sofort.
--   Grenze: Den Besitzer einer neuen Teilung setzt der Kern aus der geprueften Identitaet; die Datenbank prueft, dass
--   das Objekt diesem Besitzer gehoert. Recht aendern und beenden gehen nur ueber Funktionen mit Besitzerpruefung
--   (wamo_core hat kein update-Recht auf teilungen).
-- * Rechte: lesen (ansehen, herunterladen) oder bearbeiten (neue Fassung anlegen). Ergebnisse von
--   Entwicklungsauftraegen (canvas_items.data.auftrag) und ganze Projekte nur lesend.
-- * Dateien liegen in der Datenbank (Tabelle dateien), damit jeder Kern dieselben Daten sieht (Buero-Kern auf
--   Sokrates-2 und der Kern auf einem Admin-PC). Hoechstens 25 MB je Datei, 500 MB und 2000 Dateien je hochladendem
--   Profil (Fassungen zaehlen beim Bearbeiter), 20 Fassungen je Datei, nur Typen aus einer Liste. Groesse und
--   Pruefsumme berechnet die Datenbank. Dateien sind unveraenderlich; Loeschen ist zuerst weich (geloescht_at), nach
--   7 Tagen endgueltig (dateien_endgueltig_loeschen, ruft der Kern taeglich auf).
-- * Bearbeiten eines Canvas-Dokuments legt eine neue Fassung an (parent_id = Wurzel, version + 1, bearbeitet_von,
--   Titel der Wurzel), hoechstens 100 je Wurzel; mit Basisfassung gibt es bei gleichzeitiger Bearbeitung einen
--   Konflikt (SQLSTATE P0409) statt stillem Ueberschreiben.
-- * Jede Teilung, jede Rechteaenderung, jeder Widerruf, jede Bearbeitung durch Empfaenger und jeder Abruf durch
--   Empfaenger steht in teilung_events (ohne Inhalte). Empfaenger und Besitzer bekommen Hinweise (Tabelle hinweise aus
--   0002); ungelesene Bearbeitungshinweise je Objekt werden nicht vervielfacht.

do $$ begin
  if not exists (select 1 from __SCHEMA__.schema_migrations where version = '0002_wamo_geraete') then
    raise exception '0003 braucht 0002';
  end if;
end $$;

-- ---------- Dateien ----------
create table if not exists __SCHEMA__.dateien (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references __SCHEMA__.profiles(user_id) on delete cascade,  -- Besitzer
  wurzel_id       uuid references __SCHEMA__.dateien(id) on delete cascade,   -- nur bei Fassungen: die erste Datei
  hochgeladen_von uuid references __SCHEMA__.profiles(user_id) on delete set null,      -- traegt das Kontingent
  name            text not null,
  mime            text not null,
  groesse         integer not null,
  sha256          text not null,
  herkunft        text not null default 'upload',
  inhalt          bytea not null,
  erstellt_at     timestamptz not null default now(),
  geloescht_at    timestamptz,     -- weich geloescht; endgueltig nach 7 Tagen
  constraint dateien_name_chk check (length(name) between 1 and 200 and name !~ '[/\\[:cntrl:]]'
                                     and name not in ('.', '..')),
  constraint dateien_mime_chk check (mime in (
    'application/pdf', 'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'text/plain', 'text/csv',
    'text/markdown', 'application/json', 'application/zip',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'application/vnd.oasis.opendocument.text', 'application/vnd.oasis.opendocument.spreadsheet',
    'application/vnd.oasis.opendocument.presentation')),
  constraint dateien_groesse_chk check (groesse between 1 and 26214400),
  constraint dateien_sha_chk check (sha256 ~ '^[0-9a-f]{64}$'),
  constraint dateien_herkunft_chk check (herkunft in ('upload','scan','nas','version')),
  constraint dateien_fassung_chk check ((wurzel_id is null) = (herkunft <> 'version'))
);
-- Inhalte sind meist schon komprimiert (PDF, Bilder, Office): ohne Kompression speichern, das spart Rechenzeit
alter table __SCHEMA__.dateien alter column inhalt set storage external;
create index if not exists dateien_user_idx on __SCHEMA__.dateien (user_id, erstellt_at desc);
create index if not exists dateien_wurzel_idx on __SCHEMA__.dateien (wurzel_id, erstellt_at desc) where wurzel_id is not null;
create index if not exists dateien_hochgeladen_idx on __SCHEMA__.dateien (hochgeladen_von) where hochgeladen_von is not null;
create index if not exists dateien_geloescht_idx on __SCHEMA__.dateien (geloescht_at) where geloescht_at is not null;

create or replace function __SCHEMA__.dateien_guard() returns trigger
language plpgsql set search_path = '' as $$
declare
  v_wurzel record;
begin
  if tg_op = 'UPDATE' then
    raise exception 'dateien: Dateien sind unveraenderlich (neue Fassung anlegen)' using errcode = '42501';
  end if;
  -- Groesse und Pruefsumme immer selbst berechnen
  new.groesse := octet_length(new.inhalt);
  new.sha256 := encode(pg_catalog.sha256(new.inhalt), 'hex');
  new.erstellt_at := clock_timestamp();   -- auch innerhalb einer Transaktion eindeutig geordnet (neueste Fassung)
  new.geloescht_at := null;
  new.hochgeladen_von := coalesce(new.hochgeladen_von, new.user_id);
  if not exists (select 1 from __SCHEMA__.profiles p where p.user_id = new.user_id and p.aktiv)
     or not exists (select 1 from __SCHEMA__.profiles p where p.user_id = new.hochgeladen_von and p.aktiv) then
    raise exception 'dateien: Profil ist nicht aktiv' using errcode = '42501';
  end if;
  if new.wurzel_id is not null then
    select d.user_id, d.wurzel_id, d.geloescht_at into v_wurzel from __SCHEMA__.dateien d where d.id = new.wurzel_id;
    if not found or v_wurzel.wurzel_id is not null or v_wurzel.user_id <> new.user_id
       or v_wurzel.geloescht_at is not null then
      raise exception 'dateien: Fassung nur zu einer vorhandenen ersten Datei desselben Besitzers' using errcode = '42501';
    end if;
    if (select count(*) from __SCHEMA__.dateien d where d.wurzel_id = new.wurzel_id) >= 20 then
      raise exception 'dateien: hoechstens 20 Fassungen je Datei' using errcode = '54000';
    end if;
  end if;
  -- Kontingent je hochladendem Profil (nacheinander je Profil, damit gleichzeitige Uploads es nicht ueberschreiten)
  perform pg_advisory_xact_lock(hashtext('__SCHEMA__.dateien:' || new.hochgeladen_von::text));
  if (select coalesce(sum(d.groesse), 0) from __SCHEMA__.dateien d
      where d.hochgeladen_von = new.hochgeladen_von and d.geloescht_at is null) + new.groesse > 524288000 then
    raise exception 'dateien: Kontingent von 500 MB erreicht' using errcode = '54000';
  end if;
  if (select count(*) from __SCHEMA__.dateien d
      where d.hochgeladen_von = new.hochgeladen_von and d.geloescht_at is null) >= 2000 then
    raise exception 'dateien: hoechstens 2000 Dateien je Profil' using errcode = '54000';
  end if;
  return new;
end $$;
drop trigger if exists dateien_guard on __SCHEMA__.dateien;
-- nur die geschuetzten Spalten: das ON DELETE SET NULL von hochgeladen_von (Kontoloeschung) und das weiche Loeschen
-- (geloescht_at, nur ueber datei_loeschen) bleiben moeglich
create trigger dateien_guard before insert or update of id, user_id, wurzel_id, name, mime, groesse, sha256, herkunft,
  inhalt, erstellt_at on __SCHEMA__.dateien
  for each row execute function __SCHEMA__.dateien_guard();

-- ---------- Canvas: Fassungen geteilter Dokumente ----------
alter table __SCHEMA__.canvas_items
  add column if not exists bearbeitet_von uuid references __SCHEMA__.profiles(user_id) on delete set null;
alter table __SCHEMA__.canvas_items drop constraint if exists canvas_auftrag_projekt_chk;
-- Projektname gekappt: der Ausdruck steht in einem Index (zu lange Werte sprengten die Indexzeile)
alter table __SCHEMA__.canvas_items add constraint canvas_auftrag_projekt_chk
  check (not (data ? 'auftrag') or length(coalesce((data -> 'auftrag') ->> 'projekt', '')) <= 100);
create index if not exists canvas_bearbeiter_idx on __SCHEMA__.canvas_items (bearbeitet_von) where bearbeitet_von is not null;
create index if not exists canvas_auftrag_idx on __SCHEMA__.canvas_items (user_id, ((data -> 'auftrag') ->> 'projekt'))
  where data ? 'auftrag';
-- je Wurzel jede Fassungsnummer nur einmal (zwei gleichzeitige Bearbeitungen ueberholen sich nicht)
create unique index if not exists canvas_fassung_idx on __SCHEMA__.canvas_items (parent_id, version)
  where parent_id is not null;

-- ---------- Teilungen ----------
create table if not exists __SCHEMA__.teilungen (
  id             uuid primary key default gen_random_uuid(),
  besitzer       uuid not null references __SCHEMA__.profiles(user_id) on delete cascade,
  empfaenger     uuid not null references __SCHEMA__.profiles(user_id) on delete cascade,
  art            text not null,
  canvas_id      uuid references __SCHEMA__.canvas_items(id) on delete cascade,
  datei_id       uuid references __SCHEMA__.dateien(id) on delete cascade,
  projekt        text,
  recht          text not null default 'lesen',
  hinweis        text,            -- kurze Nachricht des Besitzers an den Empfaenger
  erstellt_at    timestamptz not null default now(),
  geaendert_at   timestamptz not null default now(),
  widerrufen_at  timestamptz,
  widerrufen_von uuid references __SCHEMA__.profiles(user_id) on delete set null,
  constraint teilungen_art_chk check (
       (art = 'canvas'  and canvas_id is not null and datei_id is null and projekt is null)
    or (art = 'datei'   and datei_id is not null and canvas_id is null and projekt is null)
    or (art = 'auftrag' and projekt is not null and canvas_id is null and datei_id is null and recht = 'lesen')),
  constraint teilungen_recht_chk check (recht in ('lesen','bearbeiten')),
  constraint teilungen_projekt_chk check (projekt is null or (projekt ~ '^[a-z0-9][a-z0-9-]{1,48}[a-z0-9]$'
                                                             and projekt !~ '--')),
  constraint teilungen_selbst_chk check (besitzer <> empfaenger),
  constraint teilungen_hinweis_chk check (hinweis is null or length(hinweis) <= 300),
  constraint teilungen_widerruf_chk check (widerrufen_von is null or widerrufen_at is not null)
);
create index if not exists teilungen_besitzer_idx on __SCHEMA__.teilungen (besitzer, erstellt_at desc);
create index if not exists teilungen_empfaenger_idx on __SCHEMA__.teilungen (empfaenger);
create index if not exists teilungen_mit_mir_idx on __SCHEMA__.teilungen (empfaenger, erstellt_at desc)
  where widerrufen_at is null;
create index if not exists teilungen_canvas_idx on __SCHEMA__.teilungen (canvas_id) where canvas_id is not null;
create index if not exists teilungen_datei_idx on __SCHEMA__.teilungen (datei_id) where datei_id is not null;
create index if not exists teilungen_widerrufer_idx on __SCHEMA__.teilungen (widerrufen_von) where widerrufen_von is not null;
-- je Objekt und Empfaenger hoechstens eine laufende Teilung
create unique index if not exists teilungen_canvas_aktiv_idx on __SCHEMA__.teilungen (empfaenger, canvas_id)
  where widerrufen_at is null and canvas_id is not null;
create unique index if not exists teilungen_datei_aktiv_idx on __SCHEMA__.teilungen (empfaenger, datei_id)
  where widerrufen_at is null and datei_id is not null;
create unique index if not exists teilungen_projekt_aktiv_idx on __SCHEMA__.teilungen (besitzer, empfaenger, projekt)
  where widerrufen_at is null and projekt is not null;

-- ---------- Protokoll (nur Trigger und Funktionen schreiben, nie Inhalte) ----------
create table if not exists __SCHEMA__.teilung_events (
  id          bigint generated always as identity primary key,
  teilung_id  uuid references __SCHEMA__.teilungen(id) on delete set null,
  besitzer    uuid references __SCHEMA__.profiles(user_id) on delete set null,
  empfaenger  uuid references __SCHEMA__.profiles(user_id) on delete set null,
  durch       uuid references __SCHEMA__.profiles(user_id) on delete set null,
  art         text not null check (art in ('canvas','datei','auftrag')),
  objekt      text check (objekt is null or length(objekt) <= 100),   -- Kennung des Objekts, kein Inhalt
  ereignis    text not null check (ereignis in ('geteilt','recht_geaendert','widerrufen','zurueckgegeben',
                                                'abgerufen','bearbeitet','geloescht')),
  details     jsonb not null default '{}'::jsonb check (jsonb_typeof(details) = 'object' and pg_column_size(details) <= 1024),
  at          timestamptz not null default now()
);
create index if not exists teilung_events_teilung_idx on __SCHEMA__.teilung_events (teilung_id, at desc) where teilung_id is not null;
create index if not exists teilung_events_besitzer_idx on __SCHEMA__.teilung_events (besitzer, at desc) where besitzer is not null;
create index if not exists teilung_events_empfaenger_idx on __SCHEMA__.teilung_events (empfaenger) where empfaenger is not null;
create index if not exists teilung_events_durch_idx on __SCHEMA__.teilung_events (durch) where durch is not null;

-- ---------- Hilfsfunktionen ----------
-- Anzeigename des geteilten Objekts (Titel, Name der neuesten Dateifassung oder Projekt), nie Inhalt
create or replace function __SCHEMA__.teilung_objekt_titel(p_art text, p_canvas uuid, p_datei uuid, p_projekt text)
returns text language sql stable set search_path = '' as $$
  select left(coalesce(case p_art
    when 'canvas' then (select c.title from __SCHEMA__.canvas_items c where c.id = p_canvas)
    when 'datei'  then (select d.name from __SCHEMA__.dateien d where d.id = p_datei or d.wurzel_id = p_datei
                        order by d.erstellt_at desc limit 1)
    when 'auftrag' then 'Projekt ' || p_projekt
  end, 'Ohne Titel'), 120)
$$;

-- Recht eines Profils an einem Canvas-Element (auch an einer Fassung): besitzer | bearbeiten | lesen | null
create or replace function __SCHEMA__.canvas_recht(p_user uuid, p_canvas uuid)
returns text language plpgsql stable set search_path = '' as $$
declare
  v_wurzel record;
  v_recht text;
begin
  select w.id, w.user_id, w.data into v_wurzel from __SCHEMA__.canvas_items c
    join __SCHEMA__.canvas_items w on w.id = coalesce(c.parent_id, c.id)
    where c.id = p_canvas;
  if not found or p_user is null then return null; end if;
  if v_wurzel.user_id = p_user then return 'besitzer'; end if;
  select t.recht into v_recht from __SCHEMA__.teilungen t
    where t.art = 'canvas' and t.canvas_id = v_wurzel.id and t.empfaenger = p_user and t.widerrufen_at is null;
  if found then
    -- Ergebnisse von Entwicklungsauftraegen sind fuer andere immer nur lesbar
    return case when v_wurzel.data ? 'auftrag' then 'lesen' else v_recht end;
  end if;
  if v_wurzel.data ? 'auftrag' and exists (select 1 from __SCHEMA__.teilungen t
       where t.art = 'auftrag' and t.besitzer = v_wurzel.user_id and t.empfaenger = p_user
         and t.widerrufen_at is null and t.projekt = (v_wurzel.data -> 'auftrag') ->> 'projekt') then
    return 'lesen';
  end if;
  return null;
end $$;

-- Recht eines Profils an einer Datei (auch an einer Fassung): besitzer | bearbeiten | lesen | null; geloescht: null
create or replace function __SCHEMA__.datei_recht(p_user uuid, p_datei uuid)
returns text language plpgsql stable set search_path = '' as $$
declare
  v_wurzel record;
  v_recht text;
begin
  select w.id, w.user_id into v_wurzel from __SCHEMA__.dateien d
    join __SCHEMA__.dateien w on w.id = coalesce(d.wurzel_id, d.id)
    where d.id = p_datei and w.geloescht_at is null;
  if not found or p_user is null then return null; end if;
  if v_wurzel.user_id = p_user then return 'besitzer'; end if;
  select t.recht into v_recht from __SCHEMA__.teilungen t
    where t.art = 'datei' and t.datei_id = v_wurzel.id and t.empfaenger = p_user and t.widerrufen_at is null;
  return v_recht;   -- null, wenn nicht geteilt
end $$;

-- Hinweis an den Besitzer nach einer Bearbeitung, nicht vervielfacht: solange ein ungelesener zum Objekt da ist, keiner
create or replace function __SCHEMA__.bearbeitung_melden(p_besitzer uuid, p_bezug uuid, p_titel text, p_daten jsonb)
returns void language sql security definer set search_path = '' as $$
  insert into __SCHEMA__.hinweise (empfaenger, art, titel, bezug, daten)
  select p_besitzer, 'teilung_geaendert', left(p_titel, 200), p_bezug, coalesce(p_daten, '{}'::jsonb)
  where not exists (select 1 from __SCHEMA__.hinweise h where h.empfaenger = p_besitzer and h.bezug = p_bezug
                    and h.art = 'teilung_geaendert' and h.gelesen_at is null)
$$;

-- ---------- Waechter fuer teilungen ----------
create or replace function __SCHEMA__.teilungen_guard() returns trigger
language plpgsql set search_path = '' as $$
begin
  if tg_op = 'INSERT' then
    if not exists (select 1 from __SCHEMA__.profiles p where p.user_id = new.besitzer and p.aktiv) then
      raise exception 'teilungen: Besitzerprofil ist nicht aktiv' using errcode = '42501';
    end if;
    if not exists (select 1 from __SCHEMA__.profiles p where p.user_id = new.empfaenger and p.aktiv) then
      raise exception 'teilungen: Empfaengerprofil ist nicht aktiv' using errcode = '42501';
    end if;
    if new.art = 'canvas' and not exists (select 1 from __SCHEMA__.canvas_items c
         where c.id = new.canvas_id and c.user_id = new.besitzer and c.parent_id is null) then
      raise exception 'teilungen: nur eigene Canvas-Elemente (erste Fassung) teilen' using errcode = '42501';
    end if;
    if new.art = 'datei' and not exists (select 1 from __SCHEMA__.dateien d
         where d.id = new.datei_id and d.user_id = new.besitzer and d.wurzel_id is null and d.geloescht_at is null) then
      raise exception 'teilungen: nur eigene Dateien (erste Fassung) teilen' using errcode = '42501';
    end if;
    if new.art = 'auftrag' and not exists (select 1 from __SCHEMA__.canvas_items c
         where c.user_id = new.besitzer and c.data ? 'auftrag'
           and (c.data -> 'auftrag') ->> 'projekt' = new.projekt) then
      raise exception 'teilungen: zu diesem Projekt gibt es keine eigenen Ergebnisse' using errcode = '42501';
    end if;
    new.erstellt_at := now(); new.geaendert_at := now();
    new.widerrufen_at := null; new.widerrufen_von := null;
  else
    if new.id is distinct from old.id or new.besitzer is distinct from old.besitzer
       or new.empfaenger is distinct from old.empfaenger or new.art is distinct from old.art
       or new.canvas_id is distinct from old.canvas_id or new.datei_id is distinct from old.datei_id
       or new.projekt is distinct from old.projekt or new.hinweis is distinct from old.hinweis
       or new.erstellt_at is distinct from old.erstellt_at then
      raise exception 'teilungen: nur Recht und Widerruf sind aenderbar' using errcode = '42501';
    end if;
    if old.widerrufen_at is not null then
      -- endgueltig; Ausnahme: FK set null, wenn das widerrufende Profil geloescht wird
      if new.widerrufen_at is distinct from old.widerrufen_at or new.recht is distinct from old.recht
         or (new.widerrufen_von is distinct from old.widerrufen_von and new.widerrufen_von is not null) then
        raise exception 'teilungen: widerrufene Teilungen sind endgueltig' using errcode = '42501';
      end if;
      return new;
    end if;
    if new.widerrufen_at is not null or new.widerrufen_von is not null then
      if new.widerrufen_von is null or new.widerrufen_von not in (old.besitzer, old.empfaenger) then
        raise exception 'teilungen: widerrufen nur durch Besitzer oder Empfaenger' using errcode = '42501';
      end if;
      if new.recht is distinct from old.recht then
        raise exception 'teilungen: Widerruf und Rechteaenderung nicht zugleich' using errcode = '42501';
      end if;
      new.widerrufen_at := now();
    end if;
    new.geaendert_at := now();
  end if;
  -- Ergebnisse von Entwicklungsauftraegen nie zum Bearbeiten teilen
  if new.art = 'canvas' and new.recht = 'bearbeiten' and new.widerrufen_at is null and exists (
       select 1 from __SCHEMA__.canvas_items c where c.id = new.canvas_id and c.data ? 'auftrag') then
    raise exception 'teilungen: Ergebnisse von Entwicklungsauftraegen nur zum Lesen' using errcode = '42501';
  end if;
  return new;
end $$;
drop trigger if exists teilungen_guard on __SCHEMA__.teilungen;
create trigger teilungen_guard before insert or update on __SCHEMA__.teilungen
  for each row execute function __SCHEMA__.teilungen_guard();

-- Protokoll und Hinweise (SECURITY DEFINER: niemand braucht Schreibrechte auf teilung_events oder fremde Hinweise)
create or replace function __SCHEMA__.teilungen_ereignis() returns trigger
language plpgsql security definer set search_path = '' as $$
declare
  v_titel text;
  v_besitzer text;
  v_empfaenger text;
  v_objekt text;
  v_recht_text text;
begin
  v_titel := __SCHEMA__.teilung_objekt_titel(new.art, new.canvas_id, new.datei_id, new.projekt);
  select p.display_name into v_besitzer from __SCHEMA__.profiles p where p.user_id = new.besitzer;
  select p.display_name into v_empfaenger from __SCHEMA__.profiles p where p.user_id = new.empfaenger;
  v_objekt := coalesce(new.canvas_id::text, new.datei_id::text, new.projekt);
  v_recht_text := case new.recht when 'bearbeiten' then 'lesen und bearbeiten' else 'lesen' end;
  if tg_op = 'INSERT' then
    insert into __SCHEMA__.teilung_events (teilung_id, besitzer, empfaenger, durch, art, objekt, ereignis, details)
    values (new.id, new.besitzer, new.empfaenger, new.besitzer, new.art, v_objekt, 'geteilt',
            jsonb_build_object('recht', new.recht));
    insert into __SCHEMA__.hinweise (empfaenger, art, titel, text, bezug, daten)
    values (new.empfaenger, 'teilung_erhalten',
            left(coalesce(v_besitzer, 'Jemand') || ' hat „' || v_titel || '“ mit dir geteilt', 200),
            left('Recht: ' || v_recht_text || case when new.hinweis is not null then '. Nachricht: ' || new.hinweis else '' end, 1000),
            new.id, jsonb_build_object('teilung', new.id, 'art', new.art, 'recht', new.recht));
  elsif new.widerrufen_at is not null and old.widerrufen_at is null then
    insert into __SCHEMA__.teilung_events (teilung_id, besitzer, empfaenger, durch, art, objekt, ereignis)
    values (new.id, new.besitzer, new.empfaenger, new.widerrufen_von, new.art, v_objekt,
            case when new.widerrufen_von = new.besitzer then 'widerrufen' else 'zurueckgegeben' end);
    if new.widerrufen_von = new.besitzer then
      insert into __SCHEMA__.hinweise (empfaenger, art, titel, bezug, daten)
      values (new.empfaenger, 'teilung_widerrufen',
              left(coalesce(v_besitzer, 'Jemand') || ' teilt „' || v_titel || '“ nicht mehr mit dir', 200),
              new.id, jsonb_build_object('teilung', new.id, 'art', new.art));
    else
      insert into __SCHEMA__.hinweise (empfaenger, art, titel, bezug, daten)
      values (new.besitzer, 'teilung_widerrufen',
              left(coalesce(v_empfaenger, 'Jemand') || ' hat „' || v_titel || '“ zurückgegeben', 200),
              new.id, jsonb_build_object('teilung', new.id, 'art', new.art));
    end if;
  elsif new.recht is distinct from old.recht then
    insert into __SCHEMA__.teilung_events (teilung_id, besitzer, empfaenger, durch, art, objekt, ereignis, details)
    values (new.id, new.besitzer, new.empfaenger, new.besitzer, new.art, v_objekt, 'recht_geaendert',
            jsonb_build_object('von', old.recht, 'nach', new.recht));
    insert into __SCHEMA__.hinweise (empfaenger, art, titel, bezug, daten)
    values (new.empfaenger, 'teilung_geaendert',
            left('„' || v_titel || '“: dein Recht ist jetzt ' || v_recht_text, 200),
            new.id, jsonb_build_object('teilung', new.id, 'art', new.art, 'recht', new.recht));
  end if;
  return null;
end $$;
drop trigger if exists teilungen_ereignis on __SCHEMA__.teilungen;
create trigger teilungen_ereignis after insert or update on __SCHEMA__.teilungen
  for each row execute function __SCHEMA__.teilungen_ereignis();

-- ---------- Funktionen fuer den Kern (SECURITY DEFINER, pruefen Besitz und Recht selbst) ----------
-- Recht einer laufenden Teilung aendern: nur der Besitzer
create or replace function __SCHEMA__.teilung_recht_setzen(p_user uuid, p_teilung uuid, p_recht text)
returns boolean language plpgsql security definer set search_path = '' as $$
begin
  update __SCHEMA__.teilungen t set recht = p_recht
    where t.id = p_teilung and t.besitzer = p_user and t.widerrufen_at is null and t.recht is distinct from p_recht;
  return found or exists (select 1 from __SCHEMA__.teilungen t where t.id = p_teilung and t.besitzer = p_user
                          and t.widerrufen_at is null);
end $$;

-- Teilung beenden: Besitzer widerruft, Empfaenger gibt zurueck. Liefert widerrufen | zurueckgegeben | null
create or replace function __SCHEMA__.teilung_beenden(p_user uuid, p_teilung uuid)
returns text language plpgsql security definer set search_path = '' as $$
declare
  t record;
begin
  select * into t from __SCHEMA__.teilungen x where x.id = p_teilung and x.widerrufen_at is null
    and p_user in (x.besitzer, x.empfaenger) for update;
  if not found then return null; end if;
  update __SCHEMA__.teilungen x set widerrufen_at = now(), widerrufen_von = p_user where x.id = p_teilung;
  return case when p_user = t.besitzer then 'widerrufen' else 'zurueckgegeben' end;
end $$;

-- Abruf durch einen Empfaenger protokollieren (hoechstens einmal je 5 Minuten, Teilung und Profil)
create or replace function __SCHEMA__.teilung_abgerufen(p_user uuid, p_teilung uuid)
returns boolean language plpgsql security definer set search_path = '' as $$
declare
  t record;
begin
  select * into t from __SCHEMA__.teilungen x where x.id = p_teilung and x.empfaenger = p_user and x.widerrufen_at is null;
  if not found then return false; end if;
  if not exists (select 1 from __SCHEMA__.teilung_events e where e.teilung_id = p_teilung and e.durch = p_user
                 and e.ereignis = 'abgerufen' and e.at > now() - interval '5 minutes') then
    insert into __SCHEMA__.teilung_events (teilung_id, besitzer, empfaenger, durch, art, objekt, ereignis)
    values (t.id, t.besitzer, t.empfaenger, p_user, t.art, coalesce(t.canvas_id::text, t.datei_id::text, t.projekt),
            'abgerufen');
  end if;
  return true;
end $$;

-- Neue Fassung eines Canvas-Elements durch den Besitzer oder einen Empfaenger mit Recht bearbeiten. Der Titel bleibt
-- der der Wurzel. p_basis: die Fassungsnummer, auf der die Bearbeitung beruht; ist inzwischen eine neuere da, gibt es
-- den Konflikt P0409 (nichts wird ueberschrieben). Liefert die neue Fassung und ihre Nummer.
create or replace function __SCHEMA__.canvas_fassung_anlegen(p_user uuid, p_canvas uuid, p_data jsonb,
                                                             p_basis integer default null)
returns table (fassung uuid, version integer)
language plpgsql security definer set search_path = '' as $$
declare
  v_recht text;
  v_wurzel_id uuid;
  v_wurzel __SCHEMA__.canvas_items%rowtype;
  v_aktuell integer;
  v_id uuid;
  v_name text;
  t record;
begin
  v_recht := __SCHEMA__.canvas_recht(p_user, p_canvas);
  if v_recht is null or v_recht not in ('besitzer','bearbeiten') then
    raise exception 'canvas_fassung_anlegen: kein Recht zum Bearbeiten' using errcode = '42501';
  end if;
  if p_data is null or jsonb_typeof(p_data) <> 'object' or pg_column_size(p_data) > 262144 then
    raise exception 'canvas_fassung_anlegen: Inhalt fehlt oder ist zu gross' using errcode = '22023';
  end if;
  select coalesce(c.parent_id, c.id) into v_wurzel_id from __SCHEMA__.canvas_items c where c.id = p_canvas;
  -- Wurzel sperren: Fassungen derselben Wurzel entstehen nacheinander
  select w.* into v_wurzel from __SCHEMA__.canvas_items w where w.id = v_wurzel_id for no key update;
  if v_wurzel.data ? 'auftrag' and p_user <> v_wurzel.user_id then
    raise exception 'canvas_fassung_anlegen: Ergebnisse von Entwicklungsauftraegen nur zum Lesen' using errcode = '42501';
  end if;
  select max(c.version) into v_aktuell from __SCHEMA__.canvas_items c where c.id = v_wurzel.id or c.parent_id = v_wurzel.id;
  if p_basis is not null and p_basis <> v_aktuell then
    raise exception 'canvas_fassung_anlegen: inzwischen gibt es eine neuere Fassung' using errcode = 'P0409';
  end if;
  if (select count(*) from __SCHEMA__.canvas_items c where c.parent_id = v_wurzel.id) >= 100 then
    raise exception 'canvas_fassung_anlegen: hoechstens 100 Fassungen' using errcode = '54000';
  end if;
  -- Titel und Projektzuordnung der Wurzel bleiben (kein irrefuehrender Titel, kein untergeschobenes Projekt)
  p_data := jsonb_set(p_data, '{title}', to_jsonb(v_wurzel.title));
  if v_wurzel.data ? 'auftrag' then
    p_data := jsonb_set(p_data, '{auftrag}', v_wurzel.data -> 'auftrag');
  else
    p_data := p_data - 'auftrag';
  end if;
  insert into __SCHEMA__.canvas_items (user_id, session_id, type, title, data, version, parent_id, bearbeitet_von)
  values (v_wurzel.user_id, null, v_wurzel.type, v_wurzel.title, p_data, v_aktuell + 1, v_wurzel.id, p_user)
  returning id into v_id;
  if p_user <> v_wurzel.user_id then
    select t2.* into t from __SCHEMA__.teilungen t2 where t2.art = 'canvas' and t2.canvas_id = v_wurzel.id
      and t2.empfaenger = p_user and t2.widerrufen_at is null;
    if found then
      insert into __SCHEMA__.teilung_events (teilung_id, besitzer, empfaenger, durch, art, objekt, ereignis, details)
      values (t.id, v_wurzel.user_id, p_user, p_user, 'canvas', v_wurzel.id::text, 'bearbeitet',
              jsonb_build_object('fassung', v_id, 'version', v_aktuell + 1));
    end if;
    select p.display_name into v_name from __SCHEMA__.profiles p where p.user_id = p_user;
    perform __SCHEMA__.bearbeitung_melden(v_wurzel.user_id, v_wurzel.id,
      coalesce(v_name, 'Jemand') || ' hat „' || left(v_wurzel.title, 120) || '“ bearbeitet',
      jsonb_build_object('canvas', v_wurzel.id, 'fassung', v_id));
  end if;
  return query select v_id, v_aktuell + 1;
end $$;

-- Neue Fassung einer Datei durch den Besitzer oder einen Empfaenger mit Recht bearbeiten. Gleiche Endung wie die
-- erste Datei; die Fassung zaehlt auf das Kontingent des Bearbeiters.
create or replace function __SCHEMA__.datei_fassung_anlegen(p_user uuid, p_datei uuid, p_name text, p_mime text,
                                                            p_inhalt bytea)
returns uuid language plpgsql security definer set search_path = '' as $$
declare
  v_recht text;
  v_wurzel record;
  v_id uuid;
  v_name text;
  t record;
begin
  v_recht := __SCHEMA__.datei_recht(p_user, p_datei);
  if v_recht is null or v_recht not in ('besitzer','bearbeiten') then
    raise exception 'datei_fassung_anlegen: kein Recht zum Bearbeiten' using errcode = '42501';
  end if;
  select w.id, w.user_id, w.name into v_wurzel from __SCHEMA__.dateien d
    join __SCHEMA__.dateien w on w.id = coalesce(d.wurzel_id, d.id) where d.id = p_datei;
  if lower(substring(p_name from '\.([^.]+)$')) is distinct from lower(substring(v_wurzel.name from '\.([^.]+)$')) then
    raise exception 'datei_fassung_anlegen: die Fassung braucht dieselbe Endung' using errcode = '22023';
  end if;
  -- groesse und sha256 setzt der Waechter aus dem Inhalt
  insert into __SCHEMA__.dateien (user_id, wurzel_id, hochgeladen_von, name, mime, groesse, sha256, herkunft, inhalt)
  values (v_wurzel.user_id, v_wurzel.id, p_user, p_name, p_mime, 1, repeat('0', 64), 'version', p_inhalt)
  returning id into v_id;
  if p_user <> v_wurzel.user_id then
    select t2.* into t from __SCHEMA__.teilungen t2 where t2.art = 'datei' and t2.datei_id = v_wurzel.id
      and t2.empfaenger = p_user and t2.widerrufen_at is null;
    if found then
      insert into __SCHEMA__.teilung_events (teilung_id, besitzer, empfaenger, durch, art, objekt, ereignis, details)
      values (t.id, v_wurzel.user_id, p_user, p_user, 'datei', v_wurzel.id::text, 'bearbeitet',
              jsonb_build_object('fassung', v_id));
    end if;
    select p.display_name into v_name from __SCHEMA__.profiles p where p.user_id = p_user;
    perform __SCHEMA__.bearbeitung_melden(v_wurzel.user_id, v_wurzel.id,
      coalesce(v_name, 'Jemand') || ' hat eine neue Fassung von „' || left(v_wurzel.name, 120) || '“ abgelegt',
      jsonb_build_object('datei', v_wurzel.id, 'fassung', v_id));
  end if;
  return v_id;
end $$;

-- Eigene Datei samt Fassungen loeschen (zuerst weich): laufende Teilungen werden widerrufen, nach 7 Tagen endgueltig
create or replace function __SCHEMA__.datei_loeschen(p_user uuid, p_datei uuid)
returns boolean language plpgsql security definer set search_path = '' as $$
declare
  v_wurzel record;
begin
  select d.id, d.user_id, d.wurzel_id, d.geloescht_at into v_wurzel from __SCHEMA__.dateien d where d.id = p_datei;
  if not found or v_wurzel.user_id <> p_user or v_wurzel.wurzel_id is not null or v_wurzel.geloescht_at is not null then
    return false;
  end if;
  update __SCHEMA__.teilungen t set widerrufen_at = now(), widerrufen_von = p_user
    where t.datei_id = v_wurzel.id and t.widerrufen_at is null;
  update __SCHEMA__.dateien d set geloescht_at = now() where d.id = v_wurzel.id or d.wurzel_id = v_wurzel.id;
  insert into __SCHEMA__.teilung_events (besitzer, durch, art, objekt, ereignis)
  values (p_user, p_user, 'datei', v_wurzel.id::text, 'geloescht');
  return true;
end $$;

-- Weich geloeschte Dateien nach 7 Tagen endgueltig entfernen (Fassungen und Teilungen per Kaskade). Ruft der Kern
-- einmal taeglich auf; liefert die Zahl der entfernten Dateien.
create or replace function __SCHEMA__.dateien_endgueltig_loeschen()
returns integer language plpgsql security definer set search_path = '' as $$
declare
  n integer;
begin
  delete from __SCHEMA__.dateien d where d.wurzel_id is null and d.geloescht_at < now() - interval '7 days';
  get diagnostics n = row_count;
  return n;
end $$;

-- ---------- Rechte ----------
revoke all on __SCHEMA__.dateien, __SCHEMA__.teilungen, __SCHEMA__.teilung_events
  from public, anon, authenticated, service_role;
revoke all on function __SCHEMA__.dateien_guard(), __SCHEMA__.teilungen_guard(), __SCHEMA__.teilungen_ereignis(),
  __SCHEMA__.teilung_objekt_titel(text, uuid, uuid, text), __SCHEMA__.canvas_recht(uuid, uuid),
  __SCHEMA__.datei_recht(uuid, uuid), __SCHEMA__.bearbeitung_melden(uuid, uuid, text, jsonb),
  __SCHEMA__.teilung_recht_setzen(uuid, uuid, text), __SCHEMA__.teilung_beenden(uuid, uuid),
  __SCHEMA__.teilung_abgerufen(uuid, uuid), __SCHEMA__.canvas_fassung_anlegen(uuid, uuid, jsonb, integer),
  __SCHEMA__.datei_fassung_anlegen(uuid, uuid, text, text, bytea), __SCHEMA__.datei_loeschen(uuid, uuid),
  __SCHEMA__.dateien_endgueltig_loeschen()
  from public, anon, authenticated, service_role;
-- Voice-Core: Dateien ablegen und lesen, teilen; alles andere (Recht, Ende, Fassungen, Abruf, Loeschen) nur ueber die
-- Funktionen mit eigener Pruefung; Protokoll lesen (der Kern zeigt nur das eigene)
grant select on __SCHEMA__.dateien to wamo_core;
grant insert (user_id, name, mime, groesse, sha256, herkunft, inhalt) on __SCHEMA__.dateien to wamo_core;
grant select on __SCHEMA__.teilungen to wamo_core;
grant insert (besitzer, empfaenger, art, canvas_id, datei_id, projekt, recht, hinweis) on __SCHEMA__.teilungen to wamo_core;
revoke update on __SCHEMA__.teilungen from wamo_core;
grant select on __SCHEMA__.teilung_events to wamo_core;
grant execute on function __SCHEMA__.teilung_objekt_titel(text, uuid, uuid, text), __SCHEMA__.canvas_recht(uuid, uuid),
  __SCHEMA__.datei_recht(uuid, uuid), __SCHEMA__.teilung_recht_setzen(uuid, uuid, text),
  __SCHEMA__.teilung_beenden(uuid, uuid), __SCHEMA__.teilung_abgerufen(uuid, uuid),
  __SCHEMA__.canvas_fassung_anlegen(uuid, uuid, jsonb, integer),
  __SCHEMA__.datei_fassung_anlegen(uuid, uuid, text, text, bytea), __SCHEMA__.datei_loeschen(uuid, uuid),
  __SCHEMA__.dateien_endgueltig_loeschen()
  to wamo_core;
-- Verwaltung: nur Metadaten (wer hat was mit wem geteilt, Protokoll), keine Inhalte
grant select on __SCHEMA__.teilungen, __SCHEMA__.teilung_events to wamo_admin;

alter table __SCHEMA__.dateien enable row level security;
alter table __SCHEMA__.teilungen enable row level security;
alter table __SCHEMA__.teilung_events enable row level security;
drop policy if exists core_all on __SCHEMA__.dateien;
create policy core_all on __SCHEMA__.dateien for all to wamo_core using (true) with check (true);
drop policy if exists core_all on __SCHEMA__.teilungen;
create policy core_all on __SCHEMA__.teilungen for all to wamo_core using (true) with check (true);
drop policy if exists admin_all on __SCHEMA__.teilungen;
create policy admin_all on __SCHEMA__.teilungen for all to wamo_admin using (true) with check (true);
drop policy if exists core_all on __SCHEMA__.teilung_events;
create policy core_all on __SCHEMA__.teilung_events for all to wamo_core using (true) with check (true);
drop policy if exists admin_all on __SCHEMA__.teilung_events;
create policy admin_all on __SCHEMA__.teilung_events for all to wamo_admin using (true) with check (true);

insert into __SCHEMA__.schema_migrations (version) values ('0003_wamo_teilen') on conflict do nothing;
