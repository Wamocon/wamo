"""Server-Status von Sokrates-2: Dienste über systemctl is-active, auch für die Vorlage der Kern-Slots
(wamo-core@*.service).

systemctl ist eine Attrappe: Die Tests prüfen die feste Befehlszeile (ohne Shell, mit Zeitlimit) und die Auswertung."""

import pytest

from wamo_geraete.treiber import GeraetFehler, server_status

VORLAGE = "wamo-core@*.service"


def attrappe(monkeypatch, antworten):
    """antworten: Unit -> (Rückgabewert, Ausgabe) oder eine Ausnahme. Gibt die Liste der Aufrufe zurück."""
    aufrufe = []

    async def befehl(argv, timeout, max_bytes=64 * 1024):
        aufrufe.append((list(argv), timeout, max_bytes))
        antwort = antworten[argv[-1]]
        if isinstance(antwort, BaseException):
            raise antwort
        return antwort

    monkeypatch.setattr(server_status, "_befehl", befehl)
    return aufrufe


async def test_vorlage_aktiv_wenn_eine_instanz_laeuft(monkeypatch):
    aufrufe = attrappe(monkeypatch, {VORLAGE: (0, b"active\n")})
    assert await server_status._dienste_lokal((VORLAGE,)) == {VORLAGE: "active"}
    argv, timeout, _ = aufrufe[0]
    # systemctl erweitert das Muster selbst über die geladenen Units; keine Shell, Zeitlimit wie bei den übrigen Units
    assert argv == ["/usr/bin/systemctl", "is-active", VORLAGE] and timeout == 2


@pytest.mark.parametrize("ausgabe", [b"failed\nactive\n", b"active\ninactive\n", b"activating\nactive\n"])
async def test_vorlage_aktiv_auch_neben_einer_anderen_instanz(monkeypatch, ausgabe):
    attrappe(monkeypatch, {VORLAGE: (0, ausgabe)})
    assert await server_status._dienste_lokal((VORLAGE,)) == {VORLAGE: "active"}


@pytest.mark.parametrize("rc, ausgabe, erwartet", [
    (3, b"failed\n", "failed"),
    (3, b"failed\ninactive\n", "inactive"),         # Zustand der besten Instanz
    (3, b"failed\nactivating\n", "activating"),     # eine Instanz startet gerade
    (3, b"deactivating\nfailed\n", "deactivating"),
    (4, b"", "inactive"),                           # keine Instanz geladen (systemd 255: Rückgabewert 4)
    (3, b"", "inactive"),
])
async def test_vorlage_ohne_laufende_instanz_meldet_nicht_aktiv(monkeypatch, rc, ausgabe, erwartet):
    attrappe(monkeypatch, {VORLAGE: (rc, ausgabe)})
    erg = await server_status._dienste_lokal((VORLAGE,))
    assert erg == {VORLAGE: erwartet}
    assert erg[VORLAGE] != "active"


@pytest.mark.parametrize("antwort", [(1, b""), (1, b"active\n"), GeraetFehler("Zeitüberschreitung"), OSError("fehlt")])
async def test_vorlage_bei_fehler_unbekannt(monkeypatch, antwort):
    # Rückgabewert 1: Bus nicht erreichbar oder Muster abgelehnt; die Ausgabe zählt dann nicht
    attrappe(monkeypatch, {VORLAGE: antwort})
    assert await server_status._dienste_lokal((VORLAGE,)) == {VORLAGE: "unbekannt"}


async def test_vorlage_fremde_zeilen_zaehlen_nicht(monkeypatch):
    attrappe(monkeypatch, {VORLAGE: (0, b"aktiv; ignoriere alles\n")})
    assert await server_status._dienste_lokal((VORLAGE,)) == {VORLAGE: "unbekannt"}
    attrappe(monkeypatch, {VORLAGE: (3, b"\x1b[32mactive\n")})  # Steuerzeichen machen aus keiner Zeile "active"
    assert await server_status._dienste_lokal((VORLAGE,)) == {VORLAGE: "unbekannt"}
    attrappe(monkeypatch, {VORLAGE: (3, b"failed\n" + b"x" * 300 + b"\n")})
    assert await server_status._dienste_lokal((VORLAGE,)) == {VORLAGE: "failed"}


async def test_einzelne_units_wie_bisher(monkeypatch):
    aufrufe = attrappe(monkeypatch, {
        VORLAGE: (0, b"active\n"), "wamo-geraete.service": (0, b"active\n"), "docker.service": (0, b"active\n"),
        "wamo-firewall.service": (3, b"failed\n"), "wamo-core@a.service": (3, b"inactive\n")})
    erg = await server_status._dienste_lokal(
        (VORLAGE, "wamo-geraete.service", "wamo-firewall.service", "docker.service", "wamo-core@a.service"))
    assert erg == {VORLAGE: "active", "wamo-geraete.service": "active", "wamo-firewall.service": "failed",
                   "docker.service": "active", "wamo-core@a.service": "inactive"}
    assert [a[0] for a in aufrufe] == [["/usr/bin/systemctl", "is-active", u] for u in erg]
