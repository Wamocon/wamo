-- Test für 0013_release_freigaben. Läuft als postgres in EINER Transaktion, die am Ende immer zurückgerollt wird (Testkonten,
-- Anfragen, Freigaben, Ereignisse und die vorübergehenden Eingriffe verschwinden wieder). Nur für wamo_test oder wamo_dev.
-- Der Test legt vorübergehend (nur in dieser Transaktion) alle anderen aktiven Admins still, schließt offene Anfragen und
-- leert release_stand, damit er auch in einem Schema mit Bestandsdaten läuft.
--   sed "s/__SCHEMA__/wamo_test/g" supabase/tests/0013_release_test.sql \
--     | ssh sokrates2 "docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 -q"
-- Erfolg: letzte Zeile "0013 OK", Exit-Code 0. Jeder Fehlschlag bricht mit einer Meldung ab.
\set ON_ERROR_STOP 1
begin;
do $$ begin
  if '__SCHEMA__' = 'wamo_prod' then raise exception 'Test nie in wamo_prod'; end if;
end $$;

grant wamo_core, wamo_admin, wamo_release, wamo_anker to postgres;
create schema wamo_testhilfe;
grant usage on schema wamo_testhilfe to wamo_core, wamo_admin, wamo_release, wamo_anker, anon, authenticated, service_role;
-- erwartet einen Fehler mit genau diesem SQLSTATE und (wenn angegeben) einem Muster im Meldungstext
create function wamo_testhilfe.fehler(p_sql text, p_code text, p_muster text default null) returns void
language plpgsql as $f$
begin
  begin
    execute p_sql;
  exception when others then
    if sqlstate <> p_code then
      raise exception 'erwartet %, bekam % (%) bei: %', p_code, sqlstate, sqlerrm, p_sql;
    end if;
    if p_muster is not null and sqlerrm not like p_muster then
      raise exception 'Meldung "%" passt nicht zu "%" bei: %', sqlerrm, p_muster, p_sql;
    end if;
    return;
  end;
  raise exception 'erwartet Fehler %, aber gelungen: %', p_code, p_sql;
end $f$;
create function wamo_testhilfe.gleich(p_ist anyelement, p_soll anyelement, p_was text) returns void language plpgsql as $f$
begin
  if p_ist is distinct from p_soll then raise exception '%: ist %, soll %', p_was, p_ist, p_soll; end if;
end $f$;
-- Die drei Schritte bis wartet_freigabe laufen mit den Rechten des Aufrufers: anlegen und fertig als wamo_release,
-- klassifizieren als wamo_anker (nur der Anker schreibt Art und Treffer).
create function wamo_testhilfe.anlegen(p_von uuid) returns uuid
language plpgsql as $f$
declare v_id uuid;
begin
  insert into __SCHEMA__.release_anfragen (angefordert_von, quelle) values (p_von, 'main') returning id into v_id;
  update __SCHEMA__.release_anfragen set status = 'uebernommen', basis_commit = repeat('1', 40), c1_commit = repeat('2', 40)
   where id = v_id;
  return v_id;
end $f$;
create function wamo_testhilfe.klassifizieren(p_id uuid, p_art text) returns void
language plpgsql as $f$
begin
  update __SCHEMA__.release_anfragen set art = p_art,
         schutz_treffer = case when p_art = 'sonder' then '["voice-core/dev_guard.py"]'::jsonb else '[]'::jsonb end
   where id = p_id;
end $f$;
create function wamo_testhilfe.fertig(p_id uuid, p_commit text, p_tree text, p_version text) returns void
language plpgsql as $f$
begin
  update __SCHEMA__.release_anfragen set status = 'versionswahl', bump = 'minor', version_vorschlag = p_version,
         version_final = p_version where id = p_id;
  update __SCHEMA__.release_anfragen set status = 'wartet_freigabe', v_commit = repeat(p_commit, 40),
         tree_id = repeat(p_tree, 40) where id = p_id;
end $f$;
-- Zeit vergeht (nur der Test als Eigentümer, Trigger ruhen für die eine Anweisung): Wartezeit vorbei, Freigabe gealtert
create function wamo_testhilfe.wartezeit_vorbei(p_id uuid) returns void
language plpgsql as $f$
begin
  alter table __SCHEMA__.release_anfragen disable trigger user;
  update __SCHEMA__.release_anfragen set wartet_bis = now() - interval '1 minute' where id = p_id and wartet_bis is not null;
  alter table __SCHEMA__.release_anfragen enable trigger user;
end $f$;
create function wamo_testhilfe.freigabe_altern(p_id uuid, p_dauer interval) returns void
language plpgsql as $f$
begin
  alter table __SCHEMA__.release_freigaben disable trigger user;
  update __SCHEMA__.release_freigaben set erstellt_at = erstellt_at - p_dauer where anfrage_id = p_id;
  alter table __SCHEMA__.release_freigaben enable trigger user;
end $f$;
grant execute on all functions in schema wamo_testhilfe to wamo_core, wamo_admin, wamo_release, wamo_anker, anon,
  authenticated, service_role;
-- anon, authenticated und service_role haben auf dem Schema kein usage (0001): hier vorübergehend, damit die Sperre
-- wirklich an den Tabellenrechten scheitert und nicht schon am Schema
grant usage on schema __SCHEMA__ to anon, authenticated, service_role;

-- Konten: zwei aktive Admins, ein Mitglied, ein stillgelegter Admin
insert into auth.users (id, email) values
  ('00000000-0000-4000-8000-0000000013a1', 'rel-admin1@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000013a2', 'rel-admin2@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000013a3', 'rel-mitglied@wamo-test.invalid'),
  ('00000000-0000-4000-8000-0000000013a4', 'rel-admin-aus@wamo-test.invalid');
-- Bestand neutralisieren (alles nur in dieser Transaktion): andere aktive Admins aus, offene Anfragen zu, Stand leer
update __SCHEMA__.profiles set aktiv = false where rolle = 'admin' and aktiv;
alter table __SCHEMA__.release_anfragen disable trigger user;
update __SCHEMA__.release_anfragen set status = 'abgebrochen' where status not in ('bewaehrt', 'zurueckgenommen', 'abgebrochen');
alter table __SCHEMA__.release_anfragen enable trigger user;
delete from __SCHEMA__.release_stand;
insert into __SCHEMA__.profiles (user_id, display_name, rolle, aktiv) values
  ('00000000-0000-4000-8000-0000000013a1', 'Release Eins', 'admin', true),
  ('00000000-0000-4000-8000-0000000013a2', 'Release Zwei', 'admin', true),
  ('00000000-0000-4000-8000-0000000013a3', 'Release Drei', 'mitglied', true),
  ('00000000-0000-4000-8000-0000000013a4', 'Release Vier', 'admin', false);

