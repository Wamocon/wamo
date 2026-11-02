"""Migrations-Lint der Prüfstrecke (Konzept docs/selbstueberarbeitung.md Abschnitt C, Schritt 2.5a).

Prüft die Dateien unter supabase/migrations/, die eine Änderung hinzufügt, ändert oder löscht. Wie die anderen Scans (pruefer_scans.py) liest
er nur Text, startet nichts und meldet nie gefundenen Text, sondern Pfad, Zeile und Art. Eine Migration mit Treffer macht die Stufe
"migrations_lint" zu "fehler"; in den Stufen 1 bis 3 gibt es keinen Weg daran vorbei (Contract nur von Hand durch Niko).

Regeln (Art im Treffer):
  Bestand        migration_veraendert        eine vorhandene Migration (auch .down.sql) wird geändert oder gelöscht; nach dem Anwenden unveränderlich
  Dateien        name_ungueltig, nummer_nicht_hoeher, nummer_doppelt, down_fehlt, down_ohne_migration
  Contract       contract_drop_*, contract_rename, contract_typaenderung, contract_not_null_ohne_vorgabe, contract_truncate,
                 contract_delete_ohne_where, contract_revoke  (nur erweitern, dann später aufräumen)
  Pflichtteil    pflicht_rls_fehlt, pflicht_core_all_fehlt, pflicht_grant_fremdrolle, pflicht_fk_ohne_index, pflicht_lock_timeout_fehlt,
                 pflicht_festes_schema, pflicht_rls_deaktiviert, pflicht_rolle_aendern, pflicht_definer_ohne_search_path  (CLAUDE.md)
  Wiederholbar   wiederholung_*  (ein zweiter Lauf bricht nicht ab: if not exists, drop ... if exists vor create, create or replace)

Das Lint ist bewusst streng und einfach (Textmuster auf dem von Kommentaren und Zeichenketten befreiten Text), nicht ein SQL-Parser. Es ersetzt
weder den Agenten database-reviewer noch die frische Anwendung auf einem Wegwerf-Postgres (Rest von Schritt 2.5), es fängt die mechanischen Fälle.
"""

from __future__ import annotations

import re

from pruefer_scans import Befund, Datei, Treffer

NAME = "migrations_lint"
ORDNER = "supabase/migrations/"
DATEI_RE = re.compile(r"^supabase/migrations/(\d{4})_([a-z0-9_]+?)(\.down)?\.sql$")
TABELLE_MAX = 40
FREMDROLLEN = r"(?:anon|authenticated|service_role|public)"

IDENT = r'(?:"[^"]+"|[a-z_][\w$]*)'
QNAME = rf"{IDENT}(?:\s*\.\s*{IDENT})*"


# ------------------------------------------------------------------------------------------------ Text bereinigen
def bereinige(sql: str) -> str:
    """Ersetzt Kommentare und den Inhalt von Zeichenketten durch Leerzeichen (Zeilenumbrüche bleiben, damit Zeilennummern stimmen) und
    schreibt klein. Der Inhalt von $$...$$ bleibt Code (Funktionsrümpfe sind Anweisungen)."""
    aus: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c == "-" and sql.startswith("--", i):
            while i < n and sql[i] != "\n":
                aus.append(" ")
                i += 1
        elif c == "/" and sql.startswith("/*", i):
            tiefe = 0
            while i < n:
                if sql.startswith("/*", i):
                    tiefe += 1
                    aus.append("  ")
                    i += 2
                elif sql.startswith("*/", i):
                    tiefe -= 1
                    aus.append("  ")
                    i += 2
                    if tiefe == 0:
                        break
                else:
                    aus.append("\n" if sql[i] == "\n" else " ")
                    i += 1
        elif c == "'":
            escape = i > 0 and sql[i - 1] in "eE" and (i < 2 or not (sql[i - 2].isalnum() or sql[i - 2] == "_"))
            aus.append("'")
            i += 1
            while i < n:
                if escape and sql[i] == "\\" and i + 1 < n:
                    aus.append("  ")
                    i += 2
                elif sql[i] == "'" and sql.startswith("''", i):
                    aus.append("  ")
                    i += 2
                elif sql[i] == "'":
                    aus.append("'")
                    i += 1
                    break
                else:
                    aus.append("\n" if sql[i] == "\n" else " ")
                    i += 1
        else:
            aus.append(c)
            i += 1
    return "".join(aus).lower()


