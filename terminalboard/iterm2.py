"""iTerm2 inline-image protocol helpers.

Emits OSC 1337 ``File=`` sequences, with tmux/screen passthrough wrapping so an
image still renders when running inside a multiplexer.
"""
from __future__ import annotations

import base64
import os
import sys


def supports_inline_images() -> bool:
    """Best-effort detection of a terminal that speaks the iTerm2 protocol."""
    if os.environ.get("TERMINALBOARD_FORCE_HQ"):
        return True
    prog = os.environ.get("TERM_PROGRAM", "").lower()
    if any(p in prog for p in ("iterm", "wezterm", "vscode", "hyper")):
        return True
    # WezTerm/Konsole and others advertise themselves via these:
    if os.environ.get("WEZTERM_PANE") is not None:
        return True
    if os.environ.get("LC_TERMINAL", "").lower().startswith("iterm"):
        return True
    return False


def _wrap_for_multiplexer(seq: str) -> str:
    if os.environ.get("TMUX"):
        return "\033Ptmux;" + seq.replace("\033", "\033\033") + "\033\\"
    if os.environ.get("TERM", "").startswith("screen"):
        return "\033P" + seq.replace("\033", "\033\033") + "\033\\"
    return seq


def image_escape(
    png_bytes: bytes,
    *,
    name: str = "plot.png",
    width: str = "auto",
    height: str = "auto",
    preserve_aspect_ratio: bool = True,
) -> str:
    """Build the iTerm2 inline-image escape string for ``png_bytes``.

    ``width``/``height`` accept iTerm2 units: ``N`` (cells), ``Npx`` (pixels),
    ``N%`` (percent of the session), or ``auto``.
    """
    data = base64.b64encode(png_bytes).decode("ascii")
    b64name = base64.b64encode(name.encode()).decode("ascii")
    args = (
        f"name={b64name};size={len(png_bytes)};inline=1;"
        f"width={width};height={height};"
        f"preserveAspectRatio={1 if preserve_aspect_ratio else 0}"
    )
    seq = f"\033]1337;File={args}:{data}\a"
    return _wrap_for_multiplexer(seq)


def show_image(png_bytes: bytes, *, stream=None, **kwargs) -> None:
    out = stream or sys.stdout
    out.write(image_escape(png_bytes, **kwargs))
    out.write("\n")
    out.flush()
