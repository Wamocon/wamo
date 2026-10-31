"""Startet voice-core/bot.py für den Messweg so, dass die Werte der Messung eine gefundene .env nicht überschreibt.

bot.py lädt die .env mit override=True (sie wird vom Arbeitsordner aus nach oben gesucht, auch in einem Worktree). Die Messung
setzt aber das Profil des Testkontos (WAMO_CLAUDE_SCHLUESSEL_PROFIL) und die zu vergleichenden Schalter (KERN_ENV) über die
Umgebung des Kerns: ohne diesen Starter gewönne die .env, und die Messung liefe mit anderen Werten als angegeben.

Aufruf (aus voice-core/): python wamo_kern_messstart.py bot.py --port 7861 ...
Umgebung: WAMO_MESS_FEST = Namen (Komma), deren Wert aus der Umgebung nach jedem load_dotenv wieder gilt.
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

import dotenv

FEST = {n: os.environ[n] for n in (os.environ.get("WAMO_MESS_FEST") or "").split(",") if n and n in os.environ}
_ORIGINAL = dotenv.load_dotenv


def _laden(*args, **kwargs):
    if not args and "dotenv_path" not in kwargs:
        # wie in bot.py gesucht (Arbeitsordner voice-core), nicht ab dieser Datei
        args = (dotenv.find_dotenv(usecwd=True),)
    ergebnis = _ORIGINAL(*args, **kwargs)
    os.environ.update(FEST)
    return ergebnis


dotenv.load_dotenv = _laden

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    ziel = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(ziel.parent))
    sys.argv = [str(ziel), *sys.argv[2:]]
    runpy.run_path(str(ziel), run_name="__main__")
