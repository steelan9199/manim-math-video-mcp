"""Secure sandbox for executing Manim code via subprocess with timeout control."""

from __future__ import annotations

import os
import subprocess
import tempfile
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RenderResult:
    success: bool
    file_path: str = ""
    error_msg: str = ""
    duration_seconds: float = 0.0
    stdout: str = ""
    stderr: str = ""


def _find_output_video(media_dir: Path, scene_name: str) -> str | None:
    """Walk media_dir looking for the rendered video file."""
    videos_dir = media_dir / "videos"
    if not videos_dir.exists():
        return None
    for root, _dirs, files in os.walk(videos_dir):
        for f in files:
            if f.endswith((".mp4", ".gif", ".webm")) and scene_name in f:
                return os.path.join(root, f)
    # Fallback: return any video found
    for root, _dirs, files in os.walk(videos_dir):
        for f in files:
            if f.endswith((".mp4", ".gif", ".webm")):
                return os.path.join(root, f)
    return None


def _resolve_python_bin() -> str:
    """Pick the interpreter used to run Manim.

    Resolution order:
      1. MAMCP_PYTHON env var (explicit override, recommended when Manim lives
         in a shared environment outside this repo).
      2. A virtualenv inside the repo: .venv/bin/python (POSIX) or
         .venv/Scripts/python.exe (Windows).
      3. Bare "python" resolved from PATH.
    """
    override = os.environ.get("MAMCP_PYTHON")
    if override and os.path.exists(override):
        return override

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    for candidate in (
        os.path.join(repo_root, ".venv", "bin", "python"),
        os.path.join(repo_root, ".venv", "Scripts", "python.exe"),
    ):
        if os.path.exists(candidate):
            return candidate
    return "python"


def render_manim_code(
    code: str,
    *,
    quality: str = "medium",
    fmt: str = "mp4",
    output_dir: str = "./animation_output",
    timeout: int = 120,
    python_bin: str | None = None,
) -> RenderResult:
    """Execute Manim code in a subprocess and return the result."""
    quality_flags = {
        "low": "-ql",
        "medium": "-qm",
        "high": "-qh",
        "4k": "-qk",
    }
    qflag = quality_flags.get(quality, "-qm")

    fmt_flag = {"mp4": "", "gif": "--format=gif", "webm": "--format=webm"}
    fflag = fmt_flag.get(fmt, "")

    scene_name = _extract_scene_name(code)
    if not scene_name:
        return RenderResult(success=False, error_msg="No Scene subclass found in code")

    os.makedirs(output_dir, exist_ok=True)
    output_dir = os.path.abspath(output_dir)

    # Work root: prefer an explicit project-local directory (MAMCP_TMP_DIR).
    # The OS temp dir is path-virtualised by some host sandboxes, which makes the
    # spawned Manim process wedge before it produces any output.
    work_root = os.environ.get("MAMCP_TMP_DIR") or None
    if work_root:
        try:
            os.makedirs(work_root, exist_ok=True)
        except Exception:
            work_root = None
    if work_root:
        tmpdir = tempfile.mkdtemp(prefix="manim_render_", dir=work_root)
    else:
        tmpdir = tempfile.mkdtemp(prefix="manim_render_")

    script_path = os.path.join(tmpdir, "scene.py")
    media_dir = os.path.join(tmpdir, "media")
    child_log_path = os.path.join(output_dir, f"_render_{scene_name}.log")
    spawn_log_path = os.path.join(output_dir, "_render_spawn.log")

    try:
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code)

        if python_bin is None:
            python_bin = _resolve_python_bin()

        cmd = [
            python_bin, "-m", "manim", "render",
            qflag,
            "--media_dir", media_dir,
        ]
        if fflag:
            cmd.append(fflag)
        cmd.extend([script_path, scene_name])

        # --- diagnostics -------------------------------------------------
        t_start = time.time()
        try:
            with open(spawn_log_path, "a", encoding="utf-8") as sf:
                sf.write(
                    "%s | SPAWN | cwd=%s | python=%s (exists=%s) | stdin=DEVNULL\n"
                    % (time.strftime("%H:%M:%S"), tmpdir, python_bin,
                       os.path.exists(python_bin))
                )
                sf.flush()
        except Exception:
            pass

        # Child stdout/stderr go to a log file (not a pipe). A pipe held open by
        # ffmpeg/latex grandchildren is a classic source of deadlocks, and this
        # also lets us watch progress while the render runs.
        returncode = None
        timed_out = False
        with open(child_log_path, "w", encoding="utf-8", errors="replace") as cf:
            cf.write("CMD: %s\nCWD: %s\n\n" % (cmd, tmpdir))
            cf.flush()
            proc = subprocess.Popen(
                cmd,
                stdout=cf,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                cwd=tmpdir,
            )
            try:
                returncode = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                alive = proc.poll() is None
                try:
                    cf.write("\n[watchdog] timeout after %.1fs, child alive=%s\n"
                             % (time.time() - t_start, alive))
                    cf.flush()
                except Exception:
                    pass
                proc.kill()
                try:
                    proc.wait(timeout=10)
                except Exception:
                    pass

        try:
            with open(spawn_log_path, "a", encoding="utf-8") as sf:
                sf.write(
                    "%s | %s | elapsed=%.1fs | rc=%s\n"
                    % (time.strftime("%H:%M:%S"),
                       "TIMEOUT" if timed_out else "DONE",
                       time.time() - t_start, returncode)
                )
                sf.flush()
        except Exception:
            pass

        try:
            with open(child_log_path, "r", encoding="utf-8", errors="replace") as cf:
                child_out = cf.read()
        except Exception:
            child_out = ""

        if timed_out:
            return RenderResult(
                success=False,
                error_msg=f"Render timed out after {timeout}s",
                stdout=child_out[-1000:],
                stderr=child_out[-2000:],
            )

        if returncode != 0:
            return RenderResult(
                success=False,
                error_msg=child_out[-2000:] if child_out else "Unknown render error",
                stdout=child_out[-1000:],
                stderr=child_out[-2000:],
            )

        video_path = _find_output_video(Path(media_dir), scene_name)
        if not video_path:
            return RenderResult(
                success=False,
                error_msg="Render succeeded but no output file found",
                stdout=child_out[-1000:],
                stderr=child_out[-1000:],
            )

        final_name = f"{scene_name}.{fmt}"
        final_path = os.path.join(output_dir, final_name)
        counter = 1
        while os.path.exists(final_path):
            final_path = os.path.join(output_dir, f"{scene_name}_{counter}.{fmt}")
            counter += 1

        shutil.copy2(video_path, final_path)

        return RenderResult(
            success=True,
            file_path=os.path.abspath(final_path),
            stdout=child_out[-500:],
            stderr=child_out[-500:],
        )

    except subprocess.TimeoutExpired:
        return RenderResult(success=False, error_msg=f"Render timed out after {timeout}s")
    except Exception as e:
        return RenderResult(success=False, error_msg=str(e))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _extract_scene_name(code: str) -> str | None:
    """Extract the first Scene subclass name from code."""
    import re
    match = re.search(r'class\s+(\w+)\s*\(\s*\w*Scene\s*\)', code)
    if match:
        return match.group(1)
    match = re.search(r'class\s+(\w+)\s*\(\s*Scene\s*\)', code)
    if match:
        return match.group(1)
    # Fallback: any class that likely extends a Manim base
    match = re.search(r'class\s+(\w+)\s*\(', code)
    if match:
        return match.group(1)
    return None
