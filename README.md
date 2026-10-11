# Local Media Processor

**A 100% offline, privacy-first image & video toolbox that runs on your own machine.**

Convert images (one or many at a time), generate favicons, trace bitmaps to SVG, compress videos, create GIFs, extract audio, trim clips, and strip EXIF/GPS metadata, all without uploading a single byte to anyone else's server.

> **Why this exists:** using public converter sites (CloudConvert, EZGIF, Smallpdf, ...) with client assets or unreleased media can breach NDAs and leak confidential material. This app binds strictly to the loopback interface (`127.0.0.1`), uses only local open-source engines, and makes **zero outbound network requests**. Your files never leave your computer.

---

## Table of Contents

1. [Features](#features)
2. [Privacy & Security Guarantees](#privacy--security-guarantees)
3. [Tech Stack](#tech-stack)
4. [Requirements](#requirements)
5. [Installation](#installation)
6. [Quick Start](#quick-start)
7. [Usage Guide](#usage-guide)
8. [REST API Reference](#rest-api-reference)
9. [Project Structure](#project-structure)
10. [Configuration](#configuration)
11. [Troubleshooting](#troubleshooting)
12. [FAQ](#faq)
13. [License](#license)

---

## Features

### Image tools

| Tool | Description | Options |
|:--|:--|:--|
| **Format conversion** | PNG ⇄ JPG ⇄ WEBP ⇄ BMP | Resize modes: none · max dimensions (ratio on/off) · percentage scale; quality 1-100 |
| **ICO favicon generator** | One image → multi-resolution `.ico` | Layers: 16, 32, 48, 64, 128, 256 px |
| **Bitmap → SVG tracing** | Vector-trace PNG/JPG logos via the vtracer engine | - |
| **EXIF stripping** | Remove device, GPS, and camera metadata | On by default; toggleable per conversion |
| **Batch conversion** | Convert many images in one go with shared settings | Multi-select drop zone, per-file results, "Download all" |

### Video & audio tools

| Tool | Description | Options |
|:--|:--|:--|
| **Convert to MP4** | MOV / MKV / AVI / WEBM → MP4 (`libx264` + `aac`) | Metadata strip |
| **Compress** | CRF-based size reduction | Light (CRF 23) · Balanced (CRF 28) · Aggressive (CRF 32) |
| **Video → GIF** | Palette-optimized animated GIF | Frame rate (default 15 fps), max width (default 480 px) |
| **Extract audio** | Video → standalone MP3 (VBR ~190 kbps, `-q:a 2`) | - |
| **Trim clip** | Lossless cut (`-c copy`) by time range | Start / end time `HH:MM:SS` |
| **Strip audio** | Mute a video entirely | - |
| **Metadata stripping** | Drops container metadata (GPS/device tags) | On by default |

### Built-in conveniences

- **Single-page web UI**: dark theme, drag & drop, tabs for images vs. video
- **Real-time progress bar** for FFmpeg jobs (parsed from `-progress` telemetry)
- **Auto browser launch**: the UI opens as soon as the server starts
- **Automatic cleanup**: uploads and outputs are deleted the moment you download them, plus a full temp sweep on every startup
- **Engine pre-flight**: FFmpeg/vtracer availability is checked at startup and surfaced in the UI badge
- **Batch images**: drop or select many images at once; the drop zone swaps to a review card (single: live preview + dimensions, bulk: count + file list) and converts them all with the same settings

---

## Privacy & Security Guarantees

| Guarantee | How it is enforced |
|:--|:--|
| **100% offline** | Server binds only to `127.0.0.1:8000`; the UI ships a local Tailwind CSS build: no CDN, no external fonts, no telemetry |
| **No data retention** | Everything lives in `temp_processing/` and is deleted after download (or on next startup) |
| **Path-traversal safe** | Uploads are stored under random UUID names; client filenames are never used as filesystem paths |
| **No shell injection** | FFmpeg runs via `asyncio.create_subprocess_exec` with an explicit argument list, never through a shell |
| **Runaway process guard** | FFmpeg jobs are killed after a 30-minute safety timeout |
| **Low memory footprint** | Uploads are streamed in 1 MB chunks; large files never fully load into RAM |

---

## Tech Stack

| Component | Technology | Purpose |
|:--|:--|:--|
| Backend | [FastAPI](https://fastapi.tiangolo.com/) + Uvicorn | Async REST API, static file serving |
| Image engine | [Pillow](https://python-pillow.org/) | Raster conversion, resize, ICO generation |
| Vector tracing | [vtracer](https://github.com/visioncortex/vtracer) | Bitmap → SVG (Rust-backed) |
| Video engine | FFmpeg | Transcoding, compression, GIF, audio, trimming |
| FFmpeg fallback | [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) | Bundled FFmpeg binary when none is on PATH |
| Frontend | HTML + Tailwind CSS (local build) + vanilla JS | Drag & drop UI, progress polling |

---

## Requirements

- **Python 3.12+**
- **FFmpeg** (optional but recommended): if `ffmpeg` is found on your `PATH` it is used automatically; otherwise the app falls back to the `imageio-ffmpeg` wheel (a full FFmpeg 7.x build, no extra install steps)
- **uv** (recommended) or plain `pip` for dependency management
- OS: Windows 10/11, macOS, or Linux

---

## Installation

### Option A: with [uv](https://docs.astral.sh/uv/) (recommended)

```bash
git clone https://github.com/<your-username>/local-media-processor.git
cd local-media-processor
uv sync          # creates .venv and installs every dependency
```

### Option B: with pip

```bash
git clone https://github.com/<your-username>/local-media-processor.git
cd local-media-processor
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

---

## Quick Start

```bash
# Windows: double-click, or:
run.bat

# macOS / Linux (first time: chmod +x run.sh)
./run.sh

# Any platform, from inside the environment:
python main.py
```

The launcher:

1. Starts the server on `http://127.0.0.1:8000`
2. Runs startup checks (temp cleanup + FFmpeg/vtracer pre-flight)
3. **Automatically opens your default browser** at the app

You should see a green **"FFmpeg ready"** badge in the top-right corner. That is it. Drag a file in and go.

---

## Usage Guide

### Converting images (single or bulk)

1. Open the **Image Converter** tab.
2. Drag one or many images onto the drop zone (or click to browse; multi-select is supported).
3. The drop zone is replaced by a **review card**:
   - **Single image** → live preview, pixel dimensions, and file size
   - **Bulk** → "N images selected", the total size, and a list of file names (capped at 50, then "+N more")
   - **Change images** returns to the drop zone at any time
4. Pick an operation; the same settings apply to every image:
   - **Convert format**: choose the target format, quality (JPG/WEBP), and a resize mode:
     - *No resize*: keep the original size
     - *By max dimensions*: fit inside a width/height box with **"Keep original aspect ratio"** on (never upscales); or stretch to an exact size with it off (both values required)
     - *By percentage*: scale uniformly (50% = half, 200% = double); always keeps the ratio
   - **Generate .ico favicon**: produces a multi-layer favicon from each image.
   - **Trace bitmap to .svg**: turns each raster logo into a scalable vector.
5. Leave **"Strip EXIF metadata"** checked unless you want to keep camera/GPS data.
6. Click **Convert image(s)**: progress shows "Converting 3 of 10...", then a results list appears with a per-file **Download** button and size delta, plus **Download all**. A failed file is listed with its error and never blocks the rest.

### Processing a video

1. Open the **Video Processor** tab and drop a video.
2. Pick an operation:
   - **Convert to MP4** / **Compress** (choose a CRF preset) / **Video → GIF** (set fps + max width)
   - **Extract audio → MP3**
   - **Trim clip**: enter start/end times as `HH:MM:SS`
   - **Strip audio**: mutes the video
3. Click **Process video**. The progress bar shows a **live percentage** streamed from FFmpeg.
4. When the job finishes, click **Download** in the result card.

> **Note:** downloads are one-shot: the file (and your original upload) is deleted from `temp_processing/` immediately after the download response is sent, so save it somewhere permanent.

---

## REST API Reference

The web UI is just a client of this API; you can script it directly.

Base URL: `http://127.0.0.1:8000`

| Method | Endpoint | Purpose |
|:--|:--|:--|
| `GET` | `/` | Serve the single-page UI |
| `GET` | `/api/health` | Engine pre-flight status (FFmpeg/vtracer) |
| `POST` | `/api/convert-image` | Run an image operation (synchronous) |
| `POST` | `/api/process-video` | Start a video job (returns immediately) |
| `GET` | `/api/progress/{job_id}` | Poll job status & percentage |
| `GET` | `/api/download/{file_id}` | Download the processed file (one-shot; auto-cleans) |

### `POST /api/convert-image` form fields

| Field | Type | Default | Notes |
|:--|:--|:--|:--|
| `file` | File | *required* | PNG, JPG, WEBP, BMP, GIF, TIFF |
| `operation` | String | `convert` | `convert` \| `ico` \| `svg` |
| `target_format` | String | `png` | `png` \| `jpg` \| `webp` \| `bmp` (convert only) |
| `resize_mode` | String | `none` | `none` \| `dimensions` \| `percentage` |
| `max_width` / `max_height` | Int 1-20000 | - | `dimensions` mode; box fit when ratio on, exact stretch when off |
| `keep_aspect_ratio` | Bool | `true` | `dimensions` mode; off = exact size (both values required) |
| `scale_percent` | Int 1-500 | `100` | `percentage` mode; uniform scale, upscaling allowed |
| `quality` | Int 1-100 | `90` | JPG / WEBP only |
| `strip_metadata` | Bool | `true` | EXIF removal toggle |

**Response:** `{ "file_id", "filename", "input_size", "output_size" }`

> **Batch conversions:** the UI converts images one at a time through this endpoint (sequentially), collecting a `file_id` per file; there is no separate batch endpoint, so the same cleanup rules apply to every image.

### `POST /api/process-video` form fields

| Field | Type | Default | Notes |
|:--|:--|:--|:--|
| `file` | File | *required* | MP4, MOV, MKV, AVI, WEBM, M4V, WMV, FLV |
| `operation` | String | *required* | `convert` \| `compress` \| `gif` \| `extract_audio` \| `trim` \| `strip_audio` |
| `crf_preset` | String | `balanced` | `light` (23) \| `balanced` (28) \| `aggressive` (32) |
| `start_time` / `end_time` | `HH:MM:SS` | - | Trim only; end must be after start |
| `gif_fps` | Int 1-50 | `15` | GIF only |
| `gif_width` | Int 64-1920 | `480` | GIF only; smaller inputs are never upscaled |
| `strip_metadata` | Bool | `true` | Drops container metadata (GPS/device tags) |

**Response:** `{ "job_id", "file_id", "input_size" }`

### `GET /api/progress/{job_id}`

```json
{ "status": "processing", "progress": 42.5, "message": "Encoding... 43%", "file_id": "..." }
```

`status` is one of `queued` → `processing` → `done` | `error` (plus `filename` / `output_size` on `done`).

### Example session (curl)

```bash
# 1. Convert an image to WEBP at 60% quality, scaled to 50%
curl -F "file=@logo.png" -F "operation=convert" \
     -F "target_format=webp" -F "quality=60" \
     -F "resize_mode=percentage" -F "scale_percent=50" \
     http://127.0.0.1:8000/api/convert-image

# 2. Compress a video with the balanced preset
curl -F "file=@clip.mov" -F "operation=compress" \
     -F "crf_preset=balanced" \
     http://127.0.0.1:8000/api/process-video
# -> { "job_id": "abc123...", ... }

# 3. Poll progress
curl http://127.0.0.1:8000/api/progress/abc123...

# 4. Download (server deletes the file right after this response)
curl -O -J http://127.0.0.1:8000/api/download/abc123...
```

---

## Project Structure

```text
local-media-processor/
├── main.py                    # FastAPI server: routes, engines, cleanup, browser auto-launch
├── requirements.txt           # Python dependencies
├── run.bat                    # Single-click launcher (Windows)
├── run.sh                     # Single-click launcher (macOS / Linux)
├── temp_processing/           # Uploads & outputs (auto-cleaned - never commit this)
├── static/
│   ├── css/
│   │   └── tailwind.min.css   # Full Tailwind build, served locally (offline UI)
│   └── js/
│       └── app.js             # Drag & drop, polling, download flow
└── templates/
    └── index.html             # Single-page UI
```

---

## Configuration

All tuning lives in the constants at the top of [`main.py`](main.py):

| Constant | Default | Meaning |
|:--|:--|:--|
| `HOST_IP` | `127.0.0.1` | Loopback-only binding (do not change for privacy) |
| `SERVER_PORT` | `8000` | Port the server listens on |
| `FILE_CHUNK_SIZE` | `1 MB` | Upload streaming chunk size |
| `FFMPEG_TIMEOUT_SECONDS` | `1800` | Safety timeout for FFmpeg jobs |
| `CRF_PRESETS` | `23 / 28 / 32` | Compression preset values |
| `ICO_LAYER_SIZES` | `16 → 256` | Favicon layer resolutions |

---

## Troubleshooting

| Symptom | Fix |
|:--|:--|
| **"FFmpeg missing" badge / 503 on video tools** | Install FFmpeg and add it to `PATH` (e.g. `winget install Gyan.FFmpeg` / `choco install ffmpeg` / `brew install ffmpeg`), or just `pip install imageio-ffmpeg`; the app uses that bundled build automatically |
| **Port 8000 already in use** | Another instance is running (check `run.bat` window or task manager); close it, or change `SERVER_PORT` in `main.py` |
| **"Unknown job id" / 404 while polling** | The server was restarted; job registries are in-memory by design. Re-submit the file |
| **Trim starts a moment later than requested** | Lossless trim (`-c copy`) snaps to the nearest keyframe; this is a trade-off for zero re-encoding. Use *Convert* on the trimmed range if frame-exact matters |
| **Transparent PNG → JPG looks white** | Expected: JPEG has no alpha channel, so transparency is composited onto a white background |
| **Image looks stretched after resizing** | Expected with **"Keep original aspect ratio"** off: it resizes to the exact width x height. Turn the ratio back on for a distortion-free fit |
| **Percentage above 100% looks soft** | Upscaling cannot add real detail; it only interpolates existing pixels |
| **`.ico` missing the 256px layer** | Layers larger than the source image are skipped (standard ICO behavior); feed a source of at least 256px |
| **Compressed file is bigger than the original** | Already-efficient sources (or synthetic patterns) can grow at low CRF; try the *aggressive* preset or a GIF/resize instead |
| **Browser asks to "allow multiple downloads"** | Expected when using **Download all**: allow it once, or use the per-file Download buttons instead |

---

## FAQ

**Is it really offline?**
Yes. The server binds to `127.0.0.1` only, the UI's Tailwind CSS is served from `static/`, and the code makes no outbound HTTP calls. Disconnect your Wi-Fi and everything still works.

**Where are my files stored?**
Only inside `temp_processing/`, under random UUID names. They are deleted right after you download the result, and any leftovers are swept on the next startup.

**Can other people on my network access it?**
No. The loopback binding means only processes on your own machine can reach it.

**Why does the video button work but progress stays at 0%?**
The percentage needs the media duration; if FFmpeg cannot parse it, the bar stays in indeterminate mode until the job finishes. The job itself still runs normally.

**Does it support batch processing?**
Yes for images: drop or select many at once and they are all converted with the same settings. Videos remain single-file for now; PDF tools, a visual trimmer, and desktop packaging are on the V2 roadmap (see the PRD).

---

## Contributing

Issues and PRs are welcome. Keep the project's goals in mind:

- **Offline-first**: no new runtime CDN/external requests, ever
- **Simple by design**: single-file backend, no database, no build step
- Follow the existing style: descriptive names, guard clauses, comments that explain *why*

1. Fork → branch → change → run the app locally and exercise both tabs
2. Make sure `temp_processing/` is empty after your test downloads
3. Open a pull request

---

## License

Released under the [MIT License](LICENSE).


