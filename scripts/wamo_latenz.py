"""Hilfen für die Latenzmessung von WAMO (Zeit bis zum ersten Ton nach dem Sprechende).

Ablauf (je Messung etwa 5 Minuten für 20 Fragen):
  1. Temporäres Testkonto anlegen (scripts/wamo_nutzer.py, Rolle admin, Schema wamo_dev), zweiten Faktor einrichten und
     E-Mail, Kennwort und Geheimnis als konto.json in den Messordner legen. Nach der Messung das Profil sperren.
  2. python scripts/wamo_latenz.py fragen <ordner>            erzeugt f20.wav und f20.json (20 Fragen, Windows-Stimme Hedda)
  3. Kern lokal mit Latenz-Log starten (eigener Port, die Oberfläche muss darauf zeigen):
       WAMO_LATENZ_LOG=1 uv run bot.py --port 7861 --allowed-origins http://127.0.0.1:3100
     Das Testprofil braucht Claude-Zugang (WAMO_CLAUDE_SCHLUESSEL_PROFIL=<Kennung des Testprofils> im Prozess des Kerns).
     Einstellungen zum Vergleichen: WAMO_VAD_STOP_SECS, SMART_TURN_STOP_SECS, WAMO_TTS_START_ZEICHEN, WAMO_ECHO_FILTER.
  4. WAMO_MESS_DIR=<ordner> WAMO_UI_URL=http://127.0.0.1:3100 node scripts/wamo-latenz-messen.cjs unterwegs f20 lauf1
     (Kern-Log in <ordner>/lauf1.kern.log umleiten; "buero" misst lesend den laufenden Kern auf Sokrates-2)
  5. python scripts/wamo_latenz.py auswerten <ordner> lauf1 [lauf2 ...]

fragen und auswerten schreiben keine Geheimnisse und keine Gesprächsinhalte in die Ausgabe.
"""

from __future__ import annotations

import json
import re
import statistics
import subprocess
import sys
import wave
from pathlib import Path

RATE = 16000
FRAGEN = [
    "Wie heißt die Hauptstadt von Frankreich?", "Was ist zwei plus drei?", "Nenn mir bitte eine Farbe.",
    "Wie viele Tage hat eine Woche?", "Welcher Planet ist der größte?", "Wie heißt die Hauptstadt von Spanien?",
    "Wie viele Beine hat eine Spinne?", "Was ist das Gegenteil von warm?", "Wie heißt die Hauptstadt von Italien?",
    "Wie viele Minuten hat eine Stunde?", "Welche Farbe hat eine reife Banane?", "Was ist vier mal fünf?",
    "Nenn mir ein Tier mit vier Beinen.", "Wie heißt der längste Fluss Deutschlands?", "Wie viele Monate hat ein Jahr?",
    "Was ist die Hälfte von zwanzig?", "Wie heißt die Hauptstadt von Polen?", "Welches Tier macht muh?",
    "Wie viele Sekunden hat eine Minute?", "Nenn mir bitte ein Obst.",
]


def _sprechen(text: str, ziel: Path) -> None:
    ps = (
        "Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.SelectVoice('Microsoft Hedda Desktop'); $s.Rate = 0; "
        f"$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo({RATE}, "
        "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono); "
        f"$s.SetOutputToWaveFile('{ziel}', $f); $s.Speak('{text}'); $s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)


def fragen(ordner: Path, name: str = "f20", vorlauf: float = 9.0, pause: float = 10.5) -> None:
    """Testmikrofon: die Fragen, dazwischen Stille (Pause lässt der Antwort Zeit, bevor die nächste Frage kommt)."""
    ordner.mkdir(parents=True, exist_ok=True)
    frames, zeiten = bytearray(b"\x00\x00" * int(RATE * vorlauf)), []
    for i, text in enumerate(FRAGEN):
        tmp = ordner / f"_teil{i}.wav"
        _sprechen(text, tmp)
        with wave.open(str(tmp), "rb") as w:
            daten = w.readframes(w.getnframes())
        tmp.unlink()
        start = len(frames) / 2 / RATE
        frames += daten
        zeiten.append({"text": text, "start": round(start, 2), "ende": round(len(frames) / 2 / RATE, 2)})
        frames += b"\x00\x00" * int(RATE * pause)
    with wave.open(str(ordner / f"{name}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(frames))
    (ordner / f"{name}.json").write_text(json.dumps(zeiten, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{name}: {len(zeiten)} Fragen, {len(frames) / 2 / RATE:.0f} s")


def _quantil(werte: list[float], p: int) -> float:
    s = sorted(werte)
    return s[min(len(s) - 1, max(0, -(-p * len(s) // 100) - 1))] if s else float("nan")


def auswerten(ordner: Path, laeufe: list[str]) -> None:
    """Browser: erster Ton nach dem Satzende (p50/p95 über alle Fragen). Kern: Abschnitte aus den Zeilen LATENZ_JSON des
    Kern-Logs (<lauf>.kern.log), in ms ab geschätztem Sprechende, p50/p95 über alle Züge."""
    print(f"{'Lauf':18}{'n':>4}{'p50':>7}{'p95':>7}{'max':>7}   (Browser, ms)")
    kern: dict[str, list[dict]] = {}
    for lauf in laeufe:
        d = json.loads((ordner / f"{lauf}.json").read_text(encoding="utf-8"))
        werte = [m for m in d["latenzenMs"] if m is not None]
        if werte:
            print(f"{lauf:18}{len(werte):4}{statistics.median(werte):7.0f}{_quantil(werte, 95):7.0f}{max(werte):7.0f}")
        log = ordner / f"{lauf}.kern.log"
        if log.exists():
            text = re.sub(r"\x1b\[[0-9;]*m", "", log.read_text(encoding="utf-8", errors="replace"))
            kern[lauf] = [json.loads(m.group(1)) for m in re.finditer(r"LATENZ_JSON (\{.*\})", text)]
    if kern:
        print("\nKern, ms ab Sprechende (p50/p95):")
    for lauf, zuege in kern.items():
        teile = []
        for k in ("stt_final", "llm_start", "api_senden", "llm_marker", "tts_satz", "tts_audio", "ausgabe"):
            w = [z["ms"][k] for z in zuege if k in z["ms"]]
            if w:
                teile.append(f"{k} {statistics.median(w):.0f}/{_quantil(w, 95):.0f}")
        print(f"{lauf:18}n={len(zuege)} " + " | ".join(teile))


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "fragen":
        fragen(Path(sys.argv[2]))
    elif len(sys.argv) >= 4 and sys.argv[1] == "auswerten":
        auswerten(Path(sys.argv[2]), sys.argv[3:])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
