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

    def render(
        self,
        runs: Dict[str, Run],
        tags: List[str],
        *,
        smooth: float = 0.0,
        max_cols: int = 3,
        header: str = "",
    ) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class TextRenderer(Renderer):
    """plotext-based braille/Unicode renderer (default, no image)."""

    name = "text"

    def __init__(self, theme: str = "pro", marker: str = "braille",
                 max_points: int = 400):
        self.theme = theme
        self.marker = marker
        self.max_points = max_points

    def render(self, runs, tags, *, smooth=0.0, max_cols=3, header=""):
        import plotext as plt

        plt.clear_figure()
        if not tags:
            print(header)
            print("\n  (no scalar tags match the current filter yet…)\n")
            return

        rows, cols = grid_dims(len(tags), max_cols)
        plt.subplots(rows, cols)
        multi_run = len(runs) > 1

        for i, tag in enumerate(tags):
            r, c = divmod(i, cols)
            sp = plt.subplot(r + 1, c + 1)
            sp.theme(self.theme)
            series = series_for_tag(runs, tag)
            for j, (run_name, s) in enumerate(series):
                xs, ys = subsample(s.steps, ema(s.values, smooth), self.max_points)
                color = _PALETTE[j % len(_PALETTE)]
                label = run_name if multi_run else None
                sp.plot(xs, ys, marker=self.marker, color=color, label=label)
            sp.title(tag)

        if header:
            print(header)
        plt.show()


class ImageRenderer(Renderer):
    """matplotlib -> in-memory PNG -> iTerm2 inline image (``--hq``)."""

    name = "hq"

    def __init__(self, dpi: int = 130, max_points: int = 5000):
        self.dpi = dpi
        self.max_points = max_points

    def render(self, runs, tags, *, smooth=0.0, max_cols=3, header=""):
        import io

        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        from .iterm2 import show_image

        if header:
            print(header)
        if not tags:
            print("\n  (no scalar tags match the current filter yet…)\n")
            return

        rows, cols = grid_dims(len(tags), max_cols)
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
        show_image(buf.getvalue())


def make_renderer(mode: str) -> Renderer:
    """mode: 'text', 'hq', or 'auto'."""
    if mode == "hq":
        return ImageRenderer()
    if mode == "auto":
        from .iterm2 import supports_inline_images

        return ImageRenderer() if supports_inline_images() else TextRenderer()
    return TextRenderer()
