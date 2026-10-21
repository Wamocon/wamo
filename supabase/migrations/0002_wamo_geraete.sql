-- 0002_wamo_geraete: Geraeteregister (persoenliche Laptops mit Ruecktunnel, gemeinsame Buerogeraete) und Hinweise.
-- Anwenden (je Schema, immer in EINER Transaktion, zweimal hintereinander fehlerfrei):
--   bash scripts/render-migration.sh <schema> supabase/migrations/0002_wamo_geraete.sql \
--     | docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 --single-transaction
-- Rueckweg: 0002_wamo_geraete.down.sql (nur solange devices leer ist).
--
-- Grundsaetze
-- * devices aus 0001 war leer und ungenutzt; es wird zum Register fuer alle Geraete. Jedes Geraet hat eine eindeutige,
--   nie wiederverwendete kennung. Adressen stehen nicht hier, sondern nur in /etc/wamo/geraete/geraete.toml.
-- * Persoenliches Geraet = genau ein Besitzerprofil (user_id), ein SSH-Schluessel (ed25519) und ein Tunnelplatz
--   (tunnel_slot 1 bis 64, Unix-Benutzer wamo-t-NN auf Sokrates-2). Die Identitaet ist der Schluessel, nicht
--   Rechnername oder Adresse; der Fingerabdruck wird hier aus dem Schluessel berechnet, nie uebernommen.
-- * Erste Bindung (geraet_binden, nur wamo_core): automatisch aktiv nur fuer ein Mitglied, das noch NIE ein
--   persoenliches Geraet hatte (auch kein widerrufenes), mit neuem Schluessel und einem Rechnernamen, den kein
--   anderes Profil hat. Admin-Profile, Geraetewechsel und alles andere: beantragt, dann entscheidet ein Admin.
--   Jede Bindung erzeugt einen Hinweis an alle aktiven Admins.
-- * Ein Geraet wechselt nie den Besitzer (Neubindung = widerrufen und neu binden, nur Admin).
-- * Statuswechsel nur durch einen aktiven Admin (geaendert_von), Verlauf in device_events (Trigger). Ausnahme: wird
--   ein Profil deaktiviert, sperrt die Datenbank dessen Geraete selbst (devices_profil_deaktiviert).
-- * Vertrag fuer alle Verbraucher (Abgleich der SSH-Schluessel auf Sokrates-2, Kern, Tunnel): gilt nur ein Geraet mit
--   status 'aktiv' und aktivem Besitzerprofil (pc_abgleich_liste). Plaetze werden nach dem Widerruf mindestens eine
--   Stunde nicht neu vergeben. Persoenliche Kennungen beginnen mit 'pc-', gemeinsame nie.
-- * Die Datenbank kann den Akteur nicht pruefen (der Kern arbeitet ohne JWT): Schluesselbesitz und zweiten Faktor
--   prueft der Kern vor geraet_binden; die Datenbank begrenzt, was ein kompromittierter Kern anrichten kann.

do $$ begin
  if not exists (select 1 from __SCHEMA__.schema_migrations where version = '0001_wamo_init') then
    raise exception '0002 braucht 0001';
  end if;
end $$;

-- ---------- Typen ----------
do $$ begin create type __SCHEMA__.geraet_art as enum ('persoenlich','gemeinsam');
  exception when duplicate_object then null; end $$;
do $$ begin create type __SCHEMA__.geraet_klasse as enum
  ('laptop','pc','mac','server','drucker','scanner','router','nas','fernseher','lautsprecher','steckdose');
  exception when duplicate_object then null; end $$;
do $$ begin create type __SCHEMA__.geraet_status as enum ('beantragt','aktiv','gesperrt','widerrufen');
  exception when duplicate_object then null; end $$;
do $$ begin create type __SCHEMA__.geraet_bindung as enum ('automatisch','admin');
  exception when duplicate_object then null; end $$;

-- ---------- Umbau von devices (nur bei leerer Tabelle; ein zweiter Lauf erkennt die Spalte art) ----------
do $$ begin
  if not exists (select 1 from information_schema.columns where table_schema = '__SCHEMA__'
                 and table_name = 'devices' and column_name = 'art')
     and exists (select 1 from __SCHEMA__.devices) then
    raise exception '0002: devices enthaelt Zeilen, Umbau abgebrochen';
  end if;
end $$;

