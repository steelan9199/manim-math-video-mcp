"""Chinese font and ctex LaTeX configuration helpers."""

from __future__ import annotations

import os

CTEX_PREAMBLE = r"""
\usepackage[UTF8]{ctex}
\setCJKmainfont{STSong}
\setCJKsansfont{STHeiti}
\setCJKmonofont{STFangsong}
"""

MANIM_CHINESE_TEX_TEMPLATE = r"""\documentclass[preview]{standalone}
\usepackage[UTF8]{ctex}
\usepackage{amsmath}
\usepackage{amssymb}
\begin{document}
YourCodeHere
\end{document}
"""


def get_chinese_tex_template_code() -> str:
    """Return a Manim TexTemplate snippet for Chinese support."""
    return '''
from manim import TexTemplate

chinese_tex_template = TexTemplate()
chinese_tex_template.add_to_preamble(r"\\usepackage[UTF8]{ctex}")
'''


def _make_default_font_injection(font: str) -> str:
    """Build a snippet that makes every `Text()` / `MarkupText()` default to `font`.

    An explicit `font=` argument in the scene code still wins.
    """
    return (
        "# --- MAMCP default text font ---\n"
        "from manim import Text as _MAMCP_Text, MarkupText as _MAMCP_MarkupText\n"
        "_MAMCP_FONT = " + repr(font) + "\n"
        "def _mamcp_patch_font(_cls):\n"
        "    _orig = _cls.__init__\n"
        "    def _init(self, *a, **kw):\n"
        "        kw.setdefault('font', _MAMCP_FONT)\n"
        "        return _orig(self, *a, **kw)\n"
        "    _cls.__init__ = _init\n"
        "_mamcp_patch_font(_MAMCP_Text)\n"
        "_mamcp_patch_font(_MAMCP_MarkupText)\n"
    )


def inject_default_font(code: str, font: str | None = None) -> str:
    """Make `Text()` / `MarkupText()` default to `font` unless explicitly set.

    The font is taken from the MAMCP_FONT env var when `font` is not passed.
    Returns the code unchanged when no font is configured.
    """
    font = font if font is not None else os.environ.get("MAMCP_FONT", "")
    if not font:
        return code
    if "_mamcp_patch_font" in code:
        return code

    injection = _make_default_font_injection(font)

    lines = code.split('\n')
    insert_idx = 0
    for i, line in enumerate(lines):
        if line.startswith('from manim') or line.startswith('import manim'):
            insert_idx = i + 1
    lines.insert(insert_idx, injection)
    return '\n'.join(lines)


def inject_chinese_support(code: str) -> str:
    """Make every LaTeX-rendered mobject load ctex, so Chinese works in Tex/Title/MathTex.

    Sets ``config.tex_template`` -- the process-wide default that every ``Tex`` /
    ``Title`` / ``MathTex`` reads when no explicit ``tex_template=`` is passed
    (see manim ``mobject/text/tex_mobject.py``). Manim's stock template only
    loads ``\\usepackage[english]{babel}``, so any CJK codepoint inside a LaTeX
    string dies with ``latex error converting to dvi`` and zero output.

    Only injected when the scene actually contains CJK and has not already set
    ``config.tex_template`` itself. This function used to create a throwaway
    ``_zh_template`` variable that nothing ever consumed, so the ctex support it
    advertised never actually took effect.
    """
    has_chinese = any('\u4e00' <= ch <= '\u9fff' for ch in code)
    if not has_chinese:
        return code
    # Respect a template choice the scene already made.
    if "config.tex_template" in code:
        return code

    injection = (
        'config.tex_template = TexTemplateLibrary.ctex'
        '  # MAMCP: LaTeX Chinese support (CJK in Tex/Title/MathTex)\n'
    )

    lines = code.split('\n')
    insert_idx = 0
    for i, line in enumerate(lines):
        if line.startswith('from manim') or line.startswith('import manim'):
            insert_idx = i + 1
    lines.insert(insert_idx, injection)
    return '\n'.join(lines)