def _name(roh: str) -> str:
    """Letzter Teil eines möglicherweise qualifizierten Namens, ohne Anführungszeichen (__schema__.tabelle wird tabelle)."""
    teil = roh.split(".")[-1].strip()
    return teil.strip('"')


def _klausel(text: str, start: int) -> str:
    """Text ab start bis zum nächsten Komma oder Semikolon außerhalb von Klammern."""
    tiefe = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "(":
            tiefe += 1
        elif c == ")":
            tiefe -= 1
        elif tiefe <= 0 and c in ",;":
            return text[start:i]
    return text[start:]


def _koerper(text: str, offen: int) -> tuple[str, int]:
    """Inhalt der Klammer, die bei text[offen] beginnt, und die Position dahinter."""
    tiefe = 0
    for i in range(offen, len(text)):
        if text[i] == "(":
            tiefe += 1
        elif text[i] == ")":
            tiefe -= 1
            if tiefe == 0:
                return text[offen + 1:i], i + 1
    return text[offen + 1:], len(text)


def _oberste_teile(koerper: str) -> list[str]:
    teile, tiefe, start = [], 0, 0
    for i, c in enumerate(koerper):
        if c == "(":
            tiefe += 1
        elif c == ")":
            tiefe -= 1
        elif c == "," and tiefe == 0:
            teile.append(koerper[start:i])
            start = i + 1
    teile.append(koerper[start:])
    return [t.strip() for t in teile if t.strip()]