alter table __SCHEMA__.devices drop constraint if exists devices_tunnel_port_key;
alter table __SCHEMA__.devices drop constraint if exists devices_tunnel_port_check;
alter table __SCHEMA__.devices drop constraint if exists devices_kind_check;
alter table __SCHEMA__.devices drop constraint if exists devices_user_id_fkey;
alter table __SCHEMA__.devices drop column if exists tunnel_port;
alter table __SCHEMA__.devices drop column if exists kind;
alter table __SCHEMA__.devices alter column user_id drop not null;   -- Besitzerprofil, bei gemeinsam null
alter table __SCHEMA__.devices
  add column if not exists art             __SCHEMA__.geraet_art not null,
  add column if not exists klasse          __SCHEMA__.geraet_klasse not null,
  add column if not exists zugang          text not null,
  add column if not exists status          __SCHEMA__.geraet_status not null default 'beantragt',
  add column if not exists bindung         __SCHEMA__.geraet_bindung not null default 'admin',
  add column if not exists kennung         text not null,   -- fuer alle Geraete, nie wiederverwendet
  add column if not exists tunnel_slot     smallint,        -- nur persoenlich: wamo-t-NN, /run/wamo-pc/tNN
  add column if not exists ssh_pubkey      text,            -- nur persoenlich: ssh-ed25519 AAAA...
  add column if not exists ssh_fingerprint text,            -- berechnet vom Trigger aus ssh_pubkey
  add column if not exists beschreibung    text,
  add column if not exists freigegeben_von uuid,
  add column if not exists freigegeben_at  timestamptz,
  add column if not exists geaendert_von   uuid,            -- Admin, der den letzten Statuswechsel ausgeloest hat
  add column if not exists gesperrt_at     timestamptz,
  add column if not exists gesperrt_grund  text,
  add column if not exists updated_at      timestamptz not null default now();
-- last_seen_at aus 0001 bleibt: letzte erfolgreiche Verbindung des Kerns.

alter table __SCHEMA__.devices
  -- set null + Pruefungen: ein Konto mit beantragtem, aktivem oder gesperrtem Geraet laesst sich nicht loeschen,
  -- nach dem Widerruf schon (Besitzer wird null, die Zeile bleibt fuer das Audit)
  drop constraint if exists devices_besitzer_fk,
  add  constraint devices_besitzer_fk foreign key (user_id) references __SCHEMA__.profiles(user_id) on delete set null,
  drop constraint if exists devices_freigeber_fk,
  add  constraint devices_freigeber_fk foreign key (freigegeben_von) references __SCHEMA__.profiles(user_id) on delete set null,
  drop constraint if exists devices_aenderer_fk,
  add  constraint devices_aenderer_fk foreign key (geaendert_von) references __SCHEMA__.profiles(user_id) on delete set null,
  drop constraint if exists devices_art_chk,
  add  constraint devices_art_chk check (
         (art = 'gemeinsam' and user_id is null and tunnel_slot is null and ssh_pubkey is null and bindung = 'admin')
      or (art = 'persoenlich' and (user_id is not null or status = 'widerrufen'))),
  drop constraint if exists devices_persoenlich_chk,
  add  constraint devices_persoenlich_chk check (art = 'gemeinsam' or (zugang = 'tunnel'
         and klasse in ('laptop','pc','mac') and ssh_pubkey is not null and ssh_fingerprint is not null
         and tunnel_slot is not null)),
  drop constraint if exists devices_gemeinsam_chk,
  add  constraint devices_gemeinsam_chk check (art = 'persoenlich' or (zugang <> 'tunnel' and ssh_fingerprint is null)),
  drop constraint if exists devices_zugang_chk,
  add  constraint devices_zugang_chk check (zugang ~ '^[a-z][a-z0-9-]{1,19}$'),
  drop constraint if exists devices_kennung_chk,
  add  constraint devices_kennung_chk check (kennung ~ '^[a-z][a-z0-9-]{1,21}[a-z0-9]$' and kennung !~ '--'),
  drop constraint if exists devices_praefix_chk,
  add  constraint devices_praefix_chk check ((art = 'persoenlich') = (kennung like 'pc-%')),
  drop constraint if exists devices_slot_chk,
  add  constraint devices_slot_chk check (tunnel_slot is null or tunnel_slot between 1 and 64),
  drop constraint if exists devices_pubkey_chk,
  add  constraint devices_pubkey_chk check (ssh_pubkey is null
         or ssh_pubkey ~ '^ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI[A-P][A-Za-z0-9+/]{42}$'),
  drop constraint if exists devices_fp_chk,
  add  constraint devices_fp_chk check (ssh_fingerprint is null or ssh_fingerprint ~ '^SHA256:[A-Za-z0-9+/]{43}$'),
  drop constraint if exists devices_hostname_chk,
  add  constraint devices_hostname_chk check (hostname is null or hostname ~ '^[A-Za-z0-9][A-Za-z0-9-]{0,62}$'),
  drop constraint if exists devices_text_chk,
  add  constraint devices_text_chk check ((beschreibung is null or length(beschreibung) <= 500)
         and (gesperrt_grund is null or length(gesperrt_grund) <= 200)),
  drop constraint if exists devices_sperre_chk,
  add  constraint devices_sperre_chk check ((status in ('gesperrt','widerrufen')) = (gesperrt_at is not null)),
  drop constraint if exists devices_bindung_chk,
  add  constraint devices_bindung_chk check (bindung = 'admin' or art = 'persoenlich');

