"""Die echten Mitspieler der Vorwärtsbefehle (vorwaerts.py): Quelle aus dem lokalen Git, Werk mit docker exec psql und den Schritt-Skripten, Datenbank mit der
Rolle wamo_anker. Läuft nur im Anker (root). Nichts hier führt Code aus dem Repository aus.

  QuelleGit     liest Objekte aus dem Bare-Repository der lokalen Quelle (git ls-tree und cat-file, ohne Arbeitsordner, ohne Attribute) und legt nur reguläre
                Dateien in einen Auszug (O_NOFOLLOW, O_EXCL, strenge Pfadprüfung). Verknüpfungen und Submodule sind ein Fehler.
  WerkDocker    Migrationen über docker exec im Container der Datenbank (psql als postgres, eine Transaktion, ON_ERROR_STOP), Kern über das Schritt-Skript
                kern-slots (root-eigene, per Prüfsumme gesicherte Kopie von wamo-core-slots.sh).
  AnkerPg       Datenbank mit der Rolle wamo_anker (Migration 0013 und 0016): Anfrage lesen, Widerruf prüfen, Status setzen (die Trigger prüfen jeden Übergang).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Callable

from schutz import aenderungen_lesen
from vorwaerts import COMMIT_RE, UUID_RE, Vorwaerts, VorwaertsFehler

GIT = "/usr/bin/git"
DOCKER = "/usr/bin/docker"
QUELLE_REPO = "/var/lib/wamo-quelle/wamo.git"
GIT_KONFIG = "/etc/wamo-release/git-quelle.gitconfig"   # gibt genau den Pfad der Quelle frei (git verweigert sonst das fremde Repository)
DB_CONTAINER = "wamo-supabase-db"
SCHEMA_RE = re.compile(r"wamo_(dev|test|prod)")
LS_TREE_RE = re.compile(rb"(\d{6}) (blob|tree|commit) ([0-9a-f]{40}) +(\d+|-)\t(.*)", re.S)
PFAD_TEIL_RE = re.compile(r"[A-Za-z0-9._@+-][A-Za-z0-9._@+ -]{0,127}")
DATEI_MAX = 8 * 1024 * 1024
DATEIEN_MAX = 5000


def git_umgebung() -> dict[str, str]:
    return {"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8", "HOME": "/nonexistent", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": GIT_KONFIG,
            "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}


class QuelleGit:
    def __init__(self, repo: str = QUELLE_REPO, git_umgebung_fn: Callable[[], dict[str, str]] = git_umgebung, git: str = GIT):
        self.repo, self._env, self.git = repo, git_umgebung_fn, git

    def _git(self, argumente: list[str], eingabe: bytes | None = None) -> bytes:
        argv = [self.git, "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "protocol.allow=never", "--git-dir", self.repo, *argumente]
        try:
            p = subprocess.run(argv, input=eingabe, capture_output=True, timeout=300, env=self._env(), cwd="/")
        except (OSError, subprocess.TimeoutExpired):
            raise VorwaertsFehler("quelle_nicht_lesbar") from None
        if p.returncode != 0:
            raise VorwaertsFehler("quelle_fehler", argumente[0])   # der Text von git kann Pfade enthalten: nicht weitergeben
        return p.stdout

    def baum_id(self, commit: str) -> str:
        if not COMMIT_RE.fullmatch(commit):
            raise VorwaertsFehler("commit_ungueltig")
        sha = self._git(["rev-parse", "--verify", "--quiet", f"{commit}^{{tree}}"]).decode().strip()
        if not COMMIT_RE.fullmatch(sha):
            raise VorwaertsFehler("quelle_ohne_commit")
        return sha

    def aenderungen(self, basis: str, neu: str) -> list[tuple[str, bytes]]:
        """Die geänderten Pfade zwischen zwei Commits der Quelle: [(Status, Pfad)], alter und neuer Pfad zählen einzeln (ohne Umbenennungserkennung)."""
        if not (COMMIT_RE.fullmatch(basis) and COMMIT_RE.fullmatch(neu)):
            raise VorwaertsFehler("commit_ungueltig")
        roh = self._git(["diff", "--name-status", "--no-renames", "--no-ext-diff", "--no-textconv", "-z", "--full-index", basis, neu, "--"])
        try:
            return aenderungen_lesen(roh)
        except ValueError:
            raise VorwaertsFehler("diff_form") from None

    def extrahiere(self, commit: str, ziel: str, pfade: tuple[str, ...]) -> None:
        if not COMMIT_RE.fullmatch(commit):
            raise VorwaertsFehler("commit_ungueltig")
        roh = self._git(["ls-tree", "-r", "-t", "-z", "--full-tree", "--long", commit, "--", *pfade])
        eintraege = [e for e in roh.split(b"\0") if e]
        if len(eintraege) > DATEIEN_MAX:
            raise VorwaertsFehler("zu_viele_dateien")
        leser = subprocess.Popen([self.git, "-c", "core.hooksPath=/dev/null", "--git-dir", self.repo, "cat-file", "--batch"], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=self._env(), cwd="/")
        try:
            for eintrag in eintraege:
                m = LS_TREE_RE.fullmatch(eintrag)
                if m is None:
                    raise VorwaertsFehler("ls_tree_form")
                modus, art, oid, groesse_text, pfad_roh = m.groups()
                try:
                    pfad = pfad_roh.decode("utf-8")
                except UnicodeDecodeError:
                    raise VorwaertsFehler("pfad_zeichensatz") from None
                teile = pfad.split("/")
                if not teile or any(t in ("", ".", "..") or not PFAD_TEIL_RE.fullmatch(t) for t in teile):
                    raise VorwaertsFehler("pfad_ungueltig")
                ziel_pfad = os.path.join(ziel, *teile)
                if art == b"tree":
                    os.makedirs(ziel_pfad, mode=0o755, exist_ok=True)
                elif art == b"commit":
                    raise VorwaertsFehler("submodul")
                elif modus in (b"100644", b"100755"):
                    groesse = int(groesse_text)
                    if groesse > DATEI_MAX:
                        raise VorwaertsFehler("datei_zu_gross")
                    self._blob(leser, oid.decode(), groesse, ziel_pfad, modus == b"100755")
                else:
                    raise VorwaertsFehler("modus_unbekannt")   # Verknüpfungen (120000) und alles andere
        finally:
            try:
                leser.stdin.close()
            except OSError:
                pass
            leser.kill()
            leser.wait()

    @staticmethod
    def _blob(leser: subprocess.Popen, oid: str, groesse: int, ziel: str, ausfuehrbar: bool) -> None:
        os.makedirs(os.path.dirname(ziel), mode=0o755, exist_ok=True)
        leser.stdin.write(oid.encode() + b"\n")
        leser.stdin.flush()
        teile = leser.stdout.readline().split()
        if len(teile) != 3 or teile[0].decode(errors="replace") != oid or teile[1] != b"blob" or not teile[2].isdigit() or int(teile[2]) != groesse:
            raise VorwaertsFehler("objekt_unstimmig")
        fd = os.open(ziel, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o755 if ausfuehrbar else 0o644)
        try:
            rest = groesse
            while rest:
                block = leser.stdout.read(min(1024 * 1024, rest))
                if not block:
                    raise VorwaertsFehler("objekt_abgebrochen")
                os.write(fd, block)
                rest -= len(block)
        finally:
            os.close(fd)
        if leser.stdout.read(1) != b"\n":
            raise VorwaertsFehler("objekt_unstimmig")


class WerkDocker:
    """Container der Datenbank und Schritt-Skript des Kerns. lauf(argv, eingabe, zeit) -> (rc, ausgabe) ist einsetzbar (Tests)."""

    def __init__(self, schritt_kern: str, container: str = DB_CONTAINER, releases: str = "/opt/wamo-core/releases",
                 lauf: Callable[[list[str], bytes | None, float], tuple[int, bytes]] | None = None, docker: str = DOCKER):
        self.schritt_kern, self.container, self.releases, self.docker = schritt_kern, container, releases, docker
        self._lauf = lauf or self._lauf_echt

    @staticmethod
    def _lauf_echt(argv: list[str], eingabe: bytes | None, zeit: float) -> tuple[int, bytes]:
        try:
            p = subprocess.run(argv, input=eingabe, capture_output=True, timeout=zeit, cwd="/",
                               env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C.UTF-8", "HOME": "/root"})
        except (OSError, subprocess.TimeoutExpired):
            return 255, b""
        return p.returncode, (p.stdout or b"")[:65536]

    def _psql(self, args: list[str], eingabe: bytes | None, zeit: float) -> tuple[int, bytes]:
        return self._lauf([self.docker, "exec", "-i", self.container, "psql", "-U", "postgres", "-X", "-q", *args], eingabe, zeit)

    def migrationsstand(self, schema: str) -> dict[str, str]:
        if not SCHEMA_RE.fullmatch(schema):
            raise VorwaertsFehler("schema_ungueltig")
        rc, aus = self._psql(["-At", "-F", "|", "-v", "ON_ERROR_STOP=1", "-c", f"select version, coalesce(sha256, '') from {schema}.schema_migrations order by version"], None, 60)
        if rc != 0:
            raise VorwaertsFehler("stand_nicht_lesbar", schema)
        stand: dict[str, str] = {}
        for zeile in aus.decode("utf-8", "replace").splitlines():
            version, _, sha = zeile.partition("|")
            if re.fullmatch(r"\d{4}_[a-z0-9_]+", version) and re.fullmatch(r"[0-9a-f]{64}|", sha):
                stand[version] = sha
        return stand

    def anwenden(self, schema: str, version: str, sql: str) -> None:
        if not SCHEMA_RE.fullmatch(schema):
            raise VorwaertsFehler("schema_ungueltig")
        rc, _ = self._psql(["-v", "ON_ERROR_STOP=1", "--single-transaction"], sql.encode("utf-8"), 300)
        if rc != 0:
            raise VorwaertsFehler("migration_fehlgeschlagen", version)

    def _kern(self, *argumente: str, zeit: float = 170.0) -> bytes:
        rc, aus = self._lauf([self.schritt_kern, *argumente], None, zeit)
        if rc != 0:
            raise VorwaertsFehler("kern_schritt_fehlgeschlagen", argumente[0])
        return aus

    @staticmethod
    def _slot(ausgabe: bytes) -> str:
        m = re.search(rb"^SLOT ([ab])$", ausgabe, re.M)
        if m is None:
            raise VorwaertsFehler("kern_ausgabe")
        return m.group(1).decode()

    def kern_probe(self) -> bool:
        """Eine einzelne Gesundheitsprobe des aktiven Kern-Slots (Schritt-Skript kern-slots probe). Nur Exit 0 heißt gesund; Zeit und Fehler heißen nicht gesund."""
        rc, _ = self._lauf([self.schritt_kern, "probe"], None, 20.0)
        return rc == 0

    def kern_bereiten(self, name: str, ordner: str) -> str:
        if not os.path.isdir(os.path.join(self.releases, name)):
            self._kern("release", name, ordner, zeit=170.0)
        return self._slot(self._kern("bereiten", name, zeit=170.0))

    def kern_schalten(self, name: str) -> str:
        return self._slot(self._kern("schalten", name, zeit=170.0))


class AnkerPg:
    """Datenbank mit der Rolle wamo_anker. pg ist ein wamo_pg.Pg."""

    def __init__(self, pg, schema: str = "wamo_dev"):
        if not SCHEMA_RE.fullmatch(schema):
            raise ValueError("unbekanntes Schema")
        self.pg, self.s = pg, schema

    def anfrage(self, kennung: str) -> dict | None:
        if not UUID_RE.fullmatch(kennung):
            return None
        z = self.pg.eine(f"select status, quelle, art, v_commit, tree_id, version_final from {self.s}.release_anfragen where id = %s::uuid", (kennung,))
        if z is None:
            return None
        return dict(zip(("status", "quelle", "art", "v_commit", "tree_id", "version_final"), z))

    def anfrage_stand(self, kennung: str) -> dict | None:
        """Status, Art und die Commits, die zur Klassifizierung gehören (nur lesend)."""
        if not UUID_RE.fullmatch(kennung):
            return None
        z = self.pg.eine(f"select status, art, basis_commit, c1_commit, r_commit, v_commit from {self.s}.release_anfragen where id = %s::uuid", (kennung,))
        if z is None:
            return None
        return dict(zip(("status", "art", "basis_commit", "c1_commit", "r_commit", "v_commit"), z))

    def klassifizierung_setzen(self, kennung: str, art: str, treffer: list[str]) -> None:
        """Schreibt Art und Treffer (nur der Anker darf das; die Art lässt sich nur anheben, das prüft die Datenbank)."""
        z = self.pg.eine(f"update {self.s}.release_anfragen set art = %s, schutz_treffer = %s::jsonb where id = %s::uuid returning 1",
                         (art, json.dumps(treffer, ensure_ascii=True, separators=(",", ":")), kennung))
        if z is None:
            raise VorwaertsFehler("klassifizierung_nicht_gesetzt")

    def freigabe_gilt(self, kennung: str, commit: str, baum: str) -> bool:
        z = self.pg.eine(f"select {self.s}.release_freigabe_nicht_widerrufen(%s::uuid, %s, %s)", (kennung, commit, baum))
        return bool(z and z[0])

    def status_setzen(self, kennung: str, von: str, nach: str) -> None:
        z = self.pg.eine(f"update {self.s}.release_anfragen set status = %s where id = %s::uuid and status = %s returning 1", (nach, kennung, von))
        if z is None:
            raise VorwaertsFehler("status_nicht_gesetzt", f"{von} nach {nach}")


def vorwaerts_bauen(pg, arbeit: str, schritt_kern: str, schema: str = "wamo_dev") -> Vorwaerts:
    return Vorwaerts(AnkerPg(pg, schema), QuelleGit(), WerkDocker(schritt_kern), arbeit)


def vorwaerts_fabrik(lib: str = "/usr/local/lib/wamo-anker", zugang: str = "/etc/wamo-anker/db.zugang", pylib: str = "/opt/wamo-freigabe/pylib",
                     arbeit: str = "/var/lib/wamo-anker/arbeit", schema: str = "wamo_dev") -> Vorwaerts:
    """Baut die echten Mitspieler im Anker (root): Zugangsdatei des Ankers (root 0600), Treiber aus der hash-gesicherten pylib des Freigabe-Dienstes."""
    import sys

    if os.path.isdir(pylib) and pylib not in sys.path:
        sys.path.append(pylib)   # hinten: ein Modul dort kann nie ein eigenes des Ankers verdecken
    from wamo_pg import Pg, zugangsdatei_lesen

    ziel = zugangsdatei_lesen(zugang)
    os.makedirs(arbeit, mode=0o700, exist_ok=True)
    return vorwaerts_bauen(Pg(ziel, anwendung="wamo-anker"), arbeit, os.path.join(lib, "schritte", "kern-slots"), schema)
