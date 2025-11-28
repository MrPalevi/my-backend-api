import os
import uuid
import shutil
import asyncio
import logging
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from yt_dlp import YoutubeDL

# -------------------------
# Logging
# -------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("video-downloader")

# -------------------------
# Config via ENV
# -------------------------
PORT = int(os.getenv("PORT", "8000"))
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "downloads")
FFMPEG_PATH = os.getenv("FFMPEG_PATH", shutil.which("ffmpeg") or "")
# Comma-separated proxies, e.g. "http://user:pass@1.2.3.4:8080, http://5.6.7.8:8080"
PROXIES_ENV = os.getenv("PROXIES", "")
# Number of concurrent workers
WORKER_COUNT = int(os.getenv("WORKER_COUNT", "2"))
# max queue size
MAX_QUEUE = int(os.getenv("MAX_QUEUE", "50"))
# Default re-encode settings (used to optimize size)
DEFAULT_CRF = os.getenv("DEFAULT_CRF", "28")  # higher CRF -> smaller size
DEFAULT_PRESET = os.getenv("DEFAULT_PRESET", "fast")  # ffmpeg preset
# Whether to enable proxy rotation
PROXY_ROTATION_ENABLED = os.getenv("PROXY_ROTATION_ENABLED", "true").lower() in ("1", "true", "yes")

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Parse proxies list
proxies_list = [p.strip() for p in PROXIES_ENV.split(",") if p.strip()]
proxy_index = 0
proxy_lock = asyncio.Lock()

def get_next_proxy() -> Optional[str]:
    """Rotate through proxies in a round-robin fashion (async-safe)."""
    global proxy_index
    if not proxies_list:
        return None
    proxy = proxies_list[proxy_index % len(proxies_list)]
    proxy_index += 1
    return proxy

# -------------------------
# FastAPI app & models
# -------------------------
app = FastAPI(title="Social Video Downloader (Fly-ready)")

class DownloadRequest(BaseModel):
    url: str
    platform: Optional[str] = "unknown"  # optional
    quality: Optional[str] = "auto"      # auto|360p|720p|1080p
    optimize: Optional[bool] = True      # re-encode to reduce size
    crf: Optional[int] = None            # override CRF for re-encode

class PreviewRequest(BaseModel):
    url: str
    platform: Optional[str] = "unknown"

class VideoSizeRequest(BaseModel):
    url: str
    platform: Optional[str] = "unknown"

# -------------------------
# Job queue & worker state
# -------------------------
jobs: Dict[str, Dict[str, Any]] = {}  # job_id -> status/info
queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE)
worker_tasks: list = []

# Helper: map quality to scale/resolution target
QUALITY_TO_MAX_HEIGHT = {
    "360p": 360,
    "720p": 720,
    "1080p": 1080
}

# -------------------------
# Utility functions
# -------------------------
def build_ydl_opts_for_download(output_template: str, proxy: Optional[str], crf: Optional[int], optimize: bool, target_height: Optional[int]):
    """
    Build yt-dlp options for downloading + optional re-encode (postprocessor args).
    """
    ydl_opts = {
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        # prefer ffmpeg for merging
        "merge_output_format": "mp4",
    }

    if proxy:
        # yt-dlp accepts 'proxy' key like 'http://host:port' (applies to all protocols)
        ydl_opts["proxy"] = proxy

    # For optimization: use ffmpeg postprocessor with custom args
    postprocessors = []
    postprocessor_args = []

    ff_crf = int(crf) if crf is not None else int(DEFAULT_CRF)

    if optimize:
        # Use FFmpegVideoConvertor to ensure mp4 and apply quality settings
        postprocessors.append({
            "key": "FFmpegVideoConvertor",
            "preferedformat": "mp4",
        })
        # Re-encode with CRF and preset; scale if target_height provided
        # Note: postprocessor_args is passed to ffmpeg.
        # We include "-preset", "-crf" and optional scale filter
        postprocessor_args = ["-preset", DEFAULT_PRESET, "-crf", str(ff_crf)]
        if target_height:
            # Use scale filter keeping aspect ratio (-2 to make width even)
            postprocessor_args += ["-vf", f"scale=-2:{target_height}"]

        # attach args
        ydl_opts["postprocessor_args"] = postprocessor_args

    if postprocessors:
        ydl_opts["postprocessors"] = postprocessors

    # ensure ffmpeg location if provided
    if FFMPEG_PATH:
        ydl_opts["ffmpeg_location"] = FFMPEG_PATH

    return ydl_opts

