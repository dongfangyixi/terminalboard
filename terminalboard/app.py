"""The interactive live dashboard loop."""
from __future__ import annotations

import fnmatch
import re
import shutil
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

# Characters that count as part of a "word" for word-wise cursor motion / delete.
_WORDCHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
)


def _word_match(word: str, name: str) -> bool:
    """Match a single filter word against ``name``.

    ``!w`` negates; ``/re/`` is a regex; ``* ? [ ]`` make it a glob; otherwise a
    case-insensitive substring.
    """
    neg = word.startswith("!")
    if neg:
        word = word[1:]
    if not word:
        return True
    if len(word) >= 2 and word.startswith("/") and word.endswith("/"):
        try:
            ok = re.search(word[1:-1], name, re.IGNORECASE) is not None
        except re.error:
            ok = False
    elif any(c in word for c in "*?["):
        ok = fnmatch.fnmatch(name.lower(), word.lower())
    else:
        ok = word.lower() in name.lower()
    return (not ok) if neg else ok


def match_filter(patterns: Optional[str], name: str) -> bool:
    """Match ``name`` against a small filter grammar (empty matches everything).

    * ``|`` or ``,`` separate **OR** alternatives.
    * whitespace or ``&`` within an alternative is **AND** (all must match).
    * a word is a case-insensitive **substring** (``loss`` → ``train/loss``);
      ``* ? [ ]`` make it a glob; ``!word`` negates; ``/regex/`` is a regex.
    """
    if not patterns:
        return True
    saw_term = False
    for term in re.split(r"[|,]", patterns):
        words = [w for w in re.split(r"[\s&]+", term) if w]
        if not words:
            continue
        saw_term = True
        if all(_word_match(w, name) for w in words):
            return True
    return not saw_term  # all-separators filter matches everything


def _prev_word(buf: list, pos: int) -> int:
    """Index of the start of the word before ``pos`` (skip seps, then word)."""
    i = pos
    while i > 0 and buf[i - 1] not in _WORDCHARS:
        i -= 1
    while i > 0 and buf[i - 1] in _WORDCHARS:
        i -= 1
    return i


