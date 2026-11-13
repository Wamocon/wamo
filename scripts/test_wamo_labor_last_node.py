"""Startet die Node-Tests des Browser-Workers (scripts/wamo-last-browser.test.cjs) im Rahmen von pytest.

Aufruf (aus scripts, mit der Python-Umgebung von voice-core):
  D:\IDEA\Projekt\wamo\voice-core\.venv\Scripts\python.exe -m pytest -q test_wamo_labor_last_node.py

Ohne Node auf dem Rechner wird der Test übersprungen (nicht bestanden: Er meldet es als übersprungen).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

TESTDATEI = Path(__file__).resolve().parent / "wamo-last-browser.test.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node ist nicht installiert")
def test_node_tests_des_browser_workers_sind_gruen():
    r = subprocess.run(["node", "--test", str(TESTDATEI)], capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
