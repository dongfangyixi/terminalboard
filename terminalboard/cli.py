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
        description="A pure-SSH terminal TensorBoard scalar viewer. Live scalar "
        "curves rendered directly in the terminal — braille text by default, or "
        "high-quality iTerm2 inline images with --hq.",
    )
    p.add_argument("logdir", nargs="?", default=None,
                   help="directory of TensorBoard event files (scanned recursively)")
    p.add_argument("--logdir", dest="logdir_opt", default=None,
                   help="alternative way to pass the log directory")

    p.add_argument("--light", action="store_true",
                   help="use the dependency-free pure-Python parser")

    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--hq", action="store_const", dest="mode", const="hq",
                      help="high-quality matplotlib curves via iTerm2 inline images")
    mode.add_argument("--text", action="store_const", dest="mode", const="text",
                      help="braille/Unicode text curves (default)")
    mode.add_argument("--auto", action="store_const", dest="mode", const="auto",
                      help="pick --hq in iTerm2-class terminals, else --text")
    p.set_defaults(mode="text")

    p.add_argument("--tags", default=None,
                   help="comma-separated glob(s) to filter tags, e.g. 'train/*loss*'")
    p.add_argument("--smooth", type=float, default=0.6, metavar="ALPHA",
                   help="EMA smoothing weight in [0,1) (default: 0.6; 0 disables)")
    p.add_argument("--grid", type=_parse_grid, default=(2, 3), metavar="RxC",
                   help="panel grid per page (default: 2x3)")
    p.add_argument("--interval", type=float, default=2.0, metavar="SECONDS",
                   help="live refresh interval (default: 2.0)")
    p.add_argument("--once", action="store_true",
                   help="render a single frame and exit (no live loop)")
    p.add_argument("--list", action="store_true", dest="list_tags",
                   help="list all scalar tags found and exit")
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
    from .render import make_renderer
    from .app import App

    reader = make_reader(logdir, light=args.light)

    if args.list_tags:
        reader.poll()
        tags = reader.all_tags()
        if not tags:
            print("(no scalar tags found)")
            return 0
        print(f"# {len(tags)} scalar tags in {logdir}")
        for t in tags:
            print(t)
        return 0

    renderer = make_renderer(args.mode)
    rows, cols = args.grid
    app = App(
        reader, renderer,
        tag_filter=args.tags, smooth=args.smooth,
        rows=rows, cols=cols, interval=args.interval,
    )
    try:
        app.run(once=args.once)
    except KeyboardInterrupt:
        pass  # Screen's context manager already restored the terminal
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
