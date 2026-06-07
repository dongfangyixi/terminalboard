"""Rendering: turn selected series into terminal output.

Panels are rendered independently into fixed-size text blocks and then tiled into
a grid, so a page can freely mix **scalars** (curves), **text** summaries, and
**histograms** (drawn as a heatmap of the distribution over steps). Everything is
pure text (plotext braille/Unicode + custom block widgets) — no images, works
over any terminal.
"""
from __future__ import annotations

import math
import re
import textwrap
from typing import Dict, List, Optional, Sequence

from .model import Run

# A small, readable palette reused across runs/overlays. Each entry pairs a
# plotext color name (for the curve) with the matching ANSI SGR code (for the
# run legend / markers we draw ourselves).
_RUN_STYLES = [
    ("cyan", "36"), ("green", "32"), ("magenta", "35"), ("yellow", "33"),
    ("blue", "34"), ("red", "31"), ("white", "37"),
]
_PALETTE = [name for name, _ in _RUN_STYLES]
_SHADES = " ░▒▓█"
_EMPTY_MSG = "\n  (nothing matches the current filter yet…)\n"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


# --- generic helpers --------------------------------------------------------

def ema(values: Sequence[float], alpha: float) -> List[float]:
    """Exponential moving average; ``alpha`` in [0, 1) is the smoothing weight."""
    if alpha <= 0 or not values:
        return list(values)
    out: List[float] = []
    m = values[0]
    for v in values:
        m = alpha * m + (1 - alpha) * v
        out.append(m)
    return out


def subsample(xs: Sequence, ys: Sequence, max_points: int):
    """Evenly thin a series to at most ``max_points`` for fast drawing."""
    n = len(xs)
    if max_points <= 0 or n <= max_points:
        return list(xs), list(ys)
    step = n / max_points
    idx = [int(i * step) for i in range(max_points)]
    if idx[-1] != n - 1:
        idx.append(n - 1)
    return [xs[i] for i in idx], [ys[i] for i in idx]


def shorten_tag(tag: str, maxlen: int) -> str:
    """Fit a tag into ``maxlen`` cells — prefer the trailing path segment, then a
    leading-ellipsis truncate."""
    if maxlen <= 0 or len(tag) <= maxlen:
        return tag
    leaf = tag.rsplit("/", 1)[-1]
    if len(leaf) <= maxlen:
        return leaf
    if maxlen <= 1:
        return leaf[:maxlen]
    return "…" + leaf[-(maxlen - 1):]


def grid_dims(n: int, max_cols: int = 3):
    """Choose (rows, cols) for ``n`` panels."""
    if n <= 0:
        return 1, 1
    cols = min(max_cols, n)
    rows = math.ceil(n / cols)
    return rows, cols


def _reset_plotext(plt) -> None:
    """Fully reset plotext's global figure between plots (clear_figure only
    re-inits the active subplot, leaking state across calls)."""
    try:
        import plotext._core as _pc
        _pc._figure.__init__()
    except Exception:
        plt.clear_figure()


