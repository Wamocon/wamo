"""Vertrauensprüfung, Audit-Protokoll mit Hashkette und atomare Zustandsdatei des Release-Dienstes (Schritt 0.3).

Diese Teile entsprechen bewusst denen des Notankers (infra/sokrates2/anker/wamo_anker.py), damit beide ohne gemeinsame
Abhängigkeit geprüft und getrennt installiert werden können. Der Unterschied: Der Release-Dienst läuft ohne root. Seine
Ordner und Dateien gehören dem Dienstbenutzer (vertrauens_uid), nicht root. Das Audit-Protokoll bekommt `chattr +a` beim
Einrichten (root); der Dienst kann nur anhängen.

Das Audit-Protokoll ist zugleich das Write-Ahead-Log des Zustandsautomaten: Eine Zeile, die den Zustand ändert, wird
zuerst angehängt und dann erst in die Zustandsdatei übernommen. Nach einem Absturz liest der Dienst die Zeilen hinter dem
Stand der Zustandsdatei erneut (audit_ab). Dafür müssen die Zeilen alles enthalten, was zum Nachvollziehen nötig ist.
Nur Standardbibliothek, Linux (unter Windows laufen die Tests ohne flock und Rechteprüfung).
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import re
import secrets
import stat
import time
from typing import Callable, Iterator

try:  # nur Linux
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

GENESIS = "0" * 64
AUDIT_ZEILE_MAX = 2048  # eine Zeile samt Zeilenende; Zustandszeilen tragen Commit-IDs und den Prüfstand
AUDIT_DATEI_MAX = 32 * 1024 * 1024  # darüber lehnen alle Zeilen ab (Handarbeit: Datei rotieren, README)
AUDIT_GRENZE_LESEN = 0.75  # bis zu diesem Füllgrad protokollieren Abweisungen und Zeilen ohne Wirkung
AUDIT_PLATZ_MIN = 1024 * 1024  # freier Platz, unter dem nichts mehr angehängt wird (keine halben Zeilen)
ZUSTAND_MAX = 64 * 1024
DATEI_MAX = 1024 * 1024

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_BINARY = getattr(os, "O_BINARY", 0)


def eigene_uid() -> int:
    return os.getuid() if hasattr(os, "getuid") else 0


def zeit_iso(sekunden: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(sekunden))


def sauber(text: object, laenge: int) -> str:
    """Nur harmlose Zeichen in Protokoll und Antworten (Steuer-, Bidi- und Nicht-ASCII-Zeichen werden zu ?)."""
    return re.sub(r"[^A-Za-z0-9 _.:/=,+@-]", "?", str(text))[:laenge]


def kanonisch(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def flock(fd: int, nicht_blockierend: bool = False) -> None:
    if fcntl is None:
        return
    fcntl.flock(fd, fcntl.LOCK_EX | (fcntl.LOCK_NB if nicht_blockierend else 0))


def fsync_ordner(pfad: str) -> None:
    if os.name != "posix":
        return
    with contextlib.suppress(OSError):
        fd = os.open(pfad, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def unsicher(pfad: str, ordner: bool, vertrauens_uid: int, privat: bool = False) -> str | None:
    """Grund, warum Datei oder Ordner nicht vertrauenswürdig ist (None: in Ordnung). Kein Link, Besitzer vertrauens_uid,
    für Gruppe und alle nicht beschreibbar (privat: auch nicht lesbar)."""
    try:
        st = os.lstat(pfad)
    except FileNotFoundError:
        return "fehlt"
    except OSError:
        return "nicht lesbar"
    if stat.S_ISLNK(st.st_mode):
        return "ist ein Link"
    if not (stat.S_ISDIR(st.st_mode) if ordner else stat.S_ISREG(st.st_mode)):
        return "hat die falsche Art"
    if os.name == "posix":
        if st.st_uid != vertrauens_uid:
            return "gehört nicht dem erwarteten Besitzer"
        if st.st_mode & (0o077 if privat else 0o022):
            return "hat zu offene Rechte"
    return None


def lies_datei(pfad: str, grenze: int, vertrauens_uid: int, privat: bool = False) -> bytes:
    """Liest eine vertrauenswürdige reguläre Datei bis grenze Bytes. Wirft OSError, ValueError."""
    grund = unsicher(pfad, False, vertrauens_uid, privat)
    if grund:
        raise ValueError(grund)
    fd = os.open(pfad, os.O_RDONLY | _NOFOLLOW | _CLOEXEC | _BINARY)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("hat die falsche Art")
        daten = b""
        while len(daten) <= grenze:
            teil = os.read(fd, 65536)
            if not teil:
                return daten
            daten += teil
        raise ValueError("zu groß")
    finally:
        os.close(fd)


# ---------------------------------------------------------------- Audit-Protokoll mit Hashkette
class AuditFehler(Exception):
    pass


class KetteDefekt(Exception):
    pass


def zeile_und_hash(rest: dict) -> tuple[bytes, str]:
    h = hashlib.sha256(kanonisch(rest).encode("ascii")).hexdigest()
    return (kanonisch({**rest, "hash": h}) + "\n").encode("ascii"), h


def zeile_bauen(rest: dict) -> bytes:
    return zeile_und_hash(rest)[0]


def zeile_lesen(zeile: bytes) -> tuple[dict, str]:
    """(Inhalt ohne hash, hash) einer Protokollzeile; wirft ValueError bei Formfehlern und falschem Hash."""
    if len(zeile) > AUDIT_ZEILE_MAX:
        raise ValueError("Zeile zu lang")
    try:
        obj = json.loads(zeile.decode("ascii"))
    except (UnicodeDecodeError, ValueError, RecursionError) as e:
        raise ValueError("Zeile unlesbar") from e
    h = obj.pop("hash", None) if isinstance(obj, dict) else None
    if not isinstance(h, str) or not isinstance(obj.get("n"), int) or isinstance(obj.get("n"), bool):
        raise ValueError("Zeile ohne n oder hash")
    if hashlib.sha256(kanonisch(obj).encode("ascii")).hexdigest() != h:
        raise ValueError("Hash stimmt nicht")
    return obj, h


def audit_zeilen(pfad: str, vertrauens_uid: int | None = None) -> Iterator[tuple[int, dict, str]]:
    """Liest die Datei Zeile für Zeile (konstanter Speicher) und prüft dabei die Kette. Liefert (n, Inhalt, hash).
    Fehlt die Datei, ist die Kette leer. Wirft KetteDefekt mit dem Grund, sobald etwas nicht stimmt (die bis dahin
    gelieferten Zeilen waren in Ordnung)."""
    uid = eigene_uid() if vertrauens_uid is None else vertrauens_uid
    grund = unsicher(pfad, False, uid, privat=True)
    if grund == "fehlt":
        return
    if grund:
        raise KetteDefekt(f"Datei {grund}")
    vorher, n, gelesen = GENESIS, 0, 0
    try:
        fd = os.open(pfad, os.O_RDONLY | _NOFOLLOW | _CLOEXEC | _BINARY)
    except OSError as e:
        raise KetteDefekt("Datei nicht lesbar") from e
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise KetteDefekt("Datei hat die falsche Art")
        while zeile := f.readline(AUDIT_ZEILE_MAX + 2):
            n += 1
            gelesen += len(zeile)
            if gelesen > AUDIT_DATEI_MAX + AUDIT_ZEILE_MAX:
                raise KetteDefekt("Datei zu groß")
            if not zeile.endswith(b"\n"):
                raise KetteDefekt(f"Zeile {n}: unvollständig oder zu lang")
            try:
                obj, h = zeile_lesen(zeile[:-1])
            except ValueError as e:
                raise KetteDefekt(f"Zeile {n}: {e}") from e
            if obj["n"] != n or obj.get("vorher") != vorher:
                raise KetteDefekt(f"Zeile {n}: Kette unterbrochen")
            vorher = h
            yield n, obj, h


def audit_ab(pfad: str, offset: int, n_vorher: int, vertrauens_uid: int | None = None,
             maximum: int = 50) -> list[tuple[int, dict, int]]:
    """Liest höchstens maximum vollständige Zeilen ab Byte-Position offset (Zeile n_vorher + 1 beginnt dort). Liefert
    (n, Inhalt, Position hinter der Zeile). Prüft Form, Hash und Nummer jeder Zeile; die Kette vor offset wird hier nicht
    erneut gelesen (das tun der Start und status). Wirft KetteDefekt. Eine unvollständige letzte Zeile wird ausgelassen."""
    uid = eigene_uid() if vertrauens_uid is None else vertrauens_uid
    grund = unsicher(pfad, False, uid, privat=True)
    if grund == "fehlt":
        return []
    if grund:
        raise KetteDefekt(f"Datei {grund}")
    ergebnis: list[tuple[int, dict, int]] = []
    try:
        fd = os.open(pfad, os.O_RDONLY | _NOFOLLOW | _CLOEXEC | _BINARY)
    except OSError as e:
        raise KetteDefekt("Datei nicht lesbar") from e
    with os.fdopen(fd, "rb") as f:
        f.seek(offset)
        n, pos = n_vorher, offset
        while len(ergebnis) < maximum and (zeile := f.readline(AUDIT_ZEILE_MAX + 2)):
            if not zeile.endswith(b"\n"):
                if len(zeile) > AUDIT_ZEILE_MAX:
                    raise KetteDefekt("Zeile zu lang")
                break
            n += 1
            try:
                obj, _h = zeile_lesen(zeile[:-1])
            except ValueError as e:
                raise KetteDefekt(f"Zeile {n}: {e}") from e
            if obj["n"] != n:
                raise KetteDefekt(f"Zeile {n}: Nummer stimmt nicht")
            pos += len(zeile)
            ergebnis.append((n, obj, pos))
    return ergebnis


def kette_pruefen(pfad: str, vertrauens_uid: int | None = None) -> tuple[bool, int, str, str | None]:
    """Ergebnis (ok, Zeilen, letzter Hash, Grund). Fehlt die Datei, ist die Kette leer und in Ordnung."""
    n, letzter = 0, GENESIS
    try:
        for n, _obj, letzter in audit_zeilen(pfad, vertrauens_uid):
            pass
    except KetteDefekt as e:
        return False, n, letzter, str(e)
    except OSError:
        return False, n, letzter, "Datei nicht lesbar"
    return True, n, letzter, None


class Audit:
    """Anhängen mit Hashkette. Jede Zeile enthält n, vorher (Hash der Vorzeile) und hash. Es wird nie überschrieben.
    Die letzte Zeile wird vor jedem Anhängen geprüft; ein Fehler dort stoppt das Anhängen (fail closed)."""

    def __init__(self, pfad: str, uhr: Callable[[], float] = time.time, vertrauens_uid: int | None = None):
        self.pfad = pfad
        self.uhr = uhr
        self.vertrauens_uid = eigene_uid() if vertrauens_uid is None else vertrauens_uid

    def fuellgrad(self) -> float:
        try:
            return os.stat(self.pfad).st_size / AUDIT_DATEI_MAX
        except OSError:
            return 0.0

    def anhaengen(self, ereignis: str, bis: float = 1.0, **felder: object) -> tuple[int, str]:
        """Hängt eine Zeile an, solange die Datei danach höchstens bis (Anteil von AUDIT_DATEI_MAX) groß ist und noch
        AUDIT_PLATZ_MIN frei ist. Ergebnis: die Nummer n und der Hash der Zeile. Wirft AuditFehler; angehängt wird dann
        nichts."""
        rest = {"ereignis": ereignis, "t": zeit_iso(self.uhr())}
        rest.update({k: v for k, v in felder.items() if v is not None})
        fd = None
        grund = unsicher(os.path.dirname(self.pfad), True, self.vertrauens_uid)
        grund_datei = unsicher(self.pfad, False, self.vertrauens_uid, privat=True)
        if grund or grund_datei not in (None, "fehlt"):  # Link, fremder Besitzer, offene Rechte: nicht anfassen
            raise AuditFehler(f"Ordner oder Datei nicht vertrauenswürdig ({grund or grund_datei})")
        try:
            fd = os.open(self.pfad, os.O_RDWR | os.O_APPEND | os.O_CREAT | _NOFOLLOW | _CLOEXEC | _BINARY, 0o600)
            flock(fd)
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise AuditFehler("keine reguläre Datei")
            vorher, n = self._letzte(fd, st.st_size)
            rest.update(n=n + 1, vorher=vorher)
            zeile, h = zeile_und_hash(rest)
            if len(zeile) > AUDIT_ZEILE_MAX:
                raise AuditFehler("Zeile zu lang")
            if st.st_size + len(zeile) > AUDIT_DATEI_MAX * bis:
                raise AuditFehler("Audit-Datei voll")
            frei = getattr(os, "statvfs", None)
            if frei is not None:  # keine halbe Zeile bei voller Platte: erst prüfen, dann schreiben
                v = frei(os.path.dirname(self.pfad))
                if v.f_bavail * v.f_frsize < AUDIT_PLATZ_MIN:
                    raise AuditFehler("zu wenig freier Platz")
            if os.write(fd, zeile) != len(zeile):
                raise AuditFehler("Teilschreibung")
            os.fsync(fd)
            return n + 1, h
        except OSError as e:
            raise AuditFehler(f"Schreibfehler {errno.errorcode.get(e.errno or 0, '?')}") from e
        finally:
            if fd is not None:
                os.close(fd)

    @staticmethod
    def _letzte(fd: int, groesse: int) -> tuple[str, int]:
        if groesse == 0:
            return GENESIS, 0
        laenge = min(groesse, 2 * AUDIT_ZEILE_MAX)
        os.lseek(fd, groesse - laenge, os.SEEK_SET)
        block = b""
        while len(block) < laenge:
            teil = os.read(fd, laenge - len(block))
            if not teil:
                break
            block += teil
        if not block.endswith(b"\n"):
            raise AuditFehler("letzte Zeile unvollständig")
        teile = block[:-1].rsplit(b"\n", 1)
        if len(teile) == 1 and groesse > laenge:
            raise AuditFehler("letzte Zeile zu lang")
        try:
            obj, h = zeile_lesen(teile[-1])
        except ValueError as e:
            raise AuditFehler(f"letzte Zeile: {e}") from e
        return h, obj["n"]


# ---------------------------------------------------------------- atomare Zustandsdatei
def datei_atomar_schreiben(pfad: str, daten: bytes, sichern: bool = True) -> None:
    """Neue Datei im selben Ordner (O_EXCL, 0600), fsync, Rename. Die bisherige Datei bleibt als .vorher (best effort).
    Bei jedem Fehler (volle Platte) bleibt die alte Datei unberührt. Wirft OSError."""
    ordner = os.path.dirname(pfad)
    tmp = os.path.join(ordner, f".zustand.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC | _BINARY, 0o600)
    try:
        try:
            if os.write(fd, daten) != len(daten):
                raise OSError(errno.ENOSPC, "Teilschreibung")
            os.fsync(fd)
        finally:
            os.close(fd)
        if sichern:
            vorher_tmp = tmp + ".v"
            try:
                os.link(pfad, vorher_tmp)
                os.replace(vorher_tmp, pfad + ".vorher")
            except OSError:
                with contextlib.suppress(OSError):
                    os.unlink(vorher_tmp)
        os.replace(tmp, pfad)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    fsync_ordner(ordner)