drop index if exists __SCHEMA__.devices_user_idx;
create index if not exists devices_user_idx on __SCHEMA__.devices (user_id) where user_id is not null;
create index if not exists devices_freigeber_idx on __SCHEMA__.devices (freigegeben_von) where freigegeben_von is not null;
create index if not exists devices_aenderer_idx on __SCHEMA__.devices (geaendert_von) where geaendert_von is not null;
create unique index if not exists devices_kennung_idx on __SCHEMA__.devices (kennung);
create unique index if not exists devices_fp_idx on __SCHEMA__.devices (ssh_fingerprint) where ssh_fingerprint is not null;
-- ein aktives persoenliches Geraet je Profil (ein Laptop je Person)
create unique index if not exists devices_ein_aktives_idx on __SCHEMA__.devices (user_id)
  where art = 'persoenlich' and status = 'aktiv';
-- ein Tunnelplatz gehoert hoechstens einem nicht widerrufenen Geraet (nach dem Widerruf wird er frei)
create unique index if not exists devices_slot_idx on __SCHEMA__.devices (tunnel_slot)
  where tunnel_slot is not null and status <> 'widerrufen';
create index if not exists devices_hostname_idx on __SCHEMA__.devices (lower(hostname))
  where art = 'persoenlich' and status <> 'widerrufen';

-- ---------- Verlauf der Statuswechsel (nur der Trigger schreibt) ----------
create table if not exists __SCHEMA__.device_events (
  id        bigint generated always as identity primary key,
  device_id uuid not null references __SCHEMA__.devices(id) on delete cascade,
  von       __SCHEMA__.geraet_status,
  nach      __SCHEMA__.geraet_status not null,
  durch     uuid references __SCHEMA__.profiles(user_id) on delete set null,
  grund     text check (grund is null or length(grund) <= 200),
  at        timestamptz not null default now()
);
create index if not exists device_events_device_idx on __SCHEMA__.device_events (device_id, at desc);
create index if not exists device_events_durch_idx on __SCHEMA__.device_events (durch) where durch is not null;

-- ---------- Hinweise an Profile (Karten in der Oberflaeche: neue Geraete fuer Admins, geteilte Arbeit) ----------
create table if not exists __SCHEMA__.hinweise (
  id          uuid primary key default gen_random_uuid(),
  empfaenger  uuid not null references __SCHEMA__.profiles(user_id) on delete cascade,
  art         text not null,
  titel       text not null check (length(titel) between 1 and 200),
  text        text check (text is null or length(text) <= 1000),
  bezug       uuid,   -- Geraet oder geteilte Arbeit; bewusst ohne Fremdschluessel (der Hinweis ueberlebt das Objekt)
  daten       jsonb not null default '{}'::jsonb check (jsonb_typeof(daten) = 'object' and pg_column_size(daten) <= 4096),
  erstellt_at timestamptz not null default now(),
  gelesen_at  timestamptz,
  constraint hinweise_art_chk check (art in ('geraet_gebunden','geraet_beantragt','geraet_konflikt',
    'geraet_gesperrt','geraet_entsperrt','geraet_widerrufen','teilung_erhalten','teilung_geaendert',
    'teilung_widerrufen','scan_fertig','system')),
  constraint hinweise_gelesen_chk check (gelesen_at is null or gelesen_at >= erstellt_at)
);
create index if not exists hinweise_empfaenger_idx on __SCHEMA__.hinweise (empfaenger, erstellt_at desc);
create index if not exists hinweise_offen_idx on __SCHEMA__.hinweise (empfaenger, erstellt_at desc) where gelesen_at is null;
create index if not exists hinweise_bezug_idx on __SCHEMA__.hinweise (bezug, erstellt_at desc) where bezug is not null;

-- ---------- Hilfsfunktionen ----------
-- SHA256-Fingerabdruck wie ssh-keygen -l (base64 ohne Auffuellung) aus "ssh-ed25519 <base64>"
create or replace function __SCHEMA__.ssh_fingerabdruck(p_pubkey text) returns text
language sql immutable strict set search_path = '' as $$
  select 'SHA256:' || rtrim(encode(pg_catalog.sha256(decode(split_part(p_pubkey, ' ', 2), 'base64')), 'base64'), '=')
$$;

-- Hinweis an alle aktiven Admins (nur aus SECURITY-DEFINER-Funktionen dieser Migration)
create or replace function __SCHEMA__.hinweis_an_admins(p_art text, p_titel text, p_text text, p_bezug uuid, p_daten jsonb)
returns void language sql security definer set search_path = '' as $$
  insert into __SCHEMA__.hinweise (empfaenger, art, titel, text, bezug, daten)
  select p.user_id, p_art, left(p_titel, 200), left(p_text, 1000), p_bezug, coalesce(p_daten, '{}'::jsonb)
  from __SCHEMA__.profiles p where p.aktiv and p.rolle = 'admin'
$$;

-- ---------- Waechter fuer devices ----------
create or replace function __SCHEMA__.devices_guard() returns trigger
language plpgsql set search_path = '' as $$
declare
  v_inaktiv boolean := false;
