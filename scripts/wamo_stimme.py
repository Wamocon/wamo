"""Messweg für Stimme und Sprachverhalten von WAMO (Unterbrechungen, Rückmeldelaute, Lücken, Wartezeiten).

Ablauf (ein Lauf dauert etwa 15 Minuten):
  1. Temporäres Testkonto anlegen (scripts/wamo_nutzer.py, Rolle mitglied, Schema wamo_dev), E-Mail und Kennwort als
     konto.json in den Messordner legen; den zweiten Faktor richtet das Messskript beim ersten Lauf selbst ein.
     Nach der Messung das Profil sperren (wamo_nutzer.py sperren).
  2. python scripts/wamo_stimme.py clips <ordner>
        erzeugt die Sprechproben der Person (Windows-Stimme Hedda) und Geräusche (Husten, Klopfen) als WAV
  3. Oberfläche aus einer Kopie bauen und starten (nie in ui/.next des Hauptordners), z. B.
        NEXT_PUBLIC_VOICE_CORE_URL=http://127.0.0.1:7861 npx next build; npx next start -p 3100 -H 127.0.0.1
  4. WAMO_MESS_DIR=<ordner> WAMO_UI_URL=http://127.0.0.1:3100 node scripts/wamo-stimme-messen.cjs <lauf> [szenarien]
        startet den Kern selbst (Port 7861, Latenz-Log an, Log in <lauf>.kern.log), spielt die Szenarien über ein
        virtuelles Mikrofon und nimmt WAMOs Ausgabestrom und das Mikrofon sample-genau auf (<lauf>-<szenario>-bot.wav)
  5. python scripts/wamo_stimme.py auswerten <ordner> <lauf> [<lauf2> ...]
        Kennzahlen je Szenario; schreibt <lauf>.metriken.json (keine Geheimnisse, keine Gesprächsinhalte)
  6. python scripts/wamo_stimme.py hoerprobe <ordner> <lauf> <ziel> [<name>]
        schneidet drei kurze Hörproben (Mikrofon und WAMO gemischt, so wie es im Raum klingt)

Messgrößen: siehe auswerten(). Alle Zeiten stammen aus einer Uhr (AudioContext des Messbrowsers), Mikrofon und
Ausgabestrom sind deshalb direkt vergleichbar.
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
import struct
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

RATE = 16000
HUELL_S = 0.010  # Hüllkurve in 10-ms-Schritten

# Sprechproben der Person: Name -> Text (Windows-Stimme Hedda)
SPRECHPROBEN = {
    "frage_zeit": "Wie spät ist es gerade?",
    "frage_datum": "Und welcher Wochentag ist heute?",
    "frage_lang_1": "Erkläre mir bitte ausführlich in mehreren Sätzen, wie ein Elektromotor funktioniert.",
    "frage_lang_2": "Erzähl mir bitte ausführlich, wie die Geschichte des Fahrrads verlief.",
    "frage_lang_3": "Beschreibe mir bitte ausführlich, wie ein Flugzeug fliegen kann.",
    "einwurf_a": "Moment, stopp, ich habe da eine andere Frage.",
    "einwurf_b": "Warte mal, was kostet das eigentlich?",
    "mhm": "Mhm.",
    "ja_genau": "Ja, genau.",
    "aha": "Aha, ach so.",
    "pause_a": "Ich wollte noch wissen, ähm,",
    "pause_b": "wie die Hauptstadt von Australien heißt.",
    "frage_suche": "Suche bitte im Internet nach dem Wetter in Frankfurt morgen.",
    # Faktenfragen ohne Werkzeug, wie in der Latenzmessung (wamo_latenz.py): Wächter für die Zeit bis zum ersten Ton
    "fakt_1": "Wie heißt die Hauptstadt von Frankreich?",
    "fakt_2": "Was ist zwei plus drei?",
    "fakt_3": "Nenn mir bitte eine Farbe.",
    "fakt_4": "Wie viele Tage hat eine Woche?",
    "fakt_5": "Welcher Planet ist der größte?",
    "fakt_6": "Wie heißt die Hauptstadt von Spanien?",
    "fakt_7": "Wie viele Beine hat eine Spinne?",
    "fakt_8": "Was ist das Gegenteil von warm?",
}


def _sprechen(text: str, ziel: Path) -> None:
    # Text und Zielpfad gehen über die Umgebung, nie in den PowerShell-Text (ein ' im Ordnernamen wäre sonst Befehlscode)
    ps = (
        "Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.SelectVoice('Microsoft Hedda Desktop'); $s.Rate = 0; "
        f"$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo({RATE}, "
        "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono); "
        "$s.SetOutputToWaveFile($env:WAMO_SPRECH_ZIEL, $f); $s.Speak($env:WAMO_SPRECH_TEXT); $s.Dispose()"
    )
    env = {**os.environ, "WAMO_SPRECH_ZIEL": str(ziel), "WAMO_SPRECH_TEXT": text}
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, env=env)


def schreibe_wav(pfad: Path, daten: np.ndarray, rate: int) -> None:
    pcm = np.clip(daten, -1.0, 1.0)
    with wave.open(str(pfad), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((pcm * 32767.0).astype("<i2").tobytes())


def lade_wav(pfad: Path) -> tuple[int, np.ndarray]:
    with wave.open(str(pfad), "rb") as w:
        rate, n, kanaele = w.getframerate(), w.getnframes(), w.getnchannels()
        roh = np.frombuffer(w.readframes(n), dtype="<i2").astype(np.float32) / 32768.0
    return rate, (roh.reshape(-1, kanaele).mean(axis=1) if kanaele > 1 else roh)


def zuschneiden(daten: np.ndarray, rate: int, schwelle: float = 0.01, rand_s: float = 0.03) -> np.ndarray:
    """Stille am Anfang und Ende abschneiden (die Windows-Stimme hängt Stille an), mit 30 ms Rand."""
    laut = np.flatnonzero(np.abs(daten) > schwelle)
    if laut.size == 0:
        return daten
    rand = int(rand_s * rate)
    return daten[max(0, laut[0] - rand): laut[-1] + 1 + rand]


def _husten(rng: np.random.Generator) -> np.ndarray:
    """Husten: drei kurze, rauhe Stöße (gefiltertes Rauschen mit schnellem Anstieg und Abklingen)."""
    teile = []
    for dauer, pegel in ((0.22, 0.55), (0.16, 0.45), (0.14, 0.35)):
        n = int(RATE * dauer)
        t = np.arange(n) / RATE
        rausch = rng.standard_normal(n)
        rausch = np.convolve(rausch, np.ones(4) / 4, mode="same")  # Höhen dämpfen: rauh, nicht zischend
        huell = np.minimum(1.0, t / 0.02) * np.exp(-t / (dauer / 3.0))
        teile.append(pegel * rausch * huell / 2.5)
        teile.append(np.zeros(int(RATE * 0.12)))
    return np.concatenate(teile).astype(np.float32)


def _klopfen(rng: np.random.Generator) -> np.ndarray:
    """Zwei dumpfe Schläge (Tür, Tisch): tiefer Ton mit kurzem Knack."""
    teile = []
    for _ in range(2):
        n = int(RATE * 0.18)
        t = np.arange(n) / RATE
        ton = np.sin(2 * math.pi * 140 * t) * np.exp(-t / 0.045)
        knack = rng.standard_normal(n) * np.exp(-t / 0.006) * 0.4
        teile.append((0.6 * ton + knack).astype(np.float32))
        teile.append(np.zeros(int(RATE * 0.25), dtype=np.float32))
    return np.concatenate(teile)


def clips(ordner: Path) -> None:
    """Sprechproben und Geräusche als <ordner>/clips/<name>.wav und clips.json (Dauer in Sekunden)."""
    ziel = ordner / "clips"
    ziel.mkdir(parents=True, exist_ok=True)
    dauern: dict[str, float] = {}
    for name, text in SPRECHPROBEN.items():
        datei = ziel / f"{name}.wav"
        _sprechen(text, datei)
        rate, daten = lade_wav(datei)
        daten = zuschneiden(daten, rate)
        schreibe_wav(datei, daten, rate)
        dauern[name] = round(len(daten) / rate, 3)
    rng = np.random.default_rng(7)
    for name, daten in (("husten", _husten(rng)), ("klopfen", _klopfen(rng))):
        schreibe_wav(ziel / f"{name}.wav", daten, RATE)
        dauern[name] = round(len(daten) / RATE, 3)
    (ziel / "clips.json").write_text(json.dumps(dauern, indent=1), encoding="utf-8")
    print(f"{len(dauern)} Sprechproben in {ziel}: " + ", ".join(f"{k} {v:.1f} s" for k, v in dauern.items()))


# ---------------------------------------------------------------------------------------------------------------------
# Auswertung

SCHWELLE_BOT = 0.003  # RMS je 10 ms, ab dem WAMO als hörbar gilt (Rauschboden der Aufnahme liegt weit darunter)
SCHWELLE_MIC = 0.004  # das virtuelle Mikrofon hat einen Rauschboden von etwa 0,0005
STILLE_ENDE_S = 0.9  # so lange kein Ton heißt: WAMO ist verstummt (kürzere Pausen sind Satz- und Wortpausen)
VERPASST_NACH_S = 4.0  # ein Einwurf, auf den WAMO länger als so viele Sekunden weiterspricht, gilt als verpasst
HART_FALL_MS = 35  # fällt der Pegel in weniger als dieser Zeit von 50 % auf nichts, wurde hart abgeschnitten
ANTWORT_TRENNER_S = 1.2  # Stille, die zwei Antworten trennt
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")  # Laufnamen und Beschriftungen: nie Pfade


def huellkurve(x: np.ndarray, rate: int, fenster: float = HUELL_S) -> np.ndarray:
    """RMS-Pegel in Fenstern von 10 ms."""
    n = max(1, int(rate * fenster))
    m = len(x) // n
    if m == 0:
        return np.zeros(0, dtype=np.float32)
    return np.sqrt(np.mean(x[: m * n].astype(np.float64).reshape(m, n) ** 2, axis=1))


def segmente(env: np.ndarray, schwelle: float, min_luecke: float = 0.06, fenster: float = HUELL_S) -> list[tuple[float, float]]:
    """Hörbare Abschnitte (Beginn, Ende in Sekunden). Pausen unter min_luecke gehören zum Abschnitt."""
    akt = np.concatenate(([False], env > schwelle, [False])).astype(np.int8)
    d = np.diff(akt)
    starts, enden = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
    segs: list[list[int]] = []
    for s, e in zip(starts, enden):
        if segs and (s - segs[-1][1]) * fenster < min_luecke:
            segs[-1][1] = int(e)
        else:
            segs.append([int(s), int(e)])
    return [(s * fenster, e * fenster) for s, e in segs]


def aktiv_bei(segs: list[tuple[float, float]], t: float, toleranz: float = 0.3) -> bool:
    return any(s <= t <= e + toleranz for s, e in segs)


def erster_ton(segs: list[tuple[float, float]], ab: float) -> float | None:
    """Beginn des ersten Abschnitts, der nach ab anfängt (ein Abschnitt, der schon läuft, zählt nicht)."""
    return next((s for s, _ in segs if s >= ab), None)


def verstummen(segs: list[tuple[float, float]], ab: float, min_still: float = STILLE_ENDE_S) -> float | None:
    """Zeit, zu der nach ab die letzte hörbare Stelle vor einer Stille von mindestens min_still endet (None: nie)."""
    for i, (s, e) in enumerate(segs):
        if e <= ab:
            continue
        folgt = segs[i + 1][0] if i + 1 < len(segs) else math.inf
        if folgt - e >= min_still:
            return e
    return None


def luecken(segs: list[tuple[float, float]], von: float, bis: float, trenner: float = 2.5) -> list[float]:
    """Stille zwischen aufeinanderfolgenden Abschnitten einer Antwort (Abschnitte in [von, bis]); eine Stille ab
    trenner trennt Antworten und zählt nicht."""
    ds = [(s, e) for s, e in segs if e > von and s < bis]
    out = []
    for (_, e1), (s2, _) in zip(ds, ds[1:]):
        if s2 - e1 < trenner:
            out.append(s2 - e1)
    return out


def antwortbloecke(segs: list[tuple[float, float]], von: float, bis: float, trenner: float = ANTWORT_TRENNER_S) -> list[tuple[float, float]]:
    """Antworten: Abschnitte, getrennt durch Stille ab trenner."""
    blocks: list[list[float]] = []
    for s, e in segs:
        if e <= von or s >= bis:
            continue
        if blocks and s - blocks[-1][1] < trenner:
            blocks[-1][1] = e
        else:
            blocks.append([s, e])
    return [(a, b) for a, b in blocks]


def schnitt_info(env: np.ndarray, t_ende: float, fenster: float = HUELL_S) -> dict:
    """Wie klingt das Ende? fall_ms: Zeit vom letzten Pegel über 50 % des Sprechpegels bis zum Ende."""
    ie = min(len(env), int(round(t_ende / fenster)))
    a, b = max(0, ie - 200), max(0, ie - 30)
    ref_werte = env[a:b][env[a:b] > SCHWELLE_BOT]
    if ref_werte.size == 0:
        return {"fall_ms": None, "hart": None}
    ref = float(np.median(ref_werte))
    i = ie - 1
    while i > 0 and env[i] < 0.5 * ref:
        i -= 1
    fall = (ie - 1 - i) * fenster * 1000.0 + fenster * 1000.0
    return {"fall_ms": round(fall), "hart": fall < HART_FALL_MS}


def lautstaerke_dip(vol: list[list[float]], ab: float, bis: float, schwelle: float = 0.9) -> dict:
    """Reaktion der Oberfläche auf die Person, aus dem Lautstärkeverlauf des Wiedergabeelements (Dämpfen, Ausblenden):
    erste Zeit unter schwelle ab ab (Sekunden, None: nie), Dauer unter schwelle bis bis und kleinster Wert."""
    pts = sorted((float(t), float(v)) for t, v in vol if t >= 0)
    aktuell = 1.0
    for t, v in pts:
        if t > ab:
            break
        aktuell = v
    folge = [(ab, aktuell)] + [(t, v) for t, v in pts if ab < t <= bis]
    erste, dauer, kleinster = None, 0.0, aktuell
    for i, (t, v) in enumerate(folge):
        bis_t = folge[i + 1][0] if i + 1 < len(folge) else bis
        kleinster = min(kleinster, v)
        if v < schwelle:
            if erste is None:
                erste = t - ab
            dauer += bis_t - t
    return {"erste": erste, "dauer": dauer, "min": kleinster}


def lautstaerke_verlauf(vol: list[list[float]], t: np.ndarray) -> np.ndarray:
    """Lautstärke des Wiedergabeelements (weiches Ausblenden der Oberfläche) zu den Zeiten t; vorher 1."""
    pts = sorted((float(a), float(b)) for a, b in vol if a >= 0)
    if not pts:
        return np.ones(len(t))
    zeiten = np.array([p[0] for p in pts])
    werte = np.array([p[1] for p in pts])
    idx = np.searchsorted(zeiten, t, side="right") - 1
    return np.where(idx >= 0, werte[np.clip(idx, 0, None)], 1.0)


def gehoert(bot: np.ndarray, rate: int, von: float, vol: list[list[float]]) -> np.ndarray:
    """Was die Person hört: Ausgabestrom mal Lautstärke des Wiedergabeelements. von: Zeit der ersten Probe."""
    t = von + np.arange(len(bot)) / rate
    return bot * lautstaerke_verlauf(vol, t)


KERN_MUSTER = [
    ("weiche_unterbrechung", re.compile(r"weiche Unterbrechung \((.*?)\)")),
    ("keine_unterbrechung", re.compile(r"keine Unterbrechung noetig \((.*?)\)")),
    ("einwurf", re.compile(r"Einwurf erkannt")),
    ("echo_verworfen", re.compile(r"Echo der eigenen Stimme verworfen")),
    ("unterbrochen", re.compile(r"WAMO wurde unterbrochen")),
    ("rueckmeldung", re.compile(r"Rückmeldung erkannt")),
    ("ducken", re.compile(r"Stimme gedämpft")),
    ("wiederaufnahme", re.compile(r"Wiederaufnahme")),
    ("wartesatz", re.compile(r"Wartesatz")),
    ("turn_wache", re.compile(r"Turn-Wache")),
]
ZEIT = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.(\d{3}) \|")
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def kern_ereignisse(pfad: Path) -> list[dict]:
    """Ereignisse aus dem Kern-Log mit Zeit (Sekunden seit 1970, lokale Uhr des Messrechners wie Date.now())."""
    import time

    if not pfad.exists():
        return []
    out = []
    for zeile in ANSI.sub("", pfad.read_text(encoding="utf-8", errors="replace")).splitlines():
        m = ZEIT.match(zeile)
        if not m:
            continue
        t = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")) + int(m.group(2)) / 1000.0
        for art, muster in KERN_MUSTER:
            mm = muster.search(zeile)
            if mm:
                out.append({"t": t, "art": art, "grund": mm.group(1) if mm.groups() else ""})
        if "LATENZ_JSON" in zeile:
            try:
                out.append({"t": t, "art": "latenz", "zug": json.loads(zeile.split("LATENZ_JSON", 1)[1])})
            except ValueError:
                pass
    return out


def im_fenster(ereignisse: list[dict], arten: tuple[str, ...], a: float, b: float) -> list[dict]:
    return [e for e in ereignisse if e["art"] in arten and a <= e["t"] <= b]


def _quantil(werte: list[float], p: int) -> float:
    s = sorted(werte)
    return s[min(len(s) - 1, max(0, -(-p * len(s) // 100) - 1))] if s else float("nan")


def _aktiv_maske(segs: list[tuple[float, float]], n: int) -> np.ndarray:
    maske = np.zeros(n, dtype=bool)
    for s, e in segs:
        maske[int(s / HUELL_S): int(e / HUELL_S) + 1] = True
    return maske


def wanduhr(sz: dict):
    """Zuordnung AudioContext-Zeit -> Wanduhr (Sekunden seit 1970, wie das Kern-Log). Die Uhr des AudioContext läuft gegen
    die Wanduhr minimal anders (bis zu einer Sekunde je Viertelstunde): jede Zeit wird am nächsten Ereignis mit
    Wanduhrstempel (Beginn einer Wiederholung, Sprechprobe) festgemacht, nicht am Ende des Blocks."""
    stempel = sorted((e.get("jetzt", e["t0"]), e["wall"] / 1000.0) for e in sz["events"] if e.get("wall"))

    def wall(t: float) -> float:
        if not stempel:
            return t + sz["wallDiff"] / 1000.0
        ref, w0 = min(stempel, key=lambda p: abs(p[0] - t))
        return w0 + (t - ref)

    return wall


def analysiere_szenario(sz: dict, bot: np.ndarray, mic: np.ndarray, kern: list[dict]) -> list[dict]:
    """Kennzahlen je Wiederholung eines Szenarios. bot/mic: Aufnahme ab sz["von"], sz["sr"] Proben je Sekunde."""
    rate, von = sz["sr"], sz["von"]

    wall = wanduhr(sz)

    def rel(t):  # AudioContext-Zeit -> Sekunden seit Aufnahmebeginn
        return t - von

    heard = gehoert(bot, rate, von, sz["vol"])
    eh, em = huellkurve(heard, rate), huellkurve(mic, rate)
    sh, sm = segmente(eh, SCHWELLE_BOT), segmente(em, SCHWELLE_MIC)
    reps = sorted((e for e in sz["events"] if e["typ"] == "rep"), key=lambda e: e["t0"])
    out = []
    for i, rep in enumerate(reps):
        bis = reps[i + 1]["t0"] if i + 1 < len(reps) else sz["bis"]
        a, b = rel(rep["t0"]), rel(bis)
        clips = [e for e in sz["events"] if e["typ"] == "clip" and e["rep"] == rep["rep"]]
        res = next((r for r in sz["ergebnisse"] if r["rep"] == rep["rep"]), {})
        r: dict = {"rep": rep["rep"], "ok": bool(res.get("ok")), "grund": res.get("grund", "")}
        if not r["ok"]:
            out.append(r)
            continue
        fragen = [c for c in clips if c["rolle"] in ("frage", "frage2", "pause_b")]
        if fragen:
            q = fragen[-1]
            q_ende = rel(q["t0"] + q["dauer"])
            t1 = erster_ton(sh, q_ende - 0.05)
            r["erster_ton_ms"] = round((t1 - q_ende) * 1000) if t1 is not None and t1 < q_ende + 25 else None
            if t1 is not None:  # Antwort: ab dem ersten Ton bis zur ersten Stille ab 2,5 s; Lücken darin
                ende = t1
                for s, e in sh:
                    if s >= t1 and s - ende < 2.5:
                        ende = e
                    elif s > ende:
                        break
                lk = luecken(sh, t1, ende + 0.01)
                r["antwort_s"] = round(ende - t1, 1)
                r["luecke_max_ms"] = round(max(lk) * 1000) if lk else 0
                r["luecken_400"] = sum(1 for x in lk if x >= 0.4)
                r["luecken_800"] = sum(1 for x in lk if x >= 0.8)
        ereign = []
        for c in clips:
            if c["rolle"] not in ("einwurf", "backchannel", "geraeusch"):
                continue
            ton = erster_ton(sm, rel(c["t0"]) - 0.05)
            onset = ton if ton is not None and ton < rel(c["t0"]) + 0.6 else rel(c["t0"])
            ev: dict = {"rolle": c["rolle"], "name": c["name"], "bot_sprach": aktiv_bei(sh, onset, 0.3)}
            dip = lautstaerke_dip(sz["vol"], von + onset - 0.05, von + onset + 4.0)
            ev["reaktion_ms"] = round(dip["erste"] * 1000) if dip["erste"] is not None else None
            ev["gedaempft_ms"] = round(dip["dauer"] * 1000)
            ev["vol_min"] = round(dip["min"], 2)
            wa = wall(c["t0"])
            if c["rolle"] == "einwurf":
                tv = verstummen(sh, onset)
                ev["verstummen_ms"] = round((tv - onset) * 1000) if tv is not None else None
                ev["verpasst"] = tv is None or (tv - onset) > VERPASST_NACH_S
                if tv is not None:
                    ev.update(schnitt_info(eh, tv))
                gr = im_fenster(kern, ("weiche_unterbrechung",), wa, wa + 6.0)
                ev["grund"] = gr[0]["grund"] if gr else ""
            else:
                ev["unterbrechung_log"] = len(im_fenster(kern, ("weiche_unterbrechung",), wa, wa + 4.5)) > 0
                tv = verstummen(sh, onset, 0.9)
                ev["verstummt_audio"] = tv is not None and (tv - onset) < 3.0
                ev["echo_verworfen"] = len(im_fenster(kern, ("echo_verworfen", "keine_unterbrechung"), wa, wa + 4.5))
            ereign.append(ev)
        if ereign:
            r["ereignisse"] = ereign
        if sz["name"] == "echo":
            wa, wb = wall(rep["t0"]), wall(bis)
            r["unterbrechungen"] = len(im_fenster(kern, ("weiche_unterbrechung",), wa, wb))
            r["echo_verworfen"] = len(im_fenster(kern, ("echo_verworfen",), wa, wb))
            r["antwort_s"] = round(sum(e - s for s, e in antwortbloecke(sh, a, b)), 1)
            dip = lautstaerke_dip(sz["vol"], von + a, von + b)  # Dämpfen durch Echo (Pumpen)
            r["gedaempft_ms"], r["vol_min"] = round(dip["dauer"] * 1000), round(dip["min"], 2)
        if sz["name"] == "werkzeug":
            bl = antwortbloecke(sh, rel(clips[0]["t0"] + clips[0]["dauer"]), b, trenner=1.2)
            r["bloecke"] = len(bl)
            stille = [round((b2[0] - b1[1]) * 1000) for b1, b2 in zip(bl, bl[1:])]
            r["stille_zwischen_ms"] = max(stille) if stille else 0  # längste Stille zwischen Füllsatz und Ergebnis
            r["fuellsatz"] = len(bl) > 1 and (bl[0][1] - bl[0][0]) < 3.5  # kurzer erster Block vor dem Ergebnis
        if sz["name"] == "schnell":
            r["antworten"] = len(antwortbloecke(sh, rel(clips[0]["t0"]) + 0.2, b))
            n = min(len(em), len(eh))
            ia, ib = int(a / HUELL_S), min(n, int(b / HUELL_S))
            both = _aktiv_maske(sm, n)[ia:ib] & _aktiv_maske(sh, n)[ia:ib]
            r["ueberlappung_ms"] = int(both.sum() * HUELL_S * 1000)
        if sz["name"] == "pause":
            pa = next(c for c in clips if c["rolle"] == "pause_a")
            pb = next(c for c in clips if c["rolle"] == "pause_b")
            ta = erster_ton(sh, rel(pa["t0"] + pa["dauer"]) + 0.1)
            r["dazwischen"] = ta is not None and ta < rel(pb["t0"]) + 0.2
            r["luecke_s"] = res.get("luecke")
            if r["dazwischen"]:
                r["dazwischen_nach_ms"] = round((ta - rel(pa["t0"] + pa["dauer"])) * 1000)
        out.append(r)
    return out


def _anteil(werte: list[bool]) -> str:
    return f"{sum(werte)}/{len(werte)}" if werte else "-"


def _verteilung(werte: list[float]) -> str:
    return f"p50 {statistics.median(werte):.0f} / p95 {_quantil(werte, 95):.0f} / max {max(werte):.0f}" if werte else "-"


def zusammenfassen(daten: dict[str, list[dict]]) -> dict:
    """Kennzahlen über alle Wiederholungen: daten[szenario] = Liste der Wiederholungen (analysiere_szenario)."""
    z: dict = {}
    antw = [r for n in ("fakten", "kurz", "lang", "werkzeug", "schnell", "pause") for r in daten.get(n, []) if r.get("ok")]
    for n in ("fakten", "kurz", "lang", "werkzeug", "schnell", "pause"):
        rs = [r for r in daten.get(n, []) if r.get("ok")]
        if rs:
            z[f"erster_ton_ms[{n}]"] = [r["erster_ton_ms"] for r in rs if r.get("erster_ton_ms") is not None]
            z[f"luecke_max_ms[{n}]"] = [r["luecke_max_ms"] for r in rs if "luecke_max_ms" in r]
            z[f"luecken_400[{n}]"] = sum(r.get("luecken_400", 0) for r in rs)
            z[f"antworten[{n}]"] = len(rs)
    z["erster_ton_ms[kurz+lang]"] = [r["erster_ton_ms"] for n in ("kurz", "lang") for r in daten.get(n, []) if r.get("erster_ton_ms") is not None]
    z["luecke_max_ms[alle]"] = [r["luecke_max_ms"] for r in antw if "luecke_max_ms" in r]
    alle = [e for r in daten.get("einwurf", []) for e in r.get("ereignisse", []) if e["rolle"] == "einwurf"]
    einw = [e for e in alle if e["bot_sprach"]]
    z["einwurf_gueltig"] = len(einw)
    z["einwurf_ungueltig"] = len(alle) - len(einw) + sum(1 for r in daten.get("einwurf", []) if not r.get("ok"))
    z["einwurf_verpasst"] = sum(1 for e in einw if e["verpasst"])
    z["verstummen_ms"] = [e["verstummen_ms"] for e in einw if e.get("verstummen_ms") is not None and not e["verpasst"]]
    z["reaktion_ms"] = [e["reaktion_ms"] for e in einw if e.get("reaktion_ms") is not None]
    z["schnitt_hart"] = [bool(e["hart"]) for e in einw if e.get("hart") is not None]
    z["fall_ms"] = [e["fall_ms"] for e in einw if e.get("fall_ms") is not None]
    z["gruende"] = {}
    for e in einw:
        g = e.get("grund") or "keiner"
        z["gruende"][g] = z["gruende"].get(g, 0) + 1
    for n in ("backchannel", "geraeusch"):
        evs = [e for r in daten.get(n, []) for e in r.get("ereignisse", []) if e["rolle"] == n and e["bot_sprach"]]
        z[f"{n}_ereignisse"] = len(evs)
        z[f"{n}_falsch_log"] = sum(1 for e in evs if e.get("unterbrechung_log"))
        z[f"{n}_falsch_audio"] = sum(1 for e in evs if e.get("verstummt_audio"))
        z[f"{n}_gedaempft"] = [e["gedaempft_ms"] for e in evs if e.get("reaktion_ms") is not None]  # Dauer der Dämpfung je Ereignis
        z[f"{n}_vol_min"] = [e["vol_min"] for e in evs if e.get("reaktion_ms") is not None]
    ec = daten.get("echo", [])
    z["echo_laeufe"] = len([r for r in ec if r.get("ok")])
    z["echo_unterbrechungen"] = sum(r.get("unterbrechungen", 0) for r in ec)
    z["echo_verworfen"] = sum(r.get("echo_verworfen", 0) for r in ec)
    z["echo_gedaempft_ms"] = [r["gedaempft_ms"] for r in ec if r.get("ok") and "gedaempft_ms" in r]
    z["echo_antwort_s"] = [r["antwort_s"] for r in ec if r.get("ok") and "antwort_s" in r]
    wz = [r for r in daten.get("werkzeug", []) if r.get("ok")]
    z["werkzeug_stille_ms"] = [r["stille_zwischen_ms"] for r in wz if "stille_zwischen_ms" in r]
    z["werkzeug_fuellsatz"] = [bool(r.get("fuellsatz")) for r in wz]
    sc = [r for r in daten.get("schnell", []) if r.get("ok")]
    z["schnell_antworten"] = [r["antworten"] for r in sc if "antworten" in r]
    z["schnell_ueberlappung_ms"] = [r["ueberlappung_ms"] for r in sc if "ueberlappung_ms" in r]
    pa = [r for r in daten.get("pause", []) if r.get("ok")]
    for luecke in (1.5, 2.5):
        z[f"pause_dazwischen[{luecke}]"] = [bool(r.get("dazwischen")) for r in pa if r.get("luecke_s") == luecke]
    return z


def drucken(name: str, z: dict) -> None:
    print(f"\n== {name}")
    for n in ("fakten", "kurz", "lang", "werkzeug", "schnell", "pause"):
        if f"antworten[{n}]" in z:
            print(f"  {n:11} erster Ton ms {_verteilung(z[f'erster_ton_ms[{n}]'])} | größte Lücke ms {_verteilung(z[f'luecke_max_ms[{n}]'])}"
                  f" | Lücken >= 400 ms: {z[f'luecken_400[{n}]']} in {z[f'antworten[{n}]']} Antworten")
    if z.get("einwurf_gueltig") or z.get("einwurf_ungueltig"):
        print(f"  Einwurf     gültig {z['einwurf_gueltig']} (ungültig {z['einwurf_ungueltig']}), verpasst {z['einwurf_verpasst']}/{z['einwurf_gueltig']}"
              f" | erste Reaktion ms {_verteilung(z['reaktion_ms'])} | Verstummen ms {_verteilung(z['verstummen_ms'])}"
              f" | hart abgeschnitten {_anteil(z['schnitt_hart'])}"
              f" | Auslauf ms {_verteilung(z['fall_ms'])} | Grund {z['gruende']}")
    for n in ("backchannel", "geraeusch"):
        if z.get(f"{n}_ereignisse"):
            dauer = z[f"{n}_gedaempft"]
            leiser = f", Dauer ms {_verteilung(dauer)}, tiefster Wert {min(z[f'{n}_vol_min']):.2f}" if dauer else ""
            print(f"  {n:11} {z[f'{n}_ereignisse']} Ereignisse | falsch unterbrochen (Log) {z[f'{n}_falsch_log']}, (Ton) {z[f'{n}_falsch_audio']}"
                  f" | Stimme gedämpft {len(dauer)}x{leiser}")
    if z.get("echo_laeufe"):
        print(f"  Echo        {z['echo_laeufe']} Läufe | Unterbrechungen durch Echo {z['echo_unterbrechungen']} | als Echo verworfen {z['echo_verworfen']}"
              f" | Stimme gedämpft ms je Lauf {z['echo_gedaempft_ms']} | Antwortdauer s {z['echo_antwort_s']}")
    if z.get("werkzeug_fuellsatz"):
        print(f"  werkzeug    Stille zwischen Füllsatz und Ergebnis ms {_verteilung(z['werkzeug_stille_ms'])} | Füllsatz vorab {_anteil(z['werkzeug_fuellsatz'])}")
    if z.get("schnell_antworten"):
        print(f"  schnell     Antworten je Doppelfrage {z['schnell_antworten']} | Überlappung ms {_verteilung(z['schnell_ueberlappung_ms'])}")
    for luecke in (1.5, 2.5):
        d = z.get(f"pause_dazwischen[{luecke}]")
        if d:
            print(f"  Pause {luecke} s WAMO redet dazwischen: {_anteil(d)}")


def lauf_analysieren(ordner: Path, lauf: str) -> dict[str, list[dict]]:
    j = json.loads((ordner / f"{lauf}.json").read_text(encoding="utf-8"))
    kern = kern_ereignisse(ordner / f"{lauf}.kern.log")
    daten: dict[str, list[dict]] = {}
    for sz in j["szenarien"]:
        _, bot = lade_wav(ordner / sz["bot"])
        _, mic = lade_wav(ordner / sz["mic"])
        daten[sz["name"]] = analysiere_szenario(sz, bot, mic, kern)
    return daten


def auswerten(ordner: Path, laeufe: list[str]) -> None:
    for lauf in laeufe:
        if not NAME_RE.match(lauf):
            sys.exit(f"Ungültiger Laufname: {lauf!r}")
        daten = lauf_analysieren(ordner, lauf)
        z = zusammenfassen(daten)
        (ordner / f"{lauf}.metriken.json").write_text(
            json.dumps({"kennzahlen": z, "wiederholungen": daten}, ensure_ascii=False, indent=1), encoding="utf-8")
        drucken(lauf, z)


def hoerprobe(ordner: Path, lauf: str, ziel: Path, label: str) -> None:
    """Drei Hörproben (10 bis 20 s): Einwurf mitten im Satz, Rückmeldelaut, Werkzeugaufruf. Mikrofon und das, was die Person
    hört (Ausgabestrom mal Lautstärke), gemischt in Mono."""
    if not NAME_RE.match(lauf) or not NAME_RE.match(label):
        sys.exit("lauf und label dürfen nur Buchstaben, Ziffern, Punkt, Unterstrich und Bindestrich enthalten.")
    j = json.loads((ordner / f"{lauf}.json").read_text(encoding="utf-8"))
    ziel.mkdir(parents=True, exist_ok=True)
    plan = {"unterbrechung": ("einwurf", "einwurf", -7.0, 11.0), "backchannel": ("backchannel", "backchannel", -6.0, 12.0),
            "werkzeug": ("werkzeug", "frage", -1.0, 17.0)}
    for name, (szenario, rolle, vor, nach) in plan.items():
        sz = next((s for s in j["szenarien"] if s["name"] == szenario), None)
        if sz is None:
            print(f"{name}: Szenario {szenario} fehlt im Lauf")
            continue
        rate, bot = lade_wav(ordner / sz["bot"])
        _, mic = lade_wav(ordner / sz["mic"])
        hoert = gehoert(bot, rate, sz["von"], sz["vol"])
        gueltig = {r["rep"] for r in sz["ergebnisse"] if r.get("ok")}
        cl = next((c for c in sz["events"] if c["typ"] == "clip" and c["rolle"] == rolle and c["rep"] in gueltig), None)
        if cl is None:
            print(f"{name}: keine gültige Wiederholung")
            continue
        t = cl["t0"] - sz["von"]
        a, b = max(0, int((t + vor) * rate)), min(len(hoert), int((t + nach) * rate))
        mix = hoert[a:b] + mic[a:b] * 0.8
        spitze = float(np.max(np.abs(mix))) or 1.0
        schreibe_wav(ziel / f"{label}-{name}.wav", mix * (0.9 / spitze), rate)
        print(f"{label}-{name}.wav: {(b - a) / rate:.1f} s")


def main() -> None:
    a = sys.argv
    if len(a) >= 3 and a[1] == "clips":
        clips(Path(a[2]))
    elif len(a) >= 4 and a[1] == "auswerten":
        auswerten(Path(a[2]), a[3:])
    elif len(a) >= 6 and a[1] == "hoerprobe":
        hoerprobe(Path(a[2]), a[3], Path(a[4]), a[5])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()


