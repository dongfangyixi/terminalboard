# Changelog

## Unreleased

### Added
- **Distributions view** (`b`): toggle histogram panels between the heatmap and
  percentile bands (0/25/50/75/100 over steps; median highlighted) — the same
  data TensorBoard shows under "Distributions". Works in the grid and detail.
- **PR curves**: the `pr_curves` plugin (precision-vs-recall) is parsed and drawn
  as a curve; the detail view steps through training with `←/→`.
- **HParams table** (`P`): a full-screen, scrollable table of runs × hyper-
  parameters × final metric values, parsed from the `hparams` plugin.
- **Type selector** (`c`): cycle the grid between all types / scalars /
  histograms / text / pr-curves — a quick filter by data type.
- The bundled demo (`examples/gen_demo_logs.py`) now also emits a PR curve and
  HParams so all five types are visible out of the box.

## 0.3.0 — 2026-06-08

### Added
- **Log-Y** (`l`) and **x-axis step↔time** (`x`) toggles for scalar panels.
- **Config diff** in the text detail view (`d`): show only the config keys that
  differ across experiments.
- **CSV export** (`w`): write the focused scalar tag to `<tag>.csv`.
- **Config file** (`~/.config/terminalboard.toml` / `$TERMINALBOARD_CONFIG`) for
  defaults (smooth, grid, interval, tags, experiments, xaxis, logy, tb).
- Bundled **demo generator** (`examples/gen_demo_logs.py`) and a **GIF recording
  script** (`scripts/record_demo.sh`); `uvx`/`pipx run` note in the README.

### Added
- **Per-logdir view persistence**: filters, zoom, smoothing, x-axis, log-Y,
  curve order and focus are saved on exit (under `$XDG_STATE_HOME` /
  `~/.local/state/terminalboard/`) and restored when you reopen the same logdir.
  Explicit CLI flags still win; `--reset-view` ignores the saved state (and
  `restore = false` in the config disables persistence).

### Fixed
- **Scalar detail cursor** now ranges over the **union of all visible runs'
  steps**, so `←/→`/`End` can reach the last point among *all* experiments —
  previously it stopped at one run's final step even when others had data
  further right. It also **starts in the middle** of the range (so it's clear it
  can move both ways) instead of parked at the far right.

### Changed
- **Legend** now shows **full experiment names**, wrapping over multiple lines
  instead of truncating with `…` — so names are readable when filtering.
- **Panel titles** show the **full tag path**, wrapping over up to 3 lines (with
  a uniform, row-aligned height across the page); when still too long the last
  line keeps the leaf via leading ellipsis. Scalar titles are drawn by us, so a
  wide title is never dropped by plotext.

## 0.2.1

### Fixed / changed
- **Filter regex**: when the *whole* filter is wrapped in `/.../` it's now a real
  regex (`re.search`, case-insensitive), so `|` and spaces work
  (e.g. `/^train\/(loss|lr)$/`). A per-word `/regex/` (without `|`/spaces) still
  works inside boolean expressions.
- Help overlay clarifies the regex rule; package summary now mentions text and
  histogram support.

## 0.2.0

### Added
- **Plot types** beyond scalars: **text summaries** and **histograms** (drawn as
  a heatmap of the distribution over steps), mixed freely in the grid.
- **Focus + drill-down**: arrow keys move a highlighted panel; **Enter** inspects
  a tag full-screen, **Esc** goes back.
  - **Scalar detail** has a TensorBoard-style **x-cursor** — `←/→` move one data
    point, `Shift+←/→` jump fast — with a per-experiment **value / smoothed /
    step / wall-time** readout.
  - **Histogram / text detail**: `←/→` switch experiment; text is **scrollable**
    (`↑/↓`, `PgUp/PgDn`, `Home/End`), one experiment per screen.
- **Curve z-order** (`o`): cycle which overlapping experiment is drawn on top.
- **Richer filter grammar**: `|`/`,` = OR, space/`&` = AND, `!` = NOT,
  `/regex/`, and `* ? [ ]` globs.
- **Readline editing** in filter prompts: `^W`, `^K`, `Alt`/`Ctrl`+arrows, and
  per-kind history.
- **Help overlay** on `H` / `?`.

### Changed
- The dependency-free **pure-Python parser is now the default**; `--tb` opts into
  the tensorboard `EventAccumulator`.
- **`Esc`** quits from the grid (and goes back one level from a detail view).

### Removed
- The **`--hq` image renderer** and the `matplotlib` / iTerm2 inline-image
  dependencies — everything is pure text now (lighter, faster, one render path).

## 0.1.0 – 0.1.3

Initial PyPI releases: live scalar dashboard in braille/Unicode (plus an early
`--hq` image mode), pure-Python and tensorboard parsers, multi-experiment overlay
with stable per-run colors, zoom, live tag/experiment filtering, and flicker-free
repaints.
