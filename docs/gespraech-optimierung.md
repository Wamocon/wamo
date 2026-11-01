# Gespräch natürlicher machen: Recherche und Maßnahmen

Stand: 31.10.2026. Ziel: Das Livegespräch mit WAMO soll sich mehr wie eine zwischenmenschliche Konversation anfühlen.
Frühere Klagen: Lücken und Stille, Unterbrechungen hackelig und an der falschen Stelle, WAMO verhaspelt sich, vergisst
Befehle, hängt sich auf. Der Abschnitt "Stimme messen" in der README bleibt die Messgrundlage.

Zur Belegqualität: Primärquellen (Dokumentationen der Hersteller, arXiv) sind als solche markiert. Vieles zu Latenz und
Turn-Taking stammt aus Herstellerblogs mit eigenem Interesse. Wo nur eine Quelle oder keine unabhängige Messung vorliegt,
steht es dabei. "Nicht bestätigt" heißt: gesucht, aber nicht gefunden.

## 1. Zusammenfassung

1. Das Ziel für die Zeit bis zum ersten Ton liegt in der Praxis bei etwa 800 ms, der Mensch liegt bei etwa 200 ms
   (Stivers 2009, Soniox-Wiki 2.7.2026). WAMO liegt im Sitzungsprotokoll vom 31.10.2026 bei 1,4 bis 1,9 s (11 von 15 Zügen),
   Ausreißer bis 6,1 s. Das ist die größte Lücke zum Ziel.
2. Die Wartezeit setzt sich in WAMO so zusammen: Sprachaktivität-Ende 150 ms, Transkript fertig etwa 270 ms, erstes
   Modellwort etwa 950 ms, erster Satz vollständig 20 bis 500 ms später, erster Ton etwa 330 ms nach dem Satz. Das Modell
   und die Sprachausgabe machen zusammen etwa zwei Drittel aus.
3. Die längsten Stillen (3,6 bis 6,1 s) kommen im Protokoll nicht vom Modell. Ursachen: Warten auf ein unfertiges Satzende
   der Person (Zug 9, 11), verspätetes Transkript (Zug 14, 2,0 s), mehrstufiger Werkzeugablauf (Zug 10).
4. Semantische Turn-Erkennung hat WAMO bereits (Smart Turn v3 plus Satzende-Marker des Modells). Der Vorsprung anderer
   Stacks liegt nicht dort, sondern bei spekulativer Antwort vor dem Turn-Ende (LiveKit `preemptive_generation`, Deepgram
   Flux `EagerEndOfTurn`). Pipecat 1.12 kann das, aber nur mit einem Transkriptionsdienst, der Eager-Signale liefert
   (im Paket: Deepgram Flux, Cartesia). Soniox liefert sie nicht.
5. Rückmeldelaute der Person und Unterbrechungen sind bei WAMO gut gelöst und gemessen (Verstummen 1,3 s Median, 0 von 15
   falschen Unterbrechungen). Die neue Forschung (LiveKit 19.3.2026: 51 Prozent weniger falsche Unterbrechungen durch ein
   Audiomodell; Full-Duplex-Bench v1.5) bestätigt das Prinzip, bringt aber nichts, was WAMO ohne Fremdmodell übernehmen kann.
6. Rückmeldelaute des Assistenten ("mhm") zu erzeugen ist ein Forschungsthema für Full-Duplex-Modelle (arXiv 2609.29418,
   24.9.2026) und in einer kaskadierten Pipeline mit Echo und Soniox-Rückhalt riskant. Empfehlung: nicht bauen.
7. Füllwörter ("ähm") wirken laut Studien gemischt, Zwischenrufe ("ach so", "oh") eher positiv (Cohn 2019). Die Persona
   verlangt heute Denkausdrücke in jeder zweiten bis dritten Antwort. Das sollte die Hörprobe entscheiden, nicht der Code.
8. Full-Duplex-Modelle (Moshi, GPT-realtime, Gemini Live) sind für den Kern nicht übertragbar: Werkzeuge, Freigaben,
   Mehrprofil und Datenhaltung hängen an der kaskadierten Pipeline. Moshi blieb bei Rückmeldelauten fast nie beim Sprechen
   (Resume 0,06, Full-Duplex-Bench v1.5).
9. Zwei der Maßnahmen sind reine Messarbeit am Bestand: die vier noch nicht gemessenen Schalter aus Paket A und B und ein
   Test des EU-Endpunkts von Soniox-TTS. Beides kostet keinen Code.
10. Umgesetzt wird jetzt: Persona-Block Gesprächsführung, Wartesatz bei langsamem Modell, eine neue Kennzahl im Messweg
    (Folgeantwort nach Einwurf). Alle Schalter stehen auf aus, bis gemessen.

## 2. Befunde je Thema

### (a) Turn-Taking und Endpointing

