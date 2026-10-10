"""Local Media Processor - a local-first, offline image & video toolbox.

Every socket is bound to the loopback interface (127.0.0.1) so no media ever
leaves the machine (FR-SYS-01). Launch with `python main.py`; the default
browser opens automatically once the server is listening (FR-SYS-02).
"""

import asyncio
import re
import shutil
import threading
import uuid
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageOps
from starlette.concurrency import run_in_threadpool

# ---------------------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------------------

BASE_DIRECTORY = Path(__file__).resolve().parent
TEMP_PROCESSING_DIRECTORY = BASE_DIRECTORY / "temp_processing"
HOST_IP = "127.0.0.1"                # FR-SYS-01: loopback binding = 100% offline isolation
SERVER_PORT = 8000
FILE_CHUNK_SIZE = 1024 * 1024        # stream uploads in 1 MB chunks (low memory footprint)
FFMPEG_TIMEOUT_SECONDS = 30 * 60     # kill runaway subprocesses (risk: FFmpeg hanging)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv", ".flv", ".gif"}
RASTER_FORMATS = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP", "bmp": "BMP"}
IMAGE_OUTPUT_EXTENSIONS = {
    "png": ".png", "jpg": ".jpg", "jpeg": ".jpg", "webp": ".webp",
    "bmp": ".bmp", "ico": ".ico", "svg": ".svg",
}
ICO_LAYER_SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]  # FR-IMG-02
CRF_PRESETS = {"light": 23, "balanced": 28, "aggressive": 32}                      # FR-VID-03
IMAGE_OPERATIONS = {"convert", "ico", "svg"}
VIDEO_OPERATIONS = {"convert", "compress", "gif", "extract_audio", "trim", "strip_audio"}
OUTPUT_EXTENSION_BY_OPERATION = {
    "convert": ".mp4", "compress": ".mp4", "gif": ".gif",
    "extract_audio": ".mp3", "trim": ".mp4", "strip_audio": ".mp4",
}
OUTPUT_LABEL_BY_OPERATION = {
    "convert": "converted", "compress": "compressed", "gif": "animation",
    "extract_audio": "audio", "trim": "trimmed", "strip_audio": "muted",
}
TRIM_TIMESTAMP_PATTERN = re.compile(r"^\d{1,2}:\d{2}:\d{2}(?:\.\d{1,3})?$")

# Runtime registries are in-memory on purpose: this is a single-user local tool (KISS, no DB).
# file_id -> {"path": processed output, "input_path": original upload, "download_name": str}
REGISTERED_FILES: dict[str, dict[str, str | None]] = {}
# job_id -> {"status": queued|processing|done|error, "progress": 0-100, "message": str}
JOB_PROGRESS: dict[str, dict[str, object]] = {}
# Filled in by the startup pre-flight check (risk: "FFmpeg missing in system PATH").
FFMPEG_STATUS: dict[str, object] = {"available": False, "source": None, "path": None, "version": None}
VTRACER_STATUS: dict[str, object] = {"available": False, "error": None}

TEMPLATES = Jinja2Templates(directory=str(BASE_DIRECTORY / "templates"))


class MediaProcessingError(Exception):
    """Raised when FFmpeg fails, times out, or exits with a non-zero status."""


# ---------------------------------------------------------------------------------------
# Startup pre-flight helpers
# ---------------------------------------------------------------------------------------

def locate_ffmpeg_executable() -> tuple[str | None, str | None]:
    """Find an FFmpeg binary: prefer the system PATH, then the bundled imageio-ffmpeg build."""
    system_executable = shutil.which("ffmpeg")
    if system_executable:
        return system_executable, "system"
    try:
        import imageio_ffmpeg

        bundled_executable = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None, None
    if bundled_executable and Path(bundled_executable).is_file():
        return bundled_executable, "bundled"
    return None, None


