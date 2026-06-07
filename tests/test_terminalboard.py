"""Smoke + unit tests. Run with: pytest -q"""
from __future__ import annotations

import math

import pytest

from terminalboard.app import App, match_filter, _prev_word, _next_word
from terminalboard.model import HistogramSeries, Run, ScalarSeries, TextSeries
from terminalboard.reader import make_reader
from terminalboard.render import TextRenderer, shorten_tag, ema


# -- parsers -----------------------------------------------------------------

def test_light_parser_reads_scalars(logdir):
    runs = make_reader(str(logdir)).poll()             # default = light
    s = runs["run_a"].series["train/loss"]
    assert s.kind == "scalar"
    assert s.steps == list(range(0, 100, 10))
    assert s.values[0] == pytest.approx(1.0, rel=1e-5)
    assert s.values[-1] == pytest.approx(math.exp(-90 / 50.0), rel=1e-5)


def test_light_parser_reads_text_and_histogram(logdir):
    runs = make_reader(str(logdir)).poll()
    series = runs["run_a"].series
    assert series["note/info"].kind == "text"
    assert series["note/info"].texts[-1] == "hello\nworld"
    h = series["weights/h"]
    assert h.kind == "histogram"
    assert len(h) == 10
    edges, counts = h.buckets[0]
    assert edges and counts


def test_tb_parser_matches_light(logdir):
    pytest.importorskip("tensorboard")
    light = make_reader(str(logdir)).poll()["run_a"].series["train/loss"]
    tb = make_reader(str(logdir), use_tb=True).poll()["run_a"].series["train/loss"]
    assert light.steps == tb.steps
    assert light.values == pytest.approx(tb.values, rel=1e-5)


# -- rendering ---------------------------------------------------------------

def test_text_render_nonempty(logdir):
    runs = make_reader(str(logdir)).poll()
    body = TextRenderer().frame(runs, ["train/loss", "train/acc"],
                                smooth=0.5, max_cols=2, width=90, height=20)
    assert isinstance(body, str) and body.strip()


def test_text_render_fits_height(logdir):
    runs = make_reader(str(logdir)).poll()
    body = TextRenderer().frame(runs, ["train/loss"], width=80, height=18)
    assert body.count("\n") + 1 <= 18


def test_render_all_kinds_mixed(logdir):
    runs = make_reader(str(logdir)).poll()
    body = TextRenderer().frame(
        runs, ["train/loss", "note/info", "weights/h"],
        max_cols=3, width=120, height=20,
    )
    assert isinstance(body, str) and body.strip()


def test_flat_series_does_not_hang():
    run = Run("r", "/tmp")
    run.series["flat"] = ScalarSeries("flat", [0, 1, 2, 3], [0.0, 0.0, 0.0, 0.0])
    body = TextRenderer().frame({"r": run}, ["flat"], width=60, height=16)
    assert isinstance(body, str) and body.strip()


def test_partial_page_no_crash(logdir):
    runs = make_reader(str(logdir)).poll()
    body = TextRenderer().frame(runs, ["train/loss"], max_cols=2, width=90, height=20)
    assert isinstance(body, str)


# -- filter grammar ----------------------------------------------------------

def test_match_filter_basic():
    assert match_filter("loss", "train/loss")
    assert match_filter("LOSS", "train/loss")            # case-insensitive
    assert match_filter("train/*acc*", "train/acc")      # glob
    assert not match_filter("xyz", "train/loss")
    assert match_filter(None, "anything")                # empty matches all


def test_match_filter_or_and_not_regex():
    assert match_filter("loss | acc", "train/acc")       # OR with |
    assert match_filter("a, b", "xbx")                   # OR with ,
    assert match_filter("train loss", "train/loss")      # AND (both present)
    assert not match_filter("train loss", "val/loss")    # AND fails (no train)
    assert match_filter("!val", "train/loss")            # NOT
    assert not match_filter("!loss", "train/loss")
    assert match_filter("/lo.s/", "train/loss")          # regex (per-word)
    assert not match_filter("/^loss$/", "train/loss")


def test_match_filter_whole_pattern_regex():
    # a whole-pattern /.../ is real regex, so | and spaces work inside it
    assert match_filter("/(loss|lr)/", "train/lr")
    assert match_filter("/(loss|lr)/", "val/loss")
    assert not match_filter("/(loss|lr)/", "train/acc")
    assert match_filter(r"/^train\/(loss|lr)$/", "train/loss")
    assert not match_filter(r"/^train\/(loss|lr)$/", "train/visual_loss")
    assert not match_filter("/[unclosed/", "anything")   # bad regex -> no match


# -- word-edit helpers -------------------------------------------------------

def test_word_motion():
    buf = list("train/loss val")
    assert _prev_word(buf, len(buf)) == buf.index("v")   # back to 'val'
    assert _next_word(buf, 0) == len("train")            # forward over 'train'


# -- z-order -----------------------------------------------------------------

def test_run_order_cycles(logdir):
    app = App(make_reader(str(logdir)), TextRenderer())
    app.reader.poll()
    base = app._run_order()
    app._order_rot += 1
    rotated = app._run_order()
    assert set(base) == set(rotated)
    if len(base) > 1:
        assert base != rotated                            # different top


# -- drill-down (focus + detail) ---------------------------------------------

class _FakeScreen:
    def draw(self, frame, hard=False):
        pass


def _app(logdir):
    a = App(make_reader(str(logdir)), TextRenderer(), rows=2, cols=2)
    a.reader.poll()
    return a


