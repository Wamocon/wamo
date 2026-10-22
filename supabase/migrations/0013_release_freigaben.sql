-- WAMO 0013: Freigaben und Rollen für die Selbstüberarbeitung (docs/selbstueberarbeitung.md, Abschnitte C, E und F).
-- __SCHEMA__ wird ersetzt durch wamo_dev | wamo_test | wamo_prod. Anwenden (in EINER Transaktion, ein zweiter Lauf ändert nichts):
--   bash scripts/render-migration.sh <schema> supabase/migrations/0013_release_freigaben.sql \
--     | docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 --single-transaction
-- Voraussetzung: 0001 (profiles, schema_migrations). Die Migration ist erweiternd: neue Tabellen, Funktionen, Rollen und
-- eine neue Spalte (sha256, null erlaubt). Es werden keine bestehenden Rechte entzogen und keine Daten verändert.
-- wamo_prod nur nach ausdrücklicher Freigabe und nach dem Agenten database-reviewer.
--
-- Was hier entsteht (Konzept E.1):
--   release_anfragen   Eine Anfrage je Release: Beauftragter, Art (normal | sonder), Quelle, Status, Commits (Basis, C1, R, V),
--                      Tree-ID, Version, Treffer der Schutzschicht, Prüfstand, Wartezeit. Höchstens eine offene Anfrage.
--   release_freigaben  Die Freigabe durch einen Admin, gebunden an Commit-ID und Tree-ID, mit frischem zweiten Faktor.
--   release_ereignisse Nur anfügbar, Kette per Hash (vorher_hash, hash), Prüfung mit release_kette_pruefen().
--   release_stand      Aktive und letzte gute Version je Komponente (ui | kern), geschrieben nur vom Anker.
--   schema_migrations  Neue Spalte sha256 (Prüfsumme der angewendeten Datei, Konzept C), einmal gesetzt unveränderlich.
--
-- Rollen (Konzept E.1): wamo_release (Release-Dienst, unprivilegiert) und wamo_anker (Freigabe-Dienst und Anker ohne root).
-- Sie werden hier NOLOGIN angelegt. Login und Passwort setzt ein Skript (wie bei wamo_core), nie die Migration. Rollen
-- gelten je Cluster, nicht je Schema; die Down-Datei löscht sie deshalb nicht.
--
-- Regeln, die die Datenbank selbst durchsetzt (zweite Linie hinter dem Dienst, auch für den Eigentümer):
--   * Beauftragen kann nur ein aktiver Admin.
--   * Freigeber ist ein aktiver Admin. Eine Freigabe gibt es nur im Status wartet_freigabe, nur mit gespeicherter Commit-ID
--     (v_commit) und Tree-ID der Anfrage und nur, wenn beide in der Freigabe genau diesen Werten gleichen.
--   * Der zweite Faktor (aal2_zeit) ist höchstens 60 Sekunden alt.
--   * Art sonder (Schutzschicht berührt): Freigeber ungleich Beauftragter, beide aktive Admins, mindestens zwei aktive
--     Admins (bei nur einem Admin bleibt die Sonderfreigabe gesperrt, fail closed) und die Wartezeit (wartet_bis) ist erreicht.
--   * Art und Treffer der Schutzschicht (schutz_treffer) schreibt nur der Anker (Konzept E.2: der Anker klassifiziert, nie der
--     Agent und nie der Release-Dienst). schutz_treffer null heißt "noch nicht klassifiziert"; ohne Klassifizierung gibt es
--     kein wartet_freigabe, und ein Treffer verlangt die Art sonder. Die Art lässt sich nur von normal auf sonder anheben.
--   * Beim Eintritt in wartet_freigabe setzt die Datenbank bei Art sonder die Wartezeit wartet_bis selbst auf jetzt plus
--     15 Minuten (Konzept E.2); niemand schreibt wartet_bis direkt.
--   * Ab wartet_freigabe sind Art, Commits, Tree-ID, Version, Treffer und Wartezeit eingefroren; die Statuswege sind
--     vorwärts gerichtet, ab der Freigabe lückenlos. "freigegeben" prüft die Freigabe erneut (nicht widerrufen, Freigeber
--     noch aktiver Admin, bei sonder weiter zwei aktive Admins und anderer Freigeber, Verfall nach 60 Minuten bzw. 24 Stunden
--     laut Konzept E.2); veroeffentlicht, bereitgestellt und umgeschaltet verlangen weiter eine nicht widerrufene Freigabe.
--   * Der Release-Dienst setzt keinen Status von freigegeben bis bewaehrt und kein zurueckgenommen; er darf veroeffentlichen
--     (Schritt 12 des Ablaufs) und abbrechen. Der Anker setzt freigegeben bis zurueckgenommen und darf jederzeit abbrechen,
--     aber keinen Status vor der Freigabe vergeben (Konzept E.1: Anker schreibt den Status ab freigegeben).
--   * Ereignisse und Freigaben lassen sich nicht löschen; Ereignisse lassen sich auch nicht ändern.
--   * Die Version ist ein Dreiklang X.Y.Z, der Vorschlag lässt sich nur erhöhen, die gewählte Version nie unter den Vorschlag
--     senken (Konzept F, Punkt 4), und eine Version gibt es höchstens einmal (außer bei abgebrochenen Anfragen).
--
-- Hinweise für die Dienste und den Betrieb:
--   * Profile mit Anfragen, Freigaben oder Ereignissen lassen sich nicht löschen (Fremdschlüssel on delete restrict): Konten
--     werden deaktiviert statt gelöscht, das ist der Prüfpfad.
--   * Sperrreihenfolge: erst die Anfrage oder Freigabe schreiben, dann das Ereignis anhängen. Die Kette serialisiert die
--     Schreiber über eine Advisory-Sperre; die umgekehrte Reihenfolge kann einen Deadlock (40P01) auslösen.
--   * Wiederherstellung aus einem Dump läuft mit session_replication_role = replica (Trigger ruhen), danach
--     release_kette_pruefen() aufrufen. Das Kettenende hat keinen äußeren Anker: den letzten hash zusätzlich im Audit-Log der
--     Dienste führen, sonst bleibt ein Kürzen am Ende unbemerkt.
--   * Ein zweiter Lauf dieser Datei setzt mit "revoke all" die Rechte der release_*-Tabellen auf den Stand dieser Datei
--     zurück (Spaltenrechte eingeschlossen). Ergänzt eine spätere Migration Rechte an diesen Tabellen, muss sie nach einem
--     erneuten Lauf von 0013 wieder angewendet werden.