-- 1. Migration eingetragen, Rollen ohne erhöhte Rechte, Kette am Anfang in Ordnung, Hilfsfunktionen
do $$ begin
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.schema_migrations
                                  where version = '0013_release_freigaben'), 1::bigint, 'Migration eingetragen');
  perform wamo_testhilfe.gleich((select count(*) from pg_roles where rolname in ('wamo_release', 'wamo_anker')
                                    and not rolsuper and not rolbypassrls and not rolcreaterole and not rolcreatedb),
                                2::bigint, 'Rollen ohne erhöhte Rechte');
  perform wamo_testhilfe.gleich(__SCHEMA__.release_kette_pruefen(), null::bigint, 'Kette zu Beginn in Ordnung');
  perform wamo_testhilfe.gleich(__SCHEMA__.release_anzahl_admins(), 2, 'zwei aktive Admins');
  perform wamo_testhilfe.gleich(__SCHEMA__.release_ist_admin('00000000-0000-4000-8000-0000000013a1'), true, 'a1 ist Admin');
  perform wamo_testhilfe.gleich(__SCHEMA__.release_ist_admin('00000000-0000-4000-8000-0000000013a3'), false, 'Mitglied kein Admin');
  perform wamo_testhilfe.gleich(__SCHEMA__.release_ist_admin('00000000-0000-4000-8000-0000000013a4'), false, 'stillgelegter Admin');
  perform wamo_testhilfe.gleich(__SCHEMA__.release_ist_admin(null), false, 'null ist kein Admin');
end $$;

-- 2. Prüfsumme in schema_migrations: Format, einmal gesetzt unveränderlich
do $$ begin
  update __SCHEMA__.schema_migrations set sha256 = repeat('a', 64) where version = '0013_release_freigaben';
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.schema_migrations set sha256 = repeat('b', 64)
    where version = '0013_release_freigaben'$s$, '42501', '%unveränderlich%');
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.schema_migrations set sha256 = null
    where version = '0013_release_freigaben'$s$, '42501', '%unveränderlich%');
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.schema_migrations set sha256 = 'xyz'
    where version = '0001_wamo_init'$s$, '23514');
  update __SCHEMA__.schema_migrations set sha256 = repeat('c', 64) where version = '0001_wamo_init' and sha256 is null;
end $$;
-- die Verwaltung (Tabellenrecht insert seit 0001) trägt keine Prüfsumme ein; ohne Prüfsumme bleibt der Eintrag möglich
set role wamo_admin;
do $$ begin
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.schema_migrations (version, sha256)
    values ('9999_probe', repeat('a', 64))$s$, '42501', '%keine Prüfsumme%');
  insert into __SCHEMA__.schema_migrations (version) values ('9999_probe_ohne');
end $$;
reset role;

-- 3. Beauftragen: nur ein aktiver Admin, höchstens eine offene Anfrage, die Art legt der Release-Dienst nicht fest
set role wamo_release;
do $$ declare v uuid; begin
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.release_anfragen (angefordert_von, quelle)
    values ('00000000-0000-4000-8000-0000000013a3', 'selbst')$s$, '42501', '%aktiver Admin%');
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.release_anfragen (angefordert_von, quelle)
    values ('00000000-0000-4000-8000-0000000013a4', 'selbst')$s$, '42501', '%aktiver Admin%');
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.release_anfragen (angefordert_von, art, quelle)
    values ('00000000-0000-4000-8000-0000000013a1', 'normal', 'selbst')$s$, '42501');
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.release_anfragen (angefordert_von, quelle)
    values ('00000000-0000-4000-8000-0000000013a1', 'anderswo')$s$, '23514');
  -- der Release-Dienst legt nur an, er setzt keinen Status beim Anlegen
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.release_anfragen (angefordert_von, quelle, status)
    values ('00000000-0000-4000-8000-0000000013a1', 'selbst', 'wartet_freigabe')$s$, '42501');
  insert into __SCHEMA__.release_anfragen (angefordert_von, quelle)
    values ('00000000-0000-4000-8000-0000000013a1', 'selbst') returning id into v;
  perform set_config('t.r1', v::text, true);
  perform wamo_testhilfe.gleich((select status from __SCHEMA__.release_anfragen where id = v), 'angefragt', 'Anfangsstatus');
  perform wamo_testhilfe.gleich((select art from __SCHEMA__.release_anfragen where id = v), 'normal', 'Standardart normal');
  perform wamo_testhilfe.gleich((select schutz_treffer is null from __SCHEMA__.release_anfragen where id = v), true,
                                'noch nicht klassifiziert');
  -- nur eine offene Anfrage zugleich
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.release_anfragen (angefordert_von, quelle)
    values ('00000000-0000-4000-8000-0000000013a2', 'main')$s$, '23505');
  -- Beauftragter, Art, Treffer und Wartezeit sind für den Dienst gar nicht beschreibbar
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set angefordert_von =
    '00000000-0000-4000-8000-0000000013a2' where id = %L$s$, v), '42501');
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set art = 'sonder' where id = %L$s$, v), '42501');
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set schutz_treffer = '[]' where id = %L$s$, v),
    '42501');
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set wartet_bis = now() where id = %L$s$, v), '42501');
  -- abbrechen schließt die Anfrage; danach lässt sich nichts mehr ändern
  update __SCHEMA__.release_anfragen set status = 'abgebrochen' where id = v;
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set pruefstand = '{}' where id = %L$s$, v),
    '42501', '%abgeschlossen%');
  -- jetzt ist wieder eine neue Anfrage möglich
  insert into __SCHEMA__.release_anfragen (angefordert_von, quelle)
    values ('00000000-0000-4000-8000-0000000013a2', 'main') returning id into v;
  update __SCHEMA__.release_anfragen set status = 'abgebrochen' where id = v;
end $$;
reset role;
-- die Art ist auch für den Eigentümer auf normal und sonder beschränkt
do $$ begin
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.release_anfragen (angefordert_von, art, quelle)
    values ('00000000-0000-4000-8000-0000000013a1', 'wild', 'selbst')$s$, '23514');
