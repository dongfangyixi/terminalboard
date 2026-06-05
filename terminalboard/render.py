"""Renderers: turn selected scalar series into terminal output.

Two backends with the same interface:
  * :class:`TextRenderer`  — plotext braille/Unicode, the default. Pure text,
    works over any SSH session, no image generated.
  * :class:`ImageRenderer` — matplotlib rendered to an in-memory PNG and streamed
    via the iTerm2 inline-image protocol (``--hq``). No temp file on disk.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

from .model import Run, ScalarSeries

# A small, readable palette reused across runs/overlays.
_PALETTE = ["cyan", "green", "magenta", "yellow", "blue", "red", "white"]
_HEX_PALETTE = [
    "#4FC3F7", "#81C784", "#BA68C8", "#FFD54F",
    "#64B5F6", "#E57373", "#A1887F", "#4DB6AC",
]


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
    """Fit a tag into ``maxlen`` cells (plotext drops titles wider than the
    panel). Prefer the trailing path segment, then a leading-ellipsis truncate."""
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


def series_for_tag(runs: Dict[str, Run], tag: str) -> List[tuple]:
    """Return [(run_name, ScalarSeries), ...] for every run that has ``tag``."""
    out = []
    for name, run in runs.items():
        s = run.series.get(tag)
        if s is not None and len(s) > 0:
            out.append((name, s))
    return out


class Renderer:
    name = "base"

    def frame(
        self,
        runs: Dict[str, Run],
        tags: List[str],
        *,
        smooth: float = 0.0,
        max_cols: int = 3,
        width: int = 0,
        height: int = 0,
    ) -> str:  # pragma: no cover - interface
        """Return the rendered body as a single string (no printing).

        ``width``/``height`` are the cell budget the body must fit within
        (0 means "use the terminal default").
        """
        raise NotImplementedError


_EMPTY_MSG = "\n  (no scalar tags match the current filter yet…)\n"


class TextRenderer(Renderer):
    """plotext-based braille/Unicode renderer (default, no image)."""

    name = "text"

    def __init__(self, theme: str = "pro", marker: str = "braille",
                 max_points: int = 400):
        self.theme = theme
        self.marker = marker
        self.max_points = max_points

    def frame(self, runs, tags, *, smooth=0.0, max_cols=3, width=0, height=0) -> str:
        import shutil

        import plotext as plt

        plt.clear_figure()
        if not tags:
            return _EMPTY_MSG

        if not width or not height:
            tw, th = shutil.get_terminal_size((100, 30))
            width = width or tw
            height = height or max(4, th - 2)

        rows, cols = grid_dims(len(tags), max_cols)
        plt.subplots(rows, cols)
        multi_run = len(runs) > 1

        # Size EVERY panel (including empty trailing cells on a partial page) so
        # all panels in a row share dimensions — plotext crashes when joining a
        # row of mismatched-height matrices. plotext adds ~1 separator row, so
        # total height ≈ rows * panel_h + 1.
        panel_w = max(20, width // cols)
        panel_h = max(5, (height - 1) // rows)
        title_max = max(6, panel_w - 9)  # reserve room for y-axis labels

        for r in range(rows):
            for c in range(cols):
                idx = r * cols + c
                sp = plt.subplot(r + 1, c + 1)
                sp.theme(self.theme)
                sp.plotsize(panel_w, panel_h)
                if idx >= len(tags):
                    continue  # empty cell: sized but left blank
                tag = tags[idx]
                for j, (run_name, s) in enumerate(series_for_tag(runs, tag)):
                    xs, ys = subsample(
                        s.steps, ema(s.values, smooth), self.max_points
                    )
                    color = _PALETTE[j % len(_PALETTE)]
                    label = run_name if multi_run else None
                    sp.plot(xs, ys, marker=self.marker, color=color, label=label)
                sp.title(shorten_tag(tag, title_max))

        return plt.build()


class ImageRenderer(Renderer):
    """matplotlib -> in-memory PNG -> iTerm2 inline image (``--hq``)."""

    name = "hq"

    def __init__(self, dpi: int = 130, max_points: int = 5000):
        self.dpi = dpi
        self.max_points = max_points

    def frame(self, runs, tags, *, smooth=0.0, max_cols=3, width=0, height=0) -> str:
        import io

        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        from .iterm2 import image_escape

        if not tags:
            return _EMPTY_MSG

        rows, cols = grid_dims(len(tags), max_cols)
        # Leave room for the header/footer lines so the image never overflows.
        img_cells = max(4, (height - 2)) if height else 6 * rows
        plt.style.use("dark_background")
        fig, axes = plt.subplots(
            rows, cols, figsize=(4.2 * cols, 2.6 * rows), dpi=self.dpi, squeeze=False
        )
        multi_run = len(runs) > 1

        for i, tag in enumerate(tags):
            ax = axes[i // cols][i % cols]
            series = series_for_tag(runs, tag)
            for j, (run_name, s) in enumerate(series):
                xs, ys = subsample(s.steps, s.values, self.max_points)
                color = _HEX_PALETTE[j % len(_HEX_PALETTE)]
                if smooth > 0:
                    ax.plot(xs, ys, lw=0.7, alpha=0.25, color=color)
                    _, sm = subsample(s.steps, ema(s.values, smooth), self.max_points)
                    ax.plot(xs, sm, lw=1.5, color=color,
                            label=run_name if multi_run else None)
                else:
                    ax.plot(xs, ys, lw=1.2, color=color,
                            label=run_name if multi_run else None)
            ax.set_title(tag, fontsize=9)
            ax.grid(True, alpha=0.18)
            ax.tick_params(labelsize=7)
            if multi_run and series:
                ax.legend(fontsize=7, loc="best")

        # hide any unused panels
        for k in range(len(tags), rows * cols):
            axes[k // cols][k % cols].set_visible(False)

        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
        plt.close(fig)
        # height in cells keeps the image a stable size across frames, so a
        # repaint overdraws the previous image cleanly instead of resizing.
        return image_escape(buf.getvalue(), height=f"{img_cells}")


def make_renderer(mode: str) -> Renderer:
    """mode: 'text', 'hq', or 'auto'."""
    if mode == "hq":
        return ImageRenderer()
    if mode == "auto":
        from .iterm2 import supports_inline_images

        return ImageRenderer() if supports_inline_images() else TextRenderer()
    return TextRenderer()
