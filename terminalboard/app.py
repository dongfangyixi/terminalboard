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

# Zoom ladder: (rows, cols) per page, from most-zoomed-in (1 big panel) to
# most-zoomed-out (36 small panels). Panel counts: 1,2,4,6,9,12,16,24,36.
_ZOOM_LADDER = [
    (1, 1), (1, 2), (2, 2), (2, 3), (3, 3), (3, 4), (4, 4), (4, 6), (6, 6),
]


def match_filter(patterns: Optional[str], name: str) -> bool:
    """True if ``name`` matches any comma-separated pattern.

    A token containing a glob char (``* ? [``) is matched with fnmatch; a plain
    token is matched as a case-insensitive substring — friendlier for typing
    interactively (``loss`` matches ``train/loss`` and ``val/loss``). An empty
    filter matches everything.
    """
    if not patterns:
        return True
    for tok in patterns.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if any(c in tok for c in "*?["):
            if fnmatch.fnmatch(name, tok):
                return True
        elif tok.lower() in name.lower():
            return True
    return False


class App:
    def __init__(
        self,
        reader: BaseReader,
        renderer: Renderer,
        *,
        tag_filter: Optional[str] = None,
        run_filter: Optional[str] = None,
        smooth: float = 0.6,
        cols: int = 3,
        rows: int = 2,
        interval: float = 2.0,
    ):
        self.reader = reader
        self.renderer = renderer
        self.tag_filter = tag_filter
        self.run_filter = run_filter
        self.smooth = smooth
        # Stable color index per run: assigned once and never reshuffled, so an
        # experiment keeps its color regardless of filtering or new runs.
        self._run_color_index: dict = {}
        self.interval = interval
        self.page = 0
        # Start at the ladder rung closest to the requested grid's panel count.
        target = max(1, rows) * max(1, cols)
        self._zoom = min(
            range(len(_ZOOM_LADDER)),
            key=lambda i: abs(_ZOOM_LADDER[i][0] * _ZOOM_LADDER[i][1] - target),
        )
        self.rows, self.cols = _ZOOM_LADDER[self._zoom]

    # -- tag selection -------------------------------------------------------

    def _matching_tags(self) -> List[str]:
        # A tag is shown only if at least one *visible* run actually has it.
        visible = self._visible_runs()
        tags = sorted({t for run in visible.values() for t in run.series})
        return [t for t in tags if match_filter(self.tag_filter, t)]

    def _visible_runs(self):
        runs = self.reader.runs
        if not self.run_filter:
            return runs
        return {n: r for n, r in runs.items() if match_filter(self.run_filter, n)}

    def _run_colors(self) -> dict:
        # Assign a stable color index to any run we haven't seen yet (sorted, so
        # the first assignment is deterministic; later runs only ever append).
        for name in sorted(self.reader.runs):
            if name not in self._run_color_index:
                self._run_color_index[name] = len(self._run_color_index)
        return self._run_color_index

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
        n_vis = len(self._visible_runs())
        n_all = len(self.reader.runs)
        runs_str = f"{n_vis}/{n_all}" if self.run_filter else str(n_all)
        tflt = self.tag_filter or "*"
        eflt = self.run_filter or "*"
        return (
            f"\033[1mterminalboard\033[0m  "
            f"exp={runs_str} (\033[36mf\033[0m:{eflt})  "
            f"tags={len(tags)} (\033[36mt\033[0m:{tflt})  "
            f"page {self.page + 1}/{n_pages}  "
            f"smooth={self.smooth:.2f}  mode={self.renderer.name}"
        )

    def _footer(self) -> str:
        per_page = self.rows * self.cols
        return (
            "\033[2m[q]uit  [n/p] page  [f]ilter exp  [t]ag filter  "
            f"[+/-] smooth  [z/Z] zoom ({per_page}/pg)  [r]efresh\033[0m"
        )

    def _prompt_footer(self, label: str, text: str, kind: str) -> str:
        if kind == "tags":
            n, unit = len(self._matching_tags()), "tags"
        else:
            n, unit = len(self._visible_runs()), "experiments"
        cursor = "\033[7m \033[0m"  # reverse-video block as a cursor
        return (
            f"\033[1m{label}>\033[0m {text}{cursor}  "
            f"\033[2m({n} {unit} · Enter=apply  Esc=cancel  ^U=clear)\033[0m"
        )

    def _build_frame(self, prompt=None) -> str:
        cols, rows = shutil.get_terminal_size((100, 30))
        all_tags = self._matching_tags()
        page_tags, n_pages = self._page_tags(all_tags)
        header = self._header(all_tags, page_tags, n_pages)
        footer = self._prompt_footer(*prompt) if prompt else self._footer()
        # Reserve the header + footer rows; the body must fit the rest so the
        # whole frame is never taller than the terminal (overflow scrolls and
        # would misalign the in-place repaint, leaving stale curves behind).
        body = self.renderer.frame(
            self._visible_runs(), page_tags, smooth=self.smooth, max_cols=self.cols,
            width=cols, height=max(4, rows - 2), run_colors=self._run_colors(),
        )
        frame = f"{header}\n{body}\n{footer}"
        # Hard safety crop: never exceed the terminal height. Line wrap is
        # disabled by the painter, so width takes care of itself.
        lines = frame.split("\n")
        if len(lines) > rows:
            lines = lines[:rows]
        return "\n".join(lines)

    def _view_sig(self):
        """The part of the state that changes the *layout* (not just the data).

        When this changes we hard-clear before repainting, so a new page/grid
        can never leave residue from the previous one.
        """
        return (self.page, round(self.smooth, 3), self.rows, self.cols,
                self.tag_filter, self.run_filter, self.renderer.name,
                shutil.get_terminal_size((100, 30)))

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
        return (total, last_step) + self._view_sig()

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
            last_view = None
            while True:
                self.reader.poll()
                sig = self._signature()
                if sig != last_sig:
                    view = self._view_sig()
                    # Hard-clear on a layout change; soft in-place on data-only.
                    screen.draw(self._build_frame(), hard=(view != last_view))
                    last_sig, last_view = sig, view

                # Wait out the interval, but react instantly to keypresses.
                deadline = time.monotonic() + self.interval
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    ch = keys.get(remaining)
                    if ch is None:
                        break
                    if ch in ("f", "t"):
                        self._edit_filter(screen, keys,
                                          "tags" if ch == "t" else "runs")
                    elif self._handle_key(ch):  # returns True to quit
                        return
                    # A keypress may have changed the view: repaint now, hard-
                    # clearing if the layout changed so no stale plots remain.
                    view = self._view_sig()
                    screen.draw(self._build_frame(), hard=(view != last_view))
                    last_sig, last_view = self._signature(), view
                    deadline = time.monotonic() + self.interval

    def _edit_filter(self, screen, keys, kind: str) -> None:
        """Modal mini line-editor for a tag or experiment filter, with live
        preview (the dashboard re-filters as you type)."""
        attr = "tag_filter" if kind == "tags" else "run_filter"
        label = "filter tags" if kind == "tags" else "filter experiments"
        original = getattr(self, attr)
        buf = list(original or "")
        while True:
            setattr(self, attr, "".join(buf).strip() or None)  # live preview
            self.page = 0
            screen.draw(self._build_frame(prompt=(label, "".join(buf), kind)),
                        hard=True)
            ch = keys.get(30)
            if ch is None:
                continue
            if ch in ("\r", "\n"):           # apply (already set)
                return
            if ch == "\x1b":                 # Esc — but distinguish arrow keys
                nxt = keys.get(0.02)
                if nxt == "[":
                    keys.get(0.02)           # swallow the arrow's final byte
                    continue
                setattr(self, attr, original)  # cancel: restore
                return
            if ch in ("\x7f", "\b", "\x08"):  # backspace
                if buf:
                    buf.pop()
            elif ch == "\x15":                # Ctrl-U: clear line
                buf = []
            elif ch.isprintable():
                buf.append(ch)

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
        elif ch == "z":
            # zoom out: more, smaller panels per page
            self._zoom = min(len(_ZOOM_LADDER) - 1, self._zoom + 1)
            self.rows, self.cols = _ZOOM_LADDER[self._zoom]
        elif ch == "Z":
            # zoom in: fewer, larger panels per page
            self._zoom = max(0, self._zoom - 1)
            self.rows, self.cols = _ZOOM_LADDER[self._zoom]
        return False