def test_focus_navigation(logdir):
    a = _app(logdir)
    a._handle_grid_key(_FakeScreen(), None, "RIGHT")
    assert a._focus == 1
    a._handle_grid_key(_FakeScreen(), None, "DOWN")
    assert a._focus == 1 + a.cols


def test_detail_text_scroll_and_switch(logdir):
    a = _app(logdir)
    a.tag_filter = "note/info"
    a._handle_grid_key(_FakeScreen(), None, "\r")        # Enter -> detail
    assert a._detail == "note/info"
    frame = a._build_detail_frame()
    assert "note/info" in frame
    a._handle_detail_key("DOWN")
    assert a._scroll == 1
    a._handle_detail_key("RIGHT")                        # switch experiment (wraps)
    assert a._build_detail_frame().strip()
    a._handle_detail_key("ESC")                          # back to grid
    assert a._detail is None


def test_detail_histogram_and_scalar(logdir):
    a = _app(logdir)
    for tag in ("weights/h", "train/loss"):
        a.tag_filter = tag
        a._detail = None
        a._handle_grid_key(_FakeScreen(), None, "\r")
        assert a._detail == tag
        assert a._build_detail_frame().strip()
        a._handle_detail_key("ESC")


def test_scalar_detail_cursor(logdir):
    a = _app(logdir)
    a.tag_filter = "train/loss"
    a._handle_grid_key(_FakeScreen(), None, "\r")
    track = a._scalar_track("train/loss", a._detail_runs())
    assert a._cursor == len(track) - 1           # starts at the latest point
    a._handle_detail_key("LEFT")
    assert a._cursor == len(track) - 2
    a._handle_detail_key("HOME")
    assert a._cursor == 0
    a._handle_detail_key("END")
    assert a._cursor == len(track) - 1
    frame = a._build_detail_frame()
    assert "cursor @ step" in frame and "value" in frame and "smoothed" in frame


def test_config_diff(tmp_path):
    # two runs whose config differs in one key
    import conftest as C
    for name, lr in [("a", "0.001"), ("b", "0.003")]:
        d = tmp_path / name
        d.mkdir()
        cfg = '{\n  "lr": %s,\n  "model": "resnet"\n}' % lr
        C.write_events(d / "events.out.tfevents.1.h.1.0",
                       [(0, [C.text_value("config", cfg)])])
    a = App(make_reader(str(tmp_path)), TextRenderer())
    a.reader.poll()
    a.tag_filter = "config"
    a._handle_grid_key(_FakeScreen(), None, "\r")
    a._handle_detail_key("d")                       # toggle diff
    frame = a._build_detail_frame()
    assert "diff across 2 experiments" in frame
    assert "lr" in frame and "0.001" in frame and "0.003" in frame
    assert "model" not in frame                     # identical key is hidden


def test_csv_export(logdir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a = _app(logdir)
    a.tag_filter = "train/loss"
    msg = a._export_csv()
    assert "wrote" in msg
    f = tmp_path / "train_loss.csv"
    assert f.exists()
    lines = f.read_text().splitlines()
    assert lines[0].startswith("step,") and len(lines) > 1


def test_csv_export_skips_nonscalar(logdir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a = _app(logdir)
    a.tag_filter = "note/info"          # a text tag
    assert "not a scalar" in a._export_csv()


def test_config_load(tmp_path, monkeypatch):
    pytest.importorskip("tomllib")      # stdlib on 3.11+
    cfg = tmp_path / "c.toml"
    cfg.write_text('[terminalboard]\nsmooth = 0.9\ngrid = "3x3"\nlogy = true\n')
    monkeypatch.setenv("TERMINALBOARD_CONFIG", str(cfg))
    from terminalboard.cli import load_config
    c = load_config()
    assert c["smooth"] == 0.9 and c["grid"] == "3x3" and c["logy"] is True


def test_logy_and_xaxis_toggle(logdir):
    a = _app(logdir)
    a._handle_view_key("l")
    assert a.logy is True
    a._handle_view_key("x")
    assert a.xaxis == "time"
    assert a._build_frame().strip()                 # renders without error


def test_detail_q_does_not_quit_esc_goes_back(logdir):
    a = _app(logdir)
    a.tag_filter = "train/loss"
    a._handle_grid_key(_FakeScreen(), None, "\r")
    assert a._detail == "train/loss"
    assert a._handle_detail_key("q") is None      # q does nothing in detail
    assert a._detail == "train/loss"              # still in detail
    a._handle_detail_key("ESC")                   # Esc -> back to grid
    assert a._detail is None
    # and from the grid, Esc quits
    assert a._handle_grid_key(_FakeScreen(), None, "ESC") is True


# -- helpers -----------------------------------------------------------------

def test_shorten_tag():
    assert shorten_tag("train/loss", 50) == "train/loss"
    assert shorten_tag("a/b/very_long_leaf_name", 6).startswith(("v", "…"))


def test_ema_smoothing():
    assert ema([1.0, 1.0, 1.0], 0.5) == [1.0, 1.0, 1.0]
    assert ema([], 0.5) == []
    assert ema([0.0, 10.0], 0.0) == [0.0, 10.0]


# -- CLI ---------------------------------------------------------------------

def test_cli_once_default(logdir, capsys):
    from terminalboard.cli import main
    rc = main([str(logdir), "--once", "--tags", "loss"])
    assert rc == 0
    assert "loss" in capsys.readouterr().out


def test_cli_list(logdir, capsys):
    from terminalboard.cli import main
    rc = main([str(logdir), "--list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "train/loss" in out and "note/info" in out and "weights/h" in out


def test_cli_missing_logdir():
    from terminalboard.cli import main
    assert main(["/no/such/dir", "--once"]) == 2