async def read_ffmpeg_version(executable_path: str) -> str | None:
    """Return the first `ffmpeg -version` line, or None if the binary cannot execute."""
    try:
        process = await asyncio.create_subprocess_exec(
            executable_path,
            "-version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, _ = await process.communicate()
    except OSError:
        return None
    version_lines = stdout_bytes.decode("utf-8", errors="replace").splitlines()
    return version_lines[0] if version_lines else None


def probe_vtracer_import() -> tuple[bool, str | None]:
    """Verify the vtracer engine can be imported before the UI offers SVG tracing."""
    try:
        import vtracer  # noqa: F401
    except Exception as import_error:
        return False, str(import_error)
    return True, None


def clear_stale_temp_files() -> int:
    """Startup cleanup: remove leftovers from a previous run/crash (risk table mitigation)."""
    if not TEMP_PROCESSING_DIRECTORY.exists():
        return 0
    removed_count = 0
    for temp_item in TEMP_PROCESSING_DIRECTORY.iterdir():
        if temp_item.is_file():
            temp_item.unlink(missing_ok=True)
            removed_count += 1
    REGISTERED_FILES.clear()
    JOB_PROGRESS.clear()
    return removed_count


# ---------------------------------------------------------------------------------------
# Upload & registry helpers (path-traversal safe)
# ---------------------------------------------------------------------------------------

def build_download_stem(original_filename: str | None) -> str:
    """Sanitize the client filename into a safe ASCII stem used only for the download name."""
    filename_stem = Path((original_filename or "converted").replace("\\", "/")).stem
    sanitized_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", filename_stem).strip("._")
    return (sanitized_stem or "converted")[:80]


def discard_temp_files(*file_paths: Path | None) -> None:
    """Best-effort deletion of temp files; used on error paths so nothing accumulates."""
    for file_path in file_paths:
        if file_path is not None:
            file_path.unlink(missing_ok=True)


async def store_uploaded_file(
    upload: UploadFile,
    allowed_extensions: set[str],
    media_label: str,
) -> tuple[str, Path, str]:
    """Save an upload under a generated UUID name.

    The client filename never becomes part of the filesystem path - only a
    whitelisted extension is reused - which blocks path-traversal attacks (NFR 5.2).
    Returns (file_id, saved_path, sanitized_download_stem).
    """
    original_extension = Path(upload.filename or "").suffix.lower()
    if original_extension not in allowed_extensions:
        allowed_text = ", ".join(sorted(allowed_extensions))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported {media_label} extension "
                   f"'{original_extension or 'unknown'}'. Allowed: {allowed_text}.",
        )
    file_id = uuid.uuid4().hex
    destination_path = TEMP_PROCESSING_DIRECTORY / f"{file_id}{original_extension}"
    # Chunked copy keeps memory flat even for multi-gigabyte videos (NFR 5.1).
    with destination_path.open("wb") as destination_file:
        while chunk := await upload.read(FILE_CHUNK_SIZE):
            destination_file.write(chunk)
    if destination_path.stat().st_size == 0:
        destination_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    return file_id, destination_path, build_download_stem(upload.filename)


def register_processed_file(
    *,
    file_id: str,
    output_path: Path,
    download_name: str,
    input_path: Path,
) -> None:
    """Remember where the output and its original upload live so download can clean both up."""
    REGISTERED_FILES[file_id] = {
        "path": str(output_path),
        "input_path": str(input_path),
        "download_name": download_name,
    }


def remove_registered_file_bundle(file_id: str) -> None:
    """Delete output + original upload (runs as a BackgroundTask after the download)."""
    registered_file = REGISTERED_FILES.pop(file_id, None)
    if registered_file is None:
        return
    input_path = Path(registered_file["input_path"]) if registered_file["input_path"] else None
    discard_temp_files(Path(registered_file["path"]), input_path)


# ---------------------------------------------------------------------------------------
# Image engine (Pillow + vtracer) - CPU-bound work runs in Starlette's threadpool
# ---------------------------------------------------------------------------------------

def flatten_transparency_onto_white(image: Image.Image) -> Image.Image:
    """JPEG has no alpha channel - composite over white so transparent pixels do not turn black."""
    rgba_image = image.convert("RGBA")
    white_canvas = Image.new("RGBA", rgba_image.size, (255, 255, 255, 255))
    return Image.alpha_composite(white_canvas, rgba_image).convert("RGB")