end $$;

-- 4. Statusweg, Version und Klassifizierung bis wartet_freigabe (Anfrage r2, Beauftragter a1)
set role wamo_release;
do $$ declare v uuid; begin
  v := wamo_testhilfe.anlegen('00000000-0000-4000-8000-0000000013a1');
  perform set_config('t.r2', v::text, true);
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set status = 'angefragt' where id = %L$s$, v),
    '23514', '%nicht erlaubt%');
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set status = 'wartet_freigabe' where id = %L$s$, v),
    '23514', '%v_commit%');
  -- Version nie unter den Vorschlag, und nur mit Vorschlag und als X.Y.Z
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set version_vorschlag = '98.2.0',
    version_final = '98.1.9' where id = %L$s$, v), '23514', '%version_nicht_gesenkt%');
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set version_final = '98.2.0' where id = %L$s$, v),
    '23514', '%version_nicht_gesenkt%');
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set version_final = '1.02.0',
    version_vorschlag = '1.2.0' where id = %L$s$, v), '23514');
  -- die Wartezeit schreibt die Datenbank, nicht der Dienst
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set wartet_bis = now() + interval '1 hour'
    where id = %L$s$, v), '42501');
  -- Erhöhen ist erlaubt (Vorschlag 98.2.0, Wahl 98.3.0), der Vorschlag lässt sich nur erhöhen
  update __SCHEMA__.release_anfragen set bump = 'minor', version_vorschlag = '98.2.0', version_final = '98.3.0' where id = v;
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set version_vorschlag = '98.1.0' where id = %L$s$, v),
    '23514', '%nur erhöhen%');
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set version_vorschlag = 'x' where id = %L$s$, v), '23514');
  update __SCHEMA__.release_anfragen set status = 'pruefung3', r_commit = repeat('3', 40) where id = v;
  -- ohne Klassifizierung durch den Anker gibt es kein wartet_freigabe
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set status = 'wartet_freigabe',
    v_commit = repeat('a', 40), tree_id = repeat('b', 40) where id = %L$s$, v), '23514', '%klassifiziert%');
end $$;
reset role;
-- zweite Linie: selbst mit einem (hier vorübergehend vergebenen) Spaltenrecht klassifiziert der Release-Dienst nicht
grant update (art, schutz_treffer) on __SCHEMA__.release_anfragen to wamo_release;
set role wamo_release;
do $$ begin
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set art = 'sonder' where id = %L$s$,
    current_setting('t.r2')), '42501', '%klassifiziert nur der Anker%');
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set schutz_treffer = '[]'::jsonb where id = %L$s$,
    current_setting('t.r2')), '42501', '%klassifiziert nur der Anker%');
end $$;
reset role;
revoke update (art, schutz_treffer) on __SCHEMA__.release_anfragen from wamo_release;
set role wamo_anker;
do $$ begin
  -- Der Anker klassifiziert: ein Treffer ohne die Art sonder lässt den Eintritt nicht zu
  update __SCHEMA__.release_anfragen set schutz_treffer = '["voice-core/dev_guard.py"]'::jsonb where id = current_setting('t.r2')::uuid;
  -- vor der Freigabe vergibt der Anker keinen Status
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set status = 'wartet_freigabe',
    v_commit = repeat('a', 40), tree_id = repeat('b', 40) where id = %L$s$, current_setting('t.r2')), '42501');
end $$;
reset role;
set role wamo_release;
do $$ begin
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set status = 'wartet_freigabe',
    v_commit = repeat('a', 40), tree_id = repeat('b', 40) where id = %L$s$, current_setting('t.r2')), '23514',
    '%verlangen die Art sonder%');
end $$;
reset role;
set role wamo_anker;
do $$ begin
  update __SCHEMA__.release_anfragen set schutz_treffer = '[]'::jsonb where id = current_setting('t.r2')::uuid;
end $$;
reset role;
set role wamo_release;
do $$ begin
  update __SCHEMA__.release_anfragen set status = 'wartet_freigabe', v_commit = repeat('a', 40), tree_id = repeat('b', 40)
   where id = current_setting('t.r2')::uuid;
  perform wamo_testhilfe.gleich((select wartet_bis is null from __SCHEMA__.release_anfragen
                                  where id = current_setting('t.r2')::uuid), true, 'bei normal keine Wartezeit');
  -- eingefroren
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set v_commit = repeat('c', 40) where id = %L$s$,
    current_setting('t.r2')), '42501', '%eingefroren%');
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set tree_id = repeat('c', 40) where id = %L$s$,
    current_setting('t.r2')), '42501', '%eingefroren%');
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set version_final = '98.4.0' where id = %L$s$,
    current_setting('t.r2')), '42501', '%eingefroren%');
  -- der Prüfstand bleibt beschreibbar, der Release-Dienst setzt aber keinen Status ab freigegeben
  update __SCHEMA__.release_anfragen set pruefstand = '{"pruefung3": "gruen"}' where id = current_setting('t.r2')::uuid;
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set status = 'freigegeben' where id = %L$s$,
    current_setting('t.r2')), '42501', '%nur der Anker%');
end $$;
reset role;
set role wamo_anker;
do $$ begin
  -- auch der Anker ändert Art und Treffer nach dem Einfrieren nicht mehr
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set art = 'sonder' where id = %L$s$,
    current_setting('t.r2')), '42501', '%eingefroren%');
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set schutz_treffer = '["x"]'::jsonb where id = %L$s$,
    current_setting('t.r2')), '42501', '%eingefroren%');
end $$;
reset role;

