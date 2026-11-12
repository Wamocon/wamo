"""Die reinen Funktionen der Oberflächen (Gruppieren, Rücknahme, Fragment, Einwilligung, DOM-Helfer) laufen mit node --test (Node, keine Pakete)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ORDNER = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node ist nicht installiert")
def test_node_tests_der_oberflaechen_sind_gruen():
    r = subprocess.run(["node", "--test", "--test-reporter=tap", "tests/js"], cwd=ORDNER, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-1500:]
    assert "# fail 0" in r.stdout and "# pass" in r.stdout
