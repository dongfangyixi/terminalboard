# terminalboard

[![CI](https://github.com/dongfangyixi/terminalboard/actions/workflows/ci.yml/badge.svg)](https://github.com/dongfangyixi/terminalboard/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/terminalboard)](https://pypi.org/project/terminalboard/)
[![Python versions](https://img.shields.io/pypi/pyversions/terminalboard)](https://pypi.org/project/terminalboard/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A **pure-terminal TensorBoard viewer**.

Watch your **live-updating scalar curves, text summaries, histogram heatmaps
right inside any terminal** — locally, or SSH'd into a remote training box —
drawn as crisp Unicode/braille. No browser, no X11, no port forwarding.

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
   directory (scanned recursively for multiple runs) and collect the series.
2. **Render** the selected tags as **Unicode/braille text** — curves, text
   panels, and histogram heatmaps — tiled into a grid that fits the terminal.
3. **Watch** the log directory and re-render whenever new data lands, giving a
   live dashboard. Repaints are **flicker-free**: the alternate screen buffer is
   redrawn in place under synchronized output (DEC mode 2026), and an idle
   dashboard isn't repainted at all (only changed data/views trigger a redraw).

## Language: Python

The viewer is written in **Python**, chosen after weighing it against a
Next.js/TypeScript implementation:

| Factor | Python ✅ | Next.js / TypeScript |
|---|---|---|
| Reading TB event logs | First-class. The format is TFRecord-framed protobuf; a small self-contained parser handles it (and `tensorboard` is there if you want it). | No mature TFRecord/TB-protobuf reader — you'd reimplement framing + protobuf decoding by hand. |
| Terminal plotting | `plotext` braille/Unicode curves + custom widgets. | No native terminal-plotting story. |
| Live tailing | `watchdog` / offset polling. | Doable, no advantage. |
| Fit for purpose | It's a terminal CLI, and Python is the lingua franca of the ML/TensorBoard ecosystem. | Next.js is a web/SSR framework; its core value (React, routing, browser) is unused here. |

The decisive factor: TensorBoard logs are a TF-specific protobuf format with
first-class Python tooling, and Python has mature terminal-plotting libraries —
so the whole thing is pure text with no browser or image protocol needed.

## Two parsing backends

- **Default**: a self-contained pure-Python TFRecord + protobuf-wire parser with
  no heavy dependencies — tiny install, fast startup, ideal for a thin remote box.
  It reads scalars, text summaries, and histograms.
- **`--tb`**: parse with the official `tensorboard` library (`EventAccumulator`)
  instead — battle-tested across exotic encodings (needs `terminalboard[tb]`;
  falls back to the built-in parser with a note if it isn't installed).

## Install

```bash
pip install terminalboard            # everything you need by default
pip install 'terminalboard[tb]'      # + tensorboard (--tb alternate parser)
uvx terminalboard <logdir>           # or run without installing (uv) / pipx run terminalboard
```

The base install pulls only `plotext` and is fully functional on its own — the
dependency-free parser (the default) reads scalars, text summaries, and
histograms with zero heavy deps. The only opt-in extra:

| Extra | Adds | Enables |
|---|---|---|
| `[tb]` | `tensorboard` | the `--tb` alternate parser (EventAccumulator) |

<details>
<summary>From source (development)</summary>

```bash
git clone https://github.com/dongfangyixi/terminalboard.git
cd terminalboard
pip install -e '.[tb,dev]'   # editable, with tensorboard + test tools
```
</details>

## Usage

```
terminalboard LOGDIR [options]

  LOGDIR / --logdir   directory of TensorBoard event files (scanned recursively)
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

### Try it without your own logs

The repo ships a generator that writes a demo logdir with 3 experiments and
every supported type (scalars, text, histograms):

```bash
python examples/gen_demo_logs.py     # writes ./demo_logs/
terminalboard demo_logs
```

A demo recording can be produced with `scripts/record_demo.sh` (needs
[`asciinema`](https://asciinema.org/) + [`agg`](https://github.com/asciinema/agg)).

```bash
terminalboard ../tb_logs                       # live dashboard
terminalboard ../tb_logs --tags 'train/*loss*' # filter to loss curves
terminalboard ../tb_logs --grid 2x2            # 4 panels per page
terminalboard ../tb_logs --once                # one frame and exit
```

## Plot types

A page can mix any of these — the panel adapts to each tag's kind:

- **Scalars** — line/braille curves (multiple experiments overlaid).
- **Text** summaries — the latest text shown in a panel.
- **Histograms** — a **heatmap** of the distribution over steps (value bins ×
  steps, shaded by density), or **distribution bands** (percentiles over steps)
  with `b`.
- **PR curves** — precision-vs-recall curves (`pr_curves` plugin).
- **HParams** — a full-screen runs × hyperparameters × metrics **table** (`P`).

### Interactive controls (live mode)

| Key | Action |
|---|---|
| arrows | move the focused panel (wraps across pages) |
| `Enter` | **inspect** the focused panel full-screen |
| `n` / `space`, `p` | next / previous page of tags |
| `t` / `f` | edit the **tag** / **experiment** filter live |
| `c` | **type selector** — cycle all / scalars / histograms / text / pr-curves |
| `o` | cycle which overlapping curve is drawn on top (z-order) |
| `z` / `Z` | zoom out / in — panels per page: `1·2·4·6·9·12·16·24·36` |
| `b` | histograms ↔ **distribution** bands |
| `+` / `-` / `0` | more / less / no smoothing |
| `x` / `l` | x-axis step↔time / toggle log-Y (scalars) |
| `w` | export the focused scalar tag to a CSV |
| `P` | **HParams** table (runs × hyperparams × metrics) |
| `r` | refresh now |
| `H` / `?` | full help overlay |
| `q` / `Esc` | quit |

**Detail view** (after `Enter`): a single tag full-screen. **`Esc`** returns to
the grid. By type:
- **scalars** overlay all experiments, with a **cursor** — `←/→` move it one data
  point (`Shift+←/→` fast), and a per-experiment **value / smoothed / step /
  wall-time** readout updates beneath the plot. `x`/`l` change axis/scale.
- **histograms** show one experiment as a heatmap (`←/→` switches; `b` toggles
  the distribution-bands view).
- **pr-curves** overlay all experiments; `←/→` steps through training.
- **text** is scrollable (`↑/↓`, `PgUp/PgDn`, `Home/End`), `←/→` switch
  experiment, and **`d`** shows a **config diff** — only the keys that differ
  across experiments.

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
| `/regex/` | regular expression (case-insensitive, unanchored — `re.search`) |

This is a small glob + boolean DSL, **not** full regex: a bare word is a
*substring* (`.` is literal, `*` is a glob wildcard). For real regex use
`/.../`. If your regex needs `|` or spaces, make the **whole** filter the regex,
e.g. `/^train\/(loss|lr)$/` — a `/.../` used as one word among others can't
contain the DSL separators (`|`, `,`, space, `&`).

Filters re-apply as you type. Tag and experiment filters combine — a tag shows
only if a currently-visible experiment has it.

### Multiple experiments

When a logdir holds several runs, their curves are **overlaid in each panel**,
each experiment in its own color, with a legend above the grid showing the
**full run names** (wrapping over multiple lines if needed — never truncated, so
you can read the exact names when filtering). Colors are **stable** — an
experiment keeps its color no matter which others you filter in or out. Use `f`
(or `--experiments`) to focus on a subset. Panel titles show the **full tag
path** (leading-ellipsis only when the panel is too narrow).

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

## Config file

Set defaults in `~/.config/terminalboard.toml` (or point `$TERMINALBOARD_CONFIG`
at a file). CLI flags override it. Needs Python 3.11+ (`tomllib`) or `tomli`.

```toml
[terminalboard]
smooth = 0.6
grid = "2x3"
interval = 2.0
xaxis = "step"   # or "time"
logy = false
tags = "train/*"
# experiments = "baseline | scaling"
# tb = true
# csv_dir = "~/tb-exports"   # pre-filled folder in the CSV save (w) prompt
# restore = true             # save/restore per-logdir view state (default: on)
```

`w` opens a path prompt pre-filled with `<csv_dir>/<tag>.csv` (editable; Enter
saves, Esc cancels).

### Saved view state

Your filters, zoom level, smoothing, x-axis, log-Y, curve order and focus are
saved **per logdir** when you quit, and restored the next time you open the same
logdir — so you pick up where you left off. State lives under
`$XDG_STATE_HOME/terminalboard/views/` (default `~/.local/state/...`). Explicit
CLI flags (e.g. `--tags`, `--smooth`) override the saved values; `--reset-view`
starts fresh, and `restore = false` in the config turns persistence off.

## LLM assistant — optional

Two ways to use it: **`a`** for a quick one-shot question (answer in an overlay),
or **`A`** for a persistent **chat sidebar** on the right.

The model both **drives the dashboard** (filter tags/experiments, pick a type,
smooth, zoom, open a tag, open the HParams table…) and **analyzes** your results
— in one turn. Examples:

- *"show only validation losses, smoothed"* → applies the filter + smoothing
- *"which run is overfitting?"* → a short comparison of train vs val gaps
- *"open the pr curve and tell me if it's good"* → opens it and gives a verdict

Install the extra and pick a model on first use:

```bash
pip install 'terminalboard[llm]'
```

It uses **[LiteLLM](https://github.com/BerriAI/litellm)**, so **any provider
works**. On first use a setup form lets you **search a model** (type `deepseek`,
`qwen`, `claude`, `gpt`… → pick from the list with `↑/↓` + Enter, or type any
custom/self-hosted string), then enter the matching API key. A **small/cheap
model is plenty** here — this isn't a hard task, so there's no need for a
flagship (your call 🙂). Some current light picks:

| Model string | Key | API base |
|---|---|---|
| `gpt-5.4-nano` / `gpt-5.4-mini` | OpenAI | *(blank)* |
| `anthropic/claude-haiku-4-5` | Anthropic | *(blank)* |
| `gemini/gemini-3.5-flash` (or `gemini/gemini-3.1-flash-lite`) | Google | *(blank)* |
| `deepseek/deepseek-v4-flash` | DeepSeek | *(blank)* |
| `openrouter/qwen/qwen3.6-35b-a3b` | OpenRouter | *(blank)* |
| `hosted_vllm/Qwen/Qwen3.6-27B` | *(your server)* | `http://host:8000/v1` |
| `ollama/llama3` | *(none)* | *(blank — local)* |

**API base** stays blank for hosted providers (LiteLLM knows their endpoints);
you only set it for your own OpenAI-compatible server (**vLLM**, Ollama, LM
Studio, Azure…).

Your **API key is stored locally** at `~/.local/state/terminalboard/llm.json`
(`chmod 600`, or under `$XDG_STATE_HOME`), and is used only to call the provider
you chose. Answers **stream** as they arrive; the status line shows tokens, cost
and time.
Actions are a fixed, typed whitelist — the assistant can't run shell or touch
files.

### Chat sidebar (`a` / `A`)

`a` (or `A`) opens a chat panel on the right (the dashboard re-tiles into the
remaining width); **`Esc` closes it**. It keeps the **full conversation**, knows
the **live view** (which tag is focused, what's on the page, counts, mode) plus
all log data, and both **answers and changes the dashboard** as you talk — so you
watch the curves update on the left while the explanation streams on the right.
Type and **Enter** to send; the input has a full line editor (`^W` delete word,
`^U` clear, `^A/^E`, word motion) and a sliding window so the cursor never runs
off-screen. **`↑/↓`** (and `PgUp/PgDn`) **scroll the transcript**; `^P`/`^N`
recall previous messages; **`^F`** toggles **full-screen chat** ↔ split (or
`/full` · `/split`); answers render light markdown. Manage **multiple sessions**
with slash commands — `/new`, `/next`, `/prev`, `/delete`, `/rename <name>`,
`/clear`, `/sessions`, `/full`, `/split`, `/model`, `/close` — saved per-logdir.

> ⚠️ **Privacy:** queries send your **tag names and metric summaries** to the
> chosen provider. Tag names can leak architecture details — if that matters,
> use a **local model** (`ollama/...`) so nothing leaves your machine. The setup
> form states this, and the feature is off until you configure it.

**Audited:** we reviewed the pinned LiteLLM version (`1.88.1`) from source: your
API key is sent **only** to the provider endpoint you configured (auth header),
there is **no telemetry** (the flag exists but nothing reads it; all logging
callbacks default to empty), and the one non-provider call — fetching a public
pricing JSON from GitHub at import — is **disabled** by terminalboard
(`LITELLM_LOCAL_MODEL_COST_MAP=true`, bundled snapshot used instead; only the
$-estimate can lag provider price changes). The extra is **version-pinned** so
what you install is what was audited; we re-audit before bumping the pin.

## Roadmap

- [x] **Reader — `--light`**: pure-Python TFRecord + protobuf-wire parser
      (Event → Summary → Value; both `simple_value` and tensor-encoded scalars).
- [x] **Reader — default**: `tensorboard` `EventAccumulator` backend with a shared
      `ScalarSeries` data model and recursive multi-run logdir scan.
- [x] **Render**: `plotext` braille grid (pure text — scalars, text, heatmaps).
- [x] **Live loop + CLI**: flicker-free repaints, keyboard navigation; argparse front end.
- [x] **Zoom** (`z`/`Z`): 1·2·4·6·9·12·16·24·36 panels per page.
- [x] **Interactive filters** (`t`/`f`): live tag & experiment filtering with a
      line editor (cursor, history, no-match warning).
- [x] **Multi-experiment overlay** with stable per-run colors and a legend.
- [x] **Published to [PyPI](https://pypi.org/project/terminalboard/)**.
- [x] **Plot types**: scalar curves, text summaries, and histogram heatmaps.
- [x] **Focus + drill-down**: arrows move focus, Enter inspects a tag full-screen
      (scalars overlay, heatmap/text switch experiments, text scrolls).
- [x] **Curve z-order** (`o`), richer **filter grammar** (OR/AND/NOT/regex),
      readline editing, **help overlay** (`H`), and `Esc` to quit.
- [x] **Default to the pure-Python parser**; `--tb` opts into tensorboard.
- [ ] Config diff across experiments; per-tag y-axis options; config file.

## Status

Working. The text dashboard, the pure-Python parser (default) and `--tb`
backend, multi-experiment overlay with z-order, zoom, focus + drill-down detail,
live tag/experiment filtering, and the scalar / text / histogram-heatmap plot
types are all functional. Test event logs are kept in the **parent working
folder** (e.g. `../tb_logs/`), deliberately outside this repository — they're
real training data and don't belong in a public repo.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[tb,dev]'
.venv/bin/terminalboard ../tb_logs --once
```

Cutting a release is documented in [RELEASING.md](RELEASING.md). The version is
single-sourced from `terminalboard/__init__.py`.

## License

[MIT](LICENSE).