def _next_word(buf: list, pos: int) -> int:
    """Index just past the end of the word at/after ``pos``."""
    n = len(buf)
    i = pos
    while i < n and buf[i] not in _WORDCHARS:
        i += 1
    while i < n and buf[i] in _WORDCHARS:
        i += 1
    return i


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
        # Rotation of the run draw order (z-order); 'o' cycles which run is on top.
        self._order_rot = 0
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

    def _run_order(self) -> List[str]:
        # Draw order (last drawn = on top); 'o' rotates which run is on top.
        names = sorted(self._visible_runs().keys())
        if not names:
            return names
        k = self._order_rot % len(names)
        return names[k:] + names[:k]

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
            "\033[2m[q/Esc]uit  [n/p]page  [f/t]ilter  [z/Z]zoom "
            f"({per_page}/pg)  [o]rder  [+/-/0]smooth  [r]efresh  [H]elp\033[0m"
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
        auto-repeat, fast typing, paste). Each token is a nav token
        (UP/DOWN/LEFT/RIGHT/HOME/END/DEL/WORD-LEFT/WORD-RIGHT/WORD-DEL-BACK/
        WORD-DEL-FWD/ESC/IGNORE) or a single ordinary character. Handles CSI
        (ESC ``[``) and SS3 (ESC ``O``), including modified arrows
        (Alt/Ctrl+←/→ as ESC ``[1;3D`` / ``[1;5D``) and Alt-b/f/d.
        """
        plain = {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT",
                 "H": "HOME", "F": "END"}
        wordkey = {"C": "WORD-RIGHT", "D": "WORD-LEFT"}
        numtilde = {"3": "DEL", "1": "HOME", "7": "HOME", "4": "END", "8": "END"}
        tokens = []
        i, n = 0, len(s)
        while i < n:
            c = s[i]
            if c != "\x1b":
                tokens.append(c)
                i += 1
                continue
            if i + 1 >= n:
                tokens.append("ESC")
                i += 1
                continue
            nxt = s[i + 1]
            if nxt in "[O":                 # CSI / SS3
                j = i + 2
                params = ""
                while j < n and (s[j].isdigit() or s[j] == ";"):
                    params += s[j]
                    j += 1
                if j >= n:
                    tokens.append("ESC")
                    break
                fin = s[j]
                mod = params.split(";")[-1] not in ("", "1") if ";" in params else False
                if fin == "~":
                    tokens.append(numtilde.get(params.split(";")[0], "IGNORE"))
                elif mod and fin in wordkey:   # Alt/Ctrl + Left/Right => by word
                    tokens.append(wordkey[fin])
                elif fin in plain:
                    tokens.append(plain[fin])
                else:
                    tokens.append("IGNORE")
                i = j + 1
            elif nxt in ("b", "B"):         # Alt-b
                tokens.append("WORD-LEFT")
                i += 2
            elif nxt in ("f", "F"):         # Alt-f
                tokens.append("WORD-RIGHT")
                i += 2
            elif nxt == "d":                # Alt-d
                tokens.append("WORD-DEL-FWD")
                i += 2
            elif nxt in ("\x7f", "\b"):     # Alt-Backspace
                tokens.append("WORD-DEL-BACK")
                i += 2
            else:
                tokens.append("ESC")        # lone Esc
                i += 1
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
            run_order=self._run_order(),
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
                self._order_rot, shutil.get_terminal_size((100, 30)))

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
                        elif tok in ("H", "?"):
                            self._show_help(screen, keys)
                        elif tok == "ESC":
                            return  # quit
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
        # The current input normally lives in a "draft" slot just past the newest
        # entry. But if it already equals an existing history entry (the usual
        # case — the editor pre-fills with the active filter, which is in the
        # history), start *on* that entry: then Up goes to the previous filter
        # (not a redundant repeat of what's shown) and Down to the next, with no
        # duplicate "extra" empty slot.
        cur = "".join(buf)
        if cur and cur in hist:
            hist_idx = hist.index(cur)
            has_draft = False
            draft = None
        else:
            hist_idx = len(hist)   # the live-draft slot
            has_draft = True
            draft = list(buf)
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
            elif key == "\x15":                           # Ctrl-U: clear line
                buf, pos = [], 0
            elif key == "\x0b":                           # Ctrl-K: kill to end
                del buf[pos:]
            elif key == "WORD-LEFT":
                pos = _prev_word(buf, pos)
            elif key == "WORD-RIGHT":
                pos = _next_word(buf, pos)
            elif key in ("WORD-DEL-BACK", "\x17"):        # Ctrl-W / Alt-Backspace
                start = _prev_word(buf, pos)
                del buf[start:pos]
                pos = start
            elif key == "WORD-DEL-FWD":                   # Alt-d
                del buf[pos:_next_word(buf, pos)]
            elif key == "UP":
                if hist_idx > 0:
                    if hist_idx == len(hist):
                        draft = list(buf)                 # stash the live draft
                    hist_idx -= 1
                    buf, pos = list(hist[hist_idx]), len(hist[hist_idx])
            elif key == "DOWN":
                # Don't descend into the draft slot when there isn't one (the
                # input started on the newest entry) — that's the "extra
                # position" bug. With a draft, Down can return to it.
                top = len(hist) if has_draft else len(hist) - 1
                if hist_idx < top:
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
        elif ch == "o":
            # cycle which overlapping curve is drawn on top
            self._order_rot += 1
        return False

    # -- help overlay --------------------------------------------------------

    def _help_text(self) -> str:
        return "\n".join([
            "  \033[1mterminalboard\033[0m — help",
            "",
            "  \033[1mNavigation\033[0m",
            "    n / space / j   next page         p / k        previous page",
            "    z / Z           zoom out / in     o            cycle curve order (z)",
            "    r               refresh now       q / Esc      quit",
            "",
            "  \033[1mSmoothing\033[0m",
            "    + / =  more      -  less      0  off",
            "",
            "  \033[1mFiltering\033[0m",
            "    t  edit tag filter        f  edit experiment filter",
            "    In the prompt:  ←/→ move   ↑/↓ history   Home/End or ^A/^E",
            "                    ^W del word   ^K kill-to-end   ^U clear",
            "                    Alt/Ctrl+←/→ word move   Enter apply   Esc cancel",
            "",
            "  \033[1mFilter syntax\033[0m (tags and experiments)",
            "    word         case-insensitive substring   (loss → train/loss)",
            "    a b          AND  (both must match)",
            "    a | b , c    OR   (| or , separate alternatives)",
            "    * ? [ ]      glob wildcards                (train/*loss*)",
            "    !word        NOT  (exclude)         /regex/   regular expression",
            "",
            "  \033[1mPlot types\033[0m  scalars (curves) · text summaries · "
            "histograms (heatmap)",
            "",
            "  \033[2mPress any key to return…\033[0m",
        ])

    def _show_help(self, screen, keys) -> None:
        cols, rows = shutil.get_terminal_size((100, 30))
        lines = self._help_text().split("\n")[:rows]
        screen.draw("\n".join(lines), hard=True)
        while keys.get(30) is None:        # wait for any key
            pass
