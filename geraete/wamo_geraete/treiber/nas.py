"""NAS über SMB (WMC_NAS1 192.168.178.25 und WMC-Testelite 192.168.178.29, beide Synology DSM).

Nur lesen: Dateien in den freigegebenen Freigaben (geraete.toml: freigaben) finden und abrufen (höchstens 25 MB).
Einzige Schreibstelle: Scans in den Scan-Ordner der Person (scanziel), sobald WMC_NAS1 dafür eingerichtet ist.
Zugangsdaten nur aus geraete.env (WAMO_GERAETE_<ZUGANG>_BENUTZER und _PASSWORT); fehlen sie, melden alle Werkzeuge
"nicht eingerichtet". SMB 3 immer verschlüsselt (encrypt=True; bietet ein NAS das nicht an, scheitert die Verbindung).
Pfade nur relativ, ohne "..", ohne Laufwerk, ohne Doppelpunkt, Teile ohne Punkt oder Leerzeichen am Ende (Windows und
SMB bilden "name." auf "name" ab); Dateinamen vom NAS sind Daten (bereinigt, gekürzt). Der Kern gibt dem Sprachmodell
keine Pfade und Namen, sondern Treffernummern (geraete_werkzeuge.py).
"""

from __future__ import annotations

import asyncio
import re
import time

from ..konfig import Geraet, Konfig
from ..text import _UNSICHTBAR, dateiname
from . import Abgelehnt, GeraetFehler, NichtEingerichtet

MAX_DATEI = 25 * 1024 * 1024
MAX_TREFFER = 50
MAX_BESUCHT = 3000
MAX_TIEFE = 6
SUCH_ZEIT = 15.0
_TEIL_RE = re.compile(r'^[^\\/:*?"<>|\x00-\x1f]{1,255}$')


def _zugang(geraet: Geraet, konfig: Konfig) -> tuple[str, str]:
    zugang = geraet.opt("zugang")
    benutzer, passwort = konfig.geheimnis(zugang, "BENUTZER"), konfig.geheimnis(zugang, "PASSWORT")
    if not (geraet.adresse and zugang and benutzer and passwort and geraet.opt("freigaben")):
        raise NichtEingerichtet("NAS ist nicht eingerichtet (Zugangsdaten und Freigaben fehlen)")
    return benutzer, passwort


def pfad_teile(pfad: str) -> list[str]:
    """Relativer Pfad in Teile; wirft Abgelehnt bei .., absoluten Pfaden, Laufwerken, Steuerzeichen."""
    if not isinstance(pfad, str) or len(pfad) > 1000:
        raise Abgelehnt("Pfad ungültig")
    teile = [t for t in re.split(r"[\\/]+", pfad.strip()) if t]
    if not teile or any(t in (".", "..") or not _TEIL_RE.fullmatch(t) or t.endswith((".", " ")) or t != t.strip()
                        for t in teile):
        raise Abgelehnt("Pfad ungültig")
    return teile


def _unc(geraet: Geraet, freigabe: str, teile: list[str]) -> str:
    return "\\\\" + geraet.adresse + "\\" + freigabe + ("\\" + "\\".join(teile) if teile else "")


def _sitzung(geraet: Geraet, benutzer: str, passwort: str):
    import smbclient  # smbprotocol, erst hier geladen

    smbclient.register_session(geraet.adresse, username=benutzer, password=passwort, port=geraet.port or 445,
                               connection_timeout=5, encrypt=True)
    return smbclient


async def status(geraet: Geraet, konfig: Konfig) -> dict:
    if not geraet.adresse:
        raise NichtEingerichtet("Adresse des NAS fehlt")
    try:
        _r, w = await asyncio.wait_for(asyncio.open_connection(geraet.adresse, geraet.port or 445), 2.5)
        w.close()
        erreichbar = True
    except (OSError, TimeoutError):
        erreichbar = False
    zugang = geraet.opt("zugang")
    eingerichtet = bool(zugang and konfig.geheimnis(zugang, "BENUTZER") and konfig.geheimnis(zugang, "PASSWORT"))
    return {"erreichbar": erreichbar, "eingerichtet": eingerichtet,
            "freigaben": list(geraet.opt("freigaben", ())) if eingerichtet else [],
            "scanziel": bool(geraet.opt("scanziel")) and eingerichtet}


def _suchen_sync(geraet: Geraet, benutzer: str, passwort: str, begriff: str, freigabe: str | None) -> list[dict]:
    smb = _sitzung(geraet, benutzer, passwort)
    begriff_k = begriff.casefold()
    treffer: list[dict] = []
    besucht = 0
    ende = time.monotonic() + SUCH_ZEIT
    for f in [freigabe] if freigabe else list(geraet.opt("freigaben", ())):
        stapel: list[tuple[list[str], int]] = [([], 0)]
        while stapel and len(treffer) < MAX_TREFFER and besucht < MAX_BESUCHT and time.monotonic() < ende:
            teile, tiefe = stapel.pop()
            try:
                eintraege = list(smb.scandir(_unc(geraet, f, teile), username=benutzer, password=passwort))
            except OSError:
                continue
            for e in eintraege:
                besucht += 1
                if e.name.startswith(".") or e.name in ("#recycle", "@eaDir", "#snapshot"):
                    continue
                if not _TEIL_RE.fullmatch(e.name) or _UNSICHTBAR.search(e.name) or e.name.endswith((".", " ")):
                    continue  # unsichtbare Zeichen im Namen: nicht anzeigen (Daten für das Sprachmodell)
                ist_ordner = e.is_dir()
                if begriff_k in e.name.casefold():
                    groesse = 0 if ist_ordner else e.stat().st_size
                    treffer.append({"name": dateiname(e.name, 80), "ordner": ist_ordner, "groesse": groesse,
                                    "pfad": "/".join([f, *teile, e.name])[:400]})
                    if len(treffer) >= MAX_TREFFER:
                        break
                if ist_ordner and tiefe < MAX_TIEFE:
                    stapel.append(([*teile, e.name], tiefe + 1))
    return treffer