-- 5. Freigabe einer normalen Anfrage (r2): Bindung an Commit-ID und Tree-ID, Admin, frischer zweiter Faktor
set role wamo_anker;
do $$ begin
  perform wamo_testhilfe.fehler(format($s$insert into __SCHEMA__.release_freigaben
    (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit) values (%L, '00000000-0000-4000-8000-0000000013a1',
     repeat('c', 40), repeat('b', 40), clock_timestamp())$s$, current_setting('t.r2')), '23514', '%Commit-ID oder Tree-ID%');
  perform wamo_testhilfe.fehler(format($s$insert into __SCHEMA__.release_freigaben
    (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit) values (%L, '00000000-0000-4000-8000-0000000013a1',
     repeat('a', 40), repeat('c', 40), clock_timestamp())$s$, current_setting('t.r2')), '23514', '%Commit-ID oder Tree-ID%');
  -- ohne Commit-ID oder Tree-ID gibt es keine Freigabe
  perform wamo_testhilfe.fehler(format($s$insert into __SCHEMA__.release_freigaben
    (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit) values (%L, '00000000-0000-4000-8000-0000000013a1',
     null, repeat('b', 40), clock_timestamp())$s$, current_setting('t.r2')), '23502');
  perform wamo_testhilfe.fehler(format($s$insert into __SCHEMA__.release_freigaben
    (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit) values (%L, '00000000-0000-4000-8000-0000000013a1',
     repeat('a', 40), null, clock_timestamp())$s$, current_setting('t.r2')), '23502');
  perform wamo_testhilfe.fehler(format($s$insert into __SCHEMA__.release_freigaben
    (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit) values (%L, '00000000-0000-4000-8000-0000000013a1',
     'abc', repeat('b', 40), clock_timestamp())$s$, current_setting('t.r2')), '23514');
  -- Freigeber: aktiver Admin
  perform wamo_testhilfe.fehler(format($s$insert into __SCHEMA__.release_freigaben
    (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit) values (%L, '00000000-0000-4000-8000-0000000013a3',
     repeat('a', 40), repeat('b', 40), clock_timestamp())$s$, current_setting('t.r2')), '42501', '%kein aktiver Admin%');
  perform wamo_testhilfe.fehler(format($s$insert into __SCHEMA__.release_freigaben
    (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit) values (%L, '00000000-0000-4000-8000-0000000013a4',
     repeat('a', 40), repeat('b', 40), clock_timestamp())$s$, current_setting('t.r2')), '42501', '%kein aktiver Admin%');
  -- zweiter Faktor: höchstens 60 Sekunden alt, nicht aus der Zukunft
  perform wamo_testhilfe.fehler(format($s$insert into __SCHEMA__.release_freigaben
    (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit) values (%L, '00000000-0000-4000-8000-0000000013a1',
     repeat('a', 40), repeat('b', 40), clock_timestamp() - interval '61 seconds')$s$, current_setting('t.r2')),
    '23514', '%60 Sekunden%');
  perform wamo_testhilfe.fehler(format($s$insert into __SCHEMA__.release_freigaben
    (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit) values (%L, '00000000-0000-4000-8000-0000000013a1',
     repeat('a', 40), repeat('b', 40), clock_timestamp() + interval '1 minute')$s$, current_setting('t.r2')),
    '23514', '%Zukunft%');
  -- Gültige Freigabe: bei normal darf der Beauftragte selbst freigeben
  insert into __SCHEMA__.release_freigaben (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit)
    values (current_setting('t.r2')::uuid, '00000000-0000-4000-8000-0000000013a1', repeat('a', 40), repeat('b', 40),
            clock_timestamp() - interval '30 seconds');
  perform set_config('t.f2', (select id::text from __SCHEMA__.release_freigaben
                               where anfrage_id = current_setting('t.r2')::uuid), true);
  perform wamo_testhilfe.fehler(format($s$insert into __SCHEMA__.release_freigaben
    (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit) values (%L, '00000000-0000-4000-8000-0000000013a1',
     repeat('a', 40), repeat('b', 40), clock_timestamp())$s$, current_setting('t.r2')), '23505');
  -- Die Freigabe eines zweiten Admins ist zusätzlich möglich
  insert into __SCHEMA__.release_freigaben (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit)
    values (current_setting('t.r2')::uuid, '00000000-0000-4000-8000-0000000013a2', repeat('a', 40), repeat('b', 40),
            clock_timestamp());
  -- Der Anker setzt jetzt freigegeben; lückenlos geht es weiter, der Release-Dienst darf hier nichts mehr setzen
  update __SCHEMA__.release_anfragen set status = 'freigegeben' where id = current_setting('t.r2')::uuid;
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set status = 'bereitgestellt' where id = %L$s$,
    current_setting('t.r2')), '23514', '%nicht erlaubt%');
  update __SCHEMA__.release_anfragen set status = 'veroeffentlicht' where id = current_setting('t.r2')::uuid;
  update __SCHEMA__.release_anfragen set status = 'bereitgestellt' where id = current_setting('t.r2')::uuid;
  update __SCHEMA__.release_anfragen set status = 'umgeschaltet' where id = current_setting('t.r2')::uuid;
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set status = 'abgebrochen' where id = %L$s$,
    current_setting('t.r2')), '23514', '%nicht erlaubt%');
  update __SCHEMA__.release_anfragen set status = 'bewaehrt' where id = current_setting('t.r2')::uuid;
  update __SCHEMA__.release_anfragen set status = 'zurueckgenommen' where id = current_setting('t.r2')::uuid;
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set status = 'bewaehrt' where id = %L$s$,
    current_setting('t.r2')), '42501', '%abgeschlossen%');
end $$;
reset role;

-- 5b. Der Release-Dienst darf nicht freigeben; ohne Freigabe oder nach einem Widerruf gibt es kein freigegeben
set role wamo_release;
do $$ declare v uuid; begin
  v := wamo_testhilfe.anlegen('00000000-0000-4000-8000-0000000013a1');
  perform set_config('t.r3', v::text, true);
end $$;
reset role;
set role wamo_anker;
do $$ begin
  perform wamo_testhilfe.klassifizieren(current_setting('t.r3')::uuid, 'normal');
end $$;
reset role;
set role wamo_release;
do $$ begin
  -- eine Version gibt es nur einmal (98.3.0 gehört der zurückgenommenen Anfrage r2)
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set version_vorschlag = '98.3.0',
    version_final = '98.3.0' where id = %L$s$, current_setting('t.r3')), '23505');
  perform wamo_testhilfe.fertig(current_setting('t.r3')::uuid, 'd', 'e', '98.5.0');
  perform wamo_testhilfe.fehler(format($s$insert into __SCHEMA__.release_freigaben
    (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit) values (%L, '00000000-0000-4000-8000-0000000013a1',
     repeat('d', 40), repeat('e', 40), clock_timestamp())$s$, current_setting('t.r3')), '42501');