Quellen:
- Pipecat Smart Turn, Dokumentation und Repository (Primärquelle, aktuell v3.2, 23 Sprachen inklusive Deutsch, Whisper-Tiny
  Encoder, etwa 8 Mio. Parameter, 10 bis 65 ms Rechenzeit, bis 8 s Audio): https://docs.pipecat.ai/api-reference/server/utilities/turn-detection/smart-turn-overview und https://github.com/pipecat-ai/smart-turn . Das Repository nennt kein Veröffentlichungsdatum.
  Das Modell läuft nur in Sprechpausen und sagt vollständig oder unvollständig; bleibt es länger still als `stop_secs`,
  gilt der Turn als vollständig.
- Pipecat, Soniox-Dienst (Primärquelle): Im Standardmodus finalisiert die lokale Sprachaktivität, das Transkript ist dann etwa 250 ms nach dem Ende
  da. Die Soniox-eigene Endpunkterkennung (`max_endpoint_delay_ms` 500 bis 3000, `endpoint_sensitivity` -1 bis 1,
  `endpoint_latency_adjustment_level` 0 bis 3) wirkt nur mit `vad_force_turn_endpoint=False`:
  https://docs.pipecat.ai/api-reference/server/services/stt/soniox und https://soniox.com/speech-to-text/features/turn-detection
- Deepgram Flux (Primärquelle): `eot_threshold` 0,5 bis 0,9 (Standard 0,7), `eager_eot_threshold` 0,3 bis 0,9 löst
  `EagerEndOfTurn` aus, die Antwort wird bei `TurnResumed` verworfen; der Preis sind mehr Modellaufrufe:
  https://developers.deepgram.com/docs/flux/configuration
- LiveKit Turn-Detector, Mehrsprachenmodell auf Transkriptbasis, Standard bei `AgentSession`:
  https://docs.livekit.io/agents/logic/turns.md . Dynamisches Endpointing (Python) passt die Frist an Pausenstatistik an.
- Tianpan 27.4.2026 (Blog, keine Messreihe): fester Stille-Schwellenwert 700 ms sei "die richtige Basis und der falsche
  Standard"; zwei Zahlen verfolgen, Fehlschnitte und Totzeit, weil Verbessern der einen die andere verschlechtert:
  https://tianpan.co/blog/2026-04-27-voice-agent-turn-taking-latency-budget

Befund: Semantische Erkennung ist Stand der Technik und bei WAMO vorhanden. Fehlalarme (Abschneiden) und Totzeit sind
zwei Seiten derselben Einstellung. WAMO misst bisher die Totzeit (erster Ton) und das Dazwischenreden bei Pausen
(Szenario `pause`), aber keinen Fehlschnitt-Anteil über viele Satzpausen.

WAMO-Stand (Code): `bot.py` Zeilen 195 bis 203 und 656 bis 692. Silero-Sprachaktivität mit `stop_secs` 0,15 s
(Pipecat empfiehlt 0,2 s und warnt einmal je Sitzung), Smart Turn v3 mit `SMART_TURN_STOP_SECS` 0,5 s, danach der
Satzende-Marker des Modells (● vollständig, ◐ kurz warten, ○ lang warten) mit Fristen 1,5 s und 3,0 s. Die Fristen
sind fest, nicht vom Satzende abhängig.

### (b) Rückmeldelaute beider Seiten

Quellen:
- arXiv 2609.29418, Züfle u. a., 24.9.2026 (Primärquelle): ein kleiner Kopf auf einem Full-Duplex-Modell sagt voraus,
  wann ein Rückmeldelaut beginnen soll, Bewertung durch Menschen gleichauf mit echten: https://arxiv.org/abs/2609.29418
- Inoue u. a., NAACL 2025 und IWSDS 2026 (arXiv 2410.15929, 2512.14085): Vorhersage von Zeitpunkt und Art, die Lage im
  Satz hängt von der Sprache ab (Japanisch während, Chinesisch nach der Äußerung). Für Deutsch nicht bestätigt.
- Full-Duplex-Bench v1.5, 28.1.2026: Wie verhalten sich Systeme bei Rückmeldelauten der Person? Nova Sonic und Gemini
  bleiben beim Sprechen (Resume 0,98 und 0,93), GPT-4o bricht zu oft ab (0,70), Moshi fast immer (0,06):
  https://arxiv.org/html/2507.23159v3

Befund: Wann ein Assistent "mhm" sagt, ist nur mit Modellen gelöst, die den eingehenden Strom ständig verfolgen. Eine
Studie, die belegt, welche Wörter Nutzer an einem Assistenten bevorzugen, wurde nicht gefunden.

WAMO-Stand: Rückmeldelaute der Person werden erkannt (`soft_interrupt.py`, `BACKCHANNEL_WORDS`, `BACKCHANNEL_PHRASES`,
Wortanfänge im Zwischenstand) und starten weder einen Zug noch brechen sie Werkzeuge ab (`WAMO_RUECKMELDUNG_WERKZEUG`
seit 31.10. an). Gemessen 0 von 15 falschen Unterbrechungen. Der Assistent sagt von sich aus kein "mhm" und soll es nicht
(die Persona verbietet Floskeln; der Ursprungsbericht verwarf es).

