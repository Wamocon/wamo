"""Sicherheit: Token (Hash, Zufall, konstante Zeit), Schutzköpfe, Protokoll nur mit Metadaten."""
from __future__ import annotations

import hashlib
import json
import re

from hilfen import KERN_TOKEN, Uhr

from wamo_labor_dienst.protokoll import Protokoll
from wamo_labor_dienst.sicherheit import PLATZ_TOKEN_RE, SICHERHEITSKOEPFE, gleich, neuer_platz_token, token_hash


def test_platz_token_hat_128_bit_zufall_und_ist_jedes_mal_anders():
    tokens = {neuer_platz_token() for _ in range(200)}
    assert len(tokens) == 200 and all(PLATZ_TOKEN_RE.fullmatch(t) and len(t) == 32 for t in tokens)
    assert all(re.fullmatch(r"[A-Za-z0-9._~+/=-]{32,200}", t) for t in tokens)  # so akzeptiert es das Abnahmeskript


def test_token_hash_ist_sha256_hex():
    assert token_hash("abc") == hashlib.sha256(b"abc").hexdigest()
    assert token_hash("abc") != token_hash("abd")


def test_gleich_vergleicht_ueber_den_hash_in_fester_laenge():
    digest = hashlib.sha256(KERN_TOKEN.encode()).digest()
    assert gleich(KERN_TOKEN, digest) is True
    for falsch in ("", KERN_TOKEN + "x", KERN_TOKEN[:-1], KERN_TOKEN.upper(), "ä" * 40):
        assert gleich(falsch, digest) is False


def test_schutzkoepfe_nach_d4():
    k = {n.lower(): v for n, v in SICHERHEITSKOEPFE.items()}
    assert "default-src 'self'" in k["content-security-policy"] and "frame-ancestors 'none'" in k["content-security-policy"]
    assert "unsafe-inline" not in k["content-security-policy"] and "unsafe-eval" not in k["content-security-policy"]
    assert k["x-content-type-options"] == "nosniff" and k["referrer-policy"] == "no-referrer" and k["cache-control"] == "no-store"


def test_jede_antwort_traegt_die_schutzkoepfe_auch_404_405_und_500(client):
    for r in (client.get("/gesundheit"), client.get("/nix"), client.post("/gesundheit"), client.get("/api/kern/durchlauf")):
        for name, wert in SICHERHEITSKOEPFE.items():
            assert r.headers[name] == wert, (r.status_code, name)


# ---------- Protokoll ----------
def test_protokoll_schreibt_json_zeilen_mit_zeit_und_art(tmp_path):
    p = Protokoll(tmp_path / "log", Uhr())
    p.ereignis("anfrage", bereich="kern", status=200, platz=3, ms=1.5, text=None)
    p.ereignis("betreuer", aktion="durchlauf_beenden", platz=3)
    p.abwarten()
    zeilen = [json.loads(z) for z in (tmp_path / "log" / "dienst.jsonl").read_text(encoding="utf-8").splitlines()]
    assert zeilen[0] == {"zeit": "2026-11-28T09:30:00Z", "art": "anfrage", "bereich": "kern", "status": 200, "platz": 3, "ms": 1.5, "text": None}
    assert zeilen[1]["aktion"] == "durchlauf_beenden"


def test_protokoll_nimmt_nur_einfache_werte_und_kuerzt_lange_texte(tmp_path):
    p = Protokoll(tmp_path, Uhr())
    p.ereignis("x", liste=[1, 2], objekt={"a": 1}, lang="a" * 500, ok=True)
    p.abwarten()
    e = json.loads((tmp_path / "dienst.jsonl").read_text(encoding="utf-8"))
    assert "liste" not in e and "objekt" not in e and len(e["lang"]) <= 80 and e["ok"] is True


def test_protokoll_rotiert_bei_der_grenze_und_leeren_loescht_alles(tmp_path):
    p = Protokoll(tmp_path, Uhr(), max_bytes=500)
    for i in range(30):
        p.ereignis("anfrage", nr=i)
    p.abwarten()
    dateien = sorted(f.name for f in tmp_path.iterdir())
    assert dateien == ["dienst.jsonl", "dienst.jsonl.1"] and (tmp_path / "dienst.jsonl").stat().st_size <= 600
    p.leeren()
    assert list(tmp_path.iterdir()) == []


def test_protokoll_scheitert_nie_laut(tmp_path):
    blockiert = tmp_path / "datei"
    blockiert.write_text("x", encoding="utf-8")
    p = Protokoll(blockiert / "unter", Uhr())
    p.ereignis("anfrage", status=200)  # der Ordner lässt sich nicht anlegen: kein Absturz
    p.abwarten()