begin
  if tg_op = 'INSERT' then
    if new.status not in ('beantragt','aktiv') then
      raise exception 'devices: neue Geraete beginnen als beantragt oder aktiv' using errcode = '23514';
    end if;
    -- Fingerabdruck nie uebernehmen, immer aus dem Schluessel berechnen
    new.ssh_fingerprint := case when new.ssh_pubkey is null then null else __SCHEMA__.ssh_fingerabdruck(new.ssh_pubkey) end;
    new.last_seen_at := null; new.gesperrt_at := null; new.gesperrt_grund := null;
    if new.status = 'beantragt' then new.freigegeben_von := null; new.freigegeben_at := null; end if;
    if new.art = 'persoenlich' and not exists (select 1 from __SCHEMA__.profiles p where p.user_id = new.user_id and p.aktiv) then
      raise exception 'devices: Besitzerprofil ist nicht aktiv' using errcode = '42501';
    end if;
  else
    -- Loeschen eines Kontos (FK set null): nur mit widerrufenem Geraet
    if new.user_id is null and old.user_id is not null and old.status <> 'widerrufen' then
      raise exception 'devices: Das Konto hat noch ein Geraet, erst widerrufen' using errcode = '23503';
    end if;
    -- unveraenderlich; Ausnahme: FK set null beim Loeschen eines Profils (Besitzer nur nach Widerruf)
    if new.art is distinct from old.art or new.klasse is distinct from old.klasse or new.zugang is distinct from old.zugang
       or new.kennung is distinct from old.kennung or new.ssh_pubkey is distinct from old.ssh_pubkey
       or new.ssh_fingerprint is distinct from old.ssh_fingerprint or new.tunnel_slot is distinct from old.tunnel_slot
       or new.bindung is distinct from old.bindung or new.created_at is distinct from old.created_at
       or (new.user_id is distinct from old.user_id and not (new.user_id is null and old.status = 'widerrufen')) then
      raise exception 'devices: art, klasse, zugang, Besitzer, Kennung, Platz und Schluessel sind unveraenderlich'
        using errcode = '42501';
    end if;
    if old.status = 'widerrufen' and (
         (to_jsonb(new) - array['last_seen_at','updated_at','user_id','freigegeben_von','geaendert_von'])
           is distinct from (to_jsonb(old) - array['last_seen_at','updated_at','user_id','freigegeben_von','geaendert_von'])
         or (new.user_id is distinct from old.user_id and new.user_id is not null)
         or (new.freigegeben_von is distinct from old.freigegeben_von and new.freigegeben_von is not null)
         or (new.geaendert_von is distinct from old.geaendert_von and new.geaendert_von is not null)) then
      raise exception 'devices: widerrufene Geraete sind endgueltig' using errcode = '42501';
    end if;
    if new.status is distinct from old.status then
      if not ((old.status = 'beantragt' and new.status in ('aktiv','widerrufen'))
           or (old.status = 'aktiv'     and new.status in ('gesperrt','widerrufen'))
           or (old.status = 'gesperrt'  and new.status in ('aktiv','widerrufen'))) then
        raise exception 'devices: Statuswechsel % nach % nicht erlaubt', old.status, new.status using errcode = '23514';
      end if;
      -- Sperren und Widerrufen darf die Datenbank selbst, wenn das Besitzerprofil nicht mehr aktiv ist
      v_inaktiv := new.status in ('gesperrt','widerrufen') and new.user_id is not null
                   and not exists (select 1 from __SCHEMA__.profiles p where p.user_id = new.user_id and p.aktiv);
      if not v_inaktiv and not exists (select 1 from __SCHEMA__.profiles p
                                       where p.user_id = new.geaendert_von and p.aktiv and p.rolle = 'admin') then
        raise exception 'devices: Statuswechsel nur durch einen aktiven Admin (geaendert_von)' using errcode = '42501';
      end if;
    elsif new.freigegeben_von is distinct from old.freigegeben_von and new.freigegeben_von is not null then
      raise exception 'devices: freigegeben_von nur bei einer Aktivierung' using errcode = '42501';
    end if;
  end if;
  if new.status = 'aktiv' and (tg_op = 'INSERT' or old.status <> 'aktiv') then
    -- automatische Erstbindung nur beim Einfuegen (die Spalte bindung setzt nur geraet_binden), sonst ein Admin
    if not (tg_op = 'INSERT' and new.bindung = 'automatisch') then
      if not exists (select 1 from __SCHEMA__.profiles p where p.user_id = new.freigegeben_von and p.aktiv and p.rolle = 'admin') then
        raise exception 'devices: aktiv nur mit Freigabe durch einen aktiven Admin' using errcode = '42501';
      end if;
    else
      new.freigegeben_von := null;
    end if;
    if new.art = 'persoenlich' and not exists (select 1 from __SCHEMA__.profiles p where p.user_id = new.user_id and p.aktiv) then
      raise exception 'devices: Besitzerprofil ist nicht aktiv' using errcode = '42501';
    end if;
    new.freigegeben_at := now(); new.gesperrt_at := null; new.gesperrt_grund := null;
  end if;
  if tg_op = 'UPDATE' and new.status in ('gesperrt','widerrufen') and old.status is distinct from new.status then
    new.gesperrt_at := now();
  end if;
  if tg_op = 'INSERT' or (to_jsonb(new) - array['last_seen_at','updated_at'])
                         is distinct from (to_jsonb(old) - array['last_seen_at','updated_at']) then
    new.updated_at := now();
  end if;
  return new;