end $$;
reset role;
set role wamo_anker;
do $$ begin
  -- ohne Freigabe gibt es kein freigegeben (auch nicht für den Anker)
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set status = 'freigegeben' where id = %L$s$,
    current_setting('t.r3')), '23514', '%gültigen, frischen Freigabe%');
  -- rückwärts geht es nie
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set status = 'pruefung1' where id = %L$s$,
    current_setting('t.r3')), '23514');
  insert into __SCHEMA__.release_freigaben (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit)
    values (current_setting('t.r3')::uuid, '00000000-0000-4000-8000-0000000013a2', repeat('d', 40), repeat('e', 40),
            clock_timestamp());
  -- widerrufen: danach zählt sie nicht mehr
  update __SCHEMA__.release_freigaben set widerrufen_at = clock_timestamp() where anfrage_id = current_setting('t.r3')::uuid;
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set status = 'freigegeben' where id = %L$s$,
    current_setting('t.r3')), '23514', '%gültigen, frischen Freigabe%');
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_freigaben set widerrufen_at = null
    where anfrage_id = %L$s$, current_setting('t.r3')), '42501', '%unveränderlich%');
  update __SCHEMA__.release_anfragen set status = 'abgebrochen' where id = current_setting('t.r3')::uuid;
end $$;
reset role;

-- 5c. Beim Wechsel auf freigegeben wird die Freigabe erneut geprüft; ein Widerruf wirkt auch danach
set role wamo_release;
do $$ declare v uuid; begin
  v := wamo_testhilfe.anlegen('00000000-0000-4000-8000-0000000013a1');
  perform set_config('t.r4', v::text, true);
end $$;
reset role;
set role wamo_anker;
do $$ begin
  perform wamo_testhilfe.klassifizieren(current_setting('t.r4')::uuid, 'normal');
end $$;
reset role;
set role wamo_release;
do $$ begin
  perform wamo_testhilfe.fertig(current_setting('t.r4')::uuid, 'f', 'a', '98.4.0');
end $$;
reset role;
set role wamo_anker;
do $$ begin
  insert into __SCHEMA__.release_freigaben (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit)
    values (current_setting('t.r4')::uuid, '00000000-0000-4000-8000-0000000013a2', repeat('f', 40), repeat('a', 40),
            clock_timestamp());
end $$;
reset role;
-- der Freigeber wird stillgelegt, bevor der Anker umschaltet
update __SCHEMA__.profiles set aktiv = false where user_id = '00000000-0000-4000-8000-0000000013a2';
set role wamo_anker;
do $$ begin
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set status = 'freigegeben' where id = %L$s$,
    current_setting('t.r4')), '23514', '%gültigen, frischen Freigabe%');
end $$;
reset role;
update __SCHEMA__.profiles set aktiv = true where user_id = '00000000-0000-4000-8000-0000000013a2';
-- Verfall: bei normal gilt eine Freigabe 60 Minuten
select wamo_testhilfe.freigabe_altern(current_setting('t.r4')::uuid, interval '2 hours');
set role wamo_anker;
do $$ begin
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set status = 'freigegeben' where id = %L$s$,
    current_setting('t.r4')), '23514', '%gültigen, frischen Freigabe%');
end $$;
reset role;
select wamo_testhilfe.freigabe_altern(current_setting('t.r4')::uuid, interval '-2 hours');
set role wamo_anker;
do $$ begin
  update __SCHEMA__.release_anfragen set status = 'freigegeben' where id = current_setting('t.r4')::uuid;
  update __SCHEMA__.release_anfragen set status = 'veroeffentlicht' where id = current_setting('t.r4')::uuid;
  update __SCHEMA__.release_freigaben set widerrufen_at = clock_timestamp() where anfrage_id = current_setting('t.r4')::uuid;
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set status = 'bereitgestellt' where id = %L$s$,
    current_setting('t.r4')), '23514', '%nicht widerrufenen%');
  -- Abbruch bleibt auch nach dem Widerruf möglich
  update __SCHEMA__.release_anfragen set status = 'abgebrochen' where id = current_setting('t.r4')::uuid;
end $$;
reset role;
-- eine Freigabe ist unveränderlich und nicht löschbar, auch nicht für den Eigentümer
do $$ begin
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_freigaben set commit_id = repeat('f', 40)
    where id = %L$s$, current_setting('t.f2')), '42501', '%unveränderlich%');
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_freigaben set freigeber =
    '00000000-0000-4000-8000-0000000013a2' where id = %L$s$, current_setting('t.f2')), '42501', '%unveränderlich%');
  perform wamo_testhilfe.fehler(format($s$delete from __SCHEMA__.release_freigaben where id = %L$s$, current_setting('t.f2')),
    '42501', '%nur anfügen%');
  perform wamo_testhilfe.fehler($s$truncate __SCHEMA__.release_freigaben$s$, '42501', '%nur anfügen%');
  perform wamo_testhilfe.fehler($s$delete from __SCHEMA__.release_anfragen$s$, '42501', '%nur anfügen%');
  -- release_anfragen ist von Freigaben und Ereignissen referenziert: schon PostgreSQL lehnt TRUNCATE ab (0A000)
  perform wamo_testhilfe.fehler($s$truncate __SCHEMA__.release_anfragen$s$, '0A000');
end $$;

-- 6. Sonderfreigabe (Schutzschicht berührt): anderer Admin, Wartezeit, mindestens zwei Admins
-- s0: die Art lässt sich nicht senken, die Datenbank setzt die Wartezeit auf 15 Minuten
set role wamo_release;
do $$ declare v uuid; begin
  v := wamo_testhilfe.anlegen('00000000-0000-4000-8000-0000000013a1');
  perform set_config('t.s0', v::text, true);
  update __SCHEMA__.release_anfragen set status = 'versionswahl', bump = 'patch', version_vorschlag = '98.6.1',
         version_final = '98.6.1' where id = v;
end $$;
reset role;
set role wamo_anker;
do $$ begin
  perform wamo_testhilfe.klassifizieren(current_setting('t.s0')::uuid, 'sonder');
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set art = 'normal' where id = %L$s$,
    current_setting('t.s0')), '42501', '%nicht auf normal senken%');
end $$;
reset role;
set role wamo_release;
do $$ begin
  update __SCHEMA__.release_anfragen set status = 'wartet_freigabe', v_commit = repeat('a', 40), tree_id = repeat('b', 40)
   where id = current_setting('t.s0')::uuid;
  perform wamo_testhilfe.gleich((select wartet_bis > now() + interval '14 minutes' from __SCHEMA__.release_anfragen
                                  where id = current_setting('t.s0')::uuid), true, 'die Datenbank setzt 15 Minuten Wartezeit');
  update __SCHEMA__.release_anfragen set status = 'abgebrochen' where id = current_setting('t.s0')::uuid;
