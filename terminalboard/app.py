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
        # Per-kind history of applied filter patterns (recalled with up/down).
        self._filter_history = {"tags": [], "runs": []}
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

    def _prompt_footer(self, label: str, text: str, pos: int, kind: str,
                       warn: bool) -> str:
        # Draw the input with a reverse-video block cursor at ``pos``.
        if pos < len(text):
            shown = text[:pos] + "\033[7m" + text[pos] + "\033[0m" + text[pos + 1:]
        else:
            shown = text + "\033[7m \033[0m"
        if warn:
            status = "\033[1;31m✗ no matches — pattern not applied\033[0m"
        else:
            if kind == "tags":
                n, unit = len(self._matching_tags()), "tags"
            else:
                n, unit = len(self._visible_runs()), "experiments"
            status = (f"\033[2m({n} {unit} · ←→ move · ↑↓ history · "
                      f"Enter apply · Esc cancel)\033[0m")
        return f"\033[1m{label}>\033[0m {shown}  {status}"

    def _count_matches(self, kind: str, value) -> int:
        if kind == "runs":
            return sum(1 for n in self.reader.runs if match_filter(value, n))
        tags = {t for run in self._visible_runs().values() for t in run.series}
        return sum(1 for t in tags if match_filter(value, t))

    def _parse_chunk(self, s):
        """Split one input chunk into a list of key tokens.

        A single ``os.read`` can return several keypresses at once (key
        auto-repeat, fast typing, paste) — e.g. ``"\\x1b[B\\x1b[B"`` is two Down
        presses. Each token is a nav token (UP/DOWN/LEFT/RIGHT/HOME/END/DEL/ESC/
        IGNORE) or a single ordinary character. Handles CSI (ESC ``[``) and SS3
        (ESC ``O``) forms.
        """
        final = {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT",
                 "H": "HOME", "F": "END"}
        num = {"3": "DEL", "1": "HOME", "7": "HOME", "4": "END", "8": "END"}
        tokens = []
        i, n = 0, len(s)
        while i < n:
            c = s[i]
            if c != "\x1b":
                tokens.append(c)
                i += 1
                continue
            if i + 1 >= n or s[i + 1] not in "[O":
                tokens.append("ESC")        # lone Esc (skip just the ESC byte)
                i += 1
                continue
            j = i + 2
            if j < n and s[j].isdigit():    # e.g. ESC [ 3 ~
                k = j
                while k < n and s[k].isdigit():
                    k += 1
                if k < n and s[k] == "~":
                    tokens.append(num.get(s[j:k], "IGNORE"))
                    i = k + 1
                else:
                    tokens.append("IGNORE")
                    i = k
            elif j < n:                     # ESC [ A  /  ESC O A
                tokens.append(final.get(s[j], "IGNORE"))
                i = j + 1
            else:
                tokens.append("ESC")        # incomplete ESC[ at end of chunk
                i = j
        return tokens

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
                    chunk = keys.get(remaining)
                    if chunk is None:
                        break
                    for tok in self._parse_chunk(chunk):
                        if tok in ("f", "t"):
                            self._edit_filter(screen, keys,
                                              "tags" if tok == "t" else "runs")
                        elif len(tok) == 1 and self._handle_key(tok):
                            return  # quit
                        # multi-char nav tokens (UP/DOWN/…) do nothing here
                    # A keypress may have changed the view: repaint now, hard-
                    # clearing if the layout changed so no stale plots remain.
                    view = self._view_sig()
                    screen.draw(self._build_frame(), hard=(view != last_view))
                    last_sig, last_view = self._signature(), view
                    deadline = time.monotonic() + self.interval

    def _edit_filter(self, screen, keys, kind: str) -> None:
        """Modal line-editor for a tag or experiment filter.

        Live preview (re-filters as you type) — but if a pattern matches nothing
        the layout is *kept* and a red warning is shown, instead of collapsing to
        an empty screen and yanking the input box to the top. ←/→ move the
        cursor, ↑/↓ recall previous patterns.
        """
        attr = "tag_filter" if kind == "tags" else "run_filter"
        label = "filter tags" if kind == "tags" else "filter experiments"
        original = getattr(self, attr)
        last_valid = original
        buf = list(original or "")
        pos = len(buf)
        hist = self._filter_history[kind]
        hist_idx = len(hist)   # points one past the end == the live draft
        draft = None
        pending = []           # tokens decoded from one read, drained one by one

        def next_key():
            nonlocal pending
            if not pending:
                chunk = keys.get(30)
                if chunk is None:
                    return None
                if chunk == "\x1b":         # maybe a split escape sequence
                    more = keys.get(0.03)
                    if more:
                        chunk += more
                pending = self._parse_chunk(chunk)
            return pending.pop(0) if pending else None

        while True:
            typed = "".join(buf).strip() or None
            warn = typed is not None and self._count_matches(kind, typed) == 0
            if not warn:
                setattr(self, attr, typed)  # commit -> layout updates live
                last_valid = typed
                self.page = 0
            # When warn: keep the last valid filter committed (layout frozen).
            screen.draw(
                self._build_frame(prompt=(label, "".join(buf), pos, kind, warn)),
                hard=True,
            )

            key = next_key()
            if key is None:
                continue
            if key in ("\r", "\n"):                       # apply last valid
                setattr(self, attr, last_valid)
                if last_valid:                            # unique, most-recent-last
                    if last_valid in hist:
                        hist.remove(last_valid)
                    hist.append(last_valid)
                return
            if key == "ESC":                              # cancel: restore
                setattr(self, attr, original)
                self.page = 0
                return
            if key in ("\x7f", "\b", "\x08"):             # backspace
                if pos > 0:
                    del buf[pos - 1]
                    pos -= 1
            elif key == "DEL":
                if pos < len(buf):
                    del buf[pos]
            elif key == "LEFT":
                pos = max(0, pos - 1)
            elif key == "RIGHT":
                pos = min(len(buf), pos + 1)
            elif key in ("HOME", "\x01"):                 # Home / Ctrl-A
                pos = 0
            elif key in ("END", "\x05"):                  # End / Ctrl-E
                pos = len(buf)
            elif key == "\x15":                           # Ctrl-U: clear
                buf, pos = [], 0
            elif key == "UP":
                if hist and hist_idx > 0:
                    if hist_idx == len(hist):
                        draft = list(buf)                 # stash the live draft
                    hist_idx -= 1
                    buf, pos = list(hist[hist_idx]), len(hist[hist_idx])
            elif key == "DOWN":
                if hist_idx < len(hist):
                    hist_idx += 1
                    nxt = hist[hist_idx] if hist_idx < len(hist) else (draft or [])
                    buf, pos = list(nxt), len(nxt)
            elif isinstance(key, str) and key.isprintable():
                # May be several chars at once (fast typing or a paste).
                for c in key:
                    buf.insert(pos, c)
                    pos += 1

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