def _zeile(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _harmlos(name: str) -> str:
    return name if re.fullmatch(r"[a-z0-9_]{1,%d}" % TABELLE_MAX, name) else ""


# ------------------------------------------------------------------------------------------------ Regeln je Datei
def pruefe_sql(pfad: str, roh: str, ist_down: bool, befund: Befund) -> None:
    """Prüft den Text einer neuen Migration; meldet Treffer in befund."""
    t = bereinige(roh)

    def melde(pos: int, art: str, detail: str = "") -> None:
        befund.melde(Treffer(pfad, _zeile(t, pos), art, _harmlos(detail)))

    for m in re.finditer(r"\bwamo_(?:dev|test|prod)\b", t):
        melde(m.start(), "pflicht_festes_schema")   # nur der Platzhalter __SCHEMA__ ist erlaubt (scripts/render-migration.sh)
    if ist_down:
        return   # die Rückweg-Datei räumt auf und darf droppen, löschen und widerrufen; für sie gilt nur das feste Schema
    if not re.search(r"\bset\s+local\s+lock_timeout\b", t):
        melde(0, "pflicht_lock_timeout_fehlt")
    for m in re.finditer(rf"\bgrant\b[^;]*?\bto\b[^;]*\b{FREMDROLLEN}\b", t):
        melde(m.start(), "pflicht_grant_fremdrolle")
    for m in re.finditer(r"\b(?:create|alter)\s+(?:role|user)\b", t):
        melde(m.start(), "pflicht_rolle_aendern")
    for m in re.finditer(r"\bdisable\s+row\s+level\s+security\b|\bno\s+force\s+row\s+level\s+security\b", t):
        melde(m.start(), "pflicht_rls_deaktiviert")
    definer = re.search(r"\bsecurity\s+definer\b", t)
    if definer and "search_path" not in t:
        melde(definer.start(), "pflicht_definer_ohne_search_path")

    # ---- Contract: nur erweitern
    for m in re.finditer(r"\bdrop\s+(table|schema|column|type|domain|sequence|view|materialized\s+view|function|procedure|role|user|extension|publication)\b", t):
        melde(m.start(), "contract_drop_" + re.sub(r"\s+", "_", m.group(1)))
    for m in re.finditer(rf"\balter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?{QNAME}\s+drop\s+(?!constraint\b|default\b|not\b|identity\b|expression\b|column\b)", t):
        melde(m.start(), "contract_drop_column")
    for m in re.finditer(r"\brename\b", t):
        melde(m.start(), "contract_rename")
    for m in re.finditer(rf"\balter\s+column\s+{IDENT}\s+(?:set\s+data\s+)?type\b", t):
        melde(m.start(), "contract_typaenderung")
    for m in re.finditer(rf"\balter\s+column\s+({IDENT})\s+set\s+not\s+null\b", t):
        spalte = re.escape(m.group(1))
        vorgabe = re.search(rf"\balter\s+column\s+{spalte}\s+set\s+default\b", t)
        nachfuellung = re.search(rf"\bupdate\b[^;]*?\bset\b[^;]*?\b{spalte}\b", t)
        if not (vorgabe and nachfuellung):
            melde(m.start(), "contract_not_null_ohne_vorgabe", _name(m.group(1)))
    for m in re.finditer(rf"\badd\s+column\s+(?:if\s+not\s+exists\s+)?({IDENT})", t):
        klausel = _klausel(t, m.start())
        if re.search(r"\bnot\s+null\b", klausel) and not re.search(r"\bdefault\b|\bgenerated\b", klausel):
            melde(m.start(), "contract_not_null_ohne_vorgabe", _name(m.group(1)))
    for m in re.finditer(r"\btruncate\b", t):
        davor, danach = t[max(0, m.start() - 14):m.start()], t[m.end():m.end() + 12]
        if re.search(r"(?:before|after|of|,|grant|revoke|select|insert|update|delete)\s*$", davor) or re.match(r"\s*(?:on\b|,)", danach):
            continue   # Rechteliste (grant/revoke ... truncate on) oder Auslöser (before truncate on), keine Anweisung
        melde(m.start(), "contract_truncate")
    for m in re.finditer(r"\bdelete\s+from\b[^;]*", t):
        if not re.search(r"\bwhere\b", m.group(0)):
            melde(m.start(), "contract_delete_ohne_where")
    angelegt = {_name(m.group(1)) for m in re.finditer(
        rf"\bcreate\s+(?:or\s+replace\s+)?(?:unlogged\s+)?(?:table|function|view|sequence)\s+(?:if\s+not\s+exists\s+)?({QNAME})", t)}
    neue_spalten = {_name(m.group(1)) for m in re.finditer(rf"\badd\s+column\s+(?:if\s+not\s+exists\s+)?({IDENT})", t)}
    for m in re.finditer(r"\brevoke\b[^;]*", t):
        satz = m.group(0)
        spalten = re.match(r"\s*revoke\s+[\w\s,]*?\(([^)]*)\)\s*on\b", satz)
        if spalten and all(_name(x.strip()) in neue_spalten for x in spalten.group(1).split(",") if x.strip()):
            continue   # Spaltenrechte nur für Spalten, die diese Datei selbst anlegt
        ziel = re.search(r"\bon\s+(?:table\s+|function\s+|sequence\s+|schema\s+)?(.+?)\s+from\b", satz, re.S)
        liste = re.sub(r"\([^)]*\)", "", ziel.group(1)) if ziel else ""
        namen = [_name(x.strip()) for x in liste.split(",") if x.strip()]
        if not namen or liste.split()[:1] == ["all"] or any(x not in angelegt for x in namen):
            melde(m.start(), "contract_revoke")

    # ---- Wiederholbarkeit
    for m in re.finditer(r"\bdrop\s+(policy|trigger|constraint|index)\b(?!\s+if\s+exists)", t):
        melde(m.start(), "wiederholung_drop_ohne_if_exists")
    for m in re.finditer(r"\bcreate\s+(?:unlogged\s+)?table\s+(?!if\s+not\s+exists)", t):
        melde(m.start(), "wiederholung_create_ohne_if_not_exists")
    for m in re.finditer(r"\bcreate\s+(?:unique\s+)?index\s+(?:concurrently\s+)?(?!if\s+not\s+exists)", t):
        melde(m.start(), "wiederholung_create_ohne_if_not_exists")
    for m in re.finditer(r"\bcreate\s+(?:function|procedure|view)\b", t):
        melde(m.start(), "wiederholung_create_ohne_replace")
    for m in re.finditer(r"\badd\s+column\s+(?!if\s+not\s+exists)", t):
        melde(m.start(), "wiederholung_spalte_ohne_if_not_exists")
    drops_policy = {(_name(a), _name(b)) for a, b in re.findall(rf"\bdrop\s+policy\s+if\s+exists\s+({IDENT})\s+on\s+({QNAME})", t)}
    for m in re.finditer(rf"\bcreate\s+policy\s+({IDENT})\s+on\s+({QNAME})", t):
        if (_name(m.group(1)), _name(m.group(2))) not in drops_policy:
            melde(m.start(), "wiederholung_policy_ohne_drop", _name(m.group(1)))
    drops_trigger = {(_name(a), _name(b)) for a, b in re.findall(rf"\bdrop\s+trigger\s+if\s+exists\s+({IDENT})\s+on\s+({QNAME})", t)}
    for m in re.finditer(rf"\bcreate\s+(?:or\s+replace\s+)?(?:constraint\s+)?trigger\s+({IDENT})\b[^;]*?\bon\s+({QNAME})", t):
        if (_name(m.group(1)), _name(m.group(2))) not in drops_trigger:
            melde(m.start(), "wiederholung_trigger_ohne_drop", _name(m.group(1)))
    drops_constraint = {_name(a) for a in re.findall(rf"\bdrop\s+constraint\s+if\s+exists\s+({IDENT})", t)}
    for m in re.finditer(rf"\badd\s+constraint\s+({IDENT})", t):
        if _name(m.group(1)) not in drops_constraint:
            melde(m.start(), "wiederholung_constraint_ohne_drop", _name(m.group(1)))

    # ---- Pflichtteil für neue Tabellen
    indizes = {(_name(tab), _name(spalte)) for tab, spalte in re.findall(
        rf"\bcreate\s+(?:unique\s+)?index\s+(?:if\s+not\s+exists\s+)?(?:{IDENT}\s+)?on\s+(?:only\s+)?({QNAME})\s*(?:using\s+\w+\s*)?\(\s*({IDENT})", t)}
    roh_klein = roh.lower()
    dyn_rls = bool(re.search(r"execute\s+format\(\s*'alter\s+table\s+[\w.%]+\s+enable\s+row\s+level\s+security'", roh_klein))
    dyn_core = bool(re.search(r"execute\s+format\(\s*'create\s+policy\s+core_all\s+on\s+[\w.%]+[^']*\bto\s+wamo_core\b", roh_klein))
    literale = set(re.findall(r"'([a-z0-9_]+)'", roh_klein))   # Tabellenlisten einer Schleife stehen als Zeichenketten da
    rls = {_name(x) for x in re.findall(rf"\balter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?({QNAME})\s+enable\s+row\s+level\s+security\b", t)}
    core_all = {_name(m.group(1)) for m in re.finditer(rf"\bcreate\s+policy\s+\"?core_all\"?\s+on\s+({QNAME})([^;]*)", t) if "wamo_core" in m.group(2)}
    for m in re.finditer(rf"\bcreate\s+(?:unlogged\s+)?table\s+(?:if\s+not\s+exists\s+)?({QNAME})\s*\(", t):
        tabelle = _name(m.group(1))
        if tabelle not in rls and not (dyn_rls and tabelle in literale):
            melde(m.start(), "pflicht_rls_fehlt", tabelle)
        if tabelle not in core_all and not (dyn_core and tabelle in literale):
            melde(m.start(), "pflicht_core_all_fehlt", tabelle)
        koerper, _ = _koerper(t, m.end() - 1)
        eindeutig: set[str] = set()
        fremd: list[str] = []
        for teil in _oberste_teile(koerper):
            tabellenregel = re.match(r"(?:constraint\s+\S+\s+)?(primary\s+key|unique|foreign\s+key)\s*\(\s*(" + IDENT + ")", teil)
            if tabellenregel:
                erste = _name(tabellenregel.group(2))
                if tabellenregel.group(1).startswith("foreign"):
                    fremd.append(erste)
                else:
                    eindeutig.add(erste)
            elif not re.match(r"(?:constraint|check|exclude|like)\b", teil):
                spalte = re.match(IDENT, teil)
                if spalte:
                    n = _name(spalte.group(0))
                    if re.search(r"\bprimary\s+key\b|\bunique\b", teil):
                        eindeutig.add(n)
                    if re.search(r"\breferences\b", teil):
                        fremd.append(n)
        for spalte in fremd:
            if spalte not in eindeutig and (tabelle, spalte) not in indizes:
                melde(m.start(), "pflicht_fk_ohne_index", tabelle)
    for m in re.finditer(rf"\balter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?({QNAME})\s+add\s+column\s+(?:if\s+not\s+exists\s+)?({IDENT})", t):
        if re.search(r"\breferences\b", _klausel(t, m.start())) and (_name(m.group(1)), _name(m.group(2))) not in indizes:
            melde(m.start(), "pflicht_fk_ohne_index", _name(m.group(1)))


# ------------------------------------------------------------------------------------------------ Dateien
def migration_lint(dateien: list[Datei], arbeitsbaum: tuple[str, ...] = ()) -> Befund:
    """Prüft alle Änderungen unter supabase/migrations/. arbeitsbaum: alle Pfade des neuen Stands (für Nummer und Gegenstück)."""
    befund = Befund(NAME)
    geaendert = [d for d in dateien if d.pfad.startswith(ORDNER)]
    if not geaendert:
        return befund
    neue_pfade = {d.pfad for d in geaendert if d.alt is None and d.neu is not None}
    bestand = [p for p in arbeitsbaum if p.startswith(ORDNER) and p not in neue_pfade]
    hoechste = max((int(m.group(1)) for p in bestand if (m := DATEI_RE.match(p)) and not m.group(3)), default=0)
    neue_nummern: dict[int, str] = {}
    for d in geaendert:
        m = DATEI_RE.match(d.pfad)
        if not m:
            befund.melde(Treffer(d.pfad, 0, "name_ungueltig"))
            continue
        if d.alt is not None:
            if d.neu != d.alt:
                befund.melde(Treffer(d.pfad, 0, "migration_veraendert"))   # auch Löschen
            continue
        if d.neu is None:
            continue
        nummer, ist_down = int(m.group(1)), bool(m.group(3))
        if not ist_down:
            if nummer <= hoechste:
                befund.melde(Treffer(d.pfad, 0, "nummer_nicht_hoeher"))
            if nummer in neue_nummern:
                befund.melde(Treffer(d.pfad, 0, "nummer_doppelt"))
            neue_nummern[nummer] = d.pfad
        gegenstueck = d.pfad[:-len(".down.sql")] + ".sql" if ist_down else d.pfad[:-len(".sql")] + ".down.sql"
        if gegenstueck not in arbeitsbaum:
            befund.melde(Treffer(d.pfad, 0, "down_ohne_migration" if ist_down else "down_fehlt"))
        pruefe_sql(d.pfad, d.neu.decode("utf-8", errors="replace"), ist_down, befund)
    return befund
