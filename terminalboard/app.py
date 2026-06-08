"""The interactive live dashboard loop."""
from __future__ import annotations

import bisect
import fnmatch
import hashlib
import json
import os
import re
import shutil
import textwrap
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

    * If the **whole** pattern is wrapped in ``/.../`` it's a single regex
      (``re.search``, case-insensitive) — use this for regexes containing
      ``|`` or spaces, e.g. ``/^train/(loss|lr)$/``.
    * Otherwise: ``|`` or ``,`` separate **OR** alternatives; whitespace or ``&``
      within an alternative is **AND**; a word is a case-insensitive **substring**
      (``loss`` → ``train/loss``); ``* ? [ ]`` make it a glob; ``!word`` negates;
      a per-word ``/regex/`` (without ``|``/spaces) is also a regex.
    """
    if not patterns:
        return True
    p = patterns.strip()
    if len(p) >= 2 and p.startswith("/") and p.endswith("/"):
        try:
            return re.search(p[1:-1], name, re.IGNORECASE) is not None
        except re.error:
            return False
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
        xaxis: str = "step",
        logy: bool = False,
        csv_dir: str = "",
        restore: bool = False,
        restore_exclude=(),
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
        # Cursor over the matching-tags list (drives the focused panel + page).
        self._focus = 0
        # Detail (drill-down) state: a tag string when zoomed in, else None.
        self._detail: Optional[str] = None
        self._detail_run = 0   # which experiment is shown in detail (text/heatmap)
        self._scroll = 0       # scroll offset in a text detail view
        self._cursor = 0       # x-cursor index in a scalar detail view
        self.xaxis = xaxis if xaxis in ("step", "time") else "step"
        self.logy = bool(logy)  # log-scale y for scalar panels
        self._textdiff = False  # text detail: show only keys that differ across runs
        self._status = ""       # transient status line (e.g. after CSV export)
        self._csv_dir = csv_dir  # default folder pre-filled in the CSV save prompt
        # Start at the ladder rung closest to the requested grid's panel count.
        target = max(1, rows) * max(1, cols)
        self._zoom = min(
            range(len(_ZOOM_LADDER)),
            key=lambda i: abs(_ZOOM_LADDER[i][0] * _ZOOM_LADDER[i][1] - target),
        )
        self.rows, self.cols = _ZOOM_LADDER[self._zoom]
        # Per-logdir view persistence: restore the last session's filters/zoom/
        # smoothing/etc. (CLI-explicit options win, via restore_exclude).
        self._restore = restore
        if restore:
            self._load_view(exclude=restore_exclude)

    # -- view persistence ----------------------------------------------------

    def _view_state_file(self) -> str:
        base = (os.environ.get("XDG_STATE_HOME")
                or os.path.expanduser("~/.local/state"))
        logdir = getattr(self.reader, "logdir", "") or ""
        h = hashlib.sha1(logdir.encode()).hexdigest()[:12]
        name = os.path.basename(logdir.rstrip("/")) or "root"
        return os.path.join(base, "terminalboard", "views", f"{name}-{h}.json")

    def _save_view(self) -> None:
        try:
            path = self._view_state_file()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            state = {
                "logdir": getattr(self.reader, "logdir", ""),
                "tag_filter": self.tag_filter, "run_filter": self.run_filter,
                "smooth": self.smooth, "xaxis": self.xaxis, "logy": self.logy,
                "order_rot": self._order_rot, "zoom": self._zoom,
                "focus": self._focus,
            }
            with open(path, "w") as f:
                json.dump(state, f, indent=2)
        except Exception:
            pass            # persistence is best-effort; never break the session

    def _load_view(self, exclude=()) -> None:
        try:
            path = self._view_state_file()
            if not os.path.isfile(path):
                return
            with open(path) as f:
                s = json.load(f)
        except Exception:
            return
        if not isinstance(s, dict):
            return
        ex = set(exclude)
        if "tag_filter" not in ex and "tag_filter" in s:
            self.tag_filter = s["tag_filter"] or None
        if "run_filter" not in ex and "run_filter" in s:
            self.run_filter = s["run_filter"] or None
        if "smooth" not in ex and isinstance(s.get("smooth"), (int, float)):
            self.smooth = max(0.0, min(0.99, float(s["smooth"])))
        if "xaxis" not in ex and s.get("xaxis") in ("step", "time"):
            self.xaxis = s["xaxis"]
        if "logy" not in ex and isinstance(s.get("logy"), bool):
            self.logy = s["logy"]
        if "order_rot" not in ex and isinstance(s.get("order_rot"), int):
            self._order_rot = s["order_rot"]
        if ("zoom" not in ex and isinstance(s.get("zoom"), int)
                and 0 <= s["zoom"] < len(_ZOOM_LADDER)):
            self._zoom = s["zoom"]
            self.rows, self.cols = _ZOOM_LADDER[self._zoom]
        if "focus" not in ex and isinstance(s.get("focus"), int):
            self._focus = max(0, s["focus"])

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

    def _layout(self):
        """Resolve the focus cursor into (tags, page slice, page idx, n_pages,
        focus-cell-within-page)."""
        tags = self._matching_tags()
        per_page = max(1, self.cols * self.rows)
        n = len(tags)
        self._focus = max(0, min(self._focus, max(0, n - 1)))
        n_pages = max(1, (n + per_page - 1) // per_page)
        page = self._focus // per_page
        start = page * per_page
        return tags, tags[start:start + per_page], page, n_pages, self._focus - start

    # -- rendering -----------------------------------------------------------

    def _header(self, tags: List[str], page: int, n_pages: int) -> str:
        n_vis = len(self._visible_runs())
        n_all = len(self.reader.runs)
        runs_str = f"{n_vis}/{n_all}" if self.run_filter else str(n_all)
        tflt = self.tag_filter or "*"
        eflt = self.run_filter or "*"
        return (
            f"\033[1mterminalboard\033[0m  "
            f"exp={runs_str} (\033[36mf\033[0m:{eflt})  "
            f"tags={len(tags)} (\033[36mt\033[0m:{tflt})  "
            f"page {page + 1}/{n_pages}  "
            f"smooth={self.smooth:.2f}  x={self.xaxis}  "
            f"y={'log' if self.logy else 'lin'}"
        )

    def _footer(self) -> str:
        per_page = self.rows * self.cols
        return (
            "\033[2m[arrows]focus [Enter]inspect [n/p]page [f/t]ilter "
            f"[z/Z]zoom({per_page}) [o]rder [+/-/0]smooth [x]axis [l]og "
            "[w]csv [H]elp [q/Esc]uit\033[0m"
        )

    def _prompt_footer(self, label: str, text: str, pos: int, kind: str,
                       warn: bool) -> str:
        # Draw the input with a reverse-video block cursor at ``pos``.
        if pos < len(text):
            shown = text[:pos] + "\033[7m" + text[pos] + "\033[0m" + text[pos + 1:]
        else:
            shown = text + "\033[7m \033[0m"
        if kind not in ("tags", "runs"):                  # generic input prompt
            return (f"\033[1m{label}>\033[0m {shown}  "
                    "\033[2m(Enter save · Esc cancel)\033[0m")
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
        numtilde = {"3": "DEL", "1": "HOME", "7": "HOME", "4": "END", "8": "END",
                    "5": "PGUP", "6": "PGDN"}
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
        if self._detail is not None and prompt is None:
            return self._build_detail_frame()
        cols, rows = shutil.get_terminal_size((100, 30))
        all_tags, page_tags, page, n_pages, focus_cell = self._layout()
        header = self._header(all_tags, page, n_pages)
        footer = (self._prompt_footer(*prompt) if prompt
                  else self._with_status(self._footer()))
        # Reserve the header + footer rows; the body must fit the rest so the
        # whole frame is never taller than the terminal (overflow scrolls and
        # would misalign the in-place repaint, leaving stale curves behind).
        body = self.renderer.frame(
            self._visible_runs(), page_tags, smooth=self.smooth, max_cols=self.cols,
            width=cols, height=max(4, rows - 2), run_colors=self._run_colors(),
            run_order=self._run_order(), xaxis=self.xaxis, logy=self.logy,
            focus=(-1 if prompt else focus_cell),
        )
        frame = f"{header}\n{body}\n{footer}"
        return self._crop(frame, rows)

    @staticmethod
    def _crop(frame: str, rows: int) -> str:
        # Hard safety crop: never exceed the terminal height (line wrap is off,
        # so width takes care of itself).
        lines = frame.split("\n")
        return "\n".join(lines[:rows])

    def _with_status(self, footer: str) -> str:
        if self._status:
            return footer + f"   \033[1;32m{self._status}\033[0m"
        return footer

    def _current_tag(self) -> Optional[str]:
        if self._detail is not None:
            return self._detail
        tags = self._matching_tags()
        return tags[self._focus] if tags and self._focus < len(tags) else None

    def _csv_default_path(self, tag: str) -> str:
        """Default save path: <csv_dir>/<sanitized-tag>.csv (csv_dir from config)."""
        import os
        name = tag.strip("/").replace("/", "_") + ".csv"
        base = os.path.expanduser(self._csv_dir) if self._csv_dir else ""
        return os.path.join(base, name) if base else name

    def _do_csv(self, screen, keys) -> None:
        """Prompt for a path (pre-filled from config) and export the focused tag."""
        tag = self._current_tag()
        if not tag:
            self._status = "nothing to export"
            return
        path = self._input_prompt(screen, keys, "save CSV",
                                  self._csv_default_path(tag))
        self._status = "" if path is None else self._export_csv(path)

    def _export_csv(self, path: Optional[str] = None) -> str:
        """Write the focused/detail scalar tag to ``path`` (default if None)."""
        import csv
        import os
        tag = self._current_tag()
        if not tag:
            return "nothing to export"
        runs = self._visible_runs()
        names = [n for n in sorted(runs)
                 if tag in runs[n].series and runs[n].series[tag].kind == "scalar"]
        if not names:
            return f"'{tag}' is not a scalar — CSV skipped"
        lut = {n: dict(zip(runs[n].series[tag].steps, runs[n].series[tag].values))
               for n in names}
        steps = sorted({s for n in names for s in lut[n]})
        path = os.path.expanduser(path or self._csv_default_path(tag))
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["step"] + names)
                for s in steps:
                    w.writerow([s] + [lut[n].get(s, "") for n in names])
        except OSError as e:
            return f"export failed: {e}"
        return f"✓ wrote {path} ({len(steps)} rows)"

    def _input_prompt(self, screen, keys, label, initial):
        """Modal single-line text input (returns the string, or None on Esc)."""
        buf = list(initial or "")
        pos = len(buf)
        pending = []

        def next_key():
            nonlocal pending
            if not pending:
                chunk = keys.get(30)
                if chunk is None:
                    return None
                if chunk == "\x1b":
                    more = keys.get(0.03)
                    if more:
                        chunk += more
                pending = self._parse_chunk(chunk)
            return pending.pop(0) if pending else None

        while True:
            screen.draw(self._build_frame(
                prompt=(label, "".join(buf), pos, "input", False)), hard=True)
            key = next_key()
            if key is None:
                continue
            if key in ("\r", "\n"):
                return "".join(buf).strip()
            if key in ("\x03", "\x04", "ESC"):
                return None
            if key in ("\x7f", "\b", "\x08"):
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
            elif key in ("HOME", "\x01"):
                pos = 0
            elif key in ("END", "\x05"):
                pos = len(buf)
            elif key == "\x15":
                buf, pos = [], 0
            elif key == "\x0b":
                del buf[pos:]
            elif key == "WORD-LEFT":
                pos = _prev_word(buf, pos)
            elif key == "WORD-RIGHT":
                pos = _next_word(buf, pos)
            elif key in ("WORD-DEL-BACK", "\x17"):
                start = _prev_word(buf, pos)
                del buf[start:pos]
                pos = start
            elif key == "WORD-DEL-FWD":
                del buf[pos:_next_word(buf, pos)]
            elif isinstance(key, str) and key.isprintable():
                for c in key:
                    buf.insert(pos, c)
                    pos += 1

    # -- detail (drill-down) view -------------------------------------------

    def _detail_runs(self):
        """Runs (sorted) that have the detail tag."""
        runs = self._visible_runs()
        return [n for n in sorted(runs) if self._detail in runs[n].series]

    def _build_detail_frame(self) -> str:
        cols, rows = shutil.get_terminal_size((100, 30))
        tag = self._detail
        runs = self._visible_runs()
        names = self._detail_runs()
        if not names:                       # tag vanished (filter/data) — bail out
            self._detail = None
            return self._build_frame()
        kind = runs[names[0]].series[tag].kind
        body_h = max(2, rows - 2)

        if kind == "text":
            if self._textdiff and len(names) > 1:
                header, body = self._text_diff_detail(tag, names, cols, body_h)
                footer = "\033[2m↑/↓ scroll · d full text · Esc back\033[0m"
            else:
                sel = names[self._detail_run % len(names)]
                header, body = self._text_detail(tag, sel, len(names), cols, body_h)
                diff = " · d diff" if len(names) > 1 else ""
                footer = ("\033[2m↑/↓ scroll · PgUp/PgDn · ←/→ switch exp"
                          f"{diff} · Esc back\033[0m")
        elif kind == "scalar":
            return self._scalar_detail(tag, names, cols, rows, body_h)
        else:                                            # histogram
            sel = names[self._detail_run % len(names)]
            order = [n for n in names if n != sel] + [sel]   # selected on top
            header = (f"\033[1m{tag}\033[0m  [{sel}]  "
                      f"exp {self._detail_run % len(names) + 1}/{len(names)}  "
                      f"kind={kind}")
            body = self.renderer.frame(
                runs, [tag], smooth=self.smooth, max_cols=1,
                width=cols, height=body_h, run_colors=self._run_colors(),
                run_order=order,
            )
            switch = "←/→ switch exp · " if len(names) > 1 else ""
            footer = f"\033[2m{switch}Esc back\033[0m"
        return self._crop(f"{header}\n{body}\n{self._with_status(footer)}", rows)

    # -- scalar detail with a TensorBoard-style x-cursor + readout -----------

    @staticmethod
    def _fmt_reltime(secs: float) -> str:
        secs = max(0, int(secs))
        if secs < 60:
            return f"+{secs}s"
        m, s = divmod(secs, 60)
        if m < 60:
            return f"+{m}m{s:02d}s"
        h, m = divmod(m, 60)
        return f"+{h}h{m:02d}m"

    def _scalar_track(self, tag, names) -> List[int]:
        """Cursor stops = the UNION of every visible run's steps, so ←/→ can reach
        the last point among all experiments (not just one run's last step)."""
        runs = self._visible_runs()
        steps = set()
        for n in names:
            steps.update(runs[n].series[tag].steps)
        return sorted(steps)

    def _cursor_time(self, tag, names, cstep) -> float:
        """Relative wall-time for the cursor on the time axis: take the
        furthest-reaching run's time at the step nearest ``cstep``."""
        runs = self._visible_runs()
        best = None                      # (reach, reltime)
        for n in names:
            s = runs[n].series[tag]
            if not s.steps or not s.wall_times:
                continue
            i = self._nearest_index(s.steps, cstep)
            if i >= len(s.wall_times):
                continue
            reach, reltime = s.steps[-1], s.wall_times[i] - s.wall_times[0]
            if best is None or reach > best[0]:
                best = (reach, reltime)
        return best[1] if best else cstep

    def _nearest_index(self, steps, target) -> int:
        i = bisect.bisect_left(steps, target)
        if i >= len(steps):
            return len(steps) - 1
        if i > 0 and abs(steps[i - 1] - target) <= abs(steps[i] - target):
            return i - 1
        return i

    def _scalar_detail(self, tag, names, cols, rows, body_h) -> str:
        from .render import ema, _RUN_STYLES
        runs = self._visible_runs()
        rc = self._run_colors()
        track = self._scalar_track(tag, names)
        if not track:
            return self._crop(f"\033[1m{tag}\033[0m\n  (no data)\n"
                              "\033[2mEsc back\033[0m", rows)
        self._cursor = max(0, min(self._cursor, len(track) - 1))
        cstep = track[self._cursor]

        # Per-run readout at the cursor step (full run names, aligned).
        readout: List[str] = []
        shown = names[:8]
        namew = max((len(n) for n in shown), default=4)
        for n in shown:
            s = runs[n].series[tag]
            if not s.steps:
                continue
            i = self._nearest_index(s.steps, cstep)
            val = s.values[i]
            sm = ema(s.values, self.smooth)[i] if self.smooth > 0 else val
            rt = ""
            if s.wall_times and i < len(s.wall_times):
                rt = "  t " + self._fmt_reltime(s.wall_times[i] - s.wall_times[0])
            code = _RUN_STYLES[rc.get(n, 0) % len(_RUN_STYLES)][1]
            readout.append(
                f"\033[{code}m●\033[0m {n:<{namew}}  step {s.steps[i]:>8}  "
                f"value {val:< 12.5g} smoothed {sm:< 12.5g}{rt}"
            )

        # Cursor x in the active axis domain (so the vertical line lands right).
        cx = self._cursor_time(tag, names, cstep) if self.xaxis == "time" else cstep

        plot_h = max(2, body_h - len(readout))
        plot = self.renderer.detail_scalar(
            runs, tag, order=self._run_order(), run_color=rc,
            w=cols, h=plot_h, smooth=self.smooth, cursor_x=cx,
            xaxis=self.xaxis, logy=self.logy,
        )
        axes = f"x={self.xaxis} y={'log' if self.logy else 'lin'}"
        header = (f"\033[1m{tag}\033[0m  cursor @ step {cstep}  "
                  f"({self._cursor + 1}/{len(track)})  exps={len(names)}  {axes}")
        footer = self._with_status(
            "\033[2m←/→ cursor · Shift+←/→ fast · Home/End · "
            "+/- smooth · x axis · l log · w csv · Esc back\033[0m")
        return self._crop("\n".join([header, plot] + readout + [footer]), rows)

    def _scroll_view(self, lines, w, h):
        """Clamp self._scroll and return (h fitted lines, total)."""
        total = len(lines)
        self._scroll = max(0, min(self._scroll, max(0, total - h)))
        view = [l for l in lines[self._scroll:self._scroll + h]]
        view += [""] * (h - len(view))
        return view, total

    def _text_detail(self, tag, run_name, n_runs, w, h):
        series = self._visible_runs()[run_name].series[tag]
        text = series.texts[-1] if series.texts else ""
        wrapped: List[str] = []
        for para in text.split("\n"):
            wrapped.extend(textwrap.wrap(para, w) or [""])
        view, total = self._scroll_view(wrapped, w, h)
        idx = self._detail_run % max(1, n_runs)
        header = (f"\033[1m{tag}\033[0m  [{run_name}]  exp {idx + 1}/{n_runs}  "
                  f"lines {self._scroll + 1}–{min(total, self._scroll + h)}/{total}")
        return header, "\n".join(view)

    @staticmethod
    def _parse_kv(text):
        """Pull key→value pairs from config-ish text (JSON / `k: v` / `k = v`)."""
        d = {}
        for line in text.splitlines():
            line = line.strip().rstrip(",")
            m = re.match(r'^"?([\w./\- ]+?)"?\s*[:=]\s*(.+)$', line)
            if m:
                d[m.group(1).strip()] = m.group(2).strip()
        return d

    def _text_diff_detail(self, tag, names, w, h):
        from .render import _RUN_STYLES
        runs = self._visible_runs()
        rc = self._run_colors()
        parsed = {n: self._parse_kv(runs[n].series[tag].texts[-1]
                                    if runs[n].series[tag].texts else "")
                  for n in names}
        keys = sorted({k for d in parsed.values() for k in d})
        lines: List[str] = []
        for k in keys:
            vals = [parsed[n].get(k, "—") for n in names]
            if len(set(vals)) <= 1:
                continue                                # identical → not a diff
            lines.append(f"\033[1m{k}\033[0m")
            for n in names:
                code = _RUN_STYLES[rc.get(n, 0) % len(_RUN_STYLES)][1]
                lines.append(f"  \033[{code}m●\033[0m {n[:18]:<18} "
                             f"{parsed[n].get(k, '—')}")
        if not lines:
            lines = ["(no differing keys — configs are identical, or not key:value)"]
        view, total = self._scroll_view(lines, w, h)
        header = (f"\033[1m{tag}\033[0m  diff across {len(names)} experiments  "
                  f"lines {self._scroll + 1}–{min(total, self._scroll + h)}/{total}")
        return header, "\n".join(view)

    def _view_sig(self):
        """Layout-level state — a change here triggers a *hard* clear so a new
        page/grid/detail can never leave residue. Excludes scroll / exp-switch /
        in-page focus moves, which repaint softly (no flash)."""
        per_page = max(1, self.rows * self.cols)
        page = self._focus // per_page
        return (page, round(self.smooth, 3), self.rows, self.cols,
                self.tag_filter, self.run_filter, self.renderer.name,
                self._order_rot, self._detail, self.xaxis, self.logy,
                shutil.get_terminal_size((100, 30)))

    def _signature(self):
        """Everything that affects the frame — a change triggers a (soft or hard)
        repaint; no change means no repaint, so an idle dashboard never flickers."""
        total = 0
        last_step = 0
        for run in self.reader.runs.values():
            for s in run.series.values():
                total += len(s)
                if s.steps:
                    last_step = max(last_step, s.steps[-1])
        return (total, last_step, self._focus, self._detail_run, self._scroll,
                self._cursor, self._textdiff, self._status) + self._view_sig()

    def render_once(self) -> None:
        self.reader.poll()
        print(self._build_frame())

    # -- interactive loop ----------------------------------------------------

    def run(self, *, once: bool = False) -> None:
        if once:
            self.render_once()
            return

        try:
            self._run_loop()
        finally:
            if self._restore:
                self._save_view()

    def _run_loop(self) -> None:
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
                        if self._detail is not None:
                            if self._handle_detail_key(screen, keys, tok) == "quit":
                                return
                        elif self._handle_grid_key(screen, keys, tok):
                            return  # quit
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
                self._focus = 0
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
                self._focus = 0
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

    def _handle_grid_key(self, screen, keys, tok: str) -> bool:
        """Handle a key in the grid (overview). Return True to quit."""
        if tok != "w":
            self._status = ""
        per_page = max(1, self.rows * self.cols)
        n = len(self._matching_tags())
        last = max(0, n - 1)
        if tok == "w":
            self._do_csv(screen, keys)
        elif tok in ("f", "t"):
            self._edit_filter(screen, keys, "tags" if tok == "t" else "runs")
        elif tok in ("H", "?"):
            self._show_help(screen, keys)
        elif tok in ("q", "Q", "\x03", "\x04", "ESC"):
            return True
        elif tok in ("\r", "\n"):                       # inspect focused panel
            if n:
                self._detail = self._matching_tags()[min(self._focus, last)]
                self._detail_run = 0
                self._scroll = 0
                # Start the x-cursor in the MIDDLE so it's obvious it can move
                # both ways (a cursor parked at the far right looks static).
                names = self._detail_runs()
                track = self._scalar_track(self._detail, names) if names else []
                self._cursor = max(0, (len(track) - 1) // 2)
        elif tok == "LEFT":
            self._focus = max(0, self._focus - 1)
        elif tok == "RIGHT":
            self._focus = min(last, self._focus + 1)
        elif tok == "UP":
            self._focus = max(0, self._focus - self.cols)
        elif tok == "DOWN":
            self._focus = min(last, self._focus + self.cols)
        elif tok in ("n", " ", "j"):                    # page down
            self._focus = min(last, self._focus + per_page)
        elif tok in ("p", "k"):                         # page up
            self._focus = max(0, self._focus - per_page)
        elif len(tok) == 1:
            self._handle_view_key(tok)
        return False

    def _handle_view_key(self, ch: str) -> None:
        """Smoothing / zoom / z-order — shared view options."""
        if ch in ("+", "="):
            self.smooth = min(0.99, round(self.smooth + 0.05, 2))
        elif ch == "-":
            self.smooth = max(0.0, round(self.smooth - 0.05, 2))
        elif ch == "0":
            self.smooth = 0.0
        elif ch == "z":
            self._zoom = min(len(_ZOOM_LADDER) - 1, self._zoom + 1)
            self.rows, self.cols = _ZOOM_LADDER[self._zoom]
        elif ch == "Z":
            self._zoom = max(0, self._zoom - 1)
            self.rows, self.cols = _ZOOM_LADDER[self._zoom]
        elif ch == "o":
            self._order_rot += 1
        elif ch == "l":
            self.logy = not self.logy
        elif ch == "x":
            self.xaxis = "time" if self.xaxis == "step" else "step"

    def _handle_detail_key(self, screen, keys, tok: str):
        """Handle a key in the detail (drill-down) view.

        Returns 'quit' only on Ctrl-C/Ctrl-D; Esc just goes *back* to the grid
        (press Esc again there to quit). 'q' does nothing here."""
        if tok in ("\x03", "\x04"):                     # Ctrl-C / Ctrl-D
            return "quit"
        if tok != "w":
            self._status = ""
        if tok in ("ESC", "\r", "\n"):                  # back to grid
            self._detail = None
            self._scroll = 0
            return None
        if tok == "w":
            self._do_csv(screen, keys)
            return None
        names = self._detail_runs()
        if not names:
            return None
        kind = self._visible_runs()[names[0]].series[self._detail].kind
        nruns = len(names)

        if kind == "scalar":                            # ←/→ move the x-cursor
            steps = max(1, len(self._scalar_track(self._detail, names)))
            fast = max(2, steps // 25)                   # Shift/Pg jump amount
            if tok == "LEFT":
                self._cursor -= 1
            elif tok == "RIGHT":
                self._cursor += 1
            elif tok in ("WORD-LEFT", "PGUP"):           # Shift+← / PgUp: fast
                self._cursor -= fast
            elif tok in ("WORD-RIGHT", "PGDN"):          # Shift+→ / PgDn: fast
                self._cursor += fast
            elif tok == "HOME":
                self._cursor = 0
            elif tok == "END":
                self._cursor = steps - 1
            elif len(tok) == 1:
                self._handle_view_key(tok)
            self._cursor = max(0, min(self._cursor, steps - 1))
        elif kind == "text":                            # ↑/↓ scroll, ←/→ switch exp
            if tok == "UP":
                self._scroll = max(0, self._scroll - 1)
            elif tok == "DOWN":
                self._scroll += 1
            elif tok == "PGUP":
                self._scroll = max(0, self._scroll - 10)
            elif tok == "PGDN":
                self._scroll += 10
            elif tok == "HOME":
                self._scroll = 0
            elif tok == "END":
                self._scroll = 10 ** 9
            elif tok == "LEFT":
                self._detail_run = (self._detail_run - 1) % nruns
                self._scroll = 0
            elif tok == "RIGHT":
                self._detail_run = (self._detail_run + 1) % nruns
                self._scroll = 0
            elif tok == "d":                            # toggle config-diff
                self._textdiff = not self._textdiff
                self._scroll = 0
            elif len(tok) == 1:
                self._handle_view_key(tok)
        else:                                           # histogram: ←/→ switch exp
            if tok == "LEFT":
                self._detail_run = (self._detail_run - 1) % nruns
            elif tok == "RIGHT":
                self._detail_run = (self._detail_run + 1) % nruns
            elif len(tok) == 1:
                self._handle_view_key(tok)
        return None

    # -- help overlay --------------------------------------------------------

    def _help_lines(self) -> List[str]:
        """Single-column help: one binding per line, key → action, with color."""
        BOLD, DIM, RST = "\033[1m", "\033[2m", "\033[0m"
        HDR = "\033[1;33m"          # bold yellow section header
        KEY = "\033[1;36m"          # bold cyan key
        KW = 15                     # key column width

        def hdr(t):
            return f"{HDR}{t}{RST}"

        def row(k, d):
            return f"  {KEY}{k:<{KW}}{RST} {d}"

        def note(t):
            return f"  {DIM}{t}{RST}"

        L: List[str] = []
        L.append(f"{BOLD}terminalboard{RST} {DIM}— keyboard help{RST}")
        L.append("")
        L.append(hdr("Navigation"))
        L += [
            row("←/↑/↓/→", "move focus between panels"),
            row("Enter", "inspect focused panel (full screen)"),
            row("n / Space / j", "next page"),
            row("p / k", "previous page"),
            row("z / Z", "zoom out / in (panels per page)"),
            row("o", "cycle curve order (which run is on top)"),
            row("x", "x-axis: step ↔ time"),
            row("l", "toggle log-scale Y"),
            row("w", "export focused scalar to CSV"),
            row("r", "refresh now"),
            row("q / Esc", "quit"),
        ]
        L.append("")
        L.append(hdr("Detail view — curve") + DIM + "  (after Enter)" + RST)
        L += [
            row("←/→", "move cursor (readout: value / step / time)"),
            row("Shift+←/→", "move cursor faster"),
            row("Home / End", "jump to first / last point"),
        ]
        L.append(hdr("Detail view — text"))
        L += [
            row("↑/↓  PgUp/PgDn", "scroll"),
            row("←/→", "switch experiment"),
            row("d", "config diff (only the keys that differ)"),
        ]
        L.append(hdr("Detail view — histogram"))
        L += [row("←/→", "switch experiment")]
        L.append(row("Esc", "back to grid (Esc again to quit)"))
        L.append("")
        L.append(hdr("Smoothing"))
        L += [
            row("+ / =", "more"),
            row("-", "less"),
            row("0", "off"),
        ]
        L.append("")
        L.append(hdr("Filtering"))
        L += [
            row("t", "edit tag filter"),
            row("f", "edit experiment filter"),
        ]
        L.append(note("in the filter prompt:"))
        L += [
            row("←/→", "move cursor"),
            row("↑/↓", "recall previous patterns"),
            row("Home/End", "line start / end  (also ^A / ^E)"),
            row("^W / ^K / ^U", "delete word / kill-to-end / clear"),
            row("Alt/Ctrl+←/→", "move by word"),
            row("Enter / Esc", "apply / cancel"),
        ]
        L.append("")
        L.append(hdr("Filter syntax") + DIM + "  (tags and experiments)" + RST)
        L += [
            row("word", "case-insensitive substring  (loss → train/loss)"),
            row("a b", "AND  (both must match)"),
            row("a | b , c", "OR  (| or , separate alternatives)"),
            row("* ? [ ]", "glob wildcards  (train/*loss*)"),
            row("!word", "NOT  (exclude)"),
            row("/regex/", "regex; wrap the WHOLE filter for | or spaces"),
        ]
        L.append("")
        L.append(hdr("Plot types"))
        L.append(note("scalars (curves) · text summaries · histograms (heatmap)"))
        L.append("")
        L.append(hdr("View state"))
        L.append(note("filters, zoom, smoothing, axis, order and focus are saved"))
        L.append(note("per-logdir and restored next time  (start fresh: --reset-view)"))
        return L

    def _show_help(self, screen, keys) -> None:
        cols, rows = shutil.get_terminal_size((100, 30))
        lines = self._help_lines()
        body_h = max(1, rows - 1)              # reserve a row for the footer
        maxscroll = max(0, len(lines) - body_h)
        scroll = 0
        while True:
            view = lines[scroll:scroll + body_h]
            view += [""] * (body_h - len(view))
            if maxscroll:
                pos = f"{scroll + 1}-{min(len(lines), scroll + body_h)}/{len(lines)}"
                foot = (f"\033[2m  ↑/↓ PgUp/PgDn scroll · "
                        f"any other key to return   ({pos})\033[0m")
            else:
                foot = "\033[2m  press any key to return…\033[0m"
            screen.draw("\n".join(view + [foot]), hard=True)
            chunk = keys.get(30)
            if chunk is None:
                continue
            if not maxscroll:                  # everything fits → any key returns
                return
            done = False
            for tok in self._parse_chunk(chunk):
                if tok == "UP":
                    scroll -= 1
                elif tok == "DOWN":
                    scroll += 1
                elif tok == "PGUP":
                    scroll -= body_h
                elif tok == "PGDN":
                    scroll += body_h
                elif tok == "HOME":
                    scroll = 0
                elif tok == "END":
                    scroll = maxscroll
                else:                          # any non-scroll key returns
                    done = True
                    break
                scroll = max(0, min(scroll, maxscroll))
            if done:
                return
