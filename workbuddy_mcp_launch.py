"""WorkBuddy launcher for math-animation-mcp.

Starts the math-animation MCP server over stdio, forcing every child process
(notably the `python -m manim ...` sandbox call) to resolve to the shared
Python environment at D:\\software\\uv\\envs\\geo instead of whatever bare
`python` happens to be first on PATH.
"""

import os
import sys

SHARED_ENV_SCRIPTS = r"D:\software\uv\envs\geo\Scripts"
# uv's Scripts\python.exe is a 45KB trampoline that re-execs the base interpreter,
# creating a second process. WorkBuddy's sandbox appears to wedge that second
# CreateProcess, so the manim child hangs at 0 CPU and render times out.
# python_direct.exe is a copy of the real base interpreter placed next to
# pyvenv.cfg, so it still resolves to this venv but spawns only ONE process.
SHARED_PYTHON = os.path.join(SHARED_ENV_SCRIPTS, "python_direct.exe")
if not os.path.exists(SHARED_PYTHON):  # fallback to the trampoline
    SHARED_PYTHON = os.path.join(SHARED_ENV_SCRIPTS, "python.exe")
REPO_ROOT = r"D:\github\math-animation-mcp"

# Manim lives in the shared env; tell the sandbox to use it explicitly instead of
# relying on PATH resolution (WorkBuddy's sandbox rewrites PATH, so bare `python`
# resolves to the uv-managed interpreter that has no Manim).
os.environ["MAMCP_PYTHON"] = SHARED_PYTHON

# Make `python` resolve to the shared env for every child process we spawn.
os.environ["PATH"] = SHARED_ENV_SCRIPTS + os.pathsep + os.environ.get("PATH", "")
os.environ.setdefault("OUTPUT_DIR", os.path.join(REPO_ROOT, "animation_output"))

# Default CJK typeface for Text()/MarkupText() in generated scenes.
# Override with MAMCP_FONT="" to fall back to Manim's built-in default,
# or set any other installed family name (e.g. "Microsoft YaHei").
os.environ.setdefault("MAMCP_FONT", "LXGW WenKai GB")

# Keep Manim's scratch directory inside the project instead of the OS temp dir.
# The host sandbox path-virtualises %TEMP%, and the Manim child process wedges
# when its working directory lives behind that mapping.
os.environ.setdefault("MAMCP_TMP_DIR", os.path.join(REPO_ROOT, "_render_tmp"))

# Keep the repo importable even if the editable install is missing.
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

if os.environ.get("MAMCP_DEBUG") == "1":
    print(f"[launcher] python={sys.executable}", file=sys.stderr)
    print(f"[launcher] PATH={os.environ.get('PATH', '')[:300]}", file=sys.stderr)

from math_animation_mcp.server import run  # noqa: E402

if __name__ == "__main__":
    run()