end $$;
drop trigger if exists devices_guard on __SCHEMA__.devices;
create trigger devices_guard before insert or update on __SCHEMA__.devices
  for each row execute function __SCHEMA__.devices_guard();

-- Verlauf und Hinweis an den Besitzer bei jedem Statuswechsel (SECURITY DEFINER: niemand braucht Schreibrechte
-- auf device_events oder fremde Hinweise)
create or replace function __SCHEMA__.devices_ereignis() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  if tg_op = 'INSERT' or new.status is distinct from old.status then
    insert into __SCHEMA__.device_events (device_id, von, nach, durch, grund)
    values (new.id, case when tg_op = 'UPDATE' then old.status end, new.status,
            coalesce(new.geaendert_von, new.freigegeben_von),
            case when tg_op = 'INSERT' and new.bindung = 'automatisch' then 'automatisch gebunden'
                 else new.gesperrt_grund end);
    if tg_op = 'UPDATE' and new.art = 'persoenlich' and new.user_id is not null
       and new.status in ('aktiv','gesperrt','widerrufen') then
      insert into __SCHEMA__.hinweise (empfaenger, art, titel, text, bezug, daten)
      values (new.user_id,
              case new.status when 'aktiv' then case when old.status = 'gesperrt' then 'geraet_entsperrt' else 'geraet_gebunden' end
                              when 'gesperrt' then 'geraet_gesperrt' else 'geraet_widerrufen' end,
              case new.status when 'aktiv' then 'Dein Gerät ' || coalesce(new.name, new.kennung) || ' ist freigegeben'
                              when 'gesperrt' then 'Dein Gerät ' || coalesce(new.name, new.kennung) || ' ist gesperrt'
                              else 'Dein Gerät ' || coalesce(new.name, new.kennung) || ' ist widerrufen' end,
              new.gesperrt_grund, new.id, jsonb_build_object('kennung', new.kennung));
    end if;
  end if;
  return null;
end $$;
drop trigger if exists devices_ereignis on __SCHEMA__.devices;
create trigger devices_ereignis after insert or update on __SCHEMA__.devices
  for each row execute function __SCHEMA__.devices_ereignis();

-- Wird ein Profil deaktiviert (Ausscheiden), sperrt die Datenbank dessen Geraete selbst: aktive werden gesperrt,
-- beantragte widerrufen. Nach einer erneuten Aktivierung entsperrt ein Admin.
create or replace function __SCHEMA__.devices_profil_deaktiviert() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  update __SCHEMA__.devices d
     set status = case when d.status = 'aktiv' then 'gesperrt'::__SCHEMA__.geraet_status
                       else 'widerrufen'::__SCHEMA__.geraet_status end,
         gesperrt_grund = 'Profil deaktiviert'
   where d.user_id = new.user_id and d.art = 'persoenlich' and d.status in ('aktiv','beantragt');
  return null;
end $$;
drop trigger if exists profiles_geraete on __SCHEMA__.profiles;
create trigger profiles_geraete after update of aktiv on __SCHEMA__.profiles
  for each row when (old.aktiv and not new.aktiv) execute function __SCHEMA__.devices_profil_deaktiviert();

-- ---------- Hinweise: Kern darf nur scan_fertig und system selbst anlegen ----------
create or replace function __SCHEMA__.hinweise_guard() returns trigger
language plpgsql set search_path = '' as $$
begin
  if current_user = 'wamo_core' and new.art not in ('scan_fertig','system') then
    raise exception 'hinweise: wamo_core darf nur scan_fertig und system anlegen' using errcode = '42501';
  end if;
  return new;
end $$;
drop trigger if exists hinweise_guard on __SCHEMA__.hinweise;
create trigger hinweise_guard before insert on __SCHEMA__.hinweise
  for each row execute function __SCHEMA__.hinweise_guard();

-- Quittieren nur ueber diese Funktionen (Empfaenger wird geprueft; das Spaltenrecht gelesen_at entfaellt)
create or replace function __SCHEMA__.hinweis_quittieren(p_user uuid, p_id uuid) returns boolean
language sql security definer set search_path = '' as $$
  with u as (update __SCHEMA__.hinweise h set gelesen_at = now()
             where h.id = p_id and h.empfaenger = p_user and h.gelesen_at is null returning 1)
  select exists (select 1 from u)
$$;
create or replace function __SCHEMA__.hinweise_alle_quittieren(p_user uuid) returns integer
language sql security definer set search_path = '' as $$
  with u as (update __SCHEMA__.hinweise h set gelesen_at = now()
             where h.empfaenger = p_user and h.gelesen_at is null returning 1)
  select count(*)::integer from u
