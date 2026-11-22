# vsum

Turn any video into a transcript **you can cite by the second**. Runs locally, nothing uploaded.
Every line is addressable, searchable, and clippable.

English · [中文](README.md)

```bash
# needs Python 3.9+ and ffmpeg on PATH
uv tool install "vsum @ git+https://github.com/mudwing-chen/vsum"

vsum talk.mp4 -o out/          # transcribe + export + extractive summary
vsum find out/ "price"         # what second was that said?
vsum clip out/ 00:41 -o clip.mp4
```

> ⚠️ **Do not `pip install vsum`.** That name on PyPI belongs to a different, unrelated
> video-summary project. This repo is not on PyPI yet (we plan to publish as `vsum-cli`;
> the import name and the `vsum` command stay the same). Use the git install above.

---

## This is not another video summarizer

Summarizers give you a paragraph you can't check. vsum gives you the **material you can check**:

| | Typical summarizer | vsum |
|---|---|---|
| Output | a paragraph | transcript + subtitles + provenance chain |
| Citing | "somewhere in the video" | one line per segment, `[mm:ss]` |
| Verification | none | every summary bullet must exist verbatim in the transcript; unverifiable ones are deleted and counted |
| Search | none | `vsum find out/ "keyword"` → timestamps |
| Clipping | none | `vsum clip out/ 00:41 -o clip.mp4` |
| Reproducibility | none | `manifest.json` records sha256, model, device, and every decode parameter |

> The summary is a **draft**. `transcript.md` is the source of truth. That's a design choice:
> a machine can guarantee "this sentence exists at 00:41", not "this is the right takeaway".

## What the output looks like