def build_ydl_opts_for_info(proxy: Optional[str]):
    opts = {
        "format": "bestvideo+bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    if proxy:
        opts["proxy"] = proxy
    if FFMPEG_PATH:
        opts["ffmpeg_location"] = FFMPEG_PATH
    return opts

def safe_filesize_to_str(size_bytes: Optional[int]) -> str:
    if not size_bytes:
        return "–"
    return f"{round(size_bytes / 1024 / 1024, 1)} MB"

# -------------------------
# Background worker
# -------------------------
async def download_worker(worker_id: int):
    logger.info(f"Worker-{worker_id} started")
    while True:
        job_id, req_data = await queue.get()
        try:
            jobs[job_id]["status"] = "processing"
            jobs[job_id]["log"].append(f"Worker-{worker_id} picked job")

            # choose proxy (if enabled)
            proxy = None
            if PROXY_ROTATION_ENABLED and proxies_list:
                async with proxy_lock:
                    proxy = get_next_proxy()
                jobs[job_id]["log"].append(f"Using proxy: {proxy}")

            # prepare output template unique per job
            unique_id = str(uuid.uuid4())[:12]
            output_template = os.path.join(DOWNLOAD_DIR, f"{unique_id}.%(ext)s")
            # target height based on requested quality
            quality = req_data.get("quality", "auto")
            target_height = QUALITY_TO_MAX_HEIGHT.get(quality)

            # ydl options
            ydl_opts = build_ydl_opts_for_download(
                output_template=output_template,
                proxy=proxy,
                crf=req_data.get("crf"),
                optimize=req_data.get("optimize", True),
                target_height=target_height
            )

            # start download (blocking) inside thread to avoid blocking event loop
            loop = asyncio.get_event_loop()
            result_info = await loop.run_in_executor(None, _download_with_ydl_sync, req_data["url"], ydl_opts)
            # result_info contains 'filepath' and 'info'
            filepath = result_info["filepath"]
            filename = os.path.basename(filepath)

            # mark success
            jobs[job_id]["status"] = "done"
            jobs[job_id]["file"] = filename
            jobs[job_id]["file_endpoint"] = f"/api/file/{filename}"
            jobs[job_id]["log"].append(f"Job finished: {filename}")
        except Exception as e:
            logger.exception("Download error")
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = str(e)
            jobs[job_id]["log"].append(f"Error: {e}")
        finally:
            queue.task_done()

def _download_with_ydl_sync(url: str, ydl_opts: dict):
    """
    Synchronous wrapper called in ThreadPool executor: performs yt-dlp download and returns output path & info.
    """
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # prepare_filename uses info to find temp path created by yt-dlp
        temp_path = ydl.prepare_filename(info)
        # ensure extension mp4 if merged
        # Sometimes temp_path might be .mkv/.webm - our postprocessors should convert to mp4
        # Find actual file (look for files starting with our unique id)
        base = os.path.splitext(temp_path)[0]
        # prefer mp4
        candidate_mp4 = f"{base}.mp4"
        if os.path.exists(candidate_mp4):
            final_path = candidate_mp4
        elif os.path.exists(temp_path):
            final_path = temp_path
        else:
            # scan downloads dir for matching prefix
            files = [f for f in os.listdir(DOWNLOAD_DIR) if f.startswith(os.path.basename(base))]
            if files:
                final_path = os.path.join(DOWNLOAD_DIR, files[0])
            else:
                raise RuntimeError("Downloaded file not found after yt-dlp run")
    return {"filepath": final_path, "info": info}

# -------------------------
# Startup: spawn workers
# -------------------------
@app.on_event("startup")
async def startup_event():
    global worker_tasks
    logger.info("Starting download workers...")
    for i in range(WORKER_COUNT):
        task = asyncio.create_task(download_worker(i+1))
        worker_tasks.append(task)
    logger.info(f"Spawned {WORKER_COUNT} workers")

# -------------------------
# Shutdown: cancel workers
# -------------------------
@app.on_event("shutdown")
async def shutdown_event():
    for t in worker_tasks:
        t.cancel()
    await asyncio.sleep(0.1)

# -------------------------
# Endpoints
# -------------------------
@app.post("/api/download")
async def enqueue_download(req: DownloadRequest):
    """
    Enqueue a download job. Returns job_id to poll status.
    """
    if queue.full():
        raise HTTPException(status_code=503, detail="Server busy: queue is full")

    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        "id": job_id,
        "status": "queued",
        "url": req.url,
        "quality": req.quality,
        "optimize": req.optimize,
        "crf": req.crf,
        "log": []
    }
    await queue.put((job_id, req.dict()))
    jobs[job_id]["log"].append("Enqueued")
    logger.info(f"Enqueued job {job_id} for {req.url}")
    return {"job_id": job_id, "status_endpoint": f"/api/job/{job_id}"}