def convert_raster_image_format(
    input_path: Path,
    output_path: Path,
    target_format: str,
    max_width: int | None,
    max_height: int | None,
    quality: int,
    strip_metadata: bool,
) -> None:
    """FR-IMG-01/04: bidirectional raster conversion with optional resize and quality control."""
    pillow_format = RASTER_FORMATS[target_format]
    with Image.open(input_path) as source_image:
        original_exif = source_image.info.get("exif")
        # Baking the orientation in before EXIF removal keeps the visual result correct.
        working_image = ImageOps.exif_transpose(source_image) if strip_metadata else source_image

        if max_width is not None or max_height is not None:
            # thumbnail() only shrinks, so the values behave as "maximum dimensions".
            limit_width = max_width or working_image.width
            limit_height = max_height or working_image.height
            working_image.thumbnail((limit_width, limit_height), Image.Resampling.LANCZOS)

        if pillow_format == "JPEG" and working_image.mode not in {"RGB", "L"}:
            working_image = flatten_transparency_onto_white(working_image)
        if pillow_format == "WEBP" and working_image.mode not in {"RGB", "RGBA", "L"}:
            working_image = working_image.convert("RGBA")

        save_arguments: dict[str, object] = {}
        if pillow_format in {"JPEG", "WEBP"}:
            save_arguments["quality"] = quality          # quality slider targets lossy formats
        if pillow_format in {"JPEG", "PNG"}:
            save_arguments["optimize"] = True            # PNG is lossless; optimize still shrinks it
        if not strip_metadata and original_exif:
            save_arguments["exif"] = original_exif       # omitting EXIF is what removes it
        working_image.save(output_path, format=pillow_format, **save_arguments)


def generate_multiresolution_icon(input_path: Path, output_path: Path) -> None:
    """FR-IMG-02: build a .ico with layers 16x16..256x256 (Pillow omits layers larger than the source)."""
    with Image.open(input_path) as source_image:
        icon_image = ImageOps.exif_transpose(source_image)
        if icon_image.mode != "RGBA":
            icon_image = icon_image.convert("RGBA")
        icon_image.save(output_path, format="ICO", sizes=ICO_LAYER_SIZES)


def trace_bitmap_to_svg(input_path: Path, output_path: Path) -> None:
    """FR-IMG-03: vector-trace bitmap pixels into an SVG through the vtracer engine."""
    import vtracer  # local import keeps the server bootable even if vtracer is absent

    vtracer.convert_image_to_svg_py(
        str(input_path),
        str(output_path),
        colormode="color",
        hierarchical="stacked",
        mode="spline",
        filter_speckle=4,
    )


# ---------------------------------------------------------------------------------------
# Video engine (FFmpeg via asyncio subprocess) - never blocks the event loop
# ---------------------------------------------------------------------------------------

def parse_timestamp_to_seconds(timestamp_text: str, field_label: str) -> float:
    """Parse an HH:MM:SS(.mmm) UI value; raises HTTP 400 with a beginner-friendly message."""
    if not TRIM_TIMESTAMP_PATTERN.match(timestamp_text):
        raise HTTPException(
            status_code=400,
            detail=f"{field_label} must use HH:MM:SS format (example: 00:00:05). "
                   f"Received '{timestamp_text}'.",
        )
    hours_text, minutes_text, seconds_text = timestamp_text.split(":")
    return int(hours_text) * 3600 + int(minutes_text) * 60 + float(seconds_text)