end $$;
reset role;
-- s1: Wartezeit läuft noch
set role wamo_release;
do $$ declare v uuid; begin
  v := wamo_testhilfe.anlegen('00000000-0000-4000-8000-0000000013a1');
  perform set_config('t.s1', v::text, true);
end $$;
reset role;
set role wamo_anker;
do $$ begin
  perform wamo_testhilfe.klassifizieren(current_setting('t.s1')::uuid, 'sonder');
end $$;
reset role;
set role wamo_release;
do $$ begin
  perform wamo_testhilfe.fertig(current_setting('t.s1')::uuid, 'a', 'b', '98.6.2');
end $$;
reset role;
set role wamo_anker;
do $$ begin
  -- der Beauftragte gibt nicht selbst frei
  perform wamo_testhilfe.fehler(format($s$insert into __SCHEMA__.release_freigaben
    (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit) values (%L, '00000000-0000-4000-8000-0000000013a1',
     repeat('a', 40), repeat('b', 40), clock_timestamp())$s$, current_setting('t.s1')), '42501', '%nicht selbst%');
  -- ein anderer Admin, aber die Wartezeit ist noch nicht vorbei
  perform wamo_testhilfe.fehler(format($s$insert into __SCHEMA__.release_freigaben
    (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit) values (%L, '00000000-0000-4000-8000-0000000013a2',
     repeat('a', 40), repeat('b', 40), clock_timestamp())$s$, current_setting('t.s1')), '23514', '%Wartezeit%');
  update __SCHEMA__.release_anfragen set status = 'abgebrochen' where id = current_setting('t.s1')::uuid;
end $$;
reset role;
-- s2: Wartezeit vorbei
set role wamo_release;
do $$ declare v uuid; begin
  v := wamo_testhilfe.anlegen('00000000-0000-4000-8000-0000000013a1');
  perform set_config('t.s2', v::text, true);
end $$;
reset role;
set role wamo_anker;
do $$ begin
  perform wamo_testhilfe.klassifizieren(current_setting('t.s2')::uuid, 'sonder');
end $$;
reset role;
set role wamo_release;
do $$ begin
  perform wamo_testhilfe.fertig(current_setting('t.s2')::uuid, 'a', 'b', '98.6.3');
end $$;
reset role;
select wamo_testhilfe.wartezeit_vorbei(current_setting('t.s2')::uuid);
-- nur ein Admin: die Sonderfreigabe bleibt gesperrt (fail closed)
update __SCHEMA__.profiles set aktiv = false where user_id = '00000000-0000-4000-8000-0000000013a2';
set role wamo_anker;
do $$ begin
  perform wamo_testhilfe.gleich(__SCHEMA__.release_anzahl_admins(), 1, 'nur noch ein Admin');
  perform wamo_testhilfe.fehler(format($s$insert into __SCHEMA__.release_freigaben
    (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit) values (%L, '00000000-0000-4000-8000-0000000013a2',
     repeat('a', 40), repeat('b', 40), clock_timestamp())$s$, current_setting('t.s2')), '42501');
  perform wamo_testhilfe.fehler(format($s$insert into __SCHEMA__.release_freigaben
    (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit) values (%L, '00000000-0000-4000-8000-0000000013a1',
     repeat('a', 40), repeat('b', 40), clock_timestamp())$s$, current_setting('t.s2')), '42501');
end $$;
reset role;
-- der Beauftragte wird stillgelegt, ein anderer Admin ist aktiv: ebenfalls gesperrt
update __SCHEMA__.profiles set aktiv = true where user_id = '00000000-0000-4000-8000-0000000013a2';
update __SCHEMA__.profiles set aktiv = false where user_id = '00000000-0000-4000-8000-0000000013a1';
set role wamo_anker;
do $$ begin
  perform wamo_testhilfe.fehler(format($s$insert into __SCHEMA__.release_freigaben
    (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit) values (%L, '00000000-0000-4000-8000-0000000013a2',
     repeat('a', 40), repeat('b', 40), clock_timestamp())$s$, current_setting('t.s2')), '42501', '%Beauftragte%');
end $$;
reset role;
-- zwei aktive Admins, anderer Freigeber, Wartezeit vorbei: die Sonderfreigabe geht
update __SCHEMA__.profiles set aktiv = true where user_id = '00000000-0000-4000-8000-0000000013a1';
set role wamo_anker;
do $$ begin
  perform wamo_testhilfe.gleich(__SCHEMA__.release_anzahl_admins(), 2, 'wieder zwei Admins');
  insert into __SCHEMA__.release_freigaben (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit, quell_ip)
    values (current_setting('t.s2')::uuid, '00000000-0000-4000-8000-0000000013a2', repeat('a', 40), repeat('b', 40),
            clock_timestamp() - interval '10 seconds', '192.168.178.20');
end $$;
reset role;
-- fällt der Beauftragte vor dem Umschalten weg, wird die Sonderfreigabe erneut verweigert
update __SCHEMA__.profiles set aktiv = false where user_id = '00000000-0000-4000-8000-0000000013a1';
set role wamo_anker;
do $$ begin
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_anfragen set status = 'freigegeben' where id = %L$s$,
    current_setting('t.s2')), '23514', '%gültigen, frischen Freigabe%');
end $$;
reset role;
update __SCHEMA__.profiles set aktiv = true where user_id = '00000000-0000-4000-8000-0000000013a1';
set role wamo_anker;
do $$ begin
  update __SCHEMA__.release_anfragen set status = 'freigegeben' where id = current_setting('t.s2')::uuid;
  update __SCHEMA__.release_anfragen set status = 'abgebrochen' where id = current_setting('t.s2')::uuid;
end $$;
reset role;

