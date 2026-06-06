# terminalboard

[![CI](https://github.com/dongfangyixi/terminalboard/actions/workflows/ci.yml/badge.svg)](https://github.com/dongfangyixi/terminalboard/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/terminalboard)](https://pypi.org/project/terminalboard/)
[![Python versions](https://img.shields.io/pypi/pyversions/terminalboard)](https://pypi.org/project/terminalboard/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A **pure-terminal TensorBoard scalar viewer**.

Watch your **live-updating scalar curves right inside any terminal** — locally,
or SSH'd into a remote training box. Crisp Unicode/braille text by default, or
real iTerm2 inline images with `--hq`. No browser, no X11, no port forwarding.

```bash
terminalboard path/to/tb_logs        # runs in any terminal, local or remote

# training on a remote box? just SSH in first — no port forwarding needed:
#   ssh remote
#   terminalboard path/to/tb_logs
```

---

## Why this exists

The usual TensorBoard workflow over SSH is painful: you either forward a port
(`ssh -L 6006:...`) and open a browser, or you give up and `grep` the logs. On a
headless training box you often can't do either cleanly. terminalboard reads the
event files directly and draws the curves in the terminal, so a plain SSH session
is all you need — and it works just as well **locally**, anywhere you have a
terminal and the event files.

## How it works

1. **Read** the TensorBoard event files (`events.out.tfevents.*`) from a log
   directory (scanned recursively for multiple runs) and collect the scalar series.
2. **Render** the selected curves — by default as Unicode/braille **text** (works
   in any terminal), or with `--hq` as a matplotlib PNG.
3. **Display** them: braille text is printed directly; the `--hq` PNG is streamed
   via the [iTerm2 inline-image protocol](https://iterm2.com/documentation-images.html)
   (built in-memory, no temp file).
4. **Watch** the log directory and re-render whenever new data lands, giving a
   live dashboard. Repaints are **flicker-free**: the alternate screen buffer is
   redrawn in place under synchronized output (DEC mode 2026), and an idle
   dashboard isn't repainted at all (only changed data/views trigger a redraw).

## Language: Python

The viewer is written in **Python**, chosen after weighing it against a
Next.js/TypeScript implementation:

| Factor | Python ✅ | Next.js / TypeScript |
|---|---|---|
| Reading TB event logs | First-class. The format is TFRecord-framed protobuf; `tensorboard`/`tbparse` parse it natively, or a small self-contained parser does. | No mature TFRecord/TB-protobuf reader — you'd reimplement framing + protobuf decoding by hand. |
| High-quality curves | matplotlib → PNG → iTerm2 inline image. | No native terminal-plotting story. |
| Live tailing | `watchdog` / offset polling. | Doable, no advantage. |
| Fit for purpose | It's a terminal CLI, and Python is the lingua franca of the ML/TensorBoard ecosystem. | Next.js is a web/SSR framework; its core value (React, routing, browser) is unused here. |
| This machine | Python 3.12 already present. | Node isn't installed. |

The decisive factor: TensorBoard logs are a TF-specific protobuf format with
first-class Python tooling, and the target terminal (iTerm2) supports an
inline-image protocol — so we can render genuine matplotlib-quality curves rather
than ASCII art.

## Two rendering backends

- **Default — text/braille** (`plotext`): curves drawn directly as Unicode/braille
  characters. No image is generated, so it works over **any** SSH session, tmux, or
  plain terminal, and redraws instantly. *(See the example below.)*
- **`--hq` — iTerm2 image**: matplotlib rendered to an **in-memory** PNG (no temp
  file) and streamed via the iTerm2 inline-image protocol. Pixel-perfect, but only
  in iTerm2/WezTerm-class terminals.
- **`--auto`**: use the image renderer in iTerm2-class terminals, else fall back to
  text automatically.

## Two parsing backends

- **Default**: a self-contained pure-Python TFRecord + protobuf-wire parser with
  no heavy dependencies — tiny install, fast startup, ideal for a thin remote box.
  It reads scalars, text summaries, and histograms.
- **`--tb`**: parse with the official `tensorboard` library (`EventAccumulator`)
  instead — battle-tested across exotic encodings (needs `terminalboard[tb]`;
  falls back to the built-in parser with a note if it isn't installed).

The two axes are independent: pick any parser with any renderer.

## Install

```bash
pip install terminalboard            # everything you need by default
pip install 'terminalboard[hq]'      # + matplotlib (--hq image renderer)
pip install 'terminalboard[tb]'      # + tensorboard (--tb alternate parser)
pip install 'terminalboard[full]'    # everything
```

The base install pulls only `plotext` and is fully functional on its own — the
text renderer **and** the dependency-free parser (now the default) read scalars,
text summaries, and histograms with zero heavy deps. Extras are opt-in:

| Extra | Adds | Enables |
|---|---|---|
| `[hq]` | `matplotlib` | the `--hq` iTerm2 image renderer |
| `[tb]` | `tensorboard` | the `--tb` alternate parser (EventAccumulator) |
| `[full]` | both | everything |

<details>
<summary>From source (development)</summary>

```bash
git clone https://github.com/dongfangyixi/terminalboard.git
cd terminalboard
pip install -e '.[full]'     # editable; full = tensorboard + matplotlib
```
</details>

## Usage

```
terminalboard LOGDIR [options]

  LOGDIR / --logdir   directory of TensorBoard event files (scanned recursively)
  --hq / --text/--auto   image / text (default) / auto-detect renderer
  --tb                parse with the tensorboard library (needs [tb]); the
                      built-in pure-Python parser is the default
  --tags GLOB         filter tags, e.g. 'train/*loss*,val/*' (live-editable: t)
  --experiments GLOB  filter experiments/runs (live-editable: f)
  --smooth ALPHA      EMA smoothing weight in [0,1) (default: 0.6; 0 disables)
  --grid RxC          panels per page (default: 2x3)
  --interval SECONDS  live refresh interval (default: 2.0)
  --once              render a single frame and exit
  --list              list all tags and exit
```

```bash
terminalboard ../tb_logs                       # live text dashboard
terminalboard ../tb_logs --tags 'train/*loss*' # filter to loss curves
terminalboard ../tb_logs --hq --grid 2x2       # high-quality iTerm2 images
terminalboard ../tb_logs --once                # one frame and exit
```

## Plot types

A page can mix any of these — the panel adapts to each tag's kind:

- **Scalars** — line/braille curves (multiple experiments overlaid).
- **Text** summaries — the latest text shown in a panel.
- **Histograms** — drawn as a **heatmap** of the distribution over steps
  (value bins × steps, shaded by density).

### Interactive controls (live mode)

| Key | Action |
|---|---|
| arrows | move the focused panel (wraps across pages) |
| `Enter` | **inspect** the focused panel full-screen |
| `n` / `space`, `p` | next / previous page of tags |
| `t` / `f` | edit the **tag** / **experiment** filter live |
| `o` | cycle which overlapping curve is drawn on top (z-order) |
| `z` / `Z` | zoom out / in — panels per page: `1·2·4·6·9·12·16·24·36` |
| `+` / `-` / `0` | more / less / no smoothing |
| `r` | refresh now |
| `H` / `?` | full help overlay |
| `q` / `Esc` | quit |

**Detail view** (after `Enter`): a single tag full-screen. **`Esc`** returns to
the grid. By type: **scalars** overlay all experiments; **histograms** show one
experiment (`←/→` switches); **text** is scrollable (`↑/↓`, `PgUp/PgDn`,
`Home/End`) with `←/→` to switch experiment.

In the filter prompt: **←/→** move, **↑/↓** recall history, **Home/End** (or
`^A`/`^E`), **^W** delete word, **^K** kill-to-end, **^U** clear, **Alt/Ctrl+←/→**
word motion, **Enter** apply, **Esc** cancel.

### Filter syntax (tags and experiments)

| Pattern | Meaning |
|---|---|
| `word` | case-insensitive **substring** (`loss` → `train/loss`) |
| `a b` | **AND** — both must match |
| `a \| b` , `a , b` | **OR** — either matches |
| `* ? [ ]` | glob wildcards (`train/*loss*`) |
| `!word` | **NOT** — exclude |
| `/regex/` | regular expression |

Filters re-apply as you type. Tag and experiment filters combine — a tag shows
only if a currently-visible experiment has it.

### Multiple experiments

When a logdir holds several runs, their curves are **overlaid in each panel**,
each experiment in its own color, with a legend above the grid. Colors are
**stable** — an experiment keeps its color no matter which others you filter in
or out — so you can always tell which curve is which. Use `f` (or
`--experiments`) to focus on a subset.

In the filter prompt: **←/→** move the cursor, **↑/↓** recall previous patterns,
**Home/End** (or `^A`/`^E`) jump, `^U` clears. If a pattern matches nothing the
current plots are **kept** (no jarring re-layout) and a red warning is shown
until you fix or cancel it.

### Example (text renderer)

```
                              train/text_token_accuracy
    ┌──────────────────────────────────────────────────────────────────────────┐
0.97┤                                                   ⡠⣄⣀⣀⡠⠖⠦⠤⠤⠖⠒⠒⠒⠒⠉⠙⠒⠒⠉⠉⠉⠉⠉│
    │                                              ⣠⠒⠒⠒⠞                       │
    │                                          ⡤⠲⠴⠤⠇                           │
0.82┤                                         ⢰⠁                               │
    │                                     ⢠⠒⠲⠤⠎                                │
0.67┤                                 ⣀⣀⣀⣠⠃                                    │
    │                           ⢀⠔⠒⠒⠲⠇                                         │
0.52┤          ⣀⣀⣀⣀⣀⣀⣀⣀⡠⠤⠤⠤⠤⠞⠉⠉⠉⠛                                              │
    │  ⡴⠲⠒⠉⠉⠉⠉⠉⠁                                                               │
    └┬─────────────────┬──────────────────┬─────────────────┬─────────────────┬┘
    10               1510               3010              4510             6010
```

## Roadmap

- [x] **Reader — `--light`**: pure-Python TFRecord + protobuf-wire parser
      (Event → Summary → Value; both `simple_value` and tensor-encoded scalars).
- [x] **Reader — default**: `tensorboard` `EventAccumulator` backend with a shared
      `ScalarSeries` data model and recursive multi-run logdir scan.
- [x] **Render — text**: `plotext` braille grid, the default (no image).
- [x] **Render — `--hq`**: matplotlib grid → in-memory PNG → iTerm2 inline image.
- [x] **Live loop + CLI**: flicker-free repaints, keyboard navigation; argparse front end.
- [x] **Zoom** (`z`/`Z`): 1·2·4·6·9·12·16·24·36 panels per page.
- [x] **Interactive filters** (`t`/`f`): live tag & experiment filtering with a
      line editor (cursor, history, no-match warning).
- [x] **Multi-experiment overlay** with stable per-run colors and a legend.
- [x] **Published to [PyPI](https://pypi.org/project/terminalboard/)**.
- [x] **Plot types**: text summaries and histogram heatmaps (text + `--hq`).
- [x] **Curve z-order** (`o`), richer **filter grammar** (OR/AND/NOT/regex),
      readline editing, **help overlay** (`H`), and `Esc` to quit.
- [x] **Default to the pure-Python parser**; `--tb` opts into tensorboard.
- [ ] Image summaries; Sixel fallback for non-iTerm2 terminals; config file.

## Status

Working. The default text dashboard, `--hq` iTerm2 images, the pure-Python parser
(default), the `--tb` backend, multi-experiment overlay with z-order, zoom,
live tag/experiment filtering, and the scalar / text / histogram-heatmap plot
types are all functional. Test event logs are kept in the **parent working
folder** (e.g. `../tb_logs/`), deliberately outside this repository — they're
real training data and don't belong in a public repo.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[full]'
.venv/bin/terminalboard ../tb_logs --once
```

Cutting a release is documented in [RELEASING.md](RELEASING.md). The version is
single-sourced from `terminalboard/__init__.py`.

## License

[MIT](LICENSE).
