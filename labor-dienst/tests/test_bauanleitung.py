"""Bauanleitung des Images (Befund 10 des Sicherheitsreviews): kein wanderndes Standard-Tag, Nutzer ohne root, feste Versionen."""
from __future__ import annotations

import re
from pathlib import Path

ORDNER = Path(__file__).resolve().parents[1]


def _dockerfile() -> str:
    return (ORDNER / "Dockerfile").read_text(encoding="utf-8")


def test_basisimage_hat_keinen_standardwert_der_digest_kommt_beim_bau():
    text = _dockerfile()
    assert re.search(r"^ARG BASIS\s*$", text, re.M), "ARG BASIS ohne Standardwert"
    assert re.search(r"^FROM \$\{BASIS\}\s*$", text, re.M)
    assert not re.search(r"^FROM\s+(?!\$\{BASIS\})", text, re.M), "kein festes Image im FROM"
    assert not re.search(r"^ARG BASIS=", text, re.M) and "latest" not in text.lower()
    assert "@sha256:" in text  # der Hinweis zum Bau nennt den Digest


def test_die_demo_kommt_nicht_ins_produktionsimage():
    """Befund der Prüfung des ausgelieferten Images (REA): wamo_labor_dienst/demo.py (Vorführung mit Wegwerf-Daten) lag im Image. Sie
    wird nur lokal gebraucht und bleibt draußen (.dockerignore), nichts im Paket importiert sie."""
    zeilen = (ORDNER / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "wamo_labor_dienst/demo.py" in zeilen
    for quelle in (ORDNER / "wamo_labor_dienst").glob("*.py"):
        if quelle.name != "demo.py":
            assert not re.search(r"\bdemo\b", quelle.read_text(encoding="utf-8")), quelle.name


def test_nutzer_ohne_root_nur_data_beschreibbar():
    text = _dockerfile()
    assert re.search(r"^USER 10001:10001\s*$", text, re.M) and not re.search(r"^USER\s+(root|0)\b", text, re.M)
    assert re.search(r'^VOLUME \["/data"\]', text, re.M) and "PYTHONDONTWRITEBYTECODE=1" in text
    assert "pip install" in text and text.index("USER 10001") > text.index("pip install")  # installiert wird beim Bau, nie zur Laufzeit


def test_requirements_sind_fest_gepinnt_und_alle_mit_hashes():
    text = (ORDNER / "requirements.txt").read_text(encoding="utf-8")
    eintraege = [e for e in re.sub(r"\\\n\s*", " ", text).splitlines() if e.strip() and not e.lstrip().startswith("#")]
    assert len(eintraege) >= 14
    for e in eintraege:
        assert re.match(r"[A-Za-z0-9_.-]+==[0-9][A-Za-z0-9_.+-]*(\s+--hash=sha256:[0-9a-f]{64})+$", e.strip()), e[:80]
    assert "--require-hashes" in _dockerfile()


def test_readme_nennt_das_bauen_mit_digest_und_die_hashes_der_abhaengigkeiten():
    text = (ORDNER / "README.md").read_text(encoding="utf-8")
    assert "--build-arg BASIS=" in text and "--generate-hashes" in text and "--require-hashes" in text


def test_pip_installiert_nur_fertige_pakete_keine_bauskripte_aus_quellpaketen():
    pip = [z for z in _dockerfile().splitlines() if "pip install" in z]
    assert pip and all("--only-binary=:all:" in z and "--require-hashes" in z for z in pip), pip


def test_readme_nennt_die_betriebshinweise_zu_503_und_log_groesse():
    text = (ORDNER / "README.md").read_text(encoding="utf-8")
    assert "limit_concurrency" in text and "Klartext" in text and "Caddy" in text
    assert "max-size" in text


def test_readme_nennt_die_restrisiken_ehrlich():
    text = (ORDNER / "README.md").read_text(encoding="utf-8")
    treffer = re.search(r"^## Bekannte Restrisiken(.*?)(?=^## )", text, re.S | re.M)
    assert treffer, "Abschnitt Bekannte Restrisiken"
    abschnitt = treffer.group(1)
    for aussage in ("SQLite-Ebene", "Volume-Snapshots", "Hostsicherungen", "weiterlaufen", "Volume löschen", "Uhrabhängigkeit", "Generalschlüssel",
                    "5 MB", "Neustart", "durchlauf-beenden", "zuruecksetzen", "nicht gesperrt", "entzieht den Token", "Einwilligungsmaximum", "Rücksetzweg"):
        assert aussage in abschnitt, aussage
