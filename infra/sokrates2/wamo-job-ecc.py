#!/usr/bin/python3 -I
"""Root-eigene Kopie des ECC-Plugins OHNE Hooks für die Auftrags-Sandbox auf Sokrates-2.

Aufruf (durch wamo-job-einrichten.sh, als root, nach jeder Installation oder Aktualisierung von ECC bei wamo):

    python3 -I wamo-job-ecc.py --quelle /srv/wamo/.claude/plugins --ziel /opt/wamo/claude-plugins --heim /srv/wamo

Hintergrund: managed-settings.json aktiviert ecc@ecc zusammen mit allowManagedHooksOnly. Damit gelten die
Plugin-Hooks als verwaltet und laufen in jedem Auftrag (SessionStart-Skripte, Fact-Forcing Gate, Formatierer, die
Programme aus node_modules/.bin des Projekts bevorzugen). Die Sandbox bekommt deshalb statt der Plugins von wamo
diese Kopie (in der Sandbox unter /srv/wamo/.claude/plugins): Skills, Agenten und Befehle bleiben gleich benannt
(Plugin und lokaler Marketplace heißen weiter "ecc", also ecc:verification-loop usw.), es fehlen nur
hooks/ (hooks.json und Hilfsdateien), scripts/hooks/, die MCP-Konfiguration des Plugins (.mcp.json; die MCP-Server
kommen allein aus managed-mcp.json) und Laufzeitreste (.in_use). Symbolische Links und Sonderdateien werden nicht
übernommen. Die Kopie gehört root (Gruppe wamo), wamo kann sie weder ändern noch umbenennen.

Aufbau des Ziels (Pfade in den JSON-Dateien so, wie die Sandbox sie sieht):
    installed_plugins.json, known_marketplaces.json    zeigen auf cache/ecc/ecc/ohne-hooks
    cache/ecc/ecc/ohne-hooks/                         Plugin samt .claude-plugin/marketplace.json (Quelle "./")
    data/                                             Einhängepunkt für die Plugin-Daten je Auftrag

Nur Standardbibliothek.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
import time
from pathlib import Path

PLUGIN_ID = "ecc@ecc"
MARKETPLACE = "ecc"
UNTERORDNER = Path("cache") / "ecc" / "ecc" / "ohne-hooks"
AUSLASSEN = {"hooks", "scripts/hooks", ".mcp.json", ".in_use", ".git"}  # relativ zur Plugin-Wurzel
PLUGIN_SCHLUESSEL_WEG = ("hooks", "mcpServers", "lspServers")


class Fehler(Exception):
    pass


def lies_json(pfad: Path) -> object:
    try:
        return json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Fehler(f"{pfad} unlesbar: {e}") from None


def quelle_finden(quelle: Path) -> tuple[Path, str]:
    """Installationspfad und Version von ecc@ecc aus installed_plugins.json; der Pfad muss im Cache liegen."""
    daten = lies_json(quelle / "installed_plugins.json")
    try:
        eintrag = daten["plugins"][PLUGIN_ID][0]
        pfad = Path(eintrag["installPath"])
    except (KeyError, IndexError, TypeError):
        raise Fehler(f"{PLUGIN_ID} ist bei wamo nicht installiert") from None
    cache = (quelle / "cache").resolve()
    echt = pfad.resolve()
    if not echt.is_relative_to(cache) or not echt.is_dir() or pfad.is_symlink():
        raise Fehler(f"Installationspfad {pfad} liegt nicht im Plugin-Cache")
    meta = lies_json(echt / ".claude-plugin" / "plugin.json")
    version = str(meta.get("version", "")) if isinstance(meta, dict) else ""
    if not version or len(version) > 40:
        raise Fehler("Plugin-Version unbekannt")
    return echt, version


def kopieren(quelle: Path, ziel: Path) -> dict[str, int]:
    """Kopiert Verzeichnisse und reguläre Dateien, ohne Links zu folgen; AUSLASSEN fällt weg."""
    zahlen = {"dateien": 0, "ausgelassen": 0}
    for wurzel, ordner, dateien in os.walk(quelle, followlinks=False):
        rel_wurzel = Path(wurzel).relative_to(quelle)
        behalten = []
        for name in sorted(ordner):
            rel = (rel_wurzel / name).as_posix()
            st = os.lstat(Path(wurzel) / name)
            if rel in AUSLASSEN or not stat.S_ISDIR(st.st_mode):
                zahlen["ausgelassen"] += 1
                continue
            behalten.append(name)
            (ziel / rel).mkdir(parents=True, exist_ok=True)
        ordner[:] = behalten
        for name in sorted(dateien):
            rel = (rel_wurzel / name).as_posix()
            q = Path(wurzel) / name
            st = os.lstat(q)
            if rel in AUSLASSEN or not stat.S_ISREG(st.st_mode):
                zahlen["ausgelassen"] += 1
                continue
            (ziel / rel).parent.mkdir(parents=True, exist_ok=True)
            with open(q, "rb") as a, open(ziel / rel, "xb") as b:
                shutil.copyfileobj(a, b)
            if st.st_mode & 0o111:
                os.chmod(ziel / rel, 0o750)
            zahlen["dateien"] += 1
    return zahlen


def bereinigen(plugin: Path) -> None:
    """plugin.json und die Einträge in marketplace.json ohne Hooks und ohne MCP-/LSP-Server."""
    pj = plugin / ".claude-plugin" / "plugin.json"
    meta = lies_json(pj)
    if not isinstance(meta, dict):
        raise Fehler("plugin.json ist kein Objekt")
    for k in PLUGIN_SCHLUESSEL_WEG:
        meta.pop(k, None)
    meta["mcpServers"] = {}
    if isinstance(meta.get("userConfig"), dict):
        meta["userConfig"].pop("hooks_enabled", None)
        meta["userConfig"].pop("hook_profile", None)
    pj.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    mj = plugin / ".claude-plugin" / "marketplace.json"
    markt = lies_json(mj) if mj.is_file() else {"name": MARKETPLACE, "owner": {"name": "WAMO"},
                                                  "plugins": [{"name": "ecc", "source": "./"}]}
    if not isinstance(markt, dict) or not isinstance(markt.get("plugins"), list):
        raise Fehler("marketplace.json hat keine Plugin-Liste")
    markt["name"] = MARKETPLACE
    eintraege = []
    for p in markt["plugins"]:
        if isinstance(p, dict) and p.get("name") == "ecc":
            for k in PLUGIN_SCHLUESSEL_WEG:
                p.pop(k, None)
            p["source"] = "./"
            eintraege.append(p)
    if not eintraege:
        raise Fehler("marketplace.json enthält das Plugin ecc nicht")
    markt["plugins"] = eintraege
    mj.write_text(json.dumps(markt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def pruefen(plugin: Path) -> dict[str, int]:
    """Fail-closed: keine Hooks mehr, Skills und Agenten vorhanden."""
    if (plugin / "hooks").exists() or (plugin / "scripts" / "hooks").exists() or (plugin / ".mcp.json").exists():
        raise Fehler("Hooks oder MCP-Konfiguration noch in der Kopie")
    meta = lies_json(plugin / ".claude-plugin" / "plugin.json")
    if "hooks" in meta:
        raise Fehler("plugin.json verweist noch auf Hooks")
    for wurzel, _, dateien in os.walk(plugin):
        for name in dateien:
            if os.path.islink(os.path.join(wurzel, name)):
                raise Fehler("symbolischer Link in der Kopie")
    skills = sum(1 for p in (plugin / "skills").iterdir() if (p / "SKILL.md").is_file()) if (plugin / "skills").is_dir() else 0
    agenten = len(list((plugin / "agents").glob("*.md"))) if (plugin / "agents").is_dir() else 0
    if skills == 0 or agenten == 0:
        raise Fehler(f"Kopie unvollständig (Skills {skills}, Agenten {agenten})")
    return {"skills": skills, "agenten": agenten}


def rechte_setzen(ziel: Path, gruppe: str | None) -> None:
    gid = -1
    if gruppe and hasattr(os, "chown"):
        import grp
        gid = grp.getgrnam(gruppe).gr_gid
    for wurzel, ordner, dateien in os.walk(ziel):
        for name in ordner:
            p = os.path.join(wurzel, name)
            os.chmod(p, 0o750)
            if gid >= 0:
                os.chown(p, 0, gid)
        for name in dateien:
            p = os.path.join(wurzel, name)
            os.chmod(p, 0o750 if os.stat(p).st_mode & 0o100 else 0o640)
            if gid >= 0:
                os.chown(p, 0, gid)
    os.chmod(ziel, 0o750)
    if gid >= 0:
        os.chown(ziel, 0, gid)


def erzeugen(quelle: Path, ziel: Path, heim: Path, gruppe: str | None = None) -> dict:
    plugin_quelle, version = quelle_finden(quelle)
    neu = ziel.with_name(ziel.name + ".neu")
    if neu.exists():
        shutil.rmtree(neu)
    plugin_ziel = neu / UNTERORDNER
    plugin_ziel.mkdir(parents=True)
    zahlen = kopieren(plugin_quelle, plugin_ziel)
    bereinigen(plugin_ziel)
    zahlen.update(pruefen(plugin_ziel))
    (neu / "data").mkdir()
    innen = heim / ".claude" / "plugins" / UNTERORDNER  # so sieht die Sandbox den Pfad
    jetzt = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    (neu / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {PLUGIN_ID: [{
        "scope": "user", "installPath": innen.as_posix(), "version": version, "installedAt": jetzt,
        "lastUpdated": jetzt}]}}, indent=2) + "\n", encoding="utf-8")
    (neu / "known_marketplaces.json").write_text(json.dumps({MARKETPLACE: {
        "source": {"source": "directory", "path": innen.as_posix()}, "installLocation": innen.as_posix(),
        "lastUpdated": jetzt}}, indent=2) + "\n", encoding="utf-8")
    rechte_setzen(neu, gruppe)
    alt = ziel.with_name(ziel.name + ".alt")
    if alt.exists():
        shutil.rmtree(alt)
    if ziel.exists():
        os.rename(ziel, alt)
    os.rename(neu, ziel)
    if alt.exists():
        shutil.rmtree(alt)
    return {"version": version, **zahlen}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ECC-Plugin ohne Hooks für die WAMO-Sandbox")
    ap.add_argument("--quelle", required=True, type=Path)
    ap.add_argument("--ziel", required=True, type=Path)
    ap.add_argument("--heim", required=True, type=Path)
    ap.add_argument("--gruppe", default=None)
    args = ap.parse_args(argv)
    try:
        info = erzeugen(args.quelle, args.ziel, args.heim, args.gruppe)
    except (Fehler, OSError) as e:
        print(f"wamo-job-ecc: {e}", file=sys.stderr)
        return 1
    print(f"ECC {info['version']} ohne Hooks: {info['skills']} Skills, {info['agenten']} Agenten, "
          f"{info['dateien']} Dateien, {info['ausgelassen']} ausgelassen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
