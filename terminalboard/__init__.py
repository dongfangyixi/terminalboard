"""terminalboard — a pure-terminal TensorBoard viewer.

Reads TensorBoard event logs and renders them live inside any terminal (local or
SSH) — scalars as curves, text summaries, and histograms as heatmaps. By default
as Unicode/braille text; with ``--hq`` as high-quality matplotlib images via the
iTerm2 inline-image protocol. No browser, no X11, no port forwarding.
"""

__version__ = "0.1.3"