async def suchen(geraet: Geraet, konfig: Konfig, begriff: str, freigabe: str | None = None) -> dict:
    benutzer, passwort = _zugang(geraet, konfig)
    begriff = " ".join(str(begriff or "").split())[:80]
    if len(begriff) < 2:
        raise Abgelehnt("Suchbegriff mit mindestens 2 Zeichen")
    if freigabe is not None and freigabe not in geraet.opt("freigaben", ()):
        raise Abgelehnt("Diese Freigabe ist nicht freigegeben")
    try:
        treffer = await asyncio.wait_for(asyncio.to_thread(_suchen_sync, geraet, benutzer, passwort, begriff, freigabe),
                                         SUCH_ZEIT + 10)
    except TimeoutError as e:
        raise GeraetFehler("Suche auf dem NAS dauert zu lange") from e
    except (OSError, ValueError) as e:
        raise GeraetFehler(f"NAS nicht erreichbar ({type(e).__name__})") from e
    return {"treffer": treffer, "anzahl": len(treffer), "gekuerzt": len(treffer) >= MAX_TREFFER}


def _lesen_sync(geraet: Geraet, benutzer: str, passwort: str, freigabe: str, teile: list[str]) -> bytes:
    smb = _sitzung(geraet, benutzer, passwort)
    pfad = _unc(geraet, freigabe, teile)
    st = smb.stat(pfad, username=benutzer, password=passwort)
    if st.st_size > MAX_DATEI:
        raise Abgelehnt("Datei ist größer als 25 MB")
    with smb.open_file(pfad, mode="rb", username=benutzer, password=passwort) as f:
        daten = f.read(MAX_DATEI + 1)
    if len(daten) > MAX_DATEI:
        raise Abgelehnt("Datei ist größer als 25 MB")
    return daten


async def abrufen(geraet: Geraet, konfig: Konfig, pfad: str) -> tuple[str, bytes]:
    benutzer, passwort = _zugang(geraet, konfig)
    teile = pfad_teile(pfad)
    freigabe, rest = teile[0], teile[1:]
    if freigabe not in geraet.opt("freigaben", ()) or not rest:
        raise Abgelehnt("Pfad liegt nicht in einer freigegebenen Freigabe")
    try:
        daten = await asyncio.wait_for(asyncio.to_thread(_lesen_sync, geraet, benutzer, passwort, freigabe, rest), 60)
    except Abgelehnt:
        raise
    except TimeoutError as e:
        raise GeraetFehler("Abruf vom NAS dauert zu lange") from e
    except (OSError, ValueError) as e:
        raise GeraetFehler(f"Datei nicht abrufbar ({type(e).__name__})") from e
    return dateiname(rest[-1], 80), daten


def _ablegen_sync(geraet: Geraet, benutzer: str, passwort: str, freigabe: str, ordner: str, person: str,
                  dateiname_: str, daten: bytes) -> str:
    smb = _sitzung(geraet, benutzer, passwort)
    basis = _unc(geraet, freigabe, [ordner, person])
    try:
        smb.makedirs(basis, exist_ok=True, username=benutzer, password=passwort)
    except OSError:
        pass
    ziel = basis + "\\" + dateiname_
    with smb.open_file(ziel, mode="xb", username=benutzer, password=passwort) as f:
        f.write(daten)
    return "/".join([freigabe, ordner, person, dateiname_])


async def scan_ablegen(geraet: Geraet, konfig: Konfig, person_name: str, user_id: str, dateiname_: str,
                      daten: bytes) -> str | None:
    """Scan in den Ordner der Person auf dem NAS legen (scanziel). None, wenn das NAS kein Scanziel ist. Der Ordner heißt
    <Name>-<erste 8 Zeichen der user_id>: zwei Personen mit gleichem Namen teilen sich nie einen Ordner."""
    ziel = geraet.opt("scanziel")
    if not ziel:
        return None
    benutzer, passwort = _zugang(geraet, konfig)
    schoen = dateiname(person_name, 40).rstrip(" .")
    person = f"{schoen}-{str(user_id)[:8]}"
    try:
        return await asyncio.wait_for(asyncio.to_thread(
            _ablegen_sync, geraet, benutzer, passwort, ziel["freigabe"], ziel["ordner"], person, dateiname_, daten), 60)
    except (OSError, ValueError, TimeoutError) as e:
        raise GeraetFehler(f"Scan nicht auf dem NAS abgelegt ({type(e).__name__})") from e
