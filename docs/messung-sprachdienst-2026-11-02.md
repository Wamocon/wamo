# Messung des Sprachdienstes im Haus (Open WebUI, Spark, Soniox)

Stand: 02.11.2026. Gemessen im Rahmen der HAI der Lernplattform (Repo `Wamocon/Wamocon_FIAE`), weil die HAI Sokrates als Rückfallebene für Sprache nutzen will. Die Messung betrifft WAMO, denn der Sprachdienst "haus" ist derselbe Weg wie in `voice-core/sprache_anbieter.py` (Anbieter `haus` über OpenAI-kompatible Endpunkte). Es wurde nichts verändert, alles ist lesend oder ein einzelner Testaufruf.

## 1. Ergebnis in Kürze

| Teil | Ergebnis |
|---|---|
| Soniox, Schlüssel eines **EU-Projekts**, EU-Adressen | bestanden: Kurzzeit-Schlüssel für Erkennung und Sprachausgabe (201), Satz gesprochen (1,6 s Sprache nach 1,1 s) und wieder erkannt ("Was ist ein Primärschlüssel?", 0,9 s) |
| Soniox, alter Schlüssel eines Projekts der Region US gegen die EU-Adresse | 401 (Region ist je Projekt festgelegt, der Schlüssel erbt sie) |
| Open WebUI auf Sokrates1, Sprachausgabe `POST /api/v1/audio/speech` | 200, MP3, 1,5 bis 2,5 s je Satz |
| Open WebUI auf Sokrates1, Spracherkennung `POST /api/v1/audio/transcriptions` | zuerst **500**, nach der Behebung (Abschnitt 6) erneut gemessen am 02.11.2026 abends: **200**, etwa 2 s, Text "Was ist ein Prima-Erschluss?" für "Was ist ein Primärschlüssel?" (zweimal), die Sprachausgabe antwortet in 0,2 s |
| Sprachdienst `192.168.178.64:8790` (dahinter leitet Open WebUI weiter), `GET /health` | `{"ok":true,"qwen_tts":{"http://10.100.88.2:8102/v1":{...}}}`, also gesund, Sprachausgabe über Qwen |
| Sprachdienst `192.168.178.64:8790`, `POST /v1/audio/transcriptions` direkt | **Internal Server Error**, aber ohne gültige Testdatei gemessen (die Datei war nicht angelegt), daher kein eigener Beleg. Belegt ist der Weg über Open WebUI mit einer echten MP3: 500 mit der Adresse dieses Dienstes in der Meldung. |

Das Problem lag also nicht in Open WebUI, sondern in der **Spracherkennung des Dienstes auf `192.168.178.64:8790`**. Er ist ein anderer Rechner als Sokrates1 (`spark-f752`, `192.168.178.75`). Stand abends: behoben (Abschnitt 6). Die Qualität der Erkennung ist offen: Der Fachbegriff "Primärschlüssel" kam zweimal als "Prima-Erschluss" zurück, die Erkennung für Fachwörter ist also schwächer als bei Soniox.

## 2. Aufbau, wie er sich aus der Messung ergibt

```
Browser  ->  Vercel (Next.js)  ->  Open WebUI auf Sokrates1 (192.168.178.75)
                                     |  /api/v1/audio/speech          (200, MP3)
                                     |  /api/v1/audio/transcriptions  (500)
                                     v
                                  Sprachdienst auf 192.168.178.64:8790
                                     |  /v1/audio/speech, /v1/audio/transcriptions,
                                     |  /v1/audio/voices, /v1/voices, /health
                                     v
                                  Qwen-Sprachausgabe auf 10.100.88.2:8102
```

Die Pfade des Dienstes stehen in seiner `openapi.json`: `/health`, `/v1/audio/speech`, `/v1/audio/transcriptions`, `/v1/audio/voices`, `/v1/voices`. Die Textmodelle der HAI (Chat und Einbettung) laufen direkt über Open WebUI und berühren diesen Dienst nicht.

Nebenbefund: Die Modellliste von Open WebUI (`GET /api/v1/models`) enthält 22 Modelle und kein Audiomodell. Open WebUI listet seine Sprach-Engines nicht auf, ein fehlender Eintrag sagt also nichts.

## 3. So lässt sich der Fehler nachstellen

Vom Rechner mit Zugang zum Heimnetz (zum Beispiel per SSH auf Sokrates1), ohne Schlüssel:

```
# gesund?
curl -s http://192.168.178.64:8790/health

# Sprachausgabe erzeugen (über Open WebUI geprüft, der direkte Aufruf wurde nicht gesondert ausgewertet)
curl -s -X POST http://192.168.178.64:8790/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"input":"Was ist ein Primaerschluessel?","model":"tts-1","voice":"alloy","response_format":"wav"}' \
  -o /tmp/probe.wav

# Erkennung (über Open WebUI mit einer echten MP3: 500; direkt bitte mit einer gültigen Datei prüfen)
curl -s -X POST http://192.168.178.64:8790/v1/audio/transcriptions \
  -F file=@/tmp/probe.wav -F model=whisper-1 -F language=de
```

