"""Run the C++ physics core as a subprocess and return its fact sheet.

The process boundary is deliberate: the core has no network or file-write
access needs, so it can be sandboxed separately from the language-model side.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path


class CoreError(RuntimeError):
    pass


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def find_core(explicit: str | os.PathLike | None = None) -> Path:
    candidates = [explicit, os.environ.get("REA_CORE")]
    root = repo_root()
    candidates += [root / "build" / "core" / "rea-core", root / "build" / "rea-core", shutil.which("rea-core")]
    for c in candidates:
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            return Path(c)
    raise CoreError("rea-core binary not found: run `make core` or set REA_CORE=/path/to/rea-core")


def run_core(
    cfg: os.PathLike | str,
    settings: os.PathLike | str | None = None,
    *,
    core: os.PathLike | str | None = None,
    filter: str = "cosine",
    timeout_s: float = 60.0,
) -> tuple[dict, float]:
    """Return (fact-sheet JSON, wall-clock seconds)."""
    exe = find_core(core)
    cmd = [str(exe), "analyze", str(cfg), "--compact", "--filter", filter]
    if settings:
        cmd += ["--settings", str(settings)]
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
    elapsed = time.perf_counter() - t0
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise CoreError(proc.stderr.strip() or "rea-core produced no JSON") from exc
    if "error" in data:
        raise CoreError(data["error"])
    return data, elapsed
