"""
sound/then_vs_now.py — Earth Jukebox Sound Engine
Person B: Mix two Night Lights tracks (2012 vs 2023).

Left channel  = 2012 audio
Right channel = 2023 audio
Growth areas  = richer harmony (extra chord tones from the scale)
Strong differences = short, quiet dissonant accent (minor second / tritone)

Usage:
    python sound/then_vs_now.py --a data/features/night_lights_2012.json --b data/features/night_lights_2023.json --out out/then_vs_now.mp3
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_AUDIO_PKG = os.path.join(_REPO_ROOT, "earth_jukebox_audio")
if _AUDIO_PKG not in sys.path:
    sys.path.insert(0, _AUDIO_PKG)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import then_vs_now


def resolve_input(path: str) -> str:
    if os.path.exists(path):
        return path
    cand = os.path.join(_REPO_ROOT, path)
    if os.path.exists(cand):
        return cand
    if "data/" in path and "features" not in path:
        fp = path.replace("data/", "data/features/")
        if os.path.exists(fp):
            return fp
        cand_fp = os.path.join(_REPO_ROOT, fp)
        if os.path.exists(cand_fp):
            return cand_fp
    return path


if __name__ == "__main__":
    # Fix paths in argv if needed
    for i in range(1, len(sys.argv)):
        if sys.argv[i - 1] in ("--a", "-a", "--b", "-b"):
            sys.argv[i] = resolve_input(sys.argv[i])
    then_vs_now.main()