### (c) Unterbrechungen

Quellen:
- LiveKit 19.3.2026 (Herstellerblog, Zahlen ohne unabhängige Prüfung): ein Audiomodell prüft die ersten Hundert
  Millisekunden (Einsatzstärke, Dauer, Tonhöhe), Präzision 86 Prozent, Erinnerung 100 Prozent, weist 51 Prozent der
  VAD-Abbrüche ab, im Median 216 ms Audio bis zur Entscheidung, 30 ms Rechenzeit:
  https://livekit.com/blog/adaptive-interruption-handling . Dazu `resume_false_interruption` (Standard an) mit
  `false_interruption_timeout` 2 s: der Agent spricht nach einer falschen Unterbrechung an derselben Stelle weiter.
- Pipecat Interruption Strategies (Primärquelle): Bedingungen für Abbruch, was sie nicht erfüllt, wird verworfen. Ein Halten
  und Fortsetzen des Tons gibt es nicht: https://docs.pipecat.ai/server/utilities/interruption-strategies
- Tianpan: im Verlauf nur das behalten, was die Person gehört hat; Werkzeuge nur abbrechen, wenn sie rein lesend sind.
- Full-Duplex-Bench v1.5: Kennzahlen Stoppzeit und Antwortzeit nach dem Überlappen, Anteil Antworten gegen Weitersprechen.

Befund: Stand der Technik ist (1) schnell dämpfen, (2) Absicht klären, (3) bei falschem Alarm fortsetzen, (4) den Verlauf
auf das Gehörte kürzen. Offen ist bei WAMO nur Punkt 3 für den Fall, dass der Abbruch schon erfolgt ist.

WAMO-Stand (Code): `soft_interrupt.py` hat (1) Dämpfen nach 0,25 s (`WAMO_DUCKEN`), (2) Stoppwörter, Rückmeldelaute, Echo-
Filter und Echo-Modus, (4) `kuerze_auf_gehoertes`, Stopp an der hörbaren Wortgrenze (`WAMO_STOPP_GRENZE`), Werkzeug-
Regel. Punkt 3 gilt nur vor dem Stopp: ein gedämpfter Ton wird zurückgenommen. Nach dem Stopp hilft der Hinweis
`INTERRUPTED_NOTE` (`bot.py` Zeile 222): bei einem bloßen Laut macht WAMO ohne Entschuldigung weiter. Gemessen:
Verstummen 1340 / 1910 ms, erste hörbare Reaktion 761 ms, Echo-Unterbrechungen 3 bis 11 je vier Läufe.

### (d) Latenz-Budget, Streaming, Spekulation

Quellen:
- Stivers u. a., PNAS 2009 (Primärquelle): bei zehn Sprachen liegt der Wechsel im Mittel bei etwa 200 ms (Zahl aus
  Sekundärquellen, Deutsch nicht bestätigt): https://doi.org/10.1073/pnas.0903616106
- Soniox-Wiki "Voice agent latency budget", aktualisiert 2.7.2026 (Herstellerseite): Ziel etwa 800 ms wahrgenommen,
  Richtwerte Endpunkt 200 bis 400, Finalisierung 50 bis 150, Modell 300 bis 500, erster Ton 100 bis 200, Netz 100 bis
  200 ms; Modell vorwärmen mit Teiltranskripten; Dienste in einer Region halten: https://soniox.com/wiki/voice-agent-latency-budget