@app.get("/api/job/{job_id}")
async def get_job_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

@app.post("/api/preview")
async def preview(req: PreviewRequest):
    """
    Return streaming URL (no download). Uses proxy rotation if configured.
    """
    # pick proxy if configured
    proxy = None
    if PROXY_ROTATION_ENABLED and proxies_list:
        async with proxy_lock:
            proxy = get_next_proxy()

    ydl_opts = build_ydl_opts_for_info(proxy)
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(req.url, download=False)
            # yt-dlp sometimes stores direct video url in 'url' or best format in formats[-1]
            video_url = info.get("url") or None
            if not video_url:
                # try to pick best format url
                formats = info.get("formats") or []
                if formats:
                    # choose the best format that has a url
                    f = next((x for x in reversed(formats) if x.get("url")), None)
                    if f:
                        video_url = f.get("url")
            if not video_url:
                raise HTTPException(status_code=404, detail="Preview URL tidak tersedia")

            return {"preview_url": video_url}
    except Exception as e:
        logger.exception("Preview failed")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/video-sizes")
async def video_sizes(req: VideoSizeRequest):
    """
    Return estimated sizes for 360p/720p/1080p/auto using format height & filesize where available.
    """
    # pick proxy if configured
    proxy = None
    if PROXY_ROTATION_ENABLED and proxies_list:
        async with proxy_lock:
            proxy = get_next_proxy()

    ydl_opts = build_ydl_opts_for_info(proxy)
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(req.url, download=False)
            formats = info.get("formats", [])

            sizes = {"360p": "–", "720p": "–", "1080p": "–", "auto": "–"}
            # For robustness: gather best filesize per bucket (choose smallest filesize for given height bucket may be more accurate)
            bucket_sizes = {"360p": None, "720p": None, "1080p": None}

            for f in formats:
                height = f.get("height")
                filesize = f.get("filesize") or f.get("filesize_approx")
                if not height or not filesize:
                    continue
                if height <= 360 and (bucket_sizes["360p"] is None or filesize < bucket_sizes["360p"]):
                    bucket_sizes["360p"] = filesize
                elif height <= 720 and (bucket_sizes["720p"] is None or filesize < bucket_sizes["720p"]):
                    bucket_sizes["720p"] = filesize
                elif height <= 1080 and (bucket_sizes["1080p"] is None or filesize < bucket_sizes["1080p"]):
                    bucket_sizes["1080p"] = filesize

            for k in bucket_sizes:
                sizes[k] = safe_filesize_to_str(bucket_sizes[k])

            # auto: try to use info main filesize or pick largest format
            best_size = info.get("filesize") or info.get("filesize_approx")
            if not best_size:
                # pick largest available format filesize
                candidate_sizes = [f.get("filesize") or f.get("filesize_approx") for f in formats if (f.get("filesize") or f.get("filesize_approx"))]
                if candidate_sizes:
                    best_size = max(candidate_sizes)
            sizes["auto"] = safe_filesize_to_str(best_size)
            return sizes
    except Exception as e:
        logger.exception("video-sizes failed")
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/file/{filename}")
async def get_file(filename: str):
    """
    Serve downloaded file (must exist in DOWNLOAD_DIR).
    """
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path, media_type="video/mp4", filename=filename)

# -------------------------
# Simple health endpoint
# -------------------------
@app.get("/healthz")
async def health():
    return {"status": "ok", "queue_size": queue.qsize(), "workers": WORKER_COUNT}

# -------------------------
# Notes for Fly.io
# -------------------------
# - Provide environment variables in Fly.io dashboard (FFMPEG_PATH if ffmpeg isn't on PATH, PROXIES, WORKER_COUNT, DOWNLOAD_DIR).
# - Start command for Fly.io: uvicorn main:app --host 0.0.0.0 --port $PORT
# - Ensure the Fly image/container includes ffmpeg. If using Fly's default python image, provide Dockerfile that installs ffmpeg.
#
# Example env vars to set on Fly:
#   PROXIES="http://user:pass@1.2.3.4:8080,http://5.6.7.8:8080"
#   PROXY_ROTATION_ENABLED="true"
#   DOWNLOAD_DIR="/data/downloads"
#   FFMPEG_PATH="/usr/bin/ffmpeg"
#   WORKER_COUNT="2"
#   DEFAULT_CRF="28"
#
# Security note: don't expose this publically without rate-limits/authentication; this code is intended for testing/uji coba.
#
# End of file