Über Open WebUI gleich, mit einem Schlüssel von Open WebUI: `POST https://sokrates.test-qualitaetsmanagement.com/api/v1/audio/transcriptions` mit `file` und `language=de` antwortet mit `Error transcribing chunk: External: 500 ... url='http://192.168.178.64:8790/v1/audio/transcriptions'`. Der Aufruf der Sprachausgabe in Open WebUI: `POST /api/v1/audio/speech` mit `{"model":"tts-1","input":"...","voice":"alloy"}`.

Hinweis: Ein Aufruf mit Umlauten über `curl -d` unter Windows ergibt `Invalid JSON payload`, das liegt am Zeichensatz des Aufrufs und nicht am Dienst. Aus einer Anwendung (UTF-8) tritt es nicht auf.

## 4. Was zu klären ist

1. Warum antwortet die Erkennung auf `192.168.178.64:8790` mit 500? Logs des Dienstes ansehen (die Erkennungsmaschine hinter `/v1/audio/transcriptions`, im Health ist nur die Sprachausgabe `qwen_tts` aufgeführt, eine Erkennung steht dort nicht).
2. Ist auf diesem Dienst überhaupt ein Erkennungsmodell eingerichtet? Wenn nicht, gibt es für den Pfad `haus` in WAMO und für die HAI nur Sprachausgabe im Haus, und die Erkennung bleibt bei Soniox oder dem Browser.
3. Welche Stimme sagt `voice: "alloy"` in Open WebUI wirklich, und spricht sie Deutsch gut? Das ist eine Hörprobe und nicht gemessen.

## 5. Folgen für die beiden Projekte

- **HAI (Lernplattform):** Chat und Einbettung sind nicht betroffen. Der Sprachassistent nutzt Soniox (EU) als Hauptanbieter. Fällt er aus, übernimmt Sokrates die Sprachausgabe, die Erkennung fällt auf den Browser zurück. Der Sprachassistent ist in Production je Organisation aus.
- **WAMO:** `sprache_anbieter.py` beschreibt, dass das Gateway "noch kein Audiomodell anbietet" (Stand 21.10.2026). Diese Messung zeigt: Die **Sprachausgabe** ist im Haus vorhanden (Open WebUI und Dienst auf `192.168.178.64:8790`), die **Erkennung** liefert einen Serverfehler. Ein Wechsel von `stt` auf `haus` wäre erst nach der Behebung sinnvoll.

## 6. Ursache und Behebung (02.11.2026, nachmittags)

**Ursache.** Die Erkennung läuft hinter dem Sprachdienst im Container `whisper-gpu` auf Caesar (Port 8791, Router `speech-router` auf 8790 leitet dorthin weiter). Dieser Container hatte den Zugriff auf die GPU verloren: Die Gerätedateien `/dev/nvidia*` waren sichtbar, aber `nvidia-smi` im Container meldete `Failed to initialize NVML: Unknown Error` und ein neuer Prozess `No CUDA GPUs are available`. Jede Erkennung endete deshalb mit `RuntimeError: CUDA error: CUBLAS_STATUS_INTERNAL_ERROR` und damit mit HTTP 500. Der Router, die Sprachausgabe (Port 8102) und der Health-Aufruf waren gesund, deshalb zeigte `GET /health` nichts an. Der letzte erfolgreiche Lauf war am 06.10., der erste Fehler dieser Art am 19.10.

**Behebung.** `docker restart whisper-gpu` auf Caesar. Der Start legt den Gerätezugriff neu an. Danach sieht der Container die GPU (`NVIDIA GB10`), `GET :8791/health` antwortet 200, und `POST :8790/v1/audio/transcriptions` mit einer Testdatei ("Was ist ein Primaerschluessel?") liefert HTTP 200 und den Text "Was ist ein Prima-Schlüssel?" (erster Lauf 8,6 s mit Aufwärmen, danach 3,6 bis 3,9 s). Sonst wurde nichts verändert.

**Zu klären, damit es nicht wiederkommt.**
1. Was den Zugriff entzieht, ist nicht belegt. Typisch bei Docker mit dem systemd-Cgroup-Treiber (hier `systemd`, Cgroup v2) ist ein Neuladen von systemd (`daemon-reload`, Paketupdate), das die Geräteregeln laufender Container zurücksetzt. Eine Gegenprobe wäre, nach jedem solchen Ereignis `docker exec whisper-gpu nvidia-smi -L` zu prüfen.
2. Der Health-Aufruf des Routers sollte die Erkennung mitprüfen (kurze Probe gegen 8791), sonst bleibt dieser Ausfall unsichtbar.
3. Der Container hat keinen Healthcheck. Ein Healthcheck mit `nvidia-smi -L` plus automatischem Neustart würde den Ausfall selbst beheben.

**Folge für WAMO.** WAMO nutzt für die Erkennung Soniox und war nie betroffen. Der Weg `haus` für die Erkennung ist damit wieder benutzbar, bleibt aber ausgeschaltet, bis er gemessen und per Hörprobe bewertet ist.
