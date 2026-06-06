"""Command-line entry point for terminalboard."""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import __version__


def _parse_grid(value: str):
    try:
        r, c = value.lower().split("x")
        return int(r), int(c)
    except Exception:
        raise argparse.ArgumentTypeError(
            f"--grid expects RxC (e.g. 2x3), got {value!r}"
        )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="terminalboard",
        description="A pure-terminal TensorBoard viewer. Live scalar curves, "
        "text summaries, and histogram heatmaps rendered directly in the "
        "terminal as braille/Unicode — no browser, no X11, no port forwarding.",
    )
    p.add_argument("logdir", nargs="?", default=None,
                   help="directory of TensorBoard event files (scanned recursively)")
    p.add_argument("--logdir", dest="logdir_opt", default=None,
                   help="alternative way to pass the log directory")

    p.add_argument("--tb", "--accurate", action="store_true", dest="tb",
                   help="parse with the tensorboard library instead of the "
                        "built-in pure-Python reader (needs 'terminalboard[tb]')")
    # The built-in parser is the default now; --light is kept as a no-op alias.
    p.add_argument("--light", action="store_true",
                   help=argparse.SUPPRESS)

    p.add_argument("--tags", default=None,
                   help="comma-separated filter for tags, e.g. 'train/*loss*' "
                        "(also editable live with the 't' key)")
    p.add_argument("--experiments", "--runs", default=None, dest="experiments",
                   help="comma-separated filter for experiments/runs "
                        "(also editable live with the 'f' key)")
    p.add_argument("--smooth", type=float, default=0.6, metavar="ALPHA",
                   help="EMA smoothing weight in [0,1) (default: 0.6; 0 disables)")
    p.add_argument("--grid", type=_parse_grid, default=(2, 3), metavar="RxC",
                   help="panel grid per page (default: 2x3)")
    p.add_argument("--interval", type=float, default=2.0, metavar="SECONDS",
                   help="live refresh interval (default: 2.0)")
    p.add_argument("--once", action="store_true",
                   help="render a single frame and exit (no live loop)")
    p.add_argument("--list", action="store_true", dest="list_tags",
                   help="list all tags found and exit")
    p.add_argument("--version", action="version",
                   version=f"terminalboard {__version__}")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logdir = args.logdir_opt or args.logdir
    if not logdir:
        print("terminalboard: a logdir is required "
              "(e.g. `terminalboard ../tb_logs`)", file=sys.stderr)
        return 2

    import os
    if not os.path.isdir(logdir):
        print(f"terminalboard: not a directory: {logdir}", file=sys.stderr)
        return 2

    from .reader import make_reader
    from .render import TextRenderer
    from .app import App

    reader = make_reader(logdir, use_tb=args.tb)

    if args.list_tags:
        reader.poll()
        tags = reader.all_tags()
        if not tags:
            print("(no tags found)")
            return 0
        print(f"# {len(tags)} tags in {logdir}")
        for t in tags:
            print(t)
        return 0

    renderer = TextRenderer()
    rows, cols = args.grid
    app = App(
        reader, renderer,
        tag_filter=args.tags, run_filter=args.experiments, smooth=args.smooth,
        rows=rows, cols=cols, interval=args.interval,
    )
    try:
        app.run(once=args.once)
    except KeyboardInterrupt:
        pass  # Screen's context manager already restored the terminal
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