def run_legend(run_order, run_colors, width: int) -> str:
    """A single colored line mapping each run to its (stable) color."""
    parts = []
    budget = max(10, width // max(1, len(run_order)) - 4)
    for name in run_order:
        code = _RUN_STYLES[run_colors.get(name, 0) % len(_RUN_STYLES)][1]
        label = name if len(name) <= budget else "…" + name[-(budget - 1):]
        parts.append(f"\033[{code}m──\033[0m {label}")
    return "  " + "   ".join(parts)


# --- block / tiling helpers (ANSI-aware) ------------------------------------

def _vis_len(s: str) -> int:
    return len(_ANSI_RE.sub("", s))


def _fit(s: str, w: int) -> str:
    """Pad/truncate ``s`` to exactly ``w`` visible columns, keeping ANSI codes."""
    vl = _vis_len(s)
    if vl == w:
        return s
    if vl < w:
        return s + " " * (w - vl)
    out, count, i, n = [], 0, 0, len(s)
    while i < n and count < w:
        m = _ANSI_RE.match(s, i)
        if m:
            out.append(m.group())
            i = m.end()
            continue
        out.append(s[i])
        count += 1
        i += 1
    return "".join(out) + "\033[0m"


def _to_block(s: str, w: int, h: int) -> List[str]:
    lines = [_fit(line, w) for line in s.split("\n")[:h]]
    while len(lines) < h:
        lines.append(" " * w)
    return lines


def _empty_block(w: int, h: int) -> List[str]:
    return [" " * w for _ in range(h)]


def _tile(blocks: List[List[str]], rows: int, cols: int, h: int,
          gutter: int = 1) -> str:
    sep = " " * gutter
    out = []
    for r in range(rows):
        for line in range(h):
            out.append(sep.join(blocks[r * cols + c][line] for c in range(cols)))
    return "\n".join(out)


def _even_indices(n: int, k: int) -> List[int]:
    if n <= 0:
        return []
    if n <= k:
        return list(range(n))
    return [int(i * n / k) for i in range(k)]


def _short_num(x: float) -> str:
    ax = abs(x)
    if ax != 0 and (ax >= 1000 or ax < 0.01):
        return f"{x:.1e}"
    return f"{x:.2f}"


def _pairs(runs, order, tag):
    """[(run_name, series)] for runs (in draw order) that have ``tag``."""
    return [(n, runs[n].series[tag]) for n in order if tag in runs[n].series]


# --- panel widgets ----------------------------------------------------------

def _xs(s, xaxis):
    """X values for a scalar series: by step, or relative wall-time (seconds)."""
    if xaxis == "time" and getattr(s, "wall_times", None):
        t0 = s.wall_times[0]
        return [wt - t0 for wt in s.wall_times]
    return s.steps


def _scalar_block(tag, pairs, run_color, w, h, smooth, marker, theme,
                  max_points, cursor=None, xaxis="step", logy=False) -> List[str]:
    import plotext as plt

    _reset_plotext(plt)
    plt.plotsize(w, h)
    plt.theme(theme)
    vmin = vmax = None
    for run_name, s in pairs:
        if not len(s):
            continue
        xs, ys = subsample(_xs(s, xaxis), ema(s.values, smooth), max_points)
        color = _PALETTE[run_color.get(run_name, 0) % len(_PALETTE)]
        plt.plot(xs, ys, marker=marker, color=color)   # draw order = z-order
        if ys:
            lo, hi = min(ys), max(ys)
            vmin = lo if vmin is None else min(vmin, lo)
            vmax = hi if vmax is None else max(vmax, hi)
    do_log = logy and vmin is not None and vmin > 0   # log needs positive values
    if do_log:
        try:
            plt.yscale("log")
        except Exception:
            do_log = False
    # A flat series gives plotext a zero-height axis whose tick search can hang.
    if not do_log and vmin is not None and (vmax - vmin) <= abs(vmin) * 1e-9 + 1e-12:
        pad = abs(vmin) * 0.5 or 1.0
        plt.ylim(vmin - pad, vmax + pad)
    if cursor is not None:
        try:
            plt.vertical_line(cursor, color="white")
        except Exception:
            pass
    plt.title(shorten_tag(tag, max(6, w - 9)))
    return _to_block(plt.build(), w, h)


def _text_block(tag, pairs, run_color, w, h, multi_run) -> List[str]:
    lines = [_fit("\033[1m" + shorten_tag(tag, w) + "\033[0m", w)]
    body: List[str] = []
    for run_name, s in pairs:
        if not len(s):
            continue
        if multi_run:
            code = _RUN_STYLES[run_color.get(run_name, 0) % len(_RUN_STYLES)][1]
            body.append(f"\033[{code}m● {shorten_tag(run_name, max(4, w - 2))}\033[0m")
        latest = s.texts[-1] if s.texts else ""
        for para in (latest.splitlines() or [""]):
            body.extend(textwrap.wrap(para, w) or [""])
        if multi_run:
            body.append("")
    if not body:
        body = ["(no text)"]
    for line in body[:h - 1]:
        lines.append(_fit(line, w))
    while len(lines) < h:
        lines.append(" " * w)
    return lines[:h]


def _rebin(edges, counts, lo, hi, bins) -> List[float]:
    """Distribute bucket counts onto a fixed ``bins``-row grid over [lo, hi]."""
    out = [0.0] * bins
    if hi <= lo or not counts:
        return out
    binw = (hi - lo) / bins
    for i, c in enumerate(counts):
        if c <= 0:
            continue
        left = edges[i - 1] if i > 0 else lo
        right = edges[i] if i < len(edges) else hi
        a, b = max(lo, left), min(hi, right)
        if b <= a:
            continue
        span = (right - left) or binw
        first = max(0, int((a - lo) / binw))
        last = min(bins - 1, int((b - lo) / binw))
        for bi in range(first, last + 1):
            bl = lo + bi * binw
            ov = min(bl + binw, b) - max(bl, a)
            if ov > 0:
                out[bi] += c * (ov / span)
    return out


def _histogram_block(tag, pairs, run_color, w, h, multi_run) -> List[str]:
    chosen = None
    for run_name, s in pairs:        # last with data = the one "on top"
        if len(s):
            chosen = (run_name, s)
    if chosen is None:
        return _empty_block(w, h)
    run_name, s = chosen
    title_txt = shorten_tag(tag, w)
    if multi_run:
        title_txt = f"{shorten_tag(tag, max(6, w - 14))} [{shorten_tag(run_name, 10)}]"
    lines = [_fit("\033[1m" + title_txt + "\033[0m", w)]

    all_edges = [e for (edges, _c) in s.buckets for e in edges]
    if not all_edges:
        return _empty_block(w, h)
    lo, hi = min(all_edges), max(all_edges)
    if hi <= lo:
        hi = lo + 1.0
    plot_h = max(2, h - 2)
    ylab_w = 8
    plot_w = max(4, w - ylab_w - 1)

    cols = []
    gmax = 0.0
    for ci in _even_indices(len(s.buckets), plot_w):
        edges, counts = s.buckets[ci]
        col = _rebin(edges, counts, lo, hi, plot_h)
        gmax = max(gmax, max(col) if col else 0.0)
        cols.append(col)
    gmax = gmax or 1.0

    for r in range(plot_h):
        bin_i = plot_h - 1 - r        # top row = highest value bin
        cells = "".join(
            _SHADES[min(len(_SHADES) - 1, int((col[bin_i] / gmax) * len(_SHADES)))]
            for col in cols
        )
        lab = _short_num(hi) if r == 0 else (_short_num(lo) if r == plot_h - 1 else "")
        lines.append(_fit(f"{lab:>{ylab_w}} \033[36m{cells}\033[0m", w))

    steps = s.steps
    xaxis = f"{'':>{ylab_w}} {steps[0]}…{steps[-1]}" if steps else ""
    lines.append(_fit(xaxis, w))
    while len(lines) < h:
        lines.append(" " * w)
    return lines[:h]


# --- renderers --------------------------------------------------------------

class Renderer:
    name = "base"

    def frame(self, runs, tags, *, smooth=0.0, max_cols=3, width=0, height=0,
              run_colors=None, run_order=None, focus=-1) -> str:  # pragma: no cover
        """Return the rendered body as a single string (no printing).

        ``run_colors`` maps run -> stable color index; ``run_order`` is the draw
        order (last = on top) used for z-order cycling; ``focus`` is the index of
        the panel to highlight (-1 = none).
        """
        raise NotImplementedError


def _highlight_block(block: List[str]) -> List[str]:
    """Reverse-video a panel's title line to mark it as focused."""
    if block:
        block[0] = "\033[7m" + _ANSI_RE.sub("", block[0]) + "\033[0m"
    return block


class TextRenderer(Renderer):
    """plotext + custom block widgets, tiled into a grid (default)."""

    name = "text"

    def __init__(self, theme: str = "pro", marker: str = "braille",
                 max_points: int = 400):
        self.theme = theme
        self.marker = marker
        self.max_points = max_points

    def frame(self, runs, tags, *, smooth=0.0, max_cols=3, width=0, height=0,
              run_colors=None, run_order=None, focus=-1, xaxis="step",
              logy=False) -> str:
        if not tags:
            return _EMPTY_MSG
        if not width or not height:
            import shutil
            tw, th = shutil.get_terminal_size((100, 30))
            width = width or tw
            height = height or max(4, th - 2)

        run_color = run_colors or {n: i for i, n in enumerate(sorted(runs))}
        order = [n for n in (run_order or sorted(runs)) if n in runs]
        multi_run = len(runs) > 1
        legend_rows = 1 if multi_run else 0

        rows, cols = grid_dims(len(tags), max_cols)
        gutter = 1
        panel_w = max(16, (width - (cols - 1) * gutter) // cols)
        panel_h = max(4, (height - legend_rows) // rows)

        blocks: List[List[str]] = []
        for idx in range(rows * cols):
            if idx >= len(tags):
                blocks.append(_empty_block(panel_w, panel_h))
                continue
            tag = tags[idx]
            pairs = _pairs(runs, order, tag)
            kind = pairs[0][1].kind if pairs else "scalar"
            if kind == "text":
                block = _text_block(tag, pairs, run_color, panel_w,
                                    panel_h, multi_run)
            elif kind == "histogram":
                block = _histogram_block(tag, pairs, run_color, panel_w,
                                         panel_h, multi_run)
            else:
                block = _scalar_block(tag, pairs, run_color, panel_w,
                                      panel_h, smooth, self.marker,
                                      self.theme, self.max_points,
                                      xaxis=xaxis, logy=logy)
            if idx == focus:
                block = _highlight_block(block)
            blocks.append(block)
        body = _tile(blocks, rows, cols, panel_h, gutter)
        if multi_run:
            return run_legend(sorted(runs), run_color, width) + "\n" + body
        return body

    def detail_scalar(self, runs, tag, *, order, run_color, w, h, smooth,
                      cursor_x, xaxis="step", logy=False) -> str:
        """Full-screen scalar plot with a vertical cursor at ``cursor_x`` (in the
        active x-axis domain)."""
        pairs = _pairs(runs, order, tag)
        block = _scalar_block(tag, pairs, run_color, w, h, smooth, self.marker,
                              self.theme, self.max_points, cursor=cursor_x,
                              xaxis=xaxis, logy=logy)
        return "\n".join(block)
