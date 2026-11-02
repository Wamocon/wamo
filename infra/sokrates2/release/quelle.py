"""Quelle des Codes für den Release-Dienst (Konzept docs/selbstueberarbeitung.md D.2, Schritt 1.3). Nur lesend.

Entscheidung 02.11.2026: Der Release-Dienst schreibt nichts nach GitHub und nie auf main; Pull Requests werden von Hand gemergt, bis die zweite GitHub-App
da ist. Dieses Modul kann darum nur lesen:

  * Spiegel      ein bare-Repository (git init --bare), das nur refs/heads/main von der Quelle holt. Alle Git-Aufrufe sind gehärtet: keine Hooks, keine
                 Konfiguration von Benutzer oder System, nur das Protokoll https (file nur im Test), Prüfung aller Objekte beim Holen, kein Nachfragen.
  * GitHubApp    die zweite App (nur lesend): JWT (RS256, signiert mit openssl) -> Installations-Token mit nur contents:read und metadata:read, begrenzt auf das
                 eine Repository; das Token geht nur über die Umgebung an git (nie über Argumente), und es wird nach dem Holen widerrufen.
  * Archiv       Commit -> Archiv für die Prüfstrecke (pruefer_archiv.py), direkt aus dem Objektspeicher (git ls-tree und git cat-file), ohne Arbeitsordner,
                 ohne Git-Attribute (export-ignore und export-subst würden den Inhalt verändern), ohne Hooks. Verknüpfungen kommen als L, Submodule sind ein Fehler.

Nichts davon führt Code aus dem Repository aus. Nur Standardbibliothek, git und openssl als feste Programme.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from typing import BinaryIO, Callable

GIT = "/usr/bin/git"
OPENSSL = "/usr/bin/openssl"
SHA_RE = re.compile(r"[0-9a-f]{40}")
REF_MAIN = "refs/heads/main"
API = "https://api.github.com"
ANTWORT_MAX = 1024 * 1024
# Lokale Quelle eines anderen Benutzers (wamocon): git verweigert sonst wegen fremden Eigentums ("dubious ownership"). Bei lokalem Transport verwirft git die
# Konfiguration von der Befehlszeile für den Kindprozess, also bleibt nur die globale Datei: root-eigen, enthält nur safe.directory für genau diesen Pfad.
GIT_QUELLE_KONFIG = "/etc/wamo-release/git-quelle.gitconfig"
LOKAL_RE = re.compile(r"/var/lib/wamo-quelle/[a-z0-9][a-z0-9._-]{0,39}\.git")
LS_TREE_RE = re.compile(rb"(\d{6}) (blob|tree|commit) ([0-9a-f]{40}) +(\d+|-)\t(.*)", re.S)


class QuelleFehler(Exception):
    """Grund als kurzes Schlüsselwort; nie ein Token, nie ein Inhalt aus dem Repository."""

    def __init__(self, grund: str, detail: str = ""):
        super().__init__(f"{grund}: {detail}" if detail else grund)
        self.grund, self.detail = grund, detail


# ------------------------------------------------------------------------------------------------ git
def git_umgebung(token: str | None = None, proxy: str | None = None, globale_konfig: str | None = None) -> dict[str, str]:
    """Saubere Umgebung: nichts aus der Umgebung des Dienstes. Das Token (falls vorhanden) steht als Kopfzeile in der Git-Konfiguration aus der Umgebung."""
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8", "HOME": "/nonexistent", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": globale_konfig or "/dev/null",
           "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/false", "GIT_OPTIONAL_LOCKS": "0"}
    if proxy:
        env["https_proxy"] = env["HTTPS_PROXY"] = proxy
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.extraHeader", GIT_CONFIG_VALUE_0=f"Authorization: Basic {basic}")
    return env


def git_aufruf(argumente: list[str], *, spiegel: str | None = None, token: str | None = None, proxy: str | None = None, eingabe: bytes | None = None,
               zeit: float = 300.0, datei_erlaubt: bool = False, globale_konfig: str | None = None) -> bytes:
    protokolle = ["-c", "protocol.allow=never", "-c", "protocol.https.allow=always"] + (["-c", "protocol.file.allow=always"] if datei_erlaubt else [])
    argv = [GIT, "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "credential.helper=", "-c", "core.askPass=", "-c", "transfer.fsckObjects=true",
            "-c", "fetch.fsckObjects=true", "-c", "uploadpack.packObjectsHook=", "-c", "advice.detachedHead=false", *protokolle]
    if spiegel:
        argv += ["--git-dir", spiegel]
    argv += argumente
    try:
        p = subprocess.run(argv, input=eingabe, capture_output=True, timeout=zeit, env=git_umgebung(token, proxy, globale_konfig), cwd="/" if os.name == "posix" else None)
    except subprocess.TimeoutExpired:
        raise QuelleFehler("git_zeitlimit", argumente[0]) from None
    except OSError as e:
        raise QuelleFehler("git_nicht_startbar", type(e).__name__) from None
    if p.returncode != 0:
        raise QuelleFehler("git_fehler", f"{argumente[0]} exit {p.returncode}")   # der Fehlertext von git kann Adressen und Pfade enthalten: nicht weitergeben
    return p.stdout


class Spiegel:
    def __init__(self, pfad: str, url: str, proxy: str | None = None, datei_erlaubt: bool = False):
        self.pfad, self.url, self.proxy, self.datei_erlaubt = pfad, url, proxy, datei_erlaubt

    def _git(self, argumente: list[str], **kw) -> bytes:
        return git_aufruf(argumente, spiegel=self.pfad, proxy=self.proxy, datei_erlaubt=self.datei_erlaubt, **kw)

    def aktualisieren(self, token: str | None = None) -> str:
        """Holt refs/heads/main (erzwungen: auch eine umgeschriebene Geschichte wird übernommen und bei der Prüfung der Basis auffallen) und liefert den Commit."""
        if not os.path.isfile(os.path.join(self.pfad, "HEAD")):
            git_aufruf(["init", "--bare", "--quiet", self.pfad], datei_erlaubt=self.datei_erlaubt)
        git_aufruf(["fetch", "--force", "--no-tags", "--no-recurse-submodules", "--quiet", self.url, f"+{REF_MAIN}:{REF_MAIN}"], spiegel=self.pfad,
                   token=token, proxy=self.proxy, datei_erlaubt=self.datei_erlaubt,
                   globale_konfig=GIT_QUELLE_KONFIG if self.datei_erlaubt and self.url.startswith("/") else None)
        return self.kopf()

    def kopf(self, ref: str = REF_MAIN) -> str:
        sha = self._git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"]).decode().strip()
        if not SHA_RE.fullmatch(sha):
            raise QuelleFehler("kein_commit", ref)
        return sha

    def baum(self, commit: str) -> str:
        self._sha(commit)
        sha = self._git(["rev-parse", "--verify", "--quiet", f"{commit}^{{tree}}"]).decode().strip()
        if not SHA_RE.fullmatch(sha):
            raise QuelleFehler("kein_baum", commit)
        return sha

    def ist_vorfahre(self, alt: str, neu: str) -> bool:
        self._sha(alt)
        self._sha(neu)
        try:
            self._git(["merge-base", "--is-ancestor", alt, neu])
            return True
        except QuelleFehler:
            return False

    def lese_datei(self, commit: str, pfad: str, max_bytes: int = 1 << 20) -> bytes | None:
        """Der Inhalt einer Datei in einem Commit (höchstens max_bytes) oder None, wenn es sie dort nicht gibt. Der Pfad ist fest vom Aufrufer, nie aus einer Anfrage."""
        self._sha(commit)
        if not re.fullmatch(r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+){0,4}", pfad):
            raise QuelleFehler("pfad_ungueltig")
        try:
            roh = self._git(["cat-file", "blob", f"{commit}:{pfad}"])
        except QuelleFehler:
            return None
        return roh[:max_bytes]

    @staticmethod
    def _sha(commit: str) -> None:
        if not isinstance(commit, str) or not SHA_RE.fullmatch(commit):
            raise QuelleFehler("commit_ungueltig")   # nie ein Name oder eine Option: nur 40 Hex-Zeichen kommen an git

    def schreibe_archiv(self, ausgang: BinaryIO, basis_commit: str, neu_commit: str) -> dict:
        """Schreibt beide Stände im Format von pruefer_archiv.py, direkt aus dem Objektspeicher."""
        import pruefer_archiv as A
        self._sha(basis_commit)
        self._sha(neu_commit)
        zaehler = {"dateien": 0, "bytes": 0}
        for name, commit in (("basis", basis_commit), ("neu", neu_commit)):
            ausgang.write(f"D {name}\n".encode())
            roh = self._git(["ls-tree", "-r", "-t", "-z", "--full-tree", "--long", commit])
            leser = subprocess.Popen([GIT, "-c", "core.hooksPath=/dev/null", "--git-dir", self.pfad, "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=git_umgebung(), cwd="/" if os.name == "posix" else None)
            try:
                for eintrag in roh.split(b"\0"):
                    if not eintrag:
                        continue
                    m = LS_TREE_RE.fullmatch(eintrag)
                    if m is None:
                        raise QuelleFehler("ls_tree_form")
                    modus, art, oid, groesse_text, pfad_roh = m.groups()
                    try:
                        pfad = pfad_roh.decode("utf-8")
                    except UnicodeDecodeError:
                        raise QuelleFehler("pfad_zeichensatz") from None
                    if any(t in A.AUSLASSEN for t in pfad.split("/")):
                        continue   # wie im Läufer: .git, node_modules, .venv und Verwandte gehören nicht zum Stand
                    voll = f"{name}/{pfad}"
                    try:
                        A.pfad_zerlegen(voll)
                    except A.ArchivFehler as e:
                        raise QuelleFehler("pfad_ungueltig", str(e)) from None
                    if art == b"tree":
                        ausgang.write(f"D {voll}\n".encode())
                    elif art == b"commit":
                        raise QuelleFehler("submodul", "Submodule sind nicht erlaubt")
                    elif modus == b"120000":
                        ausgang.write(f"L {voll}\n".encode())
                    elif modus in (b"100644", b"100755"):
                        groesse = int(groesse_text)
                        if groesse > A.DATEI_MAX:
                            raise QuelleFehler("datei_zu_gross")
                        self._blob(leser, oid.decode(), groesse, ausgang, modus == b"100755", voll)
                        zaehler["dateien"] += 1
                        zaehler["bytes"] += groesse
                    else:
                        raise QuelleFehler("modus_unbekannt", modus.decode())
            finally:
                try:
                    leser.stdin.close()
                except OSError:
                    pass
                leser.kill()
                leser.wait()
        ausgang.write(b"END\n")
        return zaehler

    @staticmethod
    def _blob(leser: subprocess.Popen, oid: str, groesse: int, ausgang: BinaryIO, ausfuehrbar: bool, voll: str) -> None:
        leser.stdin.write(oid.encode() + b"\n")
        leser.stdin.flush()
        kopf = leser.stdout.readline()
        teile = kopf.split()
        if len(teile) != 3 or teile[0].decode(errors="replace") != oid or teile[1] != b"blob" or not teile[2].isdigit() or int(teile[2]) != groesse:
            raise QuelleFehler("objekt_unstimmig")
        ausgang.write(f"F {groesse} {'x' if ausfuehrbar else '-'} {voll}\n".encode())
        rest = groesse
        while rest:
            block = leser.stdout.read(min(1024 * 1024, rest))
            if not block:
                raise QuelleFehler("objekt_abgebrochen")
            ausgang.write(block)
            rest -= len(block)
        if leser.stdout.read(1) != b"\n":
            raise QuelleFehler("objekt_unstimmig")
        ausgang.write(b"\n")


# ------------------------------------------------------------------------------------------------ GitHub-App (nur lesend)
HttpFn = Callable[[str, str, dict, "bytes | None"], "tuple[int, bytes]"]


def http_echt(methode: str, url: str, kopf: dict, body: bytes | None, proxy: str | None = None, zeit: float = 30.0) -> tuple[int, bytes]:
    if not url.startswith("https://"):
        raise QuelleFehler("nur_https")
    handler = urllib.request.ProxyHandler({"https": proxy} if proxy else {})
    opener = urllib.request.build_opener(handler)
    req = urllib.request.Request(url, data=body, method=methode, headers=kopf)
    try:
        with opener.open(req, timeout=zeit) as r:
            return r.status, r.read(ANTWORT_MAX + 1)[:ANTWORT_MAX]
    except urllib.error.HTTPError as e:
        return e.code, e.read(ANTWORT_MAX + 1)[:ANTWORT_MAX]
    except OSError as e:
        raise QuelleFehler("netz", type(e).__name__) from None


def _b64url(roh: bytes) -> str:
    return base64.urlsafe_b64encode(roh).rstrip(b"=").decode()


class GitHubApp:
    """Die zweite App des Release-Dienstes, hier nur mit Leserechten. Das Schreiben (Branch, Tag, Release, Fast-Forward von main) gibt es in diesem
    Schritt bewusst nicht (Entscheidung 02.11.2026)."""

    LESERECHTE = {"contents": "read", "metadata": "read"}

    def __init__(self, app_id: str, installation_id: str, pem_pfad: str, repo: str = "wamo", http: HttpFn | None = None, proxy: str | None = None,
                 uhr: Callable[[], float] = time.time, openssl: str | None = None):
        if not (str(app_id).isdigit() and str(installation_id).isdigit()):
            raise QuelleFehler("kennung_ungueltig")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", repo):
            raise QuelleFehler("repo_ungueltig")
        self.app_id, self.installation_id, self.pem, self.repo = str(app_id), str(installation_id), pem_pfad, repo
        self._http = http or (lambda m, u, k, b: http_echt(m, u, k, b, proxy))
        self._uhr, self._openssl = uhr, openssl or OPENSSL

    def jwt(self) -> str:
        jetzt = int(self._uhr())
        kopf = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
        nutz = _b64url(json.dumps({"iat": jetzt - 60, "exp": jetzt + 540, "iss": self.app_id}, separators=(",", ":")).encode())
        text = f"{kopf}.{nutz}".encode()
        try:
            p = subprocess.run([self._openssl, "dgst", "-sha256", "-sign", self.pem], input=text, capture_output=True, timeout=20,
                               env={"PATH": "/usr/bin:/bin"} if os.name == "posix" else None)
        except (OSError, subprocess.TimeoutExpired):
            raise QuelleFehler("jwt_signatur") from None
        if p.returncode != 0 or not p.stdout:
            raise QuelleFehler("jwt_signatur")   # der Text von openssl kann den Pfad des Schlüssels nennen: nicht weitergeben
        return f"{kopf}.{nutz}.{_b64url(p.stdout)}"

    def _kopf(self, bearer: str) -> dict:
        return {"Authorization": f"Bearer {bearer}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "wamo-release",
                "Content-Type": "application/json"}

    def token_lesen(self) -> str:
        body = json.dumps({"repositories": [self.repo], "permissions": self.LESERECHTE}).encode()
        status, roh = self._http("POST", f"{API}/app/installations/{self.installation_id}/access_tokens", self._kopf(self.jwt()), body)
        if status != 201:
            raise QuelleFehler("token_abgelehnt", str(status))
        try:
            d = json.loads(roh)
            token = d["token"]
            rechte = d.get("permissions", {})
            repos = d.get("repositories", [])
        except (ValueError, KeyError, TypeError, AttributeError):
            raise QuelleFehler("token_antwort") from None
        if not isinstance(token, str) or not 20 <= len(token) <= 400 or not re.fullmatch(r"[A-Za-z0-9_.-]+", token):
            raise QuelleFehler("token_antwort")
        # Die Antwort muss genau das Verlangte bestätigen: nie mehr Rechte, nie ein anderes oder ein zweites Repository
        if not isinstance(rechte, dict) or any(v != "read" for v in rechte.values()) or set(rechte) - set(self.LESERECHTE):
            raise QuelleFehler("token_rechte")
        if not isinstance(repos, list) or [r.get("name") for r in repos if isinstance(r, dict)] != [self.repo]:
            raise QuelleFehler("token_repo")
        return token

    def widerrufen(self, token: str) -> bool:
        try:
            status, _ = self._http("DELETE", f"{API}/installation/token", self._kopf(token), None)
        except QuelleFehler:
            return False
        return status == 204


class Quelle:
    def __init__(self, spiegel: Spiegel, app: GitHubApp | None = None):
        self.spiegel, self.app = spiegel, app

    def aktualisieren(self) -> str:
        """Holt main. Mit App: lesendes Token nur für diesen Aufruf, danach widerrufen (auch bei einem Fehler)."""
        if self.app is None:
            return self.spiegel.aktualisieren(None)
        token = self.app.token_lesen()
        try:
            return self.spiegel.aktualisieren(token)
        finally:
            self.app.widerrufen(token)


def quelle_aus_datei(pfad: str, spiegel_pfad: str = "/var/lib/wamo-release/spiegel.git") -> Quelle | None:
    """Liest /etc/wamo-release/quelle.env (WAMO_QUELLE_URL, _APP_ID, _INSTALLATION_ID, _PEM, _REPO, _PROXY). Fehlt die Datei: keine Quelle eingerichtet."""
    try:
        with open(pfad, encoding="utf-8") as f:
            zeilen = f.read().splitlines()
    except OSError:
        return None
    w: dict[str, str] = {}
    for z in zeilen:
        z = z.strip()
        if z and not z.startswith("#") and "=" in z:
            k, _, v = z.partition("=")
            w[k.strip()] = v.strip()
    url = w.get("WAMO_QUELLE_URL", "")
    if LOKAL_RE.fullmatch(url):
        # Lokale Quelle (Entscheidung Niko 02.11.2026, bis die zweite GitHub-App und der Netzweg da sind): ein Bare-Repository auf diesem Rechner, in das
        # wamocon per SSH Commits ablegt (wamo-quelle-einrichten.sh). Der Release-Dienst liest nur, genau wie bei GitHub; ob der Stand stimmt, entscheidet allein die
        # Freigabe über Commit und Tree, nie der Weg, auf dem der Commit hierher kam.
        return Quelle(Spiegel(spiegel_pfad, url, None, datei_erlaubt=True), None)
    if not url.startswith("https://"):
        raise QuelleFehler("url_ungueltig")
    proxy = w.get("WAMO_QUELLE_PROXY") or None
    app = None
    if w.get("WAMO_QUELLE_APP_ID"):
        app = GitHubApp(w["WAMO_QUELLE_APP_ID"], w.get("WAMO_QUELLE_INSTALLATION_ID", ""), w.get("WAMO_QUELLE_PEM", "/etc/wamo-release/app.pem"),
                        w.get("WAMO_QUELLE_REPO", "wamo"), proxy=proxy)
    return Quelle(Spiegel(spiegel_pfad, url, proxy), app)
