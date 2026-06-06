# Changelog

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
