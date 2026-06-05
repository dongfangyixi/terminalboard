"""terminalboard — a pure-SSH terminal TensorBoard scalar viewer.

Reads TensorBoard event logs and renders live-updated scalar curves directly
inside the terminal — by default as Unicode/braille text (works over any SSH
session), or as high-quality matplotlib images via the iTerm2 inline-image
protocol with ``--hq``. No browser, no X11, no port forwarding.
"""

__version__ = "0.1.0"