set local lock_timeout = '5s';  -- ALTER TABLE wartet nicht ewig hinter einer langen Transaktion des Kerns

do $$ begin
  if to_regclass('__SCHEMA__.profiles') is null or to_regclass('__SCHEMA__.schema_migrations') is null then
    raise exception '0013 braucht 0001 (Tabellen __SCHEMA__.profiles und __SCHEMA__.schema_migrations fehlen)';
  end if;
end $$;

-- ---------- Rollen ----------
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'wamo_release') then
    create role wamo_release nologin nosuperuser nocreatedb nocreaterole nobypassrls;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'wamo_anker') then
    create role wamo_anker nologin nosuperuser nocreatedb nocreaterole nobypassrls;
  end if;
end $$;
alter role wamo_release set statement_timeout = '10s';
alter role wamo_release set idle_in_transaction_session_timeout = '15s';
alter role wamo_anker set statement_timeout = '10s';
alter role wamo_anker set idle_in_transaction_session_timeout = '15s';
grant usage on schema __SCHEMA__ to wamo_release, wamo_anker;

-- ---------- Prüfsumme je angewendeter Migration (Konzept C) ----------
-- Der Anker trägt sie beim Anwenden ein; die Prüfstrecke vergleicht jede angewendete Datei mit ihr. Bestehende Zeilen
-- (0001 bis 0013) haben zunächst null und werden einmalig nachgetragen. Ein gesetzter Wert lässt sich nicht mehr ändern.
alter table __SCHEMA__.schema_migrations add column if not exists sha256 text;
alter table __SCHEMA__.schema_migrations drop constraint if exists schema_migrations_sha256_form;
alter table __SCHEMA__.schema_migrations add constraint schema_migrations_sha256_form
  check (sha256 is null or sha256 ~ '^[0-9a-f]{64}$');
comment on column __SCHEMA__.schema_migrations.sha256 is
  'SHA-256 der Migrationsdatei in der Fassung, die angewendet wurde (Hex, 64 Zeichen); null = vor der Einführung angewendet';

-- Die Verwaltung (wamo_admin hat seit 0001 das Tabellenrecht insert) trägt keine Prüfsumme ein: das tut der Anker.
create or replace function __SCHEMA__.schema_migrations_guard() returns trigger
language plpgsql set search_path = '' as $$
declare
  verwaltung boolean :=
    (pg_has_role(current_user, 'wamo_admin', 'USAGE') and not pg_has_role(current_user, 'wamo_core', 'USAGE'))
    or (pg_has_role(session_user, 'wamo_admin', 'USAGE') and not pg_has_role(session_user, 'wamo_core', 'USAGE'));
begin
  if tg_op = 'INSERT' then
    if new.sha256 is not null and verwaltung then
      raise exception 'schema_migrations: wamo_admin darf keine Prüfsumme eintragen' using errcode = '42501';
    end if;
    return new;
  end if;
  if old.sha256 is not null and new.sha256 is distinct from old.sha256 then
    raise exception 'schema_migrations: eine eingetragene Prüfsumme ist unveränderlich (Änderung nur als neue Migration)'
      using errcode = '42501';
  end if;
  return new;
end $$;
drop trigger if exists schema_migrations_guard on __SCHEMA__.schema_migrations;
create trigger schema_migrations_guard before insert or update on __SCHEMA__.schema_migrations
  for each row execute function __SCHEMA__.schema_migrations_guard();