async def read_media_duration_seconds(media_path: Path) -> float | None:
    """Probe duration from `ffmpeg -i` stderr - avoids depending on a separate ffprobe binary."""
    process = await asyncio.create_subprocess_exec(
        str(FFMPEG_STATUS["path"]),
        "-hide_banner",
        "-i",
        str(media_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr_bytes = await process.communicate()
    duration_match = re.search(rb"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)", stderr_bytes)
    if duration_match is None:
        return None
    hours = int(duration_match.group(1))
    minutes = int(duration_match.group(2))
    seconds = float(duration_match.group(3))
    return hours * 3600 + minutes * 60 + seconds


def update_job_progress_from_line(job_id: str, progress_line: str, total_seconds: float | None) -> None:
    """Translate one `ffmpeg -progress` key=value line into a percentage for the polling UI."""
    if not progress_line.startswith("out_time=") or not total_seconds or total_seconds <= 0:
        return
    timestamp_text = progress_line.split("=", 1)[1].strip()
    try:
        hours_text, minutes_text, seconds_text = timestamp_text.split(":")
        elapsed_seconds = int(hours_text) * 3600 + int(minutes_text) * 60 + float(seconds_text)
    except ValueError:
        return  # ffmpeg prints "N/A" while it still probes the input - ignore those samples
    progress_state = JOB_PROGRESS.get(job_id)
    if progress_state is None:
        return
    percentage = min(99.0, round(elapsed_seconds / total_seconds * 100, 1))
    # Timestamps can repeat between frames; never let the bar jump backwards.
    if percentage >= float(progress_state.get("progress", 0.0)):
        progress_state["progress"] = percentage
        progress_state["message"] = f"Encoding... {percentage:.0f}%"


async def run_ffmpeg_with_progress(job_id: str, command: list[str], total_seconds: float | None) -> None:
    """Execute FFmpeg asynchronously while streaming progress into JOB_PROGRESS.

    stdout carries `-progress` telemetry; stderr is drained concurrently so the
    pipe buffer can never fill up and deadlock the subprocess.
    """
    JOB_PROGRESS[job_id].update(status="processing", progress=0.0, message="FFmpeg started...")
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    captured_stderr = bytearray()

    async def collect_stderr_output() -> None:
        while error_chunk := await process.stderr.read(8192):
            captured_stderr.extend(error_chunk)

    stderr_collection_task = asyncio.create_task(collect_stderr_output())
    try:
        async with asyncio.timeout(FFMPEG_TIMEOUT_SECONDS):
            while True:
                progress_bytes = await process.stdout.readline()
                if not progress_bytes:
                    break
                progress_line = progress_bytes.decode("utf-8", errors="replace")
                update_job_progress_from_line(job_id, progress_line, total_seconds)
            return_code = await process.wait()
    except TimeoutError as timeout_error:
        process.kill()
        await process.wait()
        raise MediaProcessingError(
            f"FFmpeg exceeded the {FFMPEG_TIMEOUT_SECONDS // 60}-minute safety timeout and was stopped."
        ) from timeout_error
    finally:
        await stderr_collection_task

    if return_code != 0:
        error_tail = captured_stderr.decode("utf-8", errors="replace").strip()[-3000:]
        raise MediaProcessingError(f"FFmpeg failed (exit code {return_code}): {error_tail}")


def build_ffmpeg_command(
    input_path: Path,
    output_path: Path,
    operation: str,
    crf_preset: str,
    strip_metadata: bool,
    gif_fps: int,
    gif_width: int,
    trim_start_seconds: float,
    trim_duration_seconds: float,
    use_stream_copy: bool,
) -> list[str]:
    """Translate a UI operation into an explicit FFmpeg argv (list form = no shell involved)."""
    common_arguments = [str(FFMPEG_STATUS["path"]), "-y", "-hide_banner", "-nostdin"]
    # -map_metadata -1 drops container metadata such as GPS/device tags before distribution.
    metadata_arguments = ["-map_metadata", "-1"] if strip_metadata else []
    # -progress pipe:1 streams machine-readable timestamps the polling UI turns into a % bar.
    progress_arguments = ["-progress", "pipe:1", "-nostats"]
    seek_arguments: list[str] = []

    if operation in {"convert", "compress"}:
        # Conversion uses a near-transparent CRF; compression honours the chosen preset (FR-VID-01/03).
        crf_value = 20 if operation == "convert" else CRF_PRESETS[crf_preset]
        encoding_arguments = [
            "-map", "0:v:0", "-map", "0:a?",  # first video + audio; drop subs/data MP4 cannot hold
            "-c:v", "libx264", "-preset", "medium", "-crf", str(crf_value),
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
        ]
    elif operation == "gif":
        # Two-pass palette workflow keeps GIF colours faithful while capping fps/width (FR-VID-02).
        filter_graph = (
            f"[0:v]fps={gif_fps},scale=w='min({gif_width},iw)':h=-1:flags=lanczos,"
            "split[left][right];[left]palettegen=stats_mode=diff[palette];"
            "[right][palette]paletteuse=dither=sierra2_4a"
        )
        encoding_arguments = ["-filter_complex", filter_graph, "-loop", "0"]
    elif operation == "extract_audio":
        encoding_arguments = ["-vn", "-c:a", "libmp3lame", "-q:a", "2"]  # VBR ~190 kbps (FR-VID-04)
    elif operation == "trim":
        # Lossless trim (FR-VID-05): -ss before -i seeks fast, -t caps the output length.
        seek_arguments = ["-ss", f"{trim_start_seconds:.3f}"]
        encoding_arguments = ["-t", f"{trim_duration_seconds:.3f}", "-map", "0:v:0", "-map", "0:a?"]
        if use_stream_copy:
            encoding_arguments += ["-c", "copy"]
        else:
            encoding_arguments += [
                "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
            ]
    elif operation == "strip_audio":
        encoding_arguments = ["-map", "0:v:0", "-an"]  # keep video, drop all audio (FR-VID-06)
        if use_stream_copy:
            encoding_arguments += ["-c:v", "copy"]
        else:
            encoding_arguments += [
                "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-movflags", "+faststart",
            ]
    else:
        raise MediaProcessingError(f"Unknown video operation '{operation}'.")  # endpoint validates first

    return (
        common_arguments
        + seek_arguments
        + ["-i", str(input_path)]
        + encoding_arguments
        + metadata_arguments
        + progress_arguments
        + [str(output_path)]
    )


def fail_processing_job(job_id: str, upload_path: Path, error_message: str) -> None:
    """Record the failure for the polling UI and delete the temp upload immediately."""
    JOB_PROGRESS[job_id].update(status="error", message=error_message)
    discard_temp_files(upload_path)


async def execute_video_processing_job(
    job_id: str,
    file_id: str,
    input_path: Path,
    original_stem: str,
    operation: str,
    crf_preset: str,
    strip_metadata: bool,
    gif_fps: int,
    gif_width: int,
    trim_start_seconds: float,
    trim_duration_seconds: float,
) -> None:
    """BackgroundTasks worker: FFmpeg runs after the upload response has been sent (FR-SYS-03)."""
    progress_state = JOB_PROGRESS[job_id]
    output_extension = OUTPUT_EXTENSION_BY_OPERATION[operation]
    output_path = TEMP_PROCESSING_DIRECTORY / f"{file_id}_output{output_extension}"
    command_arguments = {
        "input_path": input_path,
        "output_path": output_path,
        "operation": operation,
        "crf_preset": crf_preset,
        "strip_metadata": strip_metadata,
        "gif_fps": gif_fps,
        "gif_width": gif_width,
        "trim_start_seconds": trim_start_seconds,
        "trim_duration_seconds": trim_duration_seconds,
    }
    try:
        if operation == "trim":
            # For a trim the progress bar covers only the selected segment, not the whole file.
            expected_duration = trim_duration_seconds
        else:
            expected_duration = await read_media_duration_seconds(input_path)

        try:
            await run_ffmpeg_with_progress(
                job_id,
                build_ffmpeg_command(use_stream_copy=True, **command_arguments),
                expected_duration,
            )
        except MediaProcessingError:
            # Stream copy fails when the source codec cannot live inside an MP4 container -
            # fall back to a full re-encode so the user still gets a working file.
            if operation not in {"trim", "strip_audio"}:
                raise
            progress_state["message"] = "Stream copy unsupported here, re-encoding instead..."
            await run_ffmpeg_with_progress(
                job_id,
                build_ffmpeg_command(use_stream_copy=False, **command_arguments),
                expected_duration,
            )

        download_name = f"{original_stem}_{OUTPUT_LABEL_BY_OPERATION[operation]}{output_extension}"
        register_processed_file(
            file_id=file_id,
            output_path=output_path,
            download_name=download_name,
            input_path=input_path,
        )
        progress_state.update(
            status="done",
            progress=100.0,
            message="Processing complete",
            filename=download_name,
            output_size=output_path.stat().st_size,
        )
    except Exception as processing_error:
        error_message = str(processing_error).strip() or processing_error.__class__.__name__
        fail_processing_job(job_id=job_id, upload_path=input_path, error_message=error_message)


# ---------------------------------------------------------------------------------------
# FastAPI application, routes & startup
# ---------------------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(application: FastAPI):
    """Startup pre-flight: clean stale temp files and verify both processing engines."""
    TEMP_PROCESSING_DIRECTORY.mkdir(parents=True, exist_ok=True)
    removed_count = clear_stale_temp_files()
    print(f"[Local Media Processor] startup cleanup removed {removed_count} stale file(s).")

    ffmpeg_path, ffmpeg_source = locate_ffmpeg_executable()
    if ffmpeg_path is None:
        print(
            "[Local Media Processor] WARNING: FFmpeg not found - video tools are disabled. "
            "Install FFmpeg and add it to PATH, or run `pip install imageio-ffmpeg`."
        )
    else:
        FFMPEG_STATUS.update(
            available=True,
            source=ffmpeg_source,
            path=ffmpeg_path,
            version=await read_ffmpeg_version(ffmpeg_path),
        )
        print(f"[Local Media Processor] FFmpeg ready ({ffmpeg_source}): {FFMPEG_STATUS['version']}")

    vtracer_available, vtracer_error = probe_vtracer_import()
    VTRACER_STATUS.update(available=vtracer_available, error=vtracer_error)
    if not vtracer_available:
        print(f"[Local Media Processor] WARNING: vtracer unavailable - SVG tracing disabled ({vtracer_error}).")
    yield


app = FastAPI(title="Local Media Processor", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIRECTORY / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def serve_index_page(request: Request) -> HTMLResponse:
    """Render the single-page UI (FR-UI-01)."""
    return TEMPLATES.TemplateResponse(request=request, name="index.html")


@app.get("/api/health")
async def read_application_health() -> dict:
    """Engine pre-flight status for the UI badges - local data only, zero telemetry (NFR 5.2)."""
    return {
        "ffmpeg": dict(FFMPEG_STATUS),
        "vtracer": dict(VTRACER_STATUS),
        "host": HOST_IP,
        "port": SERVER_PORT,
    }


@app.post("/api/convert-image")
async def convert_uploaded_image(
    file: Annotated[UploadFile, File(description="Source image (PNG/JPG/WEBP/BMP/...)")],
    operation: Annotated[str, Form(description="convert | ico | svg")] = "convert",
    target_format: Annotated[str, Form(description="png | jpg | webp | bmp")] = "png",
    max_width: Annotated[int | None, Form(ge=1, le=20000)] = None,
    max_height: Annotated[int | None, Form(ge=1, le=20000)] = None,
    quality: Annotated[int, Form(ge=1, le=100)] = 90,
    strip_metadata: Annotated[bool, Form(description="Remove EXIF/device metadata")] = True,
) -> dict:
    """FR-IMG-01..04: raster conversion, ICO favicon generation, and SVG vector tracing."""
    if operation not in IMAGE_OPERATIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown image operation '{operation}'. "
                   f"Choose one of: {', '.join(sorted(IMAGE_OPERATIONS))}.",
        )
    if operation == "convert" and target_format not in RASTER_FORMATS:
        raise HTTPException(status_code=400, detail=f"Unsupported target format '{target_format}'.")

    file_id, input_path, original_stem = await store_uploaded_file(file, IMAGE_EXTENSIONS, "image")
    output_extension = IMAGE_OUTPUT_EXTENSIONS[target_format if operation == "convert" else operation]
    output_path = TEMP_PROCESSING_DIRECTORY / f"{file_id}_output{output_extension}"

    try:
        if operation == "convert":
            await run_in_threadpool(
                convert_raster_image_format,
                input_path, output_path, target_format,
                max_width, max_height, quality, strip_metadata,
            )
        elif operation == "ico":
            await run_in_threadpool(generate_multiresolution_icon, input_path, output_path)
        else:
            await run_in_threadpool(trace_bitmap_to_svg, input_path, output_path)
    except Exception as processing_error:
        discard_temp_files(input_path, output_path)
        raise HTTPException(
            status_code=400,
            detail=f"Image processing failed: "
                   f"{str(processing_error).strip() or processing_error.__class__.__name__}",
        ) from processing_error

    download_name = f"{original_stem}{output_extension}"
    register_processed_file(
        file_id=file_id,
        output_path=output_path,
        download_name=download_name,
        input_path=input_path,
    )
    return {
        "file_id": file_id,
        "filename": download_name,
        "input_size": input_path.stat().st_size,
        "output_size": output_path.stat().st_size,
    }


@app.post("/api/process-video")
async def process_uploaded_video(
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile, File(description="Source video (MP4/MOV/MKV/AVI/WEBM/...)")],
    operation: Annotated[str, Form(description="convert | compress | gif | extract_audio | trim | strip_audio")],
    crf_preset: Annotated[str, Form(description="light | balanced | aggressive")] = "balanced",
    start_time: Annotated[str, Form(description="Trim start HH:MM:SS")] = "00:00:00",
    end_time: Annotated[str, Form(description="Trim end HH:MM:SS")] = "",
    gif_fps: Annotated[int, Form(ge=1, le=50)] = 15,
    gif_width: Annotated[int, Form(ge=64, le=1920)] = 480,
    strip_metadata: Annotated[bool, Form(description="Remove GPS/device metadata")] = True,
) -> dict:
    """FR-VID-01..06: start FFmpeg as a background job; the UI polls /api/progress/{job_id}."""
    if not FFMPEG_STATUS["available"]:
        raise HTTPException(
            status_code=503,
            detail="FFmpeg was not found. Install FFmpeg and add it to PATH, "
                   "or install the 'imageio-ffmpeg' package, then restart the app.",
        )
    if operation not in VIDEO_OPERATIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown video operation '{operation}'. "
                   f"Choose one of: {', '.join(sorted(VIDEO_OPERATIONS))}.",
        )
    if crf_preset not in CRF_PRESETS:
        raise HTTPException(status_code=400, detail=f"Unknown compression preset '{crf_preset}'.")

    trim_start_seconds = 0.0
    trim_duration_seconds = 0.0
    if operation == "trim":
        trim_start_seconds = parse_timestamp_to_seconds(start_time, "Start time")
        trim_end_seconds = parse_timestamp_to_seconds(end_time, "End time")
        if trim_end_seconds <= trim_start_seconds:
            raise HTTPException(status_code=400, detail="End time must be later than start time.")
        trim_duration_seconds = trim_end_seconds - trim_start_seconds

    file_id, input_path, original_stem = await store_uploaded_file(file, VIDEO_EXTENSIONS, "video")
    job_id = uuid.uuid4().hex
    # The response returns immediately; FFmpeg keeps running as a BackgroundTask.
    JOB_PROGRESS[job_id] = {
        "status": "queued",
        "progress": 0.0,
        "message": "Waiting for FFmpeg...",
        "file_id": file_id,
    }
    background_tasks.add_task(
        execute_video_processing_job,
        job_id=job_id,
        file_id=file_id,
        input_path=input_path,
        original_stem=original_stem,
        operation=operation,
        crf_preset=crf_preset,
        strip_metadata=strip_metadata,
        gif_fps=gif_fps,
        gif_width=gif_width,
        trim_start_seconds=trim_start_seconds,
        trim_duration_seconds=trim_duration_seconds,
    )
    return {"job_id": job_id, "file_id": file_id, "input_size": input_path.stat().st_size}


