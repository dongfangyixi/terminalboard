"""Flicker-free terminal painter.

The naive "clear the whole screen, then print" approach flashes badly: every
frame the terminal goes blank and then fills back in. This avoids that:

  * draw on the **alternate screen buffer** and hide the cursor;
  * each frame, move the cursor **home and overwrite in place** (clearing to the
    end of each line and below the frame) instead of blanking first;
  * wrap each repaint in **synchronized output** (DEC private mode 2026) so the
    terminal presents the whole frame atomically — no tearing, no flash.

All of these degrade gracefully: unsupported sequences are simply ignored, and
on a non-TTY the painter just writes plain text.
"""
from __future__ import annotations

import sys

_ALT_ON = "\033[?1049h"
_ALT_OFF = "\033[?1049l"
_HIDE = "\033[?25l"
_SHOW = "\033[?25h"
_SYNC_ON = "\033[?2026h"
_SYNC_OFF = "\033[?2026l"
_HOME = "\033[H"
_CLEAR_EOL = "\033[K"     # erase to end of line
_CLEAR_BELOW = "\033[J"   # erase from cursor to end of screen


class Screen:
    def __init__(self, use_alt: bool = True):
        self._tty = sys.stdout.isatty()
        self.use_alt = use_alt and self._tty

    def __enter__(self) -> "Screen":
        if self._tty:
            seq = (_ALT_ON if self.use_alt else "") + _HIDE
            sys.stdout.write(seq)
            sys.stdout.flush()
        return self

    def __exit__(self, *exc) -> None:
        if self._tty:
            seq = _SHOW + (_ALT_OFF if self.use_alt else "")
            sys.stdout.write(seq)
            sys.stdout.flush()

    def draw(self, frame: str) -> None:
        """Repaint the screen with ``frame`` in place, without flashing."""
        if not self._tty:
            sys.stdout.write(frame + "\n")
            sys.stdout.flush()
            return
        # Clear to end of each line so a shorter new line can't leave stale
        # characters behind; clear below to drop any now-unused trailing rows.
        body = frame.replace("\n", _CLEAR_EOL + "\n")
        out = _SYNC_ON + _HOME + body + _CLEAR_EOL + _CLEAR_BELOW + _SYNC_OFF
        sys.stdout.write(out)
        sys.stdout.flush()
