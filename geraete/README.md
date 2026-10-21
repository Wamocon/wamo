# WAMO-Gerätedienst (`wamo-geraete`)

Gemeinsame Bürogeräte für alle Profile über den Kern auf Sokrates-2: Server-Status (Sokrates-1, Sokrates-2, Caesar),
Drucker und Scanner (Brother MFC-L3750CDW), Router (FRITZ!Box 7590), NAS (WMC_NAS1, WMC-Testelite), Fernseher
(LG OLED83B49LA), Lautsprecher (JBL PartyBox Ultimate), Steckdosen (drei Shelly Plug S Gen3), iMac (nur Anwesenheit).
Persönliche Laptops gehören nicht hierher (die laufen über den Tunnel je Gerät).

```text
Kern (wamo-core) ──/run/wamo-geraete/geraete.sock──> wamo-geraete (eigener Benutzer, SO_PEERCRED = wamo-core)
                                                       ├─ IPAddressDeny=any + IPAddressAllow aus geraete.toml
                                                       ├─ nft inet wamo_geraete_egress (nur Adresse:Port der Geräte)
                                                       └─ Ablage je Person /var/lib/wamo-geraete/ablage/<user_id>/
```

## Protokoll

Je Verbindung eine JSON-Zeile hin, eine JSON-Zeile zurück, danach genau `anhang` Bytes (nur Dateien):

```json
{"v": 1, "werkzeug": "status", "geraet": "drucker", "argumente": {},
 "kontext": {"user_id": "<uuid>", "rolle": "mitglied", "session_id": null, "name": "Anna"}}
{"status": "success", "zustand": "bereit", "toner": [...]}
```

`status`: `success`, `error`, `rejected`, `nicht_eingerichtet`, `grenze`. Der Kontext ist eine Behauptung des Kerns;
der Dienst bedient nur die uid von `wamo-core`.

| Werkzeug | Risiko | Rollen | Geräte |
| --- | --- | --- | --- |
| `ping`, `geraete` | read | alle | |
| `status` (auch `geraet = "alle"`) | read | alle | alle |
| `steuern` (`aktion`: an, aus, lautstaerke, lauter, leiser, stumm, laut, eingang) | change | alle | Fernseher, Lautsprecher, Steckdosen |
| `drucken` (`dokument`, `kopien` 1 bis 5, `farbe`) | critical | alle, Freigabe im Kern | Drucker |
| `scannen` (`quelle` glas oder einzug, `farbe`, `aufloesung` 150 oder 300) | change | alle | Scanner |
| `ablage_liste`, `ablage_holen`, `ablage_loeschen` | read, change | alle, nur eigene | |
| `nas_suchen`, `nas_abrufen` | read | alle | NAS |
| `router_geraete` | read | nur admin | Router (TR-064) |

Grenzen: Status 30 je Minute und Profil, Steuern 20 je Minute, Drucken 10 Aufträge je Stunde und 50 Seiten je Tag und
Profil (gesamt 200), Scannen 20 je Stunde und ein Auftrag zugleich, NAS 30 je Minute. Statusantworten 20 s im
Zwischenspeicher. Gemessen am 21.10.2026 (ohne Zwischenspeicher, p50): Sokrates-2 24 ms, Sokrates-1 0,66 s (SSH),
Drucker 0,14 s, Scanner 28 ms, Router 0,15 s, Lautsprecher 36 ms, Steckdose 34 ms.

## Einrichten, aktualisieren, Rückweg

Siehe Kopfkommentar von `infra/sokrates2/wamo-geraete-einrichten.sh` (Dateien nach `~/wamo-infra`, dann mit sudo).
Konfiguration `/etc/wamo-geraete/geraete.toml` (Vorlage `infra/sokrates2/geraete.toml.example`), Zugangsdaten
`/etc/wamo-geraete/geraete.env` nur über stdin:

```bash
# Zeilen WAMO_GERAETE_<ZUGANG>_BENUTZER=... und _PASSWORT=... in einer Datei, die nie ins Repo kommt
ssh sokrates2 "sudo wamo-geraete-env setzen && sudo systemctl restart wamo-geraete" < zugang.txt
ssh sokrates2 "sudo wamo-geraete-env liste"                       # nur Namen
ssh sokrates2 "sudo bash ~/wamo-infra/wamo-geraete-einrichten.sh --fritzbox-zertifikat"   # tls_sha256 für TR-064
```

Status der Sparks: `infra/sparks/wamo-status-einrichten.sh` je Spark (Benutzer `wamo-status`, nur lesen).
Kopplung eines Fernsehers (die Person am Fernseher bestätigt):

```bash
ssh sokrates2 "sudo -u wamo-geraete env WAMO_GERAETE_KONFIG=/etc/wamo-geraete/geraete.toml \
  WAMO_GERAETE_KOPPLUNG=/var/lib/wamo-geraete/kopplung sh -c 'cd /opt/wamo-geraete/app && \
  /opt/wamo-geraete/venv/bin/python -s -m wamo_geraete.koppeln fernseher'"
```

Rückweg: `sudo bash ~/wamo-infra/wamo-geraete-rueckweg.sh [--entfernen | --alles]`, auf den Sparks
`wamo-status-rueckweg.sh [--entfernen]`.

## Tests

```powershell
cd geraete; uv run pytest -q
cd ..\voice-core; uv run pytest -q test_geraete_client.py test_geraete_werkzeuge.py
```