-- 7. Ereignisse: nur anfügen, Kette per Hash, Vorgaben der Aufrufer entfallen
set role wamo_release;
do $$ declare e1 record; begin
  insert into __SCHEMA__.release_ereignisse (anfrage_id, akteur, ereignis, daten)
    values (current_setting('t.r2')::uuid, '00000000-0000-4000-8000-0000000013a1', 'angefordert', '{"titel": "Probe"}')
    returning id, vorher_hash, hash into e1;
  perform set_config('t.e1', e1.id::text, true);
  perform set_config('t.h1', e1.hash, true);
  perform wamo_testhilfe.gleich(e1.hash ~ '^[0-9a-f]{64}$', true, 'Hash ist Hex');
  perform wamo_testhilfe.gleich(e1.hash = e1.vorher_hash, false, 'Hash ungleich Vorgänger');
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.release_ereignisse (ereignis, hash)
    values ('x', repeat('a', 64))$s$, '42501');
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.release_ereignisse (id, ereignis)
    values (99999, 'x')$s$, '42501');
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.release_ereignisse (ereignis) values ('Groß Geschrieben')$s$, '23514');
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.release_ereignisse (ereignis, daten) values ('x', '[1]')$s$, '23514');
  perform wamo_testhilfe.fehler(format($s$insert into __SCHEMA__.release_ereignisse (ereignis, daten)
    values ('x', jsonb_build_object('z', %L))$s$, repeat('z', 5000)), '23514');
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.release_ereignisse (anfrage_id, ereignis)
    values (gen_random_uuid(), 'x')$s$, '23503');
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_ereignisse set daten = '{}' where id = %s$s$, e1.id), '42501');
  perform wamo_testhilfe.fehler(format($s$delete from __SCHEMA__.release_ereignisse where id = %s$s$, e1.id), '42501');
end $$;
reset role;
set role wamo_anker;
do $$ declare e2 record; begin
  insert into __SCHEMA__.release_ereignisse (anfrage_id, akteur, ereignis, daten)
    values (current_setting('t.r2')::uuid, null, 'freigegeben', '{"version": "0.3.0"}')
    returning id, vorher_hash, hash into e2;
  perform wamo_testhilfe.gleich(e2.id, current_setting('t.e1')::bigint + 1, 'id lückenlos');
  perform wamo_testhilfe.gleich(e2.vorher_hash, current_setting('t.h1'), 'Kette: vorher_hash ist der Hash des Vorgängers');
  perform set_config('t.e2', e2.id::text, true);
end $$;
reset role;
-- auch der Eigentümer kann Ereignisse weder ändern noch löschen noch leeren; Vorgaben von id, zeit und Hashes entfallen
do $$ declare e3 record; begin
  perform wamo_testhilfe.fehler(format($s$update __SCHEMA__.release_ereignisse set daten = '{}' where id = %s$s$,
    current_setting('t.e1')), '42501', '%nur anfügen%');
  perform wamo_testhilfe.fehler(format($s$delete from __SCHEMA__.release_ereignisse where id = %s$s$,
    current_setting('t.e1')), '42501', '%nur anfügen%');
  perform wamo_testhilfe.fehler($s$truncate __SCHEMA__.release_ereignisse$s$, '42501', '%nur anfügen%');
  insert into __SCHEMA__.release_ereignisse (id, zeit, ereignis, daten, vorher_hash, hash)
    values (999999, '2000-01-01', 'gefaelscht', '{}', repeat('f', 64), repeat('e', 64))
    returning id, zeit, vorher_hash into e3;
  perform wamo_testhilfe.gleich(e3.id, current_setting('t.e2')::bigint + 1, 'Vorgabe der id entfällt');
  perform wamo_testhilfe.gleich(e3.zeit > now() - interval '1 hour', true, 'Vorgabe der Zeit entfällt');
  perform wamo_testhilfe.gleich(e3.vorher_hash, (select hash from __SCHEMA__.release_ereignisse
                                                   where id = current_setting('t.e2')::bigint), 'Vorgabe des Hashs entfällt');
  perform wamo_testhilfe.gleich(__SCHEMA__.release_kette_pruefen(), null::bigint, 'Kette in Ordnung');
end $$;

-- 8. Stand der Komponenten: nur der Anker schreibt
set role wamo_anker;
do $$ begin
  insert into __SCHEMA__.release_stand (komponente, aktive_version, aktiver_commit, farbe_oder_slot)
    values ('ui', '0.1.0', repeat('a', 40), 'blau');
  update __SCHEMA__.release_stand set aktive_version = '0.1.1', aktiver_commit = repeat('b', 40), farbe_oder_slot = 'gruen',
         letzte_gute_version = '0.1.0', seit = now() where komponente = 'ui';
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.release_stand (komponente, aktive_version, aktiver_commit,
    farbe_oder_slot) values ('kern', '0.1.0', repeat('a', 40), 'blau')$s$, '23514', '%farbe_oder_slot%');
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.release_stand (komponente, aktive_version, aktiver_commit,
    farbe_oder_slot) values ('wo', '0.1.0', repeat('a', 40), 'a')$s$, '23514');
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.release_stand (komponente, aktive_version, aktiver_commit,
    farbe_oder_slot) values ('kern', 'v0.1.0', repeat('a', 40), 'a')$s$, '23514');
  insert into __SCHEMA__.release_stand (komponente, aktive_version, aktiver_commit, farbe_oder_slot)
    values ('kern', '0.1.0', repeat('a', 40), 'a');
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.release_stand set komponente = 'ui' where komponente = 'kern'$s$, '42501');
end $$;
reset role;
set role wamo_release;
do $$ begin
  perform wamo_testhilfe.gleich((select count(*) from __SCHEMA__.release_stand), 2::bigint, 'Release-Dienst liest den Stand');
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.release_stand (komponente, aktive_version, aktiver_commit,
    farbe_oder_slot) values ('ui', '0.9.0', repeat('a', 40), 'blau')$s$, '42501');
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.release_stand set aktive_version = '0.9.0'$s$, '42501');
end $$;
reset role;

-- 9. Rechtedrift: jede Rolle hat nur das Nötige (die Migration prüft zusätzlich Spalte für Spalte gegen die Sollwerte)
-- Voice-Core: liest alles, schreibt nichts und löscht nichts
set role wamo_core;
do $$ declare t text; begin
  foreach t in array array['release_anfragen', 'release_freigaben', 'release_ereignisse', 'release_stand'] loop
    execute format('select count(*) from __SCHEMA__.%I', t);
    perform wamo_testhilfe.fehler(format('delete from __SCHEMA__.%I', t), '42501');
    perform wamo_testhilfe.fehler(format('truncate __SCHEMA__.%I', t), '42501');
  end loop;
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.release_anfragen (angefordert_von, art, quelle)
    values ('00000000-0000-4000-8000-0000000013a1', 'normal', 'selbst')$s$, '42501');
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.release_anfragen set pruefstand = '{}'$s$, '42501');
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.release_freigaben (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit)
    values (gen_random_uuid(), '00000000-0000-4000-8000-0000000013a1', repeat('a', 40), repeat('b', 40), now())$s$, '42501');
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.release_freigaben set widerrufen_at = now()$s$, '42501');
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.release_ereignisse (ereignis) values ('x')$s$, '42501');
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.release_stand (komponente, aktive_version, aktiver_commit,
    farbe_oder_slot) values ('ui', '0.9.0', repeat('a', 40), 'blau')$s$, '42501');
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.release_stand set aktive_version = '0.9.0'$s$, '42501');
  perform wamo_testhilfe.fehler($s$select __SCHEMA__.release_ist_admin('00000000-0000-4000-8000-0000000013a1')$s$, '42501');
  perform wamo_testhilfe.fehler($s$select __SCHEMA__.release_anzahl_admins()$s$, '42501');
  perform __SCHEMA__.release_kette_pruefen();   -- die Prüfung der Kette darf der Kern