- LiveKit Preemptive Generation: das Modell startet vor der Bestätigung des Turn-Endes, Standard an, TTS wartet, außer
  `preemptive_tts`; Grenze `max_speech_duration` 10 s und `max_retries` 3; Preis sind verworfene Modellaufrufe
  (https://docs.livekit.io/agents/build/speech/).
- Pipecat 1.12 im Paket (Quelltext `pipecat/turns/user_stop/eager_user_turn_stop_strategy.py`, `speculation_gate.py`):
  eine Antwort wird spekulativ erzeugt und von einer Sperre gehalten, bis das endgültige Transkript passt.
- Cascaded gegen Sprache-zu-Sprache 2026 (Blog): 600 bis 1200 ms gegen 300 bis 500 ms bis zum ersten Ton, fast alle
  Produktivsysteme sind weiter kaskadiert (https://futureagi.com/blog/cascaded-voice-ai-vs-speech-to-speech-2026/, Herstellerblog).

Befund: Alles über etwa 1,5 s wirkt wie ein Aussetzer (Tianpan, Blog). Spekulation ist der einzige Hebel, der die
Wartezeit vom Turn-Ende entkoppelt, verlangt aber Eager-Signale vom Transkriptionsdienst.

WAMO-Stand (Sitzungsprotokoll 31.10.2026, `D:\tmp\sitzung-log\kern-sitzung-2026-10-31.log`, 15 Züge):

| Segment (ms ab Sprechende) | typisch | Bemerkung |
| --- | --- | --- |
| Sprachaktivität-Ende | 150 | `WAMO_VAD_STOP_SECS` |
| Transkript fertig | 265 bis 313 | Soniox, Standardmodus |
| erstes Modellwort | 850 bis 1020 | Claude Haiku mit Cache (11 bis 15 Tsd. gelesene Token) |
| erster Satz an die Sprachausgabe | +20 bis +500 | Satzende oder 68 Zeichen (`WAMO_TTS_START_ZEICHEN`) |
| erster Ton | +330 | Soniox-TTS, globaler Endpunkt `tts-rt.soniox.com` |
| erster Ton gesamt | 1400 bis 1900 | 11 von 15 Zügen; Ausreißer 3607, 4370, 4801, 6090 ms |

Die STT nutzt den EU-Endpunkt, die TTS den globalen (`bot.py` Zeilen 174 und 175). Der Hostname `tts-rt.eu.soniox.com` löst
auf (Cloudflare, HTTP 404 auf die Wurzel, geprüft am 31.10.2026); ob er als Sprachausgabe-Endpunkt gilt und schneller
ist, steht in keiner Quelle (die Pipecat-Seite nennt nur den globalen). Das ist eine Messfrage, kein Codeproblem.

Die vier Ausreißer: Zug 9 Warten auf das Turn-Ende (2 Antworten, Frist 1,5 s), Zug 11 dasselbe, Zug 14 Transkript erst nach
2,0 s, Zug 10 vier Werkzeugschritte. Ein langsames Modell (über 2,5 s ohne ein Zeichen) kam in den 15 Zügen nicht vor.

### (e) Prosodie und Sprachausgabe

Quellen:
- Soniox-TTS (Primärquelle): 60 Sprachen, alle Stimmen sprechen alle Sprachen, `speed` 0,7 bis 1,3 und `reduce_silence`
  sind nur in der Pipecat-Beschreibung zu finden (https://docs.pipecat.ai/api-reference/server/services/tts/soniox); die
  Soniox-Seite zu Stimmen nennt weder SSML noch Betonungs- oder Emotionsmarken. Nicht bestätigt: SSML, Atemgeräusche, Emotionsmarken.
- Qwen3-TTS (Modellkarte, Sekundärberichte): deutsch unterstützt, Streaming, 3 s Referenzton für eine Stimme,
  "97 ms" je nach Quelle erste Paketzeit oder Gesamtzeit; Veröffentlichungsdatum nicht bestätigt:
  https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base
- Hassan, Lison, Halvorsen, arXiv 2412.12710 (Fassung Oktober 2025): eingefügte Stockungen steigern die wahrgenommene
  Spontaneität leicht, die Verständlichkeit sinkt leicht: https://arxiv.org/abs/2412.12710
- Cohn, Chen, Yu, SIGDIAL 2019 (n = 5527): Zwischenrufe und Füllwörter verbesserten die Gesamtwertung im Feld, im Hörtest
  half nur der Zwischenruf: https://aclanthology.org/W19-5935/ . Pfeifer und Bickmore 2009 (über Sekundärquelle):
  die Meinungen über Füllwörter sind geteilt, Füllwörter können sicherer klingen lassen, als das System ist.

Befund: Natürlichkeit kommt eher aus kurzen Sätzen, Rückbezug und Tempo als aus eingestreuten "ähm". Prosodie über
Markierungen im Text ist bei Soniox nicht dokumentiert. Das Einzige, was die Schalter steuern, sind Tempo und Pausen.

WAMO-Stand: `soniox_tts.py` `stimme_einstellung` (Tempo, `reduce_silence` aus der Umgebung, Standard Soniox), `fuer_soniox`
(Satzleerzeichen, `WAMO_TTS_SATZLEERZEICHEN`, Standard aus), Stimme `SONIOX_VOICE` Standard Bryce. Das Haus-TTS (Piper,
Qwen3-TTS) liefert ganze Sätze ohne Wortzeiten, dort greift nur die Zeitgrenze (`soft_interrupt.py`). Die Persona
verlangt Denkausdrücke wie "Hm," und "Ähm," in jeder zweiten bis dritten Antwort (`persona.py` Zeilen 45 bis 50).

### (f) Gesprächsführung im Prompt

Quellen: kaum belastbare Messstudien. Die verwertbaren Hinweise sind: Pipecat-Hinweis zu Satzende-Markern (Primärquelle,
`user_turn_completion_mixin.py`), der Ursprungsbericht (Paket A, Rückfragen begrenzen, nach "Ja" nicht erneut fragen, sein
Befund: eine Kette von fünf "Soll ich ...?" ließ sich per Prompt allein nicht beheben), Tianpan (Werkzeuge nach einer
Unterbrechung nur bei Lesezugriff abbrechen). Zur Selbstkorrektur ("nein, ich meine ...") wurde keine Studie gefunden.

Befund: Die Regeln im Prompt sind eher Handwerk als Forschung. Was sich belegen lässt, ist der Rahmen: Rückfragen
begrenzen, nicht wiederholen, nur das letzte Wort bei Selbstkorrektur gelten lassen, vor langen Werkzeugen genau einen
Satz sagen.

WAMO-Stand: `persona.py` `BASE_VOR` und `BASE_NACH` haben kurze Sätze, Kern zuerst, Denkausdrücke, Abrunden mit
Einschätzung oder Rückfrage, Verhalten bei Unterbrechung und Rückmeldelauten, Marker, eine Ankündigung vor Werkzeugen
über einer Sekunde. Es fehlen: Selbstkorrektur der Person, Rückbezug auf früher Gesagtes, "nicht wiederholen, was gerade
gesagt wurde", nur eine Frage auf einmal.

### (g) Full-Duplex-Modelle

Quellen: Moshi (Kyutai, arXiv 2410.00037): 160 ms theoretisch, etwa 200 ms praktisch, aber ein Dialogmodell ohne
Werkzeuge; MoshiRAG 30.4.2026 (https://kyutai.org/blog/2026-04-30-moshi-rag/); OpenAI Realtime `semantic_vad` mit
`eagerness` (https://developers.openai.com/api/docs/guides/realtime-vad); Gemini Live mit Barge-in, affektivem Dialog
und proaktivem Audio (https://docs.cloud.google.com/vertex-ai/generative-ai/docs/live-api); Sesame CSM ist ein
Sprachausgabemodell, kein Zuhörer (Sekundärberichte). Claude-Sprachfunktionen: keine technische Dokumentation gefunden.

Befund: Für kaskadierte Pipelines übertragbar sind drei Ideen: semantische Turn-Erkennung mit einstellbarer Eile
(`eagerness`, in WAMO die Fristen), Verlauf auf das Gehörte kürzen (in WAMO vorhanden) und Dämpfen vor Abbruch (vorhanden).
Nicht übertragbar sind ein einziges Modell für Hören und Sprechen und das Zuhören während des Sprechens im Modell selbst.

### (h) Messmethoden

Quellen: Full-Duplex-Bench v1 (arXiv 2503.04721), v1.5 (2507.23159, Überlappungen, Kennzahlen Stoppzeit, Antwortzeit,
Anteil Antworten gegen Weitersprechen, Sprechrate und Tonhöhe vor und nach dem Überlappen), v2 (2510.07838, mehrstufig,
automatischer Prüfer), MTR-DuplexBench (2511.10262). Die Angabe zu "v3" mit Werkzeugen und Pass@1 stammt aus einer
einzigen Wörterbuchseite und ist nicht bestätigt. Tianpan: Fehlschnitt-Anteil gegen Totzeit.

Befund: Die Bench-Szenarien sind Einwurf, Rückmeldelaut, Sprechen mit Dritten, Hintergrundsprache. Der Messweg hat
Einwurf, Rückmeldelaut, Geräusch, Echo. Es fehlen die Antwortzeit nach einem Einwurf und der Fehlschnitt-Anteil.

WAMO-Stand: `scripts/wamo_stimme.py` und `scripts/wamo-stimme-messen.cjs` (elf Szenarien). Kennzahlen: erster Ton, größte
Lücke, Verstummen, erste Reaktion, hartes Abschneiden, falsche Unterbrechungen, Echo, Werkzeug mit Rückmeldelaut. Neu:
`folge_ton_ms`, Zeit vom Ende des Einwurfs bis zum ersten Ton der Folgeantwort (Antwortzeit nach Überlappen).

## 3. Vergleich mit dem Stand und Maßnahmen

| Nr | Maßnahme | Nutzen | Risiko | Aufwand (h) | Messbarkeit | Empfehlung |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Offene Schalter aus Paket A und B messen: `WAMO_TTS_SATZLEERZEICHEN` (Echo, Pause), `WAMO_PERSONA_RUECKFRAGEN`, `WAMO_PERSONA_PC_ORDNER`, `WAMO_JA_WAECHTER`, dann alle zusammen | hoch (vorgelesenes "Punkt", Rückfrage-Ketten, Wiederholen von Befehlen) | niedrig | 0 Code, 3 Messung | Messweg plus Hörprobe | jetzt (Messung) |
| 2 | Persona-Block Gesprächsführung `WAMO_PERSONA_GESPRAECH` | mittel (Selbstkorrektur, Rückbezug, keine Wiederholung) | niedrig (Prompt, Cache) | 2 | Hörprobe, `erster_ton_ms` bleibt gleich | jetzt |
| 3 | Wartesatz bei langsamem Modell `WAMO_WARTESATZ_MODELL` | niedrig bis mittel (schützt vor stiller Totzeit über 2,5 s) | niedrig (nur ohne Zeichen, einmal je Zug) | 3 | Einheitstests, im Lauf `erster_ton_ms` p95 und `luecke_max_ms` | jetzt |
| 4 | Messweg: Kennzahl `folge_ton_ms` (Antwortzeit nach Einwurf, Full-Duplex-Bench v1.5) | mittel (Lücke nach dem Abbruch ist bisher unsichtbar) | keines | 1,5 | selbst Messung | jetzt |
| 5 | Soniox-TTS über `tts-rt.eu.soniox.com` testen (`SONIOX_TTS_URL`) | mittel (erster Ton bis etwa 330 ms früher, unbelegt) | niedrig (Rückfall auf global per Umgebung) | 0,5 Konfiguration, 1 Messung | `erster_ton_ms[fakten]` | jetzt (Messung) |
| 6 | Pause-Szenario um Fehlschnitt-Anteil erweitern (mehr Wiederholungen, Lücken 0,8 bis 2,5 s) | mittel (Totzeit gegen Fehlschnitt) | keines | 1 | selbst Messung | später |
| 7 | Soniox-Endpunkterkennung statt Pipecat-Finalisierung (`vad_force_turn_endpoint=False`) | unklar | hoch (Pipecat nennt den Standard als schnellsten; `should_interrupt` neu verdrahten) | 6 | `erster_ton_ms` | verwerfen |
| 8 | Spekulative Antwort vor dem Turn-Ende (Eager) | hoch (bis etwa 300 bis 500 ms) | hoch (Dienstwechsel auf Flux oder Cartesia, doppelte Modellaufrufe, Werkzeuge) | 12 bis 20 | `erster_ton_ms` | später, nur wenn der erste Ton nach Maßnahme 5 über 1,2 s bleibt |
| 9 | Halten und Fortsetzen des Tons nach falscher Unterbrechung (AudioHold) | mittel | hoch (Eingriff in Pipecat, Mikrofon-Echo) | 16 | neues Szenario "ein Wort" | später, nur wenn Messung falsche Abbrüche zeigt |
| 10 | Audiobasiertes Unterbrechungsmodell (LiveKit adaptive) | mittel | hoch (Cloud-Modell, nicht übertragbar) | 20 | Messweg | verwerfen |
| 11 | "Mhm" des Assistenten | niedrig | hoch (Echo, Soniox-Rückhalt) | 8 | nicht messbar | verwerfen |
| 12 | Denkausdrücke ("ähm") nach Hörprobe zurücknehmen oder auf Zwischenrufe beschränken | unsicher | niedrig | 1 | Hörprobe | später |
| 13 | Haus-TTS Qwen3-TTS als Sprachausgabe (Hörprobe, Streaming, Wortzeiten) | unsicher | mittel (Wortzeiten fehlen, Zeitgrenze statt Wortgrenze) | 8 | Messweg plus Hörprobe | später |
| 14 | Full-Duplex-Modell (Moshi, GPT-realtime, Gemini Live) im Kern | hoch in der Latenz | sehr hoch (Werkzeuge, Freigaben, Mehrprofil) | 40 und mehr | Messweg | verwerfen, beobachten |
| 15 | Smart-Turn-Fristen vom Satzende abhängig machen (kürzer nach "?", länger nach "und") | mittel | mittel (zwei Fristen im Pipecat-Ablauf) | 6 | Szenario `pause` plus Fehlschnitt | später, nach Maßnahme 6 |

### Reihenfolge

1. Maßnahme 4, dann 2 und 3 (alle drei in diesem Lauf umgesetzt, Standard aus).
2. Maßnahme 1 und 5 als eine Messreihe, sobald das Testprofil freigegeben ist.
3. Nach der Messreihe entscheidet sich: Schalter einschalten (wie bei Paket A und B), Maßnahme 8 prüfen (erster Ton über 1,2 s),
   Maßnahme 6 und 15 (nur wenn Fehlschnitte auffallen), Maßnahme 12 nach der Hörprobe.

## 4. Was umgesetzt ist

| Schalter | Standard | Wirkung | Datei |
| --- | --- | --- | --- |
| `WAMO_PERSONA_GESPRAECH` | aus | Prompt-Block "Gesprächsführung": Selbstkorrektur der Person gilt nur in der letzten Fassung, Rückbezug statt Neufragen, nicht wiederholen was gerade gesagt wurde, nur eine Frage, knappe Bestätigung | `voice-core/persona.py` |
| `WAMO_WARTESATZ_MODELL` | aus | Antwortet das Modell nach einer Anfrage über `WAMO_WARTESATZ_MODELL_SECS` (Standard 2,5 s) mit keinem einzigen Zeichen, sagt WAMO einmal "Einen Moment.", nicht im Verlauf | `voice-core/wartesatz.py`, `bot.py` |
| Messweg `folge_ton_ms` | immer | Zeit vom Ende des Einwurfs bis zum ersten Ton der Folgeantwort | `scripts/wamo_stimme.py` |

Erlaubnislisten ergänzt: `infra/sokrates2/wamo_core_env.py` und `scripts/wamo_kern_sokrates2.py` (`WAMO_PERSONA_GESPRAECH`,
`WAMO_WARTESATZ_MODELL`, `WAMO_WARTESATZ_MODELL_SECS`).

## 5. Was gemessen werden muss (nach Freigabe des Testprofils)

Das Testprofil `wamo.stimmtest.1022@wamocon.com` ist gesperrt und wird nur mit Freigabe des Nutzers für eine Messung kurz
reaktiviert (so am 1.11.2026, Ergebnis in Abschnitt 7). Vorbereitet ist:

1. Basis: alle Schalter aus (`KERN_ENV="WAMO_PERSONA_GESPRAECH=aus;WAMO_WARTESATZ_MODELL=aus"`), Szenarien
   `fakten:8,kurz:5,einwurf:8,backchannel:6,pause:6,werkzeug:5`, Lauf `basis4`.
2. Je Schalter ein Lauf mit nur diesem an: `gespraech`, `wartesatzmodell`. Für `WAMO_WARTESATZ_MODELL` zusätzlich
   `WAMO_WARTESATZ_MODELL_SECS=0,8`, um das Auslösen überhaupt zu sehen (bei 2,5 s kommt es im Normalfall nicht vor, das
   ist der gewünschte Befund: kein Auslösen bei normalen Antworten).
3. Lauf `eu-tts` mit `SONIOX_TTS_URL=wss://tts-rt.eu.soniox.com/tts-websocket`, nur `fakten:8,kurz:5`. Entscheidend ist der
   Median des ersten Tons (heute 2316 bis 2617 ms im Messweg, 1400 bis 1900 ms im Livebetrieb).
4. Die offenen Läufe aus Paket A und B (README, "Nicht gemessen und offen").
5. Kennzahlen: `erster_ton_ms` (p50, p95), `luecke_max_ms`, `folge_ton_ms` (neu), Verstummen, falsche Unterbrechungen,
   Pause-Dazwischenreden. Ein Schalter gilt als unauffällig, wenn alle Werte innerhalb der Streuung der Basis liegen.
6. Hörproben `python scripts\wamo_stimme.py hoerprobe`: für `WAMO_PERSONA_GESPRAECH` nach Selbstkorrektur und Rückfrage
   achten: Bei dem von Hand gesprochenen Satz "Wie spät ist es, nein, ich meine, welcher Tag ist heute?" soll die Antwort
   nur den Tag nennen. Zusätzlich: Wird nach einem Rückbezug nichts neu gefragt, was schon gesagt wurde?

Einheitstests mit aufgezeichneten Zeiten laufen ohne Testprofil: `voice-core\test_wartesatz.py`, `test_persona.py`,
`test_stimme_messung.py`, `test_env_schalter.py`.

## 6. Grenzen der Recherche

- Keine Primärquelle für "ähm" in deutschen Sprachassistenten, keine für Selbstkorrektur im Prompt.
- Die Zahlen von LiveKit, Soniox, Deepgram und den Blogs stammen von den Anbietern, nicht von unabhängigen Tests.
- Soniox-TTS: SSML, Betonungs- und Emotionsmarken nicht bestätigt, EU-Endpunkt der Sprachausgabe nur als Hostname bestätigt.
- Full-Duplex-Bench "v3" ist nur in einer Wörterbuchseite genannt.
- Öffentliche Demos mit REA wurden nicht erneut beobachtet; die Auswertung der Demo unmute.sh steht in der README.
- Smart Turn v3.2 hat keine Änderungsliste auf der Seite, ob v3.2 in Pipecat 1.12 enthalten ist, ist nicht geprüft.

## 7. Messergebnis vom 1.11.2026

Aufbau wie in Abschnitt 5: Unterwegs (lokaler Kern, Haiku), Oberfläche frisch aus `main` gebaut, Testprofil für die Dauer der
Läufe aktiv und danach wieder gesperrt. Je Lauf `fakten:8, kurz:5, einwurf:8, backchannel:6, pause:6, werkzeug:5`. Zahlen sind
Median / 95. Perzentil bei 5 bis 8 Wiederholungen, jeder Lauf ist ein einzelner Durchgang. Der Lauf `wartesatzmodell` nutzte
absichtlich die Schwelle `WAMO_WARTESATZ_MODELL_SECS=0.8`, damit man das Auslösen überhaupt sieht.

| Kennzahl | Basis (beide Schalter aus) | `WAMO_PERSONA_GESPRAECH` an | `WAMO_WARTESATZ_MODELL` an (0,8 s) |
| --- | --- | --- | --- |
| Erster Ton, Faktenfrage, ms | 2338 / 3344 | 2272 / 2914 | 1902 / 2334 |
| Erster Ton, kurze Antwort, ms | 2313 / 3113 | 2303 / 3093 | 1831 / 1873 |
| Erster Ton, Werkzeug, ms | 2351 / 2413 | 2231 / 2513 | 1851 / 1861 |
| Größte Lücke, kurze Antwort, p95 ms | 2090 | 1440 | 1580 |
| Verpasste Einwürfe | 0 von 7 | 0 von 8 | 0 von 8 |
| Verstummen nach Einwurf, ms | 1290 / 2060 | 1485 / 1950 | 1270 / 2120 |
| Folgeantwort nach Einwurfende, ms (`folge_ton_ms`) | 2261 | 3234 | 76 (der Wartesatz) |
| Falsche Unterbrechung durch Rückmeldelaute (Log) | 0 | 0 | 0 |
| Pause: WAMO redet dazwischen | 0 von 6 | 0 von 6 | 6 von 6 |
| Wartesatz ausgelöst | nicht möglich | nicht möglich | 45 von 63 Zügen |

Modell-Schweigezeit nach der Anfrage (Zeit von `llm_start` bis `llm_token`, aus den Latenzzeilen des Kerns, je 46 Züge):
Basis Median 1032 ms, p90 1368 ms, höchstens 5299 ms, über 2500 ms in 1 Zug, über 800 ms in 35 Zügen. Mit
`WAMO_PERSONA_GESPRAECH`: Median 1090 ms, p90 2666 ms, höchstens 8513 ms, über 2500 ms in 5 Zügen.

Befunde:

1. **`WAMO_WARTESATZ_MODELL` bei 0,8 s ist unbrauchbar, bei der Standardschwelle von 2,5 s plausibel.** Das Modell schweigt im
   Normalfall etwa 1 s, bei 0,8 s feuert der Satz in 45 von 63 Zügen und redet in jede Pause hinein. Bei 2,5 s träfe er in der
   Basis 1 von 46 Zügen, im Gesprächs-Lauf 5 von 46, also genau die Züge mit 3 bis 8 Sekunden Stille. Ein Lauf mit der
   Standardschwelle im Messweg fehlt noch; die Zahlen stammen aus den Latenzzeilen, nicht aus einer Messung des Schalters.
2. **Die schnelleren ersten Töne im Lauf `wartesatzmodell` (etwa 400 ms) sind der Streuung des Modells zuzurechnen**, nicht dem
   Schalter: Er kann das Modell nicht beschleunigen. Der Wert gilt nicht als Gewinn.
3. **`WAMO_PERSONA_GESPRAECH` verändert die messbaren Kennzahlen nicht sicher.** Erster Ton und Lücken liegen in der Streuung. Die
   Folgeantwort nach einem Einwurf liegt mit 3234 gegen 2261 ms höher (kleine Stichprobe, ein Lauf), die Stille zwischen
   Füllsatz und Ergebnis beim Werkzeug schwankt stark (p50 0 gegen 6060 ms, p95 7250 gegen 8180 ms). Ob die Antworten
   natürlicher werden, entscheiden die Hörproben.
4. **Die Basis deckt sich mit den früheren Läufen** (erster Ton Median 2,3 s, keine verpassten Einwürfe, Dämpfen ab etwa 770 ms).

**Bestätigungslauf mit der Standardschwelle (`wartesatz25`, `WAMO_WARTESATZ_MODELL=an`, keine Schwelle gesetzt, 1.11.2026,
`pause:6, fakten:8, werkzeug:5, einwurf:6`):** Der Wartesatz hat in 31 Zügen kein einziges Mal gefeuert. In den Pausen redete WAMO nie
dazwischen (0 von 6, Pause 1,5 s und 2,5 s je 0 von 3). Erster Ton Fakten 2257 / 2674 ms (Basis 2338 / 3344), Pause 2034 ms
(Basis 1980), verpasste Einwürfe 0 von 6, Verstummen 1160 / 1390 ms (Basis 1290 / 2060), Folgeantwort nach Einwurfende 2058 ms (Basis
2261). Alle Werte liegen in der Streuung der Basis. Der Schalter stört bei 2,5 s also nicht. Ein Nutzen ist damit noch nicht belegt,
weil in diesem Lauf kein Zug länger als 2,5 s schwieg; er zeigt sich nur in langsamen Zügen (in der Basis etwa 1 von 46, im
Gesprächs-Lauf 5 von 46). Die Stille zwischen Füllsatz und Ergebnis bei Werkzeugen (p50 8340 ms) betrifft er nicht: Dafür gibt es
den eigenen Werkzeug-Wartesatz.

Entscheidung: Beide Schalter bleiben AUS, bis der Nutzer die Hörproben gehört hat. Der Hörvergleich ist auf Wunsch des Nutzers
zurückgestellt. Für `WAMO_WARTESATZ_MODELL` spricht nach den Messungen nichts gegen das Einschalten bei der Standardschwelle von
2,5 s; zu hören bleibt, ob "Einen Moment." im Gespräch angenehm klingt. Für `WAMO_PERSONA_GESPRAECH` entscheidet allein der
Hörvergleich mit dem von Hand gesprochenen Satz aus Abschnitt 5. Hörproben: `D:\tmp\stimme-hoerprobe3\` (`basis4-*`, `gespraech-*`,
`wartesatz-*`, der Lauf mit der Standardschwelle ist nur ausgewertet, nicht als Hörprobe erzeugt). Messdaten: `D:\tmp\wamo-stimme3\mess\`
(`basis4`, `gespraech`, `wartesatzmodell`, `wartesatz25`).