Real output from a 4-minute Chinese AI-news video is in [`docs/example/`](docs/example/)
(the sample text is Chinese — the *format* is what's worth looking at):

`transcript.md`:

```markdown
[00:08] ⚠ Cloud的Fibre 5.2花了209美元。
[00:11] ⚠ GPD 602 Astra只花了不到10美元。
[00:48] 两个都是105万token的上下文。
```

- one segment per line, timestamp first — that's your citation anchor
- `⚠` marks the 10% least confident segments (by rank, exactly 10%)
- chapters every 5 minutes

`summary.md`:

```markdown
## 概览
- Cloud的Fibre 5.2花了209美元 [00:08]

## 关键数字
- GPD 602 Astra只花了不到10美元 [00:11]
```

Every trailing `[mm:ss]` is verified against the transcript; bullets that fail are dropped,
and the count is recorded in `result.json` under `summary.dropped`.

## Install

```bash
# ffmpeg must be on PATH
uv tool install "vsum @ git+https://github.com/mudwing-chen/vsum"
vsum doctor      # checks python / ffmpeg / model / GPU / browser / OCR
```

The first run downloads a model (`small`, ~480 MB by default).
Behind a restricted network, point HuggingFace at a mirror:

```bash
export HF_ENDPOINT=https://hf-mirror.com     # Windows: set HF_ENDPOINT=...
```

Or point at a local model directory: `--model /path/to/faster-whisper-small`.

## Usage

```bash
vsum talk.mp4 -o out/                        # local file
vsum "https://www.youtube.com/watch?v=..." -o out/link
vsum batch urls.txt -o out/batch             # serial, keeps going on failure
vsum find out/ "price" --context 1           # search with surrounding lines
vsum clip out/ 01:14 -o clip.mp4             # cut that bit, 1.5s of padding
```

Common flags: `--model tiny|base|small|medium|large-v3` (default `small`, or a local dir),
`--lang zh|en|auto`, `--summary extractive|llm|none`, `--clean`, `--force`,
`--json` (single-line stdout), `--no-context`.

### All commands

`vsum --help` prints the same table; `vsum help <command>` shows that command's full flags.

| Command | What it does |
|---|---|
| `vsum <file\|url> -o DIR` | transcribe + export + summary (the default command; spelled out it's `vsum transcribe`) |
| `vsum export --segments DIR -o DIR` | re-export the four formats from `segments.json` (`--segments` accepts a directory) |
| `vsum fetch URL -o DIR` | download only: Douyin / Bilibili / YouTube → local video |
| `vsum login douyin` | QR login, stores the session in a separate profile |
| `vsum batch LIST -o DIR` | many links or a manifest, serial, keeps going on failure |
| `vsum find DIR words...` | search the transcript, hits carry `[mm:ss]` |
| `vsum clip DIR mm:ss -o FILE` | cut that bit out (1.5 s of padding) |
| `vsum ocr DIR` | sample frames + OCR, checks proper nouns against what's on screen |
| `vsum fix DIR` | apply the term list (dry run unless `--apply`) |
| `vsum audit DIR` | term-list health report |
| `vsum mine DIR` | mine variant candidates not in the term list |
| `vsum doctor` | check python / ffmpeg / model / GPU / browser / OCR |
| `vsum bench --audio FILE` | measure RTF on your own audio, compare compute types |
| `vsum eval --manifest FILE` | CER and term recall for one run |
| `vsum holdout --corpus DIR` | CER and regression comparison over a fixed corpus |

## How fast is it?

`vsum bench` measures your own machine. These are two real runs, not estimates:

| Machine | Model | Audio | Time | RTF |
|---|---|---|---|---|
| Windows 11, NVIDIA P106-100 6 GB (`int8_float32`) | small | 15:41 | 103.8 s | 0.11 |
| Apple Silicon, CPU (`int8`) | small | 13:47 | 504.5 s | 0.61 |
| Apple Silicon, CPU (`int8`) | tiny | 13:47 | 252.6 s | 0.31 |

RTF is compute-seconds per audio-second: 0.11 ≈ 9× faster than real time. `tiny` is fast
but not usable as a transcript — on Chinese it turned 考研 into 烤煙 (8×), 考验 (3×) and
考庆 (1×), never once getting it right. `small` is the default for that reason.
`medium` / `large-v3` need 1.5 GB / 3 GB of weights and roughly double the CPU time again.

## `find` and `clip` — the actually useful part

```bash
$ vsum find out/ 价格
2 处命中（价格）
> [00:08] Cloud的Fibre 5.2花了209美元。
  [00:11] GPD 602 Astra只花了不到10美元。

剪出来听：vsum clip out/ 00:08 -o clip.mp4
```

`find` accepts multiple words (all must match), `--context N`, and `--json` for scripting.
`clip` cuts from the original video recorded in `manifest.json`; falls back to `audio.wav`.

## Fixing proper nouns — read the screen

Speech recognition gets product names, people, and version numbers wrong, and **a bigger model
does not fix it** (measured: `small` vs `medium`, CPU vs GPU — identical mistakes).
The correct spelling is usually on screen, so vsum reads the screen:

```bash
uv tool install "vsum[ocr] @ git+https://github.com/mudwing-chen/vsum"
vsum ocr out/                # pick frames, OCR them, diff against the transcript
vsum ocr out/ --every 30     # scan for title cards / info cards
vsum fix out/ --from-ocr out/ocr.json --apply
```

- **English names**: matched by shape (similarity ≥0.8) or by sound (same Soundex skeleton,
  which catches `Cloud`→`Claude` and `GPD`→`GPT` that edit distance misses)
- **Chinese text**: contextual character diffs (`有加奖`→`有嘉奖`), never single characters —
  replacing a lone `大` would hit unrelated sentences
- a candidate seen in ≥2 frames is `high` and gets applied; a one-off is `medium` and is only reported

Output: `ocr.md` (side-by-side, human-readable), `ocr.json` (machine-readable), `frames/`.

## Bring your own model for the summary

The default summary is extractive. For a written one:

```bash
vsum talk.mp4 -o out/ --summary llm \
  --llm-url http://127.0.0.1:11434/v1 --llm-model qwen2.5:7b
```

Any OpenAI-compatible endpoint works — Ollama, llama.cpp, LM Studio, a cloud API. Two levels on
purpose: a one-sentence overview (model-written, explicitly labelled as *not* verbatim-verified)
and bullets (must be a substring of a real segment, so they still pass the citation gate).
Long transcripts are chunked; a failed chunk doesn't kill the others, and every failure reason —
HTTP status plus the server's own message — goes into `warnings`. If the endpoint is unreachable
vsum falls back to extractive and tells you why.

## Output files

| File | Contents |
|---|---|
| `transcript.md` | YAML header + one line per segment `[mm:ss]` + low-confidence `⚠` + chapters |
| `transcript.txt` / `.srt` / `.vtt` / `.plain.txt` | timestamped / subtitles / plain text |
| `summary.md` | overview / chapters / key numbers / keywords / doubtful segments, all with `[mm:ss]` |
| `segments.json` | per-segment data: `start` `end` `text` `avg_logprob` `no_speech_prob` `compression_ratio` `temperature` |
| `manifest.json` | input sha256, decode params, model, device, artifact list, warnings |
| `export.source.json` | which segments file produced the export, its sha256, cue count |
| `result.json` | machine-readable result: `ok` `counts` `reused` `summary` `sha256` `warnings` |

## Two defaults worth knowing

- **`condition_on_previous_text=True`** — Chinese output keeps simplified characters and
  punctuation (measured: traditional 41/143 segments → 0). `--no-context` restores the old behaviour.
- **Traditional → simplified normalisation** — Whisper has no orthographic constraint; about
  2.5% of characters came out traditional in a 13:47 test clip. Fixed before writing to disk
  using a per-character Unihan mapping (`vsum/data/t2s.json`, MIT-clean, no GPL dependency).

## Platform support

| Platform | Status |
|---|---|
| macOS (arm64) | transcription / export / summary / fix verified |
| Linux | CI runs the full test suite |
| Windows 11 | verified end-to-end: P106 GPU, Douyin short-link → detail API → download → transcribe, OCR, find, clip |

Link fetching: Douyin (China) goes through the browser's own page API; Bilibili / YouTube use
yt-dlp (`pip install 'vsum[yt]'`). Douyin requires a one-time `vsum login douyin` QR scan
in some regions.

## Known limitations

- **Transcription is not reproducible**: temperature fallback is enabled, so re-running with
  `--force` can change segment counts and text; switching GPU/compute type changes it too.
  Reuse is guarded by input sha256, model, language, and context setting.
- **The summary only guarantees provenance**, not quality.
- **LLM mode is only tested against a protocol-level fake server** (local OpenAI-compatible
  stub, verifying chunking, parsing, the citation gate and two-level rendering) — never
  quality-evaluated against a real model. How good the writing is depends on the model you plug in.
- **No human-labelled accuracy set** for the term metrics — the misses in the repo are
  regression assertions, not accuracy numbers.
- **OCR candidates are candidates**: vsum reports what's on screen; whether to change the
  transcript is still a human call.
- Douyin rate limits are undocumented: don't hammer it, `batch` is serial and has no backoff.

## Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `WHISPER_DEVICE` | `cpu` / `cuda`, overrides auto-detection | auto |
| `WHISPER_COMPUTE` | `int8` / `int8_float32` / `float16` / `int8_float16` | picked per GPU |
| `WHISPER_COND` | `0` disables context conditioning (old behaviour) | on |
| `WHISPER_PROMPT` | override the decoder prompt | built-in Chinese prompt |
| `WHISPER_HOTWORDS` | `1` injects the term list into the decoder prompt (experimental) | off |
| `HF_ENDPOINT` | HuggingFace mirror | official |
| `VSUM_BROWSER_PROFILE` | browser profile used for link fetching | `~/.cache/vsum/chrome` |
| `VSUM_BROWSER_ARGS` | extra browser flags, comma separated (`--no-sandbox,--disable-gpu` rescues `tab crashed`) | empty |
| `SE_CACHE_PATH` | selenium driver cache when the default dir is unwritable | `~/.cache/selenium` |

## Development

```bash
python -m py_compile vsum/*.py tests/*.py
for t in terms exports eval cli loop transcribe fetch hardware holdout browser summary p2 batch lookup ocr llm; do
  python "tests/test_$t.py" || exit 1
done
```

The tests need no third-party packages (no model download, no browser). CI runs on Ubuntu and Windows.

## License

MIT