-- ---------- Tabellen ----------
create table if not exists __SCHEMA__.release_anfragen (
  id                uuid primary key default gen_random_uuid(),
  angefordert_von   uuid not null references __SCHEMA__.profiles(user_id) on delete restrict,
  art               text not null default 'normal' check (art in ('normal', 'sonder')),
  quelle            text not null check (quelle in ('selbst', 'main')),
  status            text not null default 'angefragt' check (status in (
                      'angefragt', 'arbeit', 'uebernommen', 'pruefung1', 'review', 'refactor', 'pruefung2', 'versionswahl',
                      'pruefung3', 'wartet_freigabe', 'freigegeben', 'veroeffentlicht', 'bereitgestellt', 'umgeschaltet',
                      'bewaehrt', 'zurueckgenommen', 'abgebrochen')),
  basis_commit      text check (basis_commit is null or basis_commit ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'),
  c1_commit         text check (c1_commit is null or c1_commit ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'),
  r_commit          text check (r_commit is null or r_commit ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'),
  v_commit          text check (v_commit is null or v_commit ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'),
  tree_id           text check (tree_id is null or tree_id ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'),
  bump              text check (bump is null or bump in ('patch', 'minor', 'major')),
  version_vorschlag text check (version_vorschlag is null
                                or version_vorschlag ~ '^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})$'),
  version_final     text check (version_final is null
                                or version_final ~ '^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})$'),
  schutz_treffer    jsonb,
  pruefstand        jsonb not null default '{}'::jsonb,
  wartet_bis        timestamptz,
  erstellt_at       timestamptz not null default now(),
  aktualisiert_at   timestamptz not null default now(),
  -- CASE statt AND: die Auswertung bleibt geordnet, ein kaputter Wert gibt 23514 und nie einen Umwandlungsfehler
  constraint release_anfragen_version_nicht_gesenkt
    check (version_final is null
           or (version_vorschlag is not null
               and case when version_final ~ '^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})$'
                         and version_vorschlag ~ '^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})$'
                        then string_to_array(version_final, '.')::int[] >= string_to_array(version_vorschlag, '.')::int[]
                        else false end)),
  constraint release_anfragen_schutz_form
    check (schutz_treffer is null
           or (jsonb_typeof(schutz_treffer) = 'array' and jsonb_array_length(schutz_treffer) <= 200
               and octet_length(schutz_treffer::text) <= 32768)),
  constraint release_anfragen_pruefstand_form
    check (jsonb_typeof(pruefstand) = 'object' and octet_length(pruefstand::text) <= 32768),
  constraint release_anfragen_wartezeit_nur_sonder check (wartet_bis is null or art = 'sonder'),
  -- Die Invarianten der Freigabe gelten auch, falls ein Trigger einmal fehlt: ab wartet_freigabe sind Commit-ID, Tree-ID und
  -- Version gesetzt, bei sonder auch die Wartezeit (abgebrochene Anfragen vor diesem Punkt sind ausgenommen)
  constraint release_anfragen_ab_freigabe_vollstaendig
    check (status in ('angefragt', 'arbeit', 'uebernommen', 'pruefung1', 'review', 'refactor', 'pruefung2', 'versionswahl',
                      'pruefung3', 'abgebrochen')
           or (v_commit is not null and tree_id is not null and version_final is not null
               and schutz_treffer is not null and (art <> 'sonder' or wartet_bis is not null)))
);
comment on table __SCHEMA__.release_anfragen is
  'Eine Anfrage je Release (Konzept E.1). v_commit ist der Release-Commit V, an den die Freigabe gebunden ist; Version ohne v (X.Y.Z), der Tag heißt vX.Y.Z';
comment on column __SCHEMA__.release_anfragen.art is
  'normal | sonder (Schutzschicht berührt); nur der Anker klassifiziert, die Art lässt sich nur anheben';
comment on column __SCHEMA__.release_anfragen.schutz_treffer is
  'Pfade, die der Anker als Schutzschicht klassifiziert hat (Liste, höchstens 200); null = noch nicht klassifiziert; nie vom Agenten';
comment on column __SCHEMA__.release_anfragen.wartet_bis is
  'Bei Art sonder früheste Freigabe (setzt die Datenbank beim Eintritt in wartet_freigabe auf jetzt plus 15 Minuten)';

create table if not exists __SCHEMA__.release_freigaben (
  id            uuid primary key default gen_random_uuid(),
  anfrage_id    uuid not null references __SCHEMA__.release_anfragen(id) on delete restrict,
  freigeber     uuid not null references __SCHEMA__.profiles(user_id) on delete restrict,
  commit_id     text not null check (commit_id ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'),
  tree_id       text not null check (tree_id ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'),
  aal2_zeit     timestamptz not null,
  quell_ip      inet,
  erstellt_at   timestamptz not null default now(),
  widerrufen_at timestamptz,
  constraint release_freigaben_eine_je_person unique (anfrage_id, freigeber),
  constraint release_freigaben_widerruf_zeit check (widerrufen_at is null or widerrufen_at >= erstellt_at)
);
comment on table __SCHEMA__.release_freigaben is
  'Freigabe eines Admins, gebunden an Commit-ID und Tree-ID; geschrieben nur vom Freigabe-Dienst des Ankers (wamo_anker)';

-- Kette: id zählt lückenlos von 1, vorher_hash verweist auf den hash des Vorgängers (der erste auf 64 Nullen).
-- id, zeit, vorher_hash und hash setzt der Trigger release_ereignisse_kette; Aufrufer können sie nicht vorgeben.
create table if not exists __SCHEMA__.release_ereignisse (
  id          bigint primary key,
  anfrage_id  uuid references __SCHEMA__.release_anfragen(id) on delete restrict,
  zeit        timestamptz not null,
  akteur      uuid references __SCHEMA__.profiles(user_id) on delete restrict,
  ereignis    text not null check (ereignis ~ '^[a-z0-9_.-]{1,60}$'),
  daten       jsonb not null default '{}'::jsonb,
  vorher_hash text not null check (vorher_hash ~ '^[0-9a-f]{64}$'),
  hash        text not null check (hash ~ '^[0-9a-f]{64}$'),
  constraint release_ereignisse_daten_form check (jsonb_typeof(daten) = 'object' and octet_length(daten::text) <= 4096),
  constraint release_ereignisse_hash_eindeutig unique (hash),
  constraint release_ereignisse_kette_linear unique (vorher_hash)
);
comment on table __SCHEMA__.release_ereignisse is
  'Nur anfügen, Kette per Hash (Konzept D.1/E.1); akteur null = System; daten höchstens 4 KB, nie Schlüssel, Tokens oder Gesprächsinhalte';

create table if not exists __SCHEMA__.release_stand (
  komponente          text primary key check (komponente in ('ui', 'kern')),
  aktive_version      text not null check (aktive_version ~ '^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})$'),
  aktiver_commit      text not null check (aktiver_commit ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'),
  farbe_oder_slot     text not null,
  seit                timestamptz not null default now(),
  letzte_gute_version text check (letzte_gute_version is null
                                  or letzte_gute_version ~ '^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})$'),
  constraint release_stand_farbe_oder_slot
    check ((komponente = 'ui' and farbe_oder_slot in ('blau', 'gruen'))
           or (komponente = 'kern' and farbe_oder_slot in ('a', 'b')))
);
comment on table __SCHEMA__.release_stand is
  'Aktive und letzte gute Version je Komponente; ui: Farbe blau | gruen, kern: Slot a | b; geschrieben nur vom Anker';

-- ---------- Indizes (FK-Indizes, partiell bei optionalen Spalten und auf offene Status) ----------
create index if not exists release_anfragen_von_idx on __SCHEMA__.release_anfragen (angefordert_von, erstellt_at desc);
-- eine Version gibt es höchstens einmal (der Tag vX.Y.Z ist eindeutig); abgebrochene Anfragen geben sie wieder frei
create unique index if not exists release_anfragen_version_eindeutig_idx on __SCHEMA__.release_anfragen (version_final)
  where version_final is not null and status <> 'abgebrochen';
-- "Es läuft höchstens ein Release zugleich" (Konzept D.1): eine offene Anfrage, solange keine abgeschlossen ist
create unique index if not exists release_anfragen_ein_offener_idx on __SCHEMA__.release_anfragen ((true))
  where status not in ('bewaehrt', 'zurueckgenommen', 'abgebrochen');
create index if not exists release_freigaben_freigeber_idx on __SCHEMA__.release_freigaben (freigeber);
create index if not exists release_ereignisse_anfrage_idx on __SCHEMA__.release_ereignisse (anfrage_id, id)
  where anfrage_id is not null;
create index if not exists release_ereignisse_akteur_idx on __SCHEMA__.release_ereignisse (akteur) where akteur is not null;

-- ---------- Hilfsfunktionen ----------
-- Lesen der Rolle und des Aktivstatus, ohne dass die beiden neuen Rollen die Tabelle profiles sehen (SECURITY DEFINER,
-- nur Wahrheitswert und Zahl). Nur für wamo_release und wamo_anker ausführbar.
create or replace function __SCHEMA__.release_ist_admin(p_user uuid) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (select 1 from __SCHEMA__.profiles p where p.user_id = p_user and p.aktiv and p.rolle = 'admin')
$$;
create or replace function __SCHEMA__.release_anzahl_admins() returns integer
language sql stable security definer set search_path = '' as $$
  select count(*)::integer from __SCHEMA__.profiles p where p.aktiv and p.rolle = 'admin'
$$;

-- Eine Zeile der Kette: SHA-256 über vorher_hash, id, Anfrage, Zeit (UTC), Akteur, Ereignis und Daten.
create or replace function __SCHEMA__.release_ereignis_hash(
  p_vorher text, p_id bigint, p_anfrage uuid, p_zeit timestamptz, p_akteur uuid, p_ereignis text, p_daten jsonb
) returns text
language sql stable set search_path = '' as $$
  select encode(sha256(convert_to(concat_ws('|',
    p_vorher, p_id::text, coalesce(p_anfrage::text, ''),
    to_char(p_zeit at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
    coalesce(p_akteur::text, ''), p_ereignis, p_daten::text), 'UTF8')), 'hex')
$$;

-- Prüft die ganze Kette; null = in Ordnung, sonst die id der ersten fehlerhaften Zeile.
create or replace function __SCHEMA__.release_kette_pruefen() returns bigint
language plpgsql stable set search_path = '' as $$
declare
  e record;
  vor text := repeat('0', 64);
  naechste bigint := 1;
begin
  for e in select * from __SCHEMA__.release_ereignisse order by id loop
    if e.id <> naechste or e.vorher_hash <> vor
       or e.hash <> __SCHEMA__.release_ereignis_hash(e.vorher_hash, e.id, e.anfrage_id, e.zeit, e.akteur, e.ereignis, e.daten)
    then
      return e.id;
    end if;
    vor := e.hash;
    naechste := e.id + 1;
  end loop;
  return null;
end $$;

-- ---------- Trigger ----------
-- Nur anfügen: kein Löschen und kein Leeren (Anfragen, Freigaben, Ereignisse), kein Ändern (Ereignisse).
create or replace function __SCHEMA__.release_nur_anfuegen() returns trigger
language plpgsql set search_path = '' as $$
begin
  raise exception '%: nur anfügen, nicht ändern oder löschen', tg_table_name using errcode = '42501';
end $$;

-- Kette: serialisiert die Schreiber, vergibt die nächste id, setzt Zeit und beide Hashes (Vorgaben der Aufrufer entfallen).
create or replace function __SCHEMA__.release_ereignisse_kette() returns trigger
language plpgsql set search_path = '' as $$
declare
  letzter record;
begin
  perform pg_advisory_xact_lock(hashtextextended('__SCHEMA__.release_ereignisse', 0));
  select e.id, e.hash into letzter from __SCHEMA__.release_ereignisse e order by e.id desc limit 1;
  new.id := coalesce(letzter.id, 0) + 1;
  new.vorher_hash := coalesce(letzter.hash, repeat('0', 64));
  new.zeit := clock_timestamp();
  new.hash := __SCHEMA__.release_ereignis_hash(new.vorher_hash, new.id, new.anfrage_id, new.zeit, new.akteur,
                                               new.ereignis, new.daten);
  return new;
end $$;

-- Freigaben: die zweite Linie hinter dem Freigabe-Dienst (Konzept E.1 und E.2).
create or replace function __SCHEMA__.release_freigaben_guard() returns trigger
language plpgsql security invoker set search_path = '' as $$
declare
  a __SCHEMA__.release_anfragen%rowtype;
begin
  if tg_op = 'UPDATE' then
    -- Einzig erlaubte Änderung: widerrufen_at von null auf einen Zeitpunkt
    if (new.id, new.anfrage_id, new.freigeber, new.commit_id, new.tree_id, new.aal2_zeit, new.quell_ip, new.erstellt_at)
       is distinct from
       (old.id, old.anfrage_id, old.freigeber, old.commit_id, old.tree_id, old.aal2_zeit, old.quell_ip, old.erstellt_at)
       or (old.widerrufen_at is not null and new.widerrufen_at is distinct from old.widerrufen_at) then
      raise exception 'release_freigaben: eine Freigabe ist unveränderlich, nur das Widerrufen ist möglich'
        using errcode = '42501';
    end if;
    return new;
  end if;

  -- Die Anfrage wird für die Dauer der Transaktion gegen Statuswechsel gesperrt
  select * into a from __SCHEMA__.release_anfragen r where r.id = new.anfrage_id for share;
  if not found then
    raise exception 'release_freigaben: Anfrage % nicht gefunden', new.anfrage_id using errcode = '23503';
  end if;
  if new.widerrufen_at is not null then
    raise exception 'release_freigaben: eine neue Freigabe ist nicht widerrufen' using errcode = '23514';
  end if;
  if a.status <> 'wartet_freigabe' then
    raise exception 'release_freigaben: Freigabe nur im Status wartet_freigabe (Anfrage ist %)', a.status
      using errcode = '23514';
  end if;
  if a.v_commit is null or a.tree_id is null then
    raise exception 'release_freigaben: Freigabe nur mit gespeicherter Commit-ID und Tree-ID der Anfrage'
      using errcode = '23514';
  end if;
  if new.commit_id <> a.v_commit or new.tree_id <> a.tree_id then
    raise exception 'release_freigaben: Commit-ID oder Tree-ID passt nicht zur Anfrage' using errcode = '23514';
  end if;
  if not __SCHEMA__.release_ist_admin(new.freigeber) then
    raise exception 'release_freigaben: der Freigeber ist kein aktiver Admin' using errcode = '42501';
  end if;
  if new.aal2_zeit > clock_timestamp() + interval '5 seconds' then
    raise exception 'release_freigaben: aal2_zeit liegt in der Zukunft' using errcode = '23514';
  end if;
  if clock_timestamp() - new.aal2_zeit > interval '60 seconds' then
    raise exception 'release_freigaben: der zweite Faktor ist älter als 60 Sekunden' using errcode = '23514';
  end if;
  if a.art = 'sonder' then
    if new.freigeber = a.angefordert_von then
      raise exception 'release_freigaben: bei Art sonder darf der Beauftragte nicht selbst freigeben' using errcode = '42501';
    end if;
    if not __SCHEMA__.release_ist_admin(a.angefordert_von) then
      raise exception 'release_freigaben: bei Art sonder muss auch der Beauftragte ein aktiver Admin sein'
        using errcode = '42501';
    end if;
    -- ausdrückliche Sperre (fail closed); folgt schon aus "Freigeber ungleich Beauftragter, beide aktive Admins"
    if __SCHEMA__.release_anzahl_admins() < 2 then
      raise exception 'release_freigaben: Braucht einen zweiten Admin (Sonderfreigabe gesperrt)' using errcode = '42501';
    end if;
    if a.wartet_bis is null or clock_timestamp() < a.wartet_bis then
      raise exception 'release_freigaben: die Wartezeit der Sonderfreigabe ist noch nicht vorbei' using errcode = '23514';
    end if;
  end if;
  new.erstellt_at := now();
  return new;
end $$;

-- Anfragen: Beauftragter, Art, Statusweg und Rollen je Status (Konzept D.1 und E.1).
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
drop trigger if exists release_anfragen_nicht_loeschen on __SCHEMA__.release_anfragen;
create trigger release_anfragen_nicht_loeschen before delete on __SCHEMA__.release_anfragen
  for each row execute function __SCHEMA__.release_nur_anfuegen();
drop trigger if exists release_anfragen_nicht_leeren on __SCHEMA__.release_anfragen;
create trigger release_anfragen_nicht_leeren before truncate on __SCHEMA__.release_anfragen
  for each statement execute function __SCHEMA__.release_nur_anfuegen();

drop trigger if exists release_freigaben_guard on __SCHEMA__.release_freigaben;
create trigger release_freigaben_guard before insert or update on __SCHEMA__.release_freigaben
  for each row execute function __SCHEMA__.release_freigaben_guard();
drop trigger if exists release_freigaben_nicht_loeschen on __SCHEMA__.release_freigaben;
create trigger release_freigaben_nicht_loeschen before delete on __SCHEMA__.release_freigaben
  for each row execute function __SCHEMA__.release_nur_anfuegen();
drop trigger if exists release_freigaben_nicht_leeren on __SCHEMA__.release_freigaben;
create trigger release_freigaben_nicht_leeren before truncate on __SCHEMA__.release_freigaben
  for each statement execute function __SCHEMA__.release_nur_anfuegen();

drop trigger if exists release_ereignisse_kette on __SCHEMA__.release_ereignisse;
create trigger release_ereignisse_kette before insert on __SCHEMA__.release_ereignisse
  for each row execute function __SCHEMA__.release_ereignisse_kette();
drop trigger if exists release_ereignisse_nicht_aendern on __SCHEMA__.release_ereignisse;
create trigger release_ereignisse_nicht_aendern before update or delete on __SCHEMA__.release_ereignisse
  for each row execute function __SCHEMA__.release_nur_anfuegen();
drop trigger if exists release_ereignisse_nicht_leeren on __SCHEMA__.release_ereignisse;
create trigger release_ereignisse_nicht_leeren before truncate on __SCHEMA__.release_ereignisse
  for each statement execute function __SCHEMA__.release_nur_anfuegen();

-- ---------- Rechte (geringste Rechte, nur Spaltenrechte, kein delete) ----------
-- Zuerst alles zurücksetzen (ein REVOKE auf der Tabelle nimmt auch Spaltenrechte): ein zweiter Lauf nimmt so zu viel
-- vergebene Rechte zurück.
revoke all on __SCHEMA__.release_anfragen, __SCHEMA__.release_freigaben, __SCHEMA__.release_ereignisse,
              __SCHEMA__.release_stand
  from public, anon, authenticated, service_role, wamo_core, wamo_admin, wamo_release, wamo_anker;
revoke all on function __SCHEMA__.schema_migrations_guard(), __SCHEMA__.release_nur_anfuegen(),
              __SCHEMA__.release_ereignisse_kette(), __SCHEMA__.release_freigaben_guard(),
              __SCHEMA__.release_anfragen_guard(), __SCHEMA__.release_ist_admin(uuid), __SCHEMA__.release_anzahl_admins(),
              __SCHEMA__.release_ereignis_hash(text, bigint, uuid, timestamptz, uuid, text, jsonb),
              __SCHEMA__.release_kette_pruefen()
  from public, anon, authenticated, service_role, wamo_core, wamo_admin, wamo_release, wamo_anker;

-- Voice-Core: nur lesen (Karte "Änderungen", Servernachrichten). Die Verwaltung (wamo_admin) hat hier nichts.
grant select on __SCHEMA__.release_anfragen, __SCHEMA__.release_freigaben, __SCHEMA__.release_ereignisse,
                __SCHEMA__.release_stand to wamo_core;
-- Die Kettenprüfung und der Trigger der Ereignisse laufen mit den Rechten des Aufrufers und rechnen den Hash selbst nach.
grant execute on function __SCHEMA__.release_kette_pruefen(),
  __SCHEMA__.release_ereignis_hash(text, bigint, uuid, timestamptz, uuid, text, jsonb)
  to wamo_core, wamo_release, wamo_anker;

-- Release-Dienst: legt Anfragen an und führt Stand und Prüfstand bis wartet_freigabe; hängt Ereignisse an.
-- Keine Freigaben, keine Stände, kein delete, keine Art und keine Treffer (das klassifiziert der Anker), keine Wartezeit.
grant select on __SCHEMA__.release_anfragen, __SCHEMA__.release_freigaben, __SCHEMA__.release_ereignisse,
                __SCHEMA__.release_stand to wamo_release;
grant insert (angefordert_von, quelle) on __SCHEMA__.release_anfragen to wamo_release;
grant update (status, basis_commit, c1_commit, r_commit, v_commit, tree_id, bump, version_vorschlag, version_final,
              pruefstand) on __SCHEMA__.release_anfragen to wamo_release;
grant insert (anfrage_id, akteur, ereignis, daten) on __SCHEMA__.release_ereignisse to wamo_release;
grant execute on function __SCHEMA__.release_ist_admin(uuid), __SCHEMA__.release_anzahl_admins() to wamo_release;

-- Anker (Freigabe-Dienst und Schalten): klassifiziert (Art, Treffer), schreibt Freigaben, den Status ab freigegeben,
-- Ereignisse und den Stand.
grant select on __SCHEMA__.release_anfragen, __SCHEMA__.release_freigaben, __SCHEMA__.release_ereignisse,
                __SCHEMA__.release_stand to wamo_anker;
grant update (status, art, schutz_treffer) on __SCHEMA__.release_anfragen to wamo_anker;
grant insert (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit, quell_ip) on __SCHEMA__.release_freigaben to wamo_anker;
grant update (widerrufen_at) on __SCHEMA__.release_freigaben to wamo_anker;
grant insert (anfrage_id, akteur, ereignis, daten) on __SCHEMA__.release_ereignisse to wamo_anker;
grant insert (komponente, aktive_version, aktiver_commit, farbe_oder_slot, seit, letzte_gute_version)
  on __SCHEMA__.release_stand to wamo_anker;
grant update (aktive_version, aktiver_commit, farbe_oder_slot, seit, letzte_gute_version)
  on __SCHEMA__.release_stand to wamo_anker;
grant execute on function __SCHEMA__.release_ist_admin(uuid), __SCHEMA__.release_anzahl_admins() to wamo_anker;

-- ---------- Row Level Security ----------
-- Grants begrenzen, was jede Rolle darf; die Policies öffnen die Zeilen nur für die drei Rollen.
do $$
declare t text;
begin
  foreach t in array array['release_anfragen', 'release_freigaben', 'release_ereignisse', 'release_stand'] loop
    execute format('alter table __SCHEMA__.%I enable row level security', t);
    execute format('drop policy if exists core_all on __SCHEMA__.%I', t);
    execute format('create policy core_all on __SCHEMA__.%I for all to wamo_core using (true) with check (true)', t);
    execute format('drop policy if exists release_all on __SCHEMA__.%I', t);
    execute format('create policy release_all on __SCHEMA__.%I for all to wamo_release using (true) with check (true)', t);
    execute format('drop policy if exists anker_all on __SCHEMA__.%I', t);
    execute format('create policy anker_all on __SCHEMA__.%I for all to wamo_anker using (true) with check (true)', t);
  end loop;
end $$;

-- ---------- Selbsttest: bricht die Transaktion bei Rechtedrift ab ----------
-- Jedes Recht jeder Rolle auf jeder Spalte wird einzeln gegen die Sollwerte geprüft (has_column_privilege mit mehreren
-- Rechten wäre schon bei einem davon wahr). Fremde Rollen haben nichts, niemand außer dem Eigentümer hat DELETE,
-- TRUNCATE, REFERENCES oder TRIGGER.
do $$
declare
  tabellen constant text[] := array['release_anfragen', 'release_freigaben', 'release_ereignisse', 'release_stand'];
  rollen constant text[] := array['wamo_core', 'wamo_release', 'wamo_anker', 'wamo_admin', 'anon', 'authenticated',
                                  'service_role'];
  soll constant jsonb := $j${
    "wamo_core": {
      "release_anfragen": {"SELECT": "*"}, "release_freigaben": {"SELECT": "*"},
      "release_ereignisse": {"SELECT": "*"}, "release_stand": {"SELECT": "*"}},
    "wamo_release": {
      "release_anfragen": {"SELECT": "*", "INSERT": ["angefordert_von", "quelle"],
        "UPDATE": ["status", "basis_commit", "c1_commit", "r_commit", "v_commit", "tree_id", "bump",
                   "version_vorschlag", "version_final", "pruefstand"]},
      "release_freigaben": {"SELECT": "*"},
      "release_ereignisse": {"SELECT": "*", "INSERT": ["anfrage_id", "akteur", "ereignis", "daten"]},
      "release_stand": {"SELECT": "*"}},
    "wamo_anker": {
      "release_anfragen": {"SELECT": "*", "UPDATE": ["status", "art", "schutz_treffer"]},
      "release_freigaben": {"SELECT": "*",
        "INSERT": ["anfrage_id", "freigeber", "commit_id", "tree_id", "aal2_zeit", "quell_ip"],
        "UPDATE": ["widerrufen_at"]},
      "release_ereignisse": {"SELECT": "*", "INSERT": ["anfrage_id", "akteur", "ereignis", "daten"]},
      "release_stand": {"SELECT": "*",
        "INSERT": ["komponente", "aktive_version", "aktiver_commit", "farbe_oder_slot", "seit", "letzte_gute_version"],
        "UPDATE": ["aktive_version", "aktiver_commit", "farbe_oder_slot", "seit", "letzte_gute_version"]}}
  }$j$::jsonb;
  t text;
  r text;
  s text;
  recht text;
  vorgabe jsonb;
  erwartet boolean;
begin
  foreach t in array tabellen loop
    foreach r in array rollen loop
      foreach recht in array array['DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'] loop
        if has_table_privilege(r, '__SCHEMA__.' || t, recht) then
          raise exception 'release (0013): % darf % auf % nicht haben', r, recht, t using errcode = '42501';
        end if;
      end loop;
      for s in select a.attname::text from pg_attribute a
                where a.attrelid = ('__SCHEMA__.' || t)::regclass and a.attnum > 0 and not a.attisdropped loop
        foreach recht in array array['SELECT', 'INSERT', 'UPDATE', 'REFERENCES'] loop
          vorgabe := soll #> array[r, t, recht];
          erwartet := case when vorgabe is null then false
                           when jsonb_typeof(vorgabe) = 'string' then true
                           else vorgabe ? s end;
          if has_column_privilege(r, '__SCHEMA__.' || t, s, recht) is distinct from erwartet then
            raise exception 'release (0013): % % % auf %.% (Rechtedrift)',
              r, case when erwartet then 'braucht' else 'darf nicht' end, recht, t, s using errcode = '42501';
          end if;
        end loop;
      end loop;
    end loop;
    -- nichts für PUBLIC (Empfänger 0 in der ACL), weder auf der Tabelle noch auf einer Spalte
    if exists (select 1 from pg_class c, aclexplode(c.relacl) x
                where c.oid = ('__SCHEMA__.' || t)::regclass and x.grantee = 0)
       or exists (select 1 from pg_attribute a, aclexplode(a.attacl) x
                   where a.attrelid = ('__SCHEMA__.' || t)::regclass and a.attacl is not null and x.grantee = 0) then
      raise exception 'release (0013): % hat Rechte für PUBLIC', t using errcode = '42501';
    end if;
    -- keine unbekannten Empfänger (nur Eigentümer, Superuser und die drei Rollen), auch nicht auf Spalten
    if exists (select 1 from pg_class c, aclexplode(c.relacl) x
                where c.oid = ('__SCHEMA__.' || t)::regclass and x.grantee <> 0 and x.grantee <> c.relowner
                  and x.grantee not in (select oid from pg_roles where rolname in ('wamo_core', 'wamo_release', 'wamo_anker'))
                  and not exists (select 1 from pg_roles q where q.oid = x.grantee and q.rolsuper))
       or exists (select 1 from pg_class c, pg_attribute a, aclexplode(a.attacl) x
                   where c.oid = ('__SCHEMA__.' || t)::regclass and a.attrelid = c.oid and a.attacl is not null
                     and x.grantee <> 0 and x.grantee <> c.relowner
                     and x.grantee not in (select oid from pg_roles where rolname in ('wamo_core', 'wamo_release', 'wamo_anker'))
                     and not exists (select 1 from pg_roles q where q.oid = x.grantee and q.rolsuper)) then
      raise exception 'release (0013): % hat Rechte für eine unbekannte Rolle', t using errcode = '42501';
    end if;
    if not (select c.relrowsecurity from pg_class c where c.oid = ('__SCHEMA__.' || t)::regclass)
       or (select count(*) from pg_policies where schemaname = '__SCHEMA__' and tablename = t
             and policyname in ('core_all', 'release_all', 'anker_all')) <> 3 then
      raise exception 'release (0013): % ohne RLS oder ohne Policies core_all, release_all, anker_all', t
        using errcode = '42501';
    end if;
  end loop;

  -- die Funktionen sind nur für die vorgesehenen Rollen ausführbar, keine für PUBLIC (null in proacl wäre der Standard PUBLIC)
  if exists (select 1 from pg_proc p
              where p.pronamespace = '__SCHEMA__'::regnamespace
                and p.proname in ('schema_migrations_guard', 'release_nur_anfuegen', 'release_ereignisse_kette',
                                  'release_freigaben_guard', 'release_anfragen_guard', 'release_ist_admin',
                                  'release_anzahl_admins', 'release_ereignis_hash', 'release_kette_pruefen')
                and (p.proacl is null or exists (select 1 from aclexplode(p.proacl) x where x.grantee = 0))) then
    raise exception 'release (0013): eine Release-Funktion ist für PUBLIC ausführbar' using errcode = '42501';
  end if;
  foreach r in array array['wamo_admin', 'anon', 'authenticated', 'service_role'] loop
    if has_function_privilege(r, '__SCHEMA__.release_ist_admin(uuid)', 'EXECUTE')
       or has_function_privilege(r, '__SCHEMA__.release_anzahl_admins()', 'EXECUTE')
       or has_function_privilege(r, '__SCHEMA__.release_kette_pruefen()', 'EXECUTE')
       or has_function_privilege(r, '__SCHEMA__.release_ereignis_hash(text, bigint, uuid, timestamptz, uuid, text, jsonb)',
                                 'EXECUTE') then
      raise exception 'release (0013): % darf die Release-Funktionen nicht ausführen', r using errcode = '42501';
    end if;
  end loop;
  if has_function_privilege('wamo_core', '__SCHEMA__.release_ist_admin(uuid)', 'EXECUTE')
     or has_function_privilege('wamo_core', '__SCHEMA__.release_anzahl_admins()', 'EXECUTE')
     or not has_function_privilege('wamo_core', '__SCHEMA__.release_kette_pruefen()', 'EXECUTE') then
    raise exception 'release (0013): wamo_core darf nur die Kettenprüfung ausführen' using errcode = '42501';
  end if;
  foreach r in array array['wamo_release', 'wamo_anker'] loop
    if not (has_function_privilege(r, '__SCHEMA__.release_ist_admin(uuid)', 'EXECUTE')
            and has_function_privilege(r, '__SCHEMA__.release_anzahl_admins()', 'EXECUTE')
            and has_function_privilege(r, '__SCHEMA__.release_kette_pruefen()', 'EXECUTE')
            and has_function_privilege(r, '__SCHEMA__.release_ereignis_hash(text, bigint, uuid, timestamptz, uuid, text, jsonb)',
                                       'EXECUTE')) then
      raise exception 'release (0013): % braucht die Release-Funktionen', r using errcode = '42501';
    end if;
  end loop;

  -- die Rollen sind keine Superuser, nicht untereinander oder in den anderen Rollen Mitglied
  if exists (select 1 from pg_roles where rolname in ('wamo_release', 'wamo_anker')
               and (rolsuper or rolbypassrls or rolreplication or rolcreaterole or rolcreatedb)) then
    raise exception 'release (0013): wamo_release oder wamo_anker hat erhöhte Rollenrechte' using errcode = '42501';
  end if;
  if exists (select 1 from pg_roles r where not r.rolsuper
               and ((pg_has_role(r.oid, 'wamo_release', 'USAGE') and pg_has_role(r.oid, 'wamo_anker', 'USAGE'))
                    or (r.rolname in ('wamo_release', 'wamo_anker')
                        and (pg_has_role(r.oid, 'wamo_core', 'USAGE') or pg_has_role(r.oid, 'wamo_admin', 'USAGE'))))) then
    raise exception 'release (0013): unzulässige Rollenmitgliedschaft (wamo_release, wamo_anker, wamo_core, wamo_admin)'
      using errcode = '42501';
  end if;

  -- die Trigger hängen und sind eingeschaltet
  if (select count(*) from pg_trigger g
       where not g.tgisinternal and g.tgenabled = 'O'
         and (g.tgrelid, g.tgname) in (
           ('__SCHEMA__.release_anfragen'::regclass, 'release_anfragen_guard'),
           ('__SCHEMA__.release_anfragen'::regclass, 'release_anfragen_nicht_loeschen'),
           ('__SCHEMA__.release_anfragen'::regclass, 'release_anfragen_nicht_leeren'),
           ('__SCHEMA__.release_freigaben'::regclass, 'release_freigaben_guard'),
           ('__SCHEMA__.release_freigaben'::regclass, 'release_freigaben_nicht_loeschen'),
           ('__SCHEMA__.release_freigaben'::regclass, 'release_freigaben_nicht_leeren'),
           ('__SCHEMA__.release_ereignisse'::regclass, 'release_ereignisse_kette'),
           ('__SCHEMA__.release_ereignisse'::regclass, 'release_ereignisse_nicht_aendern'),
           ('__SCHEMA__.release_ereignisse'::regclass, 'release_ereignisse_nicht_leeren'),
           ('__SCHEMA__.schema_migrations'::regclass, 'schema_migrations_guard'))) <> 10 then
    raise exception 'release (0013): ein Trigger fehlt oder ist abgeschaltet' using errcode = '42501';
  end if;
end $$;

insert into __SCHEMA__.schema_migrations (version) values ('0013_release_freigaben') on conflict do nothing;