$$;

-- ---------- Kern meldet "gesehen" nur fuer aktive Geraete und nur mit now() ----------
create or replace function __SCHEMA__.geraet_gesehen(p_kennung text) returns void
language sql security definer set search_path = '' as $$
  update __SCHEMA__.devices d set last_seen_at = now() where d.kennung = p_kennung and d.status = 'aktiv'
$$;

-- ---------- Liste fuer den Abgleich auf Sokrates-2 (laeuft als postgres, nie fuer wamo_core) ----------
create or replace function __SCHEMA__.pc_abgleich_liste()
returns table (tunnel_slot smallint, kennung text, ssh_pubkey text, ssh_fingerprint text)
language sql stable set search_path = '' as $$
  select d.tunnel_slot, d.kennung, d.ssh_pubkey, d.ssh_fingerprint
  from __SCHEMA__.devices d join __SCHEMA__.profiles p on p.user_id = d.user_id
  where d.art = 'persoenlich' and d.status = 'aktiv' and p.aktiv
  order by d.tunnel_slot
$$;

-- ---------- Erste Bindung eines Laptops (nur wamo_core, nach Anmeldung mit zweitem Faktor auf diesem Geraet) ----------
-- Der Kern hat vorher Token (aal2, frische Anmeldung), Profil und die Signatur des Geraeteschluessels ueber eine
-- Einmal-Herausforderung geprueft. Hier: Regeln des Registers, atomar und fuer alle Kerne gleich.
-- ergebnis: neu_aktiv | neu_beantragt | bekannt | fremd | zu_viele
-- Kennung und Platz werden nur fuer aktive Geraete zurueckgegeben (sonst null).
create or replace function __SCHEMA__.geraet_binden(p_user_id uuid, p_pubkey text, p_hostname text, p_name text)
returns table (device_id uuid, kennung text, tunnel_slot smallint, status __SCHEMA__.geraet_status,
               name text, ergebnis text)
language plpgsql security definer set search_path = '' as $$
declare
  v_fp text;
  v_dev __SCHEMA__.devices%rowtype;
  v_profil record;
  v_name text;
  v_basis text;
  v_kennung text;
  v_slot smallint;
  v_status __SCHEMA__.geraet_status;
  v_grund text;
  i int;
