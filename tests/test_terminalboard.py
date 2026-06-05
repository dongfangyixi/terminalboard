"""Smoke + unit tests. Run with: pytest -q"""
from __future__ import annotations

import math

import pytest

from terminalboard.app import App, match_filter
from terminalboard.model import Run, ScalarSeries
from terminalboard.reader import make_reader
from terminalboard.render import TextRenderer, shorten_tag, ema


# -- parsers -----------------------------------------------------------------

def test_light_parser_reads_scalars(logdir):
    runs = make_reader(str(logdir), light=True).poll()
    assert "run_a" in runs
    s = runs["run_a"].series["train/loss"]
    assert s.steps == list(range(0, 100, 10))
    assert s.values[0] == pytest.approx(1.0, rel=1e-5)
    assert s.values[-1] == pytest.approx(math.exp(-90 / 50.0), rel=1e-5)


def test_tb_parser_matches_light(logdir):
    pytest.importorskip("tensorboard")
    light = make_reader(str(logdir), light=True).poll()["run_a"].series["train/loss"]
    tb = make_reader(str(logdir), light=False).poll()["run_a"].series["train/loss"]
    assert light.steps == tb.steps
    assert light.values == pytest.approx(tb.values, rel=1e-5)


# -- rendering ---------------------------------------------------------------

def test_text_render_nonempty(logdir):
    runs = make_reader(str(logdir), light=True).poll()
    body = TextRenderer().frame(runs, ["train/loss", "train/acc"],
                                smooth=0.5, max_cols=2, width=90, height=20)
    assert isinstance(body, str) and body.strip()


def test_text_render_fits_height(logdir):
    runs = make_reader(str(logdir), light=True).poll()
    body = TextRenderer().frame(runs, ["train/loss"], width=80, height=18)
    assert body.count("\n") + 1 <= 18


def test_flat_series_does_not_hang():
    run = Run("r", "/tmp")
    run.series["flat"] = ScalarSeries("flat", [0, 1, 2, 3], [0.0, 0.0, 0.0, 0.0])
    body = TextRenderer().frame({"r": run}, ["flat"], width=60, height=16)
    assert isinstance(body, str) and body.strip()


def test_partial_page_no_crash(logdir):
    # 1 tag in a 2x2 grid -> 3 empty cells; must not raise.
    runs = make_reader(str(logdir), light=True).poll()
    body = TextRenderer().frame(runs, ["train/loss"], max_cols=2, width=90, height=20)
    assert isinstance(body, str)


# -- helpers -----------------------------------------------------------------

def test_match_filter():
    assert match_filter("loss", "train/loss")
    assert match_filter("LOSS", "train/loss")            # case-insensitive
    assert match_filter("train/*acc*", "train/acc")      # glob
    assert match_filter("lr,loss", "train/loss")         # comma-separated
    assert not match_filter("xyz", "train/loss")
    assert match_filter(None, "anything")                # empty matches all


def test_shorten_tag():
    assert shorten_tag("train/loss", 50) == "train/loss"
    assert shorten_tag("a/b/very_long_leaf_name", 6).startswith(("v", "…"))


def test_ema_smoothing():
    assert ema([1.0, 1.0, 1.0], 0.5) == [1.0, 1.0, 1.0]
    assert ema([], 0.5) == []
    assert ema([0.0, 10.0], 0.0) == [0.0, 10.0]          # alpha 0 = no smoothing


# -- CLI ---------------------------------------------------------------------

def test_cli_once_light(logdir, capsys):
    from terminalboard.cli import main
    rc = main([str(logdir), "--once", "--light", "--tags", "loss"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "loss" in out


def test_cli_list(logdir, capsys):
    from terminalboard.cli import main
    rc = main([str(logdir), "--light", "--list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "train/loss" in out and "train/acc" in out


def test_cli_missing_logdir():
    from terminalboard.cli import main
    assert main(["/no/such/dir", "--once"]) == 2
