# terminalboard

A **pure-SSH terminal TensorBoard scalar viewer**.

Train on a remote box, SSH in with iTerm2, and watch your **live-updating scalar
curves rendered as real images right inside the terminal** — no browser, no X11,
no port forwarding.

```
ssh remote
terminalboard --logdir path/to/tb_logs
```

---

## Why this exists

The usual TensorBoard workflow over SSH is painful: you either forward a port
(`ssh -L 6006:...`) and open a browser, or you give up and `grep` the logs. On a
headless training box you often can't do either cleanly. terminalboard reads the
event files directly and draws the curves in the terminal, so a plain SSH session
is all you need.

## How it works

1. **Read** the TensorBoard event files (`events.out.tfevents.*`) from a log
   directory and collect the scalar series.
2. **Render** the selected curves with matplotlib into a PNG.
3. **Display** that PNG inside the terminal using the
   [iTerm2 inline-image protocol](https://iterm2.com/documentation-images.html).
4. **Watch** the log directory and re-render whenever new data lands, giving a
   live dashboard.

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

- **Default** (no flag): parse with the official `tensorboard` library
  (`EventAccumulator`) — most robust, handles exotic summary encodings. If
  `tensorboard` isn't installed, terminalboard falls back to `--light` automatically.
- **`--light`**: a self-contained pure-Python TFRecord + protobuf-wire parser with
  no heavy dependencies — tiny install, fast startup, ideal for a thin remote box.

The two axes are independent: pick any parser with any renderer.

## Install

```bash
pip install terminalboard            # text renderer + pure-Python --light parser
pip install 'terminalboard[tb]'      # + tensorboard (default parser)
pip install 'terminalboard[hq]'      # + matplotlib (--hq image renderer)
pip install 'terminalboard[full]'    # everything
```

## Usage

```
terminalboard LOGDIR [options]

  LOGDIR / --logdir   directory of TensorBoard event files (scanned recursively)
  --light             use the dependency-free pure-Python parser
  --hq / --text/--auto   image / text (default) / auto-detect renderer
  --tags GLOB         comma-separated glob(s), e.g. 'train/*loss*,val/*'
  --smooth ALPHA      EMA smoothing weight in [0,1) (default: 0.6; 0 disables)
  --grid RxC          panels per page (default: 2x3)
  --interval SECONDS  live refresh interval (default: 2.0)
  --once              render a single frame and exit
  --list              list all scalar tags and exit
```

```bash
terminalboard ../tb_logs                       # live text dashboard
terminalboard ../tb_logs --tags 'train/*loss*' # filter to loss curves
terminalboard ../tb_logs --hq --grid 2x2       # high-quality iTerm2 images
terminalboard ../tb_logs --light --once        # one frame, no deps, no loop
```

### Interactive controls (live mode)

| Key | Action |
|---|---|
| `q` | quit |
| `n` / `space`, `p` | next / previous page of tags |
| `r` | refresh now |
| `+` / `-` | more / less smoothing |
| `0` | disable smoothing |
| `g` | cycle grid layout |

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
- [x] **Live loop + CLI**: watch the logdir, refresh on change, keyboard
      navigation (quit / page / smoothing / grid); argparse front end.
- [ ] Multi-run overlay legends polish, per-tag y-axis options.
- [ ] Sixel fallback for non-iTerm2 terminals; config file; tag search UI.

## Status

Working v0.1. Default text dashboard, `--hq` iTerm2 images, `--light` parser, and
the live interactive loop are all functional. Test event logs are kept in the
**parent working folder** (e.g. `../tb_logs/`), deliberately outside this
repository — they're real training data and don't belong in a public repo.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[full]'
.venv/bin/terminalboard ../tb_logs --once
```

## License

TBD.