@app.get("/api/progress/{job_id}")
async def read_job_progress(job_id: str) -> dict:
    """Poll endpoint backing the real-time progress bar (FR-UI-03)."""
    progress_state = JOB_PROGRESS.get(job_id)
    if progress_state is None:
        raise HTTPException(
            status_code=404,
            detail="Unknown job id - jobs do not survive a server restart.",
        )
    return dict(progress_state)


@app.get("/api/download/{file_id}")
async def download_processed_file(file_id: str, background_tasks: BackgroundTasks) -> FileResponse:
    """Stream the finished file, then delete input + output via BackgroundTasks (FR-SYS-03)."""
    registered_file = REGISTERED_FILES.get(file_id)
    if registered_file is None:
        raise HTTPException(status_code=404, detail="File not found - it may have already been downloaded.")
    output_path = Path(registered_file["path"])
    if not output_path.is_file():
        REGISTERED_FILES.pop(file_id, None)
        raise HTTPException(status_code=404, detail="File no longer exists on disk.")
    # Starlette runs this task only after the response has been fully sent to the browser.
    background_tasks.add_task(remove_registered_file_bundle, file_id)
    return FileResponse(
        path=output_path,
        filename=registered_file["download_name"],
        media_type="application/octet-stream",
    )


# ---------------------------------------------------------------------------------------
# Browser auto-launch & server entry point (FR-SYS-02)
# ---------------------------------------------------------------------------------------

def open_browser_when_ready() -> None:
    """Open the default browser shortly after the server starts listening."""
    try:
        webbrowser.open(f"http://{HOST_IP}:{SERVER_PORT}")
    except Exception as browser_error:
        print(f"[Local Media Processor] Could not auto-open the browser: {browser_error}")


if __name__ == "__main__":
    print(f"[Local Media Processor] starting at http://{HOST_IP}:{SERVER_PORT} (loopback only)")
    threading.Timer(1.5, open_browser_when_ready).start()
    uvicorn.run(app, host=HOST_IP, port=SERVER_PORT, log_level="info")








