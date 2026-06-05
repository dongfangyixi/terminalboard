"""Non-blocking single-key reader for the interactive live loop.

Puts the terminal in cbreak mode and reads one keypress at a time, with a
timeout so the loop can also wake up to refresh on its interval. A no-op
fallback is used when stdin is not a TTY (pipes, ``--once``, CI).
"""
from __future__ import annotations

import select
import sys
from typing import Optional


class KeyReader:
    def __init__(self):
        self._isatty = sys.stdin.isatty()
        self._fd = None
        self._saved = None

    def __enter__(self) -> "KeyReader":
        if not self._isatty:
            return self
        try:
            import termios
            import tty

            self._fd = sys.stdin.fileno()
            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        except Exception:
            self._isatty = False
        return self

    def __exit__(self, *exc) -> None:
        if self._saved is not None and self._fd is not None:
            import termios

            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)

    def get(self, timeout: float) -> Optional[str]:
        """Return one character if a key is pressed within ``timeout`` seconds."""
        if not self._isatty:
            if timeout > 0:
                import time

                time.sleep(timeout)
            return None
        r, _, _ = select.select([sys.stdin], [], [], timeout)
        if r:
            ch = sys.stdin.read(1)
            return ch
        return None
