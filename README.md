# terminalboard

A **pure-SSH terminal TensorBoard scalar viewer**.

Train on a remote box, SSH in with iTerm2, and watch your **live-updating scalar
curves rendered as real images right inside the terminal** — no browser, no X11,
no port forwarding.

```
ssh remote
terminalboard --logdir tb_logs
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

## Two parsing backends

- **Default** (no flag): parse with the official `tensorboard` library
  (`EventAccumulator`) — most robust, handles exotic summary encodings.
- **`--light`**: a self-contained pure-Python TFRecord + protobuf-wire parser with
  no heavy dependencies — tiny install, fast startup, ideal for a thin remote box.

## Planned CLI

```
terminalboard --logdir DIR [options]

  --logdir DIR        directory of TensorBoard event files (scanned recursively)
  --light             use the dependency-free pure-Python parser
  --tags GLOB         only show tags matching a glob (e.g. 'train/*loss*')
  --interval SECONDS  refresh interval for the live loop (default: 2)
  --grid RxC          subplot grid layout (e.g. 2x3)
  --smooth ALPHA      EMA smoothing factor for curves
  --once              render a single frame and exit (no live loop)
```

## Roadmap

- [ ] **Reader — `--light`**: pure-Python TFRecord + protobuf-wire parser
      (Event → Summary → Value; both `simple_value` and tensor-encoded scalars).
- [ ] **Reader — default**: `tensorboard` `EventAccumulator` backend with a shared
      `ScalarSeries` data model and recursive multi-run logdir scan.
- [ ] **Render**: matplotlib grid → PNG → iTerm2 inline-image escape sequence,
      sized to the terminal cell grid.
- [ ] **Live loop + CLI**: watch the logdir, refresh on change, keyboard
      navigation (quit / page tags / filter / toggle smoothing); argparse front end.
- [ ] Non-iTerm2 fallback, tmux passthrough, multi-run overlay, config file.

## Status

Early scaffolding — planning and README only so far. A local example log
directory (`tb_logs/`) is used for development but is **git-ignored** (it holds
real training data and shouldn't go in a public repo).

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install matplotlib protobuf crc32c tensorboard
```

## License

TBD.