begin
  if p_user_id is null or p_pubkey is null or p_hostname is null then
    raise exception 'geraet_binden: Angaben fehlen' using errcode = '22004';
  end if;
  if p_pubkey !~ '^ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI[A-P][A-Za-z0-9+/]{42}$' then
    raise exception 'geraet_binden: nur ed25519-Schluessel' using errcode = '22023';
  end if;
  if p_hostname !~ '^[A-Za-z0-9][A-Za-z0-9-]{0,62}$' then
    raise exception 'geraet_binden: ungueltiger Rechnername' using errcode = '22023';
  end if;
  select p.display_name, p.aktiv, p.rolle into v_profil from __SCHEMA__.profiles p where p.user_id = p_user_id;
  if not found or not v_profil.aktiv then
    raise exception 'geraet_binden: Profil ist nicht aktiv' using errcode = '42501';
  end if;
  v_name := left(coalesce(nullif(btrim(regexp_replace(coalesce(p_name, ''), '[[:cntrl:]]', '', 'g')), ''), p_hostname), 100);
  v_fp := __SCHEMA__.ssh_fingerabdruck(p_pubkey);
  -- Bindungen nacheinander (selten; schuetzt Kennung, Platz und "ein Geraet je Profil" vor Wettlaeufen)
  perform pg_advisory_xact_lock(hashtext('__SCHEMA__.geraet_binden'));

  select * into v_dev from __SCHEMA__.devices d where d.ssh_fingerprint = v_fp;
  if found then
    if v_dev.user_id is distinct from p_user_id then
      -- Schluessel gehoert einem anderen Profil (oder ist widerrufen ohne Besitzer): nichts verraten, Admins informieren
      if not exists (select 1 from __SCHEMA__.hinweise h where h.art = 'geraet_konflikt' and h.bezug = v_dev.id
                     and h.erstellt_at > now() - interval '1 day') then
        perform __SCHEMA__.hinweis_an_admins('geraet_konflikt',
          'Anmeldung auf einem fremden Gerät: ' || v_profil.display_name,
          'Das Gerät ' || v_dev.kennung || ' gehört einem anderen Profil. Eine Neubindung macht ein Admin.',
          v_dev.id, jsonb_build_object('kennung', v_dev.kennung, 'profil', p_user_id, 'rechner', p_hostname));
      end if;
      return query select null::uuid, null::text, null::smallint, null::__SCHEMA__.geraet_status, null::text, 'fremd'::text;
      return;
    end if;
    -- Rechnername ist nur Anzeige: Wechsel (ohne Gross/Klein) hoechstens einmal je Stunde protokollieren
    if v_dev.status = 'aktiv' and lower(v_dev.hostname) is distinct from lower(p_hostname)
       and not exists (select 1 from __SCHEMA__.device_events e where e.device_id = v_dev.id and e.von = e.nach
                       and e.at > now() - interval '1 hour') then
      update __SCHEMA__.devices d set hostname = p_hostname where d.id = v_dev.id;
      insert into __SCHEMA__.device_events (device_id, von, nach, durch, grund)
      values (v_dev.id, v_dev.status, v_dev.status, null,
              left('Rechnername ' || coalesce(v_dev.hostname, '-') || ' -> ' || p_hostname, 200));
    end if;
    if v_dev.status = 'aktiv' then
      return query select v_dev.id, v_dev.kennung, v_dev.tunnel_slot, v_dev.status, v_dev.name, 'bekannt'::text;
    else
      return query select v_dev.id, null::text, null::smallint, v_dev.status, v_dev.name, 'bekannt'::text;
    end if;
    return;
  end if;

  -- neuer Schluessel: Grenzen
  if (select count(*) from __SCHEMA__.devices d where d.user_id = p_user_id and d.art = 'persoenlich'
      and d.status <> 'widerrufen') >= 3
     or exists (select 1 from __SCHEMA__.devices d where d.user_id = p_user_id and d.status = 'beantragt') then
    return query select null::uuid, null::text, null::smallint, null::__SCHEMA__.geraet_status, null::text, 'zu_viele'::text;
    return;
  end if;

  -- Status zuerst entscheiden: automatisch nur fuer ein Mitglied ohne jedes frueheres Geraet
  if v_profil.rolle = 'admin' then
    v_status := 'beantragt'; v_grund := 'Admin-Profile gibt ein Admin frei';
  elsif exists (select 1 from __SCHEMA__.devices d where d.user_id = p_user_id and d.art = 'persoenlich') then
    v_status := 'beantragt'; v_grund := 'Das Profil hatte schon ein Gerät (Gerätewechsel)';
  elsif exists (select 1 from __SCHEMA__.devices d where d.art = 'persoenlich' and d.status <> 'widerrufen'
                and d.user_id is distinct from p_user_id and lower(d.hostname) = lower(p_hostname)) then
    v_status := 'beantragt'; v_grund := 'Der Rechnername gehört schon zu einem anderen Profil';
  else
    v_status := 'aktiv'; v_grund := null;
  end if;
  -- die globale Warteschlange begrenzt nur Antraege, nie die automatische Erstbindung
  if v_status = 'beantragt' and (select count(*) from __SCHEMA__.devices d where d.status = 'beantragt') >= 10 then
    return query select null::uuid, null::text, null::smallint, null::__SCHEMA__.geraet_status, null::text, 'zu_viele'::text;
    return;
  end if;

  -- Platz: nie benutzt zuerst, dann der am laengsten freie; nach einem Widerruf mindestens eine Stunde Ruhe
  select s::smallint into v_slot from generate_series(1, 64) s
   where not exists (select 1 from __SCHEMA__.devices d where d.tunnel_slot = s
                     and (d.status <> 'widerrufen' or d.gesperrt_at > now() - interval '1 hour'))
   order by (select max(d.gesperrt_at) from __SCHEMA__.devices d where d.tunnel_slot = s) nulls first, s
   limit 1;
  if v_slot is null then
    raise exception 'geraet_binden: kein freier Tunnelplatz' using errcode = '53400';
  end if;
  -- Kennung pc-<rechnername>: klein, nur a-z 0-9 -, eindeutig fuer immer
  v_basis := regexp_replace(regexp_replace(lower(p_hostname), '[^a-z0-9]+', '-', 'g'), '(^-+|-+$)', '', 'g');
  v_basis := rtrim(left(v_basis, 17), '-');
  if length(v_basis) < 1 then v_basis := 'geraet'; end if;
  v_kennung := 'pc-' || v_basis;
  i := 1;
  while exists (select 1 from __SCHEMA__.devices d where d.kennung = v_kennung) loop
    i := i + 1;
    if i > 99 then raise exception 'geraet_binden: keine freie Kennung' using errcode = '53400'; end if;
    v_kennung := 'pc-' || v_basis || '-' || i;
  end loop;

  insert into __SCHEMA__.devices (user_id, name, hostname, art, klasse, zugang, status, bindung, kennung,
                                  tunnel_slot, ssh_pubkey)
  values (p_user_id, v_name, p_hostname, 'persoenlich', 'laptop', 'tunnel', v_status,
          case when v_status = 'aktiv' then 'automatisch' else 'admin' end::__SCHEMA__.geraet_bindung,
          v_kennung, v_slot, p_pubkey)
  returning * into v_dev;

  if v_status = 'aktiv' then
    perform __SCHEMA__.hinweis_an_admins('geraet_gebunden',
      'Neues Gerät gebunden: ' || v_name || ' (' || v_profil.display_name || ')',
      'Automatisch bei der Anmeldung mit zweitem Faktor gebunden. Kennung ' || v_kennung || ', Fingerabdruck ' || v_fp
        || '. Sperren: wamo_geraete.py sperren --kennung ' || v_kennung || '.',
      v_dev.id, jsonb_build_object('kennung', v_kennung, 'profil', p_user_id, 'rechner', p_hostname,
                                   'fingerabdruck', v_fp));
    return query select v_dev.id, v_dev.kennung, v_dev.tunnel_slot, v_dev.status, v_dev.name, 'neu_aktiv'::text;
  else
    perform __SCHEMA__.hinweis_an_admins('geraet_beantragt',
      'Gerät wartet auf Freigabe: ' || v_name || ' (' || v_profil.display_name || ')',
      v_grund || '. Kennung ' || v_kennung || ', Fingerabdruck ' || v_fp
        || '. Freigeben: wamo_geraete.py freigeben --kennung ' || v_kennung || '.',
      v_dev.id, jsonb_build_object('kennung', v_kennung, 'profil', p_user_id, 'rechner', p_hostname,
                                   'fingerabdruck', v_fp, 'grund', v_grund));
    return query select null::uuid, null::text, null::smallint, v_dev.status, v_dev.name, 'neu_beantragt'::text;
  end if;