end $$;
reset role;

-- Release-Dienst: keine Freigaben, keine Stände, nichts ändern oder löschen außer seinen Spalten
set role wamo_release;
do $$ begin
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.release_freigaben (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit)
    values (gen_random_uuid(), '00000000-0000-4000-8000-0000000013a1', repeat('a', 40), repeat('b', 40), now())$s$, '42501');
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.release_freigaben set widerrufen_at = now()$s$, '42501');
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.release_ereignisse set daten = '{}'$s$, '42501');
  perform wamo_testhilfe.fehler($s$delete from __SCHEMA__.release_anfragen$s$, '42501');
  perform wamo_testhilfe.fehler($s$delete from __SCHEMA__.release_ereignisse$s$, '42501');
  perform wamo_testhilfe.fehler($s$truncate __SCHEMA__.release_anfragen$s$, '42501');
  perform wamo_testhilfe.fehler($s$select count(*) from __SCHEMA__.profiles$s$, '42501');   -- Admin-Prüfung nur über die Funktion
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.schema_migrations set sha256 = repeat('d', 64)$s$, '42501');
end $$;
reset role;

-- Anker: schreibt Freigaben und den Status, aber keine Anfragen und keine Commits
set role wamo_anker;
do $$ begin
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.release_anfragen (angefordert_von, art, quelle)
    values ('00000000-0000-4000-8000-0000000013a1', 'normal', 'selbst')$s$, '42501');
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.release_anfragen set v_commit = repeat('f', 40)$s$, '42501');
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.release_anfragen set art = 'normal'$s$, '42501');
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.release_freigaben set commit_id = repeat('f', 40)$s$, '42501');
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.release_ereignisse set daten = '{}'$s$, '42501');
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.release_ereignisse (ereignis, hash) values ('x', repeat('a', 64))$s$,
    '42501');
  perform wamo_testhilfe.fehler($s$delete from __SCHEMA__.release_freigaben$s$, '42501');
  perform wamo_testhilfe.fehler($s$delete from __SCHEMA__.release_stand$s$, '42501');
  perform wamo_testhilfe.fehler($s$truncate __SCHEMA__.release_ereignisse$s$, '42501');
  perform wamo_testhilfe.fehler($s$select count(*) from __SCHEMA__.profiles$s$, '42501');
end $$;
reset role;

-- Verwaltung und fremde Rollen: nichts, auch nicht lesen
set role wamo_admin;
do $$ declare t text; begin
  foreach t in array array['release_anfragen', 'release_freigaben', 'release_ereignisse', 'release_stand'] loop
    perform wamo_testhilfe.fehler(format('select count(*) from __SCHEMA__.%I', t), '42501');
    perform wamo_testhilfe.fehler(format('delete from __SCHEMA__.%I', t), '42501');
  end loop;
  perform wamo_testhilfe.fehler($s$insert into __SCHEMA__.release_freigaben (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit)
    values (gen_random_uuid(), '00000000-0000-4000-8000-0000000013a1', repeat('a', 40), repeat('b', 40), now())$s$, '42501');
  perform wamo_testhilfe.fehler($s$select __SCHEMA__.release_kette_pruefen()$s$, '42501');
  perform wamo_testhilfe.fehler($s$select __SCHEMA__.release_anzahl_admins()$s$, '42501');
  perform wamo_testhilfe.fehler($s$update __SCHEMA__.schema_migrations set sha256 = repeat('d', 64)$s$, '42501');
end $$;
reset role;
set role anon;
select wamo_testhilfe.fehler($s$select count(*) from __SCHEMA__.release_anfragen$s$, '42501');
select wamo_testhilfe.fehler($s$select count(*) from __SCHEMA__.release_freigaben$s$, '42501');
select wamo_testhilfe.fehler($s$select count(*) from __SCHEMA__.release_ereignisse$s$, '42501');
select wamo_testhilfe.fehler($s$select count(*) from __SCHEMA__.release_stand$s$, '42501');
select wamo_testhilfe.fehler($s$select __SCHEMA__.release_ist_admin(null)$s$, '42501');
reset role;
set role authenticated;
select wamo_testhilfe.fehler($s$select count(*) from __SCHEMA__.release_anfragen$s$, '42501');
select wamo_testhilfe.fehler($s$insert into __SCHEMA__.release_freigaben (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit)
  values (gen_random_uuid(), '00000000-0000-4000-8000-0000000013a1', repeat('a', 40), repeat('b', 40), now())$s$, '42501');
select wamo_testhilfe.fehler($s$select count(*) from __SCHEMA__.release_ereignisse$s$, '42501');
reset role;
set role service_role;
select wamo_testhilfe.fehler($s$select count(*) from __SCHEMA__.release_freigaben$s$, '42501');
select wamo_testhilfe.fehler($s$select count(*) from __SCHEMA__.release_stand$s$, '42501');
select wamo_testhilfe.fehler($s$update __SCHEMA__.release_anfragen set status = 'freigegeben'$s$, '42501');
reset role;

-- 10. Eine verfälschte Zeile der Kette wird gefunden (als Eigentümer bei abgeschaltetem Trigger, nur in dieser Transaktion)
alter table __SCHEMA__.release_ereignisse disable trigger user;
update __SCHEMA__.release_ereignisse set daten = '{"gefaelscht": true}' where id = current_setting('t.e2')::bigint;
alter table __SCHEMA__.release_ereignisse enable trigger user;
select wamo_testhilfe.gleich(__SCHEMA__.release_kette_pruefen(), current_setting('t.e2')::bigint, 'Verfälschung wird gefunden');

rollback;
\echo 0013 OK
