"""Abnahme der Gesprächsmodelle: 50 Gesprächszüge gegen Claude oder ein Modell hinter LiteLLM.

Der Lauf nutzt denselben Pipecat-Dienst wie das Gespräch (llm_anbieter.gespraech_dienst), dieselbe
Persona (persona.py), dieselben Werkzeugschemas und das Satzende-Protokoll von Pipecat. Werkzeuge sind
simuliert (faelle.ergebnis_fuer), es gibt keine Datenbank und keinen PC-Host. Die Züge laufen
nacheinander in einer Pipeline (warme Verbindung wie in einer Sitzung, ein ungewerteter Aufwärmzug vorweg),
mit kurzer Pause dazwischen, damit das Gateway andere Nutzer nicht ausbremst.

Gemessen je Zug (eval/messung.py): Werkzeugaufrufe, Marker, Format, Inhalt, erstes Token, erster
gesprochener Text, erstes Token nach einem Werkzeugergebnis, Tokens pro Sekunde und bei Claude die Kosten.

Aufruf im Ordner voice-core/:
    uv run python eval/abnahme.py --anbieter claude                              # Referenz (CLAUDE_MODEL_FAST)
    uv run python eval/abnahme.py --liste                                        # Modelle des Gateways
    uv run python eval/abnahme.py --anbieter litellm --modell <alias> --wiederholungen 3
    uv run python eval/abnahme.py --anbieter litellm --alle --wiederholungen 3   # alle Chat-Modelle
    uv run python eval/abnahme.py --bericht                                      # nur bericht.md neu schreiben

Ergebnisse: eval/ergebnisse/<zeit>_<anbieter>_<modell>.json je Lauf (nur anhängen, nie umschreiben) und
eval/ergebnisse/bericht.md mit der Freigabe nach pass^3. Beides enthält keine Schlüssel und keine Adressen.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import platform
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from importlib.metadata import version
from itertools import chain
from pathlib import Path

EVAL = Path(__file__).resolve().parent
VOICE_CORE = EVAL.parent
sys.path[:0] = [str(VOICE_CORE), str(EVAL)]

from dotenv import load_dotenv  # noqa: E402

load_dotenv(VOICE_CORE.parent / ".env")  # Werte aus der Shell haben Vorrang
load_dotenv(VOICE_CORE / ".env")

from loguru import logger  # noqa: E402

from pipecat.adapters.schemas.function_schema import FunctionSchema  # noqa: E402
from pipecat.adapters.schemas.tools_schema import ToolsSchema  # noqa: E402
from pipecat.frames.frames import InterruptionFrame, LLMContextFrame, LLMUpdateSettingsFrame  # noqa: E402
from pipecat.pipeline.pipeline import Pipeline  # noqa: E402
from pipecat.pipeline.worker import PipelineWorker  # noqa: E402
from pipecat.processors.aggregators.llm_context import LLMContext  # noqa: E402
from pipecat.processors.aggregators.llm_response_universal import LLMAssistantAggregator  # noqa: E402
from pipecat.services.llm_service import FunctionCallParams  # noqa: E402
from pipecat.services.settings import LLMSettings  # noqa: E402
from pipecat.turns.user_turn_completion_mixin import UserTurnCompletionConfig  # noqa: E402
from pipecat.workers.runner import WorkerRunner  # noqa: E402

import bericht  # noqa: E402
import bewertung  # noqa: E402
import faelle  # noqa: E402
from llm_anbieter import (  # noqa: E402
    LLMFehler,
    Wahl,
    Zugang,
    claude_standardmodell,
    gespraech_dienst,
    litellm_extra,
    ausblenden_text,
    ausgeblendet,
    litellm_modelle,
    litellm_problem,
    sichtbare_modelle,
)
from messung import Messung, Sammler, messen  # noqa: E402
from persona import build_system_prompt  # noqa: E402
from screen import SCHEMAS as BILDSCHIRM_SCHEMAS  # noqa: E402
from tools import CANVAS_SCHEMA, WEBSUCHE_SCHEMA, ZEIT_SCHEMA, merken_schema  # noqa: E402

ERGEBNISSE = EVAL / "ergebnisse"
PAUSE_SECS = 0.5
NACHLAUF_MAX_SECS = 2.0  # so lange höchstens warten, bis der Aggregator die letzte Antwort eingetragen hat
MAX_FEHLER_IN_FOLGE = 3
MAX_KOSTEN_USD = 0.5
# Nur ganze Namensteile, damit z. B. "democracy" nicht wegen "ocr" herausfällt
KEIN_CHAT = re.compile(r"(^|[-_:/.])(embed\w*|whisper|asr|tts|rerank\w*|ocr|speech|audio|transcri\w*)($|[-_:/.])",
                       re.IGNORECASE)
AUFWAERMEN = faelle.Fall("aufwaermen", "aufwaermen", "Hallo WAMO.")


# --- Testumgebung --------------------------------------------------------------------------------


def werkzeug_schemas() -> list[FunctionSchema]:
    pc = json.loads((EVAL / "pc_werkzeuge.json").read_text(encoding="utf-8"))
    pc_schemas = [FunctionSchema(w["name"], w["description"], w["properties"], w["required"]) for w in pc]
    return [ZEIT_SCHEMA, merken_schema(faelle.NAME), WEBSUCHE_SCHEMA, CANVAS_SCHEMA, *pc_schemas, *BILDSCHIRM_SCHEMAS]


def system_prompt() -> str:
    return build_system_prompt(
        faelle.NAME, faelle.ANREDE, faelle.PERSOENLICH, faelle.GEMEINSAM, pc_werkzeuge=True, entwicklung=False
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def aktuelle_hashes() -> dict:
    """Fingerabdrücke von Testsatz, Prompt (Persona und Werkzeuge) und Bewertung (Prüfungen und Messung)."""
    schemas = json.dumps([w.to_default_dict() for w in werkzeug_schemas()], sort_keys=True, ensure_ascii=False)
    return {
        "testsatz": faelle.testsatz_hash(),
        "prompt": _sha((system_prompt() + schemas).encode("utf-8")),
        "bewertung": _sha((EVAL / "bewertung.py").read_bytes() + (EVAL / "messung.py").read_bytes()),
        "pc_werkzeuge": _sha((EVAL / "pc_werkzeuge.json").read_bytes()),
    }


def _git() -> tuple[str, bool]:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=VOICE_CORE, capture_output=True,
                             text=True, timeout=5).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "."], cwd=VOICE_CORE, capture_output=True,
                               text=True, timeout=5).stdout.strip()
        return sha or "unbekannt", not dirty
    except (OSError, subprocess.SubprocessError):
        return "unbekannt", False


def stand() -> dict:
    sha, sauber = _git()
    return {
        "git": sha, "git_sauber": sauber, "python": platform.python_version(),
        "pipecat": version("pipecat-ai"), "anthropic": version("anthropic"), "openai": version("openai"),
        "messort": platform.node(),
    }


def konfiguration(llm, wahl: Wahl) -> dict:
    s = llm._settings
    if wahl.anbieter == "claude":
        thinking = s.thinking.model_dump(exclude_unset=True) if getattr(s, "thinking", None) else None
        return {"thinking": thinking, "prompt_caching": bool(s.enable_prompt_caching), "max_tokens": s.max_tokens,
                "sdk_wiederholungen": llm._client.max_retries, "temperatur": "Standard"}
    return {"extra": litellm_extra(), "sdk_wiederholungen": llm._client.max_retries,
            "timeout_s": llm._client.timeout.read, "temperatur": "Standard", "seed": "keiner"}


# --- Ein Lauf ------------------------------------------------------------------------------------


@dataclass
class Lauf:
    anbieter: str
    modell: str
    zuege: list[bewertung.ZugErgebnis] = field(default_factory=list)
    konfiguration: dict = field(default_factory=dict)
    abbruch: str | None = None


async def _nachlauf(context: LLMContext) -> None:
    """Wartet, bis sich der Kontext nicht mehr ändert (Aggregator fertig), höchstens NACHLAUF_MAX_SECS."""
    ende = time.monotonic() + NACHLAUF_MAX_SECS
    vorher = -1
    while time.monotonic() < ende:
        jetzt = len(context.get_messages())
        if jetzt == vorher:
            return
        vorher = jetzt
        await asyncio.sleep(0.2)


async def _zug(task, context, sammler, messungen, fall) -> tuple[float, list[Messung]]:
    messungen.clear()
    sammler.neuer_fall(fall)
    context.set_messages([*copy.deepcopy(list(fall.verlauf)), {"role": "user", "content": fall.eingabe}])
    start = time.perf_counter()
    await task.queue_frame(LLMContextFrame(context))
    try:
        await asyncio.wait_for(sammler.fertig.wait(), bewertung.ZUG_TIMEOUT_SECS)
    except TimeoutError:
        sammler.abbrechen("Zeitüberschreitung")
    await _nachlauf(context)
    dauer = time.perf_counter() - start
    sammler.fall = None
    await task.queue_frame(InterruptionFrame())  # Zustand der Satzende-Erkennung zurücksetzen
    return dauer, list(messungen)


async def lauf_ausfuehren(wahl: Wahl, faelle_liste: list[faelle.Fall], pause: float, max_kosten: float) -> Lauf:
    # Werkzeug des Betreibers: Claude läuft mit ANTHROPIC_API_KEY, nie mit dem Schlüssel eines Profils
    llm = gespraech_dienst(wahl, system_prompt(), Zugang.betreiber())
    messungen: list[Messung] = []
    messen(llm, wahl.anbieter, messungen)
    sammler = Sammler(llm)

    async def simuliert(params: FunctionCallParams):
        fall = sammler.fall
        ergebnis = faelle.ergebnis_fuer(fall, params.function_name, dict(params.arguments or {})) if fall else {}
        await params.result_callback(ergebnis)

    llm.register_function(None, simuliert)
    context = LLMContext(tools=ToolsSchema(standard_tools=werkzeug_schemas()))
    task = PipelineWorker(Pipeline([llm, LLMAssistantAggregator(context)]), observers=[sammler],
                          idle_timeout_secs=None)
    laeuft = asyncio.create_task(WorkerRunner(handle_sigint=False).run(task))
    laeuft.add_done_callback(lambda _t: sammler.abbrechen("Pipeline beendet"))
    # Satzende-Protokoll wie im Gespräch (LLMTurnCompletionUserTurnStopStrategy). Die Wartezeiten sind
    # lang, damit zwischen zwei Zügen keine Nachfrage nach einer Pause losläuft.
    await task.queue_frame(LLMUpdateSettingsFrame(delta=LLMSettings(
        filter_incomplete_user_turns=True,
        user_turn_completion_config=UserTurnCompletionConfig(
            incomplete_short_timeout=3600, incomplete_long_timeout=3600
        ),
    )))

    lauf = Lauf(wahl.anbieter, wahl.modell, konfiguration=konfiguration(llm, wahl))
    fehler_in_folge, kosten = 0, 0.0
    try:
        await _zug(task, context, sammler, messungen, AUFWAERMEN)  # Verbindung und Cache, nicht gewertet
        if sammler.fehler:
            lauf.abbruch = f"Aufwärmzug fehlgeschlagen: {sammler.fehler[0]}"
            print(f"  Abbruch: {lauf.abbruch}")
            return lauf
        await asyncio.sleep(pause)
        for fall in faelle_liste:
            dauer, gemessen = await _zug(task, context, sammler, messungen, fall)
            zug = zug_auswerten(fall, wahl.modell, sammler.antworten, sammler.fehler, gemessen, dauer)
            lauf.zuege.append(zug)
            kosten += zug.kosten_usd or 0.0
            zeichen = "ok" if zug.werkzeug_ok and zug.marker_ok else "FEHLER"
            ttft = f"{zug.ttft_s:.2f}s" if zug.ttft_s is not None else "-"
            gruende = "; ".join(chain.from_iterable(zug.gruende.values()))
            denken = " Denkphase" if zug.denkphase else ""
            print(f"  {fall.id:4} {zeichen:6} erstes Token {ttft:>6}  Eingabe {zug.eingabe_tokens:>6} Tokens{denken}  "
                  f"{gruende[:100]}")
            if zeichen != "ok" and zug.antworten:
                print(f"       letzte Antwort: {ausblenden_text(zug.antworten[-1][:100])!r}")
            fehler_in_folge = fehler_in_folge + 1 if zug.infrastruktur else 0
            if fehler_in_folge >= MAX_FEHLER_IN_FOLGE:
                lauf.abbruch = f"{MAX_FEHLER_IN_FOLGE} Fehler in Folge ({zug.infrastruktur[0]})"
                break
            if kosten > max_kosten:
                lauf.abbruch = f"Kostengrenze {max_kosten:.2f} USD erreicht"
                break
            await asyncio.sleep(pause)
    finally:
        await task.cancel()
        try:
            await asyncio.wait_for(laeuft, 10)
        except TimeoutError:
            logger.warning("Pipeline hat sich nicht rechtzeitig beendet")
        except asyncio.CancelledError:
            if (aktuell := asyncio.current_task()) is not None and aktuell.cancelling():
                raise  # echter Abbruch (Strg+C) wird nicht verschluckt
    if lauf.abbruch:
        print(f"  Abbruch: {lauf.abbruch}")
    return lauf


def zug_auswerten(
    fall: faelle.Fall, modell: str, antworten: list[bewertung.Antwortteil], fehler: list[str],
    messungen: list[Messung], dauer: float,
) -> bewertung.ZugErgebnis:
    urteil = bewertung.zug_bewerten(fall, antworten, fehler)
    erste = messungen[0] if messungen else None
    summe = {k: sum(getattr(m, k) for m in messungen) for k in ("eingabe", "ausgabe", "cache_lesen", "cache_schreiben")}
    kosten = bewertung.kosten_usd(modell, **summe) if messungen else None
    kalt = bewertung.kosten_usd(modell, summe["eingabe"] + summe["cache_lesen"] + summe["cache_schreiben"],
                                summe["ausgabe"], 0, 0) if messungen else None
    gen = [m.gen for m in messungen]
    return bewertung.ZugErgebnis(
        id=fall.id,
        kategorie=fall.kategorie,
        eingabe=fall.eingabe,
        mit_werkzeug=fall.mit_werkzeug,
        muss=fall.muss,
        aufrufe=[{"name": n, "args": a} for t in antworten for n, a in t.aufrufe],
        antworten=[t.raw for t in antworten],
        werkzeug_ok=urteil.werkzeug_ok,
        marker_ok=urteil.marker_ok,
        format_ok=urteil.format_ok,
        inhalt_ok=urteil.inhalt,
        gruende={k: v for k, v in (("werkzeug", urteil.werkzeug), ("marker", urteil.marker),
                                   ("format", urteil.format)) if v},
        infrastruktur=urteil.infrastruktur,
        ttft_s=round(erste.ttft, 3) if erste and erste.ttft is not None else None,
        ttfat_s=round(erste.ttfat, 3) if erste and erste.ttfat is not None else None,
        ttft_folge_s=[round(m.ttft, 3) for m in messungen[1:] if m.ttft is not None],
        gen_tokens=sum(t for t, _ in gen),
        gen_s=round(sum(s for _, s in gen), 3),
        denkphase=any(m.t_denken is not None or m.denken_tokens for m in messungen),
        eingabe_tokens=summe["eingabe"],
        ausgabe_tokens=summe["ausgabe"],
        cache_lesen=summe["cache_lesen"],
        cache_schreiben=summe["cache_schreiben"],
        kosten_usd=round(kosten, 6) if kosten is not None else None,
        kosten_kalt_usd=round(kalt, 6) if kalt is not None else None,
        modell_bedient=ausblenden_text(next((m.modell for m in messungen if m.modell), "")),
        messfehler=sum(m.fehler for m in messungen),
        dauer_s=round(dauer, 2),
    )


# --- Ablage --------------------------------------------------------------------------------------


def speichern(lauf: Lauf, k: dict, hashes: dict, notiz: str = "") -> Path:
    ERGEBNISSE.mkdir(parents=True, exist_ok=True)
    jetzt = datetime.now().astimezone()
    sicher = re.sub(r"[^a-z0-9.-]+", "-", lauf.modell.lower()).strip("-")
    pfad = ERGEBNISSE / f"{jetzt:%Y-%m-%d_%H%M%S}_{lauf.anbieter}_{sicher}.json"
    daten = {
        "zeitpunkt": jetzt.isoformat(timespec="seconds"),
        "anbieter": lauf.anbieter,
        "modell": lauf.modell,
        "notiz": notiz,
        "stand": stand(),
        "hashes": hashes,
        "testsatz": {"zuege": len(faelle.FAELLE), "mit_werkzeug": sum(f.mit_werkzeug for f in faelle.FAELLE),
                     "muss": sum(f.muss for f in faelle.FAELLE), "hash": hashes["testsatz"]},
        "konfiguration": lauf.konfiguration,
        "schwellen": {"werkzeug": bewertung.FREIGABE_WERKZEUG, "marker": bewertung.FREIGABE_MARKER,
                      "ttft_p95_s": bewertung.FREIGABE_TTFT_P95, "laeufe": bewertung.FREIGABE_LAEUFE},
        "abbruch": lauf.abbruch,
        "kennzahlen": k,
        "zuege": [asdict(z) for z in lauf.zuege],
    }
    text = ausblenden_text(json.dumps(daten, ensure_ascii=False, indent=1))  # auch in Modellantworten
    json.loads(text)  # das Ersetzen darf die Datei nicht beschädigen
    pfad.write_text(text + "\n", encoding="utf-8")
    return pfad


# --- Kommandozeile -------------------------------------------------------------------------------


async def _modelle_im_haus() -> list[str]:
    try:
        return sichtbare_modelle(await litellm_modelle(timeout=10))  # WAMO_LLM_AUSBLENDEN
    except LLMFehler as e:
        raise SystemExit(f"LiteLLM: {e}") from None


async def main() -> None:
    p = argparse.ArgumentParser(description="Abnahme der Gesprächsmodelle von WAMO")
    p.add_argument("--anbieter", choices=("claude", "litellm"))
    p.add_argument("--modell", help="Modellname (Claude-ID oder LiteLLM-Alias)")
    p.add_argument("--alle", action="store_true", help="alle Chat-Modelle des Gateways nacheinander")
    p.add_argument("--liste", action="store_true", help="nur die Modellnamen des Gateways ausgeben")
    p.add_argument("--bericht", action="store_true", help="nur bericht.md aus den gespeicherten Läufen schreiben")
    p.add_argument("--faelle", help="nur diese Züge, kommagetrennt (z. B. z01,p01); Probelauf ohne Ablage")
    p.add_argument("--wiederholungen", type=int, default=1, help="Läufe je Modell (Freigabe braucht 3)")
    p.add_argument("--pause", type=float, default=PAUSE_SECS, help="Pause zwischen zwei Zügen in Sekunden")
    p.add_argument("--max-kosten", type=float, default=MAX_KOSTEN_USD, help="Abbruch eines Claude-Laufs über "
                   "dieser Summe in USD")
    p.add_argument("--notiz", default="", help="kurze Notiz zum Lauf, z. B. welche Prompt-Fassung")
    args = p.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Marker und Umlaute in der Windows-Konsole
    logger.remove()
    logger.configure(patcher=lambda r: r.update(message=ausblenden_text(r["message"])))  # auch Pipecat-Meldungen
    logger.add(sys.stderr, level="WARNING")
    hashes = aktuelle_hashes()

    if args.liste:
        for name in await _modelle_im_haus():
            print(name)
        return
    if args.bericht:
        print(bericht.bericht_schreiben(ERGEBNISSE, hashes))
        return
    if not args.anbieter:
        p.error("--anbieter fehlt")

    if args.anbieter == "claude":
        modelle = [args.modell or claude_standardmodell("gespraech")]
        if (unbekannt := [m for m in modelle if m not in bewertung.PREISE_USD]):
            raise SystemExit(f"Kein Preis für {unbekannt[0]} in bewertung.PREISE_USD: Kostengrenze nicht prüfbar")
    elif args.alle:
        alle = await _modelle_im_haus()
        modelle = [m for m in alle if not KEIN_CHAT.search(m)]
        if ausgelassen := sorted(set(alle) - set(modelle)):
            print(f"Ausgelassen (kein Chat-Modell): {', '.join(ausgelassen)}")
    elif args.modell:
        if ausgeblendet(args.modell):
            raise SystemExit("Dieses Modell ist über WAMO_LLM_AUSBLENDEN ausgeblendet")
        modelle = [args.modell]
    else:
        p.error("--modell oder --alle fehlt")

    auswahl = list(faelle.FAELLE)
    if args.faelle:
        ids = {i.strip() for i in args.faelle.split(",")}
        auswahl = [f for f in faelle.FAELLE if f.id in ids]
    for modell in modelle:
        if args.anbieter == "litellm" and (problem := await litellm_problem(modell)):
            print(f"{modell}: übersprungen ({problem})")
            continue
        for runde in range(1, max(1, args.wiederholungen) + 1):
            print(f"{args.anbieter} {modell}: {len(auswahl)} Züge, Lauf {runde}")
            try:
                lauf = await lauf_ausfuehren(Wahl("gespraech", args.anbieter, modell, args.anbieter), auswahl,
                                             args.pause, args.max_kosten)
            except Exception as e:  # ein kaputtes Modell soll die übrigen nicht aufhalten
                print(f"  Fehler im Lauf: {bewertung.maskieren(f'{type(e).__name__}: {e}')[:200]}")
                break
            k = bewertung.kennzahlen(lauf.zuege, len(auswahl))
            print(json.dumps({x: k[x] for x in ("werkzeug_quote", "marker_quote", "format_quote", "muss_fehler",
                                                "ttft_p50_s", "ttft_p95_s", "ttfat_p95_s", "tokens_pro_s",
                                                "denkphase_zuege", "kosten_usd", "freigabe")}, ensure_ascii=False))
            if not args.faelle:
                print(speichern(lauf, k, hashes, args.notiz))
            if lauf.abbruch:
                break
    if not args.faelle:
        print(bericht.bericht_schreiben(ERGEBNISSE, hashes))


if __name__ == "__main__":
    asyncio.run(main())