end $$;

-- ---------- Rechte ----------
revoke all on __SCHEMA__.devices, __SCHEMA__.device_events, __SCHEMA__.hinweise
  from public, anon, authenticated, service_role;
revoke all on function __SCHEMA__.devices_guard(), __SCHEMA__.devices_ereignis(), __SCHEMA__.ssh_fingerabdruck(text),
  __SCHEMA__.hinweis_an_admins(text, text, text, uuid, jsonb), __SCHEMA__.geraet_binden(uuid, text, text, text),
  __SCHEMA__.devices_profil_deaktiviert(), __SCHEMA__.hinweise_guard(), __SCHEMA__.hinweis_quittieren(uuid, uuid),
  __SCHEMA__.hinweise_alle_quittieren(uuid), __SCHEMA__.geraet_gesehen(text), __SCHEMA__.pc_abgleich_liste()
  from public, anon, authenticated, service_role;
-- der Waechter laeuft mit den Rechten des Aufrufers und berechnet damit den Fingerabdruck
grant execute on function __SCHEMA__.ssh_fingerabdruck(text) to wamo_core, wamo_admin;
-- Voice-Core: lesen, erste Bindung und "gesehen" nur ueber Funktionen; Hinweise lesen, selten anlegen, ueber Funktionen quittieren
grant select on __SCHEMA__.devices to wamo_core;
revoke update (last_seen_at) on __SCHEMA__.devices from wamo_core;
grant execute on function __SCHEMA__.geraet_binden(uuid, text, text, text) to wamo_core;
grant execute on function __SCHEMA__.geraet_gesehen(text) to wamo_core;
grant execute on function __SCHEMA__.hinweis_quittieren(uuid, uuid) to wamo_core;
grant execute on function __SCHEMA__.hinweise_alle_quittieren(uuid) to wamo_core;
grant select, insert on __SCHEMA__.hinweise to wamo_core;
revoke update (gelesen_at) on __SCHEMA__.hinweise from wamo_core;
-- Verwaltung (wamo_admin.py): Register pflegen, Verlauf lesen; bindung ist nicht schreibbar (Standard admin)
grant select on __SCHEMA__.devices to wamo_admin;
grant insert (user_id, name, hostname, art, klasse, zugang, status, kennung, tunnel_slot, ssh_pubkey, beschreibung,
              freigegeben_von, geaendert_von) on __SCHEMA__.devices to wamo_admin;
grant update (name, hostname, status, beschreibung, freigegeben_von, geaendert_von, gesperrt_grund)
  on __SCHEMA__.devices to wamo_admin;
grant select on __SCHEMA__.device_events to wamo_admin;
grant select on __SCHEMA__.hinweise to wamo_admin;

alter table __SCHEMA__.devices enable row level security;
alter table __SCHEMA__.device_events enable row level security;
alter table __SCHEMA__.hinweise enable row level security;
drop policy if exists core_all on __SCHEMA__.devices;
create policy core_all on __SCHEMA__.devices for all to wamo_core using (true) with check (true);
drop policy if exists admin_all on __SCHEMA__.devices;
create policy admin_all on __SCHEMA__.devices for all to wamo_admin using (true) with check (true);
drop policy if exists core_all on __SCHEMA__.device_events;
create policy core_all on __SCHEMA__.device_events for all to wamo_core using (true) with check (true);   -- ohne Grant wirkungslos
drop policy if exists admin_all on __SCHEMA__.device_events;
create policy admin_all on __SCHEMA__.device_events for all to wamo_admin using (true) with check (true);
drop policy if exists core_all on __SCHEMA__.hinweise;
create policy core_all on __SCHEMA__.hinweise for all to wamo_core using (true) with check (true);
drop policy if exists admin_all on __SCHEMA__.hinweise;
create policy admin_all on __SCHEMA__.hinweise for all to wamo_admin using (true) with check (true);

insert into __SCHEMA__.schema_migrations (version) values ('0002_wamo_geraete') on conflict do nothing;
