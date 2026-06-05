"""The interactive live dashboard loop."""
from __future__ import annotations

import fnmatch
import shutil
import sys
import time
from typing import List, Optional

from .keys import KeyReader
from .reader import BaseReader
from .render import Renderer, grid_dims
from .screen import Screen


class App:
    def __init__(
        self,
        reader: BaseReader,
        renderer: Renderer,
        *,
        tag_filter: Optional[str] = None,
        smooth: float = 0.6,
        cols: int = 3,
        rows: int = 2,
        interval: float = 2.0,
    ):
        self.reader = reader
        self.renderer = renderer
        self.tag_filter = tag_filter
        self.smooth = smooth
        self.cols = max(1, cols)
        self.rows = max(1, rows)
        self.interval = interval
        self.page = 0

    # -- tag selection -------------------------------------------------------

    def _matching_tags(self) -> List[str]:
        tags = self.reader.all_tags()
        if self.tag_filter:
            patterns = [p.strip() for p in self.tag_filter.split(",") if p.strip()]
            tags = [t for t in tags if any(fnmatch.fnmatch(t, p) for p in patterns)]
        return tags

    def _page_tags(self, tags: List[str]):
        per_page = self.cols * self.rows
        if per_page <= 0:
            return tags, 1
        n_pages = max(1, (len(tags) + per_page - 1) // per_page)
        # Clamp (don't wrap): paging past either end stays on the edge page.
        self.page = max(0, min(self.page, n_pages - 1))
        start = self.page * per_page
        return tags[start:start + per_page], n_pages

    # -- rendering -----------------------------------------------------------

    def _header(self, tags: List[str], page_tags: List[str], n_pages: int) -> str:
        n_runs = len(self.reader.runs)
        total_pts = sum(
            len(s) for run in self.reader.runs.values() for s in run.series.values()
        )
        flt = self.tag_filter or "*"
        return (
            f"\033[1mterminalboard\033[0m  "
            f"runs={n_runs}  tags={len(tags)} (filter: {flt})  "
            f"page {self.page + 1}/{n_pages}  "
            f"smooth={self.smooth:.2f}  mode={self.renderer.name}  pts={total_pts}"
        )

    def _footer(self) -> str:
        return (
            "\033[2m[q]uit  [n]ext/[p]rev page  [r]efresh  "
            "[+/-] smooth  [g] grid  [0] no-smooth\033[0m"
        )

    def _build_frame(self) -> str:
        cols, rows = shutil.get_terminal_size((100, 30))
        all_tags = self._matching_tags()
        page_tags, n_pages = self._page_tags(all_tags)
        header = self._header(all_tags, page_tags, n_pages)
        footer = self._footer()
        # Reserve the header + footer rows; the body must fit the rest so the
        # whole frame is never taller than the terminal (overflow scrolls and
        # would misalign the in-place repaint, leaving stale curves behind).
        body = self.renderer.frame(
            self.reader.runs, page_tags, smooth=self.smooth, max_cols=self.cols,
            width=cols, height=max(4, rows - 2),
        )
        frame = f"{header}\n{body}\n{footer}"
        # Hard safety crop: never exceed the terminal height. Line wrap is
        # disabled by the painter, so width takes care of itself.
        lines = frame.split("\n")
        if len(lines) > rows:
            lines = lines[:rows]
        return "\n".join(lines)

    def _signature(self):
        """Cheap fingerprint of everything that affects the rendered frame.

        Repainting only when this changes is what keeps an idle dashboard from
        flickering — no new data means no redraw at all.
        """
        total = 0
        last_step = 0
        for run in self.reader.runs.values():
            for s in run.series.values():
                total += len(s)
                if s.steps:
                    last_step = max(last_step, s.steps[-1])
        return (total, last_step, self.page, round(self.smooth, 3),
                self.rows, self.cols, self.tag_filter, self.renderer.name,
                shutil.get_terminal_size((100, 30)))

    def render_once(self) -> None:
        self.reader.poll()
        print(self._build_frame())

    # -- interactive loop ----------------------------------------------------

    def run(self, *, once: bool = False) -> None:
        if once:
            self.render_once()
            return

        with Screen() as screen, KeyReader() as keys:
            last_sig = None
            while True:
                self.reader.poll()
                sig = self._signature()
                if sig != last_sig:
                    screen.draw(self._build_frame())
                    last_sig = sig

                # Wait out the interval, but react instantly to keypresses.
                deadline = time.monotonic() + self.interval
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    ch = keys.get(remaining)
                    if ch is None:
                        break
                    if self._handle_key(ch):  # returns True to quit
                        return
                    # A keypress may have changed the view: repaint now.
                    screen.draw(self._build_frame())
                    last_sig = self._signature()
                    deadline = time.monotonic() + self.interval

    def _handle_key(self, ch: str) -> bool:
        """Handle a keypress. Return True to quit."""
        if ch in ("q", "Q", "\x03", "\x04"):  # q, Ctrl-C, Ctrl-D
            return True
        if ch in ("n", " ", "j"):
            self.page += 1
        elif ch in ("p", "k"):
            self.page -= 1
        elif ch == "r":
            pass  # falls through to immediate re-render
        elif ch in ("+", "="):
            self.smooth = min(0.99, round(self.smooth + 0.05, 2))
        elif ch == "-":
            self.smooth = max(0.0, round(self.smooth - 0.05, 2))
        elif ch == "0":
            self.smooth = 0.0
        elif ch == "g":
            # cycle a few common grid shapes
            shapes = [(2, 3), (3, 3), (1, 2), (2, 2), (1, 1)]
            cur = (self.rows, self.cols)
            i = (shapes.index(cur) + 1) % len(shapes) if cur in shapes else 0
            self.rows, self.cols = shapes[i]
        return False
