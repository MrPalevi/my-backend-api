import os
import uuid
import shutil
import queue
import threading

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from yt_dlp import YoutubeDL

# ======================================================
# FASTAPI + CORS
# ======================================================

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_headers=["*"],
    allow_methods=["*"],
)

# ======================================================
# REQUEST MODEL
# ======================================================

class DownloadReq(BaseModel):
    url: str
    platform: str
    quality: str = "auto"
    compress: bool = False

# ======================================================
# UTIL: FFMPEG path
# ======================================================

def get_ffmpeg():
    p = shutil.which("ffmpeg")
    if p:
        return p

    # user may upload ffmpeg binary to root
    if os.path.exists("./ffmpeg"):
        return "./ffmpeg"

    raise HTTPException(500, "FFmpeg tidak ditemukan di Replit. Upload binary ke root.")

# ======================================================
# DOWNLOAD QUEUE
# ======================================================

download_queue = queue.Queue()
results = {}  # task_id → result

def worker_thread():
    while True:
        task_id, req = download_queue.get()
        try:
            results[task_id] = process_download(req)
        except Exception as e:
            results[task_id] = {"error": str(e)}
        download_queue.task_done()

threading.Thread(target=worker_thread, daemon=True).start()

# ======================================================
# PROXY SETTING (untuk FB/TikTok/IG)
# ======================================================

PROXY = "https://video-proxy.fly.dev"   # opsional, kamu bisa ganti
USE_PROXY = False                      # switch-on kalau perlu

def make_ydl_opts(req: DownloadReq, output_template):
    ffmpeg = get_ffmpeg()

    ydl_opts = {
        "quiet": True,
        "noplaylist": True,
        "merge_output_format": "mp4",
        "ffmpeg_location": ffmpeg,
        "outtmpl": output_template,
        "proxy": PROXY if USE_PROXY else None,
    }

    # Target quality
    if req.quality == "360p":
        ydl_opts["format"] = "bestvideo[height<=360]+bestaudio/best"
    elif req.quality == "720p":
        ydl_opts["format"] = "bestvideo[height<=720]+bestaudio/best"
    elif req.quality == "1080p":
        ydl_opts["format"] = "bestvideo[height<=1080]+bestaudio/best"
    else:
        ydl_opts["format"] = "bestvideo+bestaudio/best"

    return ydl_opts

# ======================================================
# PROCESS DOWNLOAD (actual executor)
# ======================================================

def process_download(req: DownloadReq):
    uid = str(uuid.uuid4())[:8]
    os.makedirs("downloads", exist_ok=True)
    template = f"downloads/{uid}.%(ext)s"

    ydl_opts = make_ydl_opts(req, template)

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(req.url, download=True)

    original_path = ydl.prepare_filename(info)
    final_file = f"downloads/{uid}.mp4"

    # rename
    if os.path.exists(original_path) and original_path != final_file:
        shutil.move(original_path, final_file)

    # OPTIONAL COMPRESSION
    if req.compress:
        compressed = f"downloads/{uid}_c.mp4"
        ffmpeg = get_ffmpeg()
        os.system(f'{ffmpeg} -i {final_file} -vcodec libx264 -crf 28 -preset fast {compressed}')
        final_file = compressed

    return {
        "file": f"/api/file/{os.path.basename(final_file)}",
        "filename": os.path.basename(final_file)
    }

# ======================================================
# API ENDPOINTS
# ======================================================

@app.get("/")
def root():
    return {"status": "Backend aktif", "queue": download_queue.qsize()}

@app.post("/api/preview")
def preview(req: DownloadReq):
    try:
        with YoutubeDL({"quiet": True}) as ydl:
            info = ydl.extract_info(req.url, download=False)

        return {
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "platform": req.platform
        }
    except Exception as e:
        raise HTTPException(400, str(e))

@app.post("/api/video-sizes")
def video_sizes(req: DownloadReq):
    try:
        with YoutubeDL({"quiet": True}) as ydl:
            info = ydl.extract_info(req.url, download=False)
        formats = info.get("formats", [])

        def size_for(h):
            for f in formats:
                if f.get("height") == h:
                    sz = f.get("filesize") or f.get("filesize_approx")
                    if sz:
                        return f"{round(sz/1024/1024, 2)} MB"
            return "–"

        return {
            "360p": size_for(360),
            "720p": size_for(720),
            "1080p": size_for(1080),
            "auto": size_for(info.get("height"))
        }

    except Exception as e:
        raise HTTPException(400, str(e))

@app.post("/api/download")
def queue_download(req: DownloadReq):
    task_id = str(uuid.uuid4())
    results[task_id] = None
    download_queue.put((task_id, req))
    return {"task_id": task_id, "status": "queued"}

@app.get("/api/status/{task_id}")
def status(task_id: str):
    result = results.get(task_id)
    if result is None:
        return {"status": "processing", "queue": download_queue.qsize()}
    return {"status": "done", "result": result}

@app.get("/api/file/{filename}")
def file(filename: str):
    path = f"downloads/{filename}"
    if not os.path.exists(path):
        raise HTTPException(404, "File not found.")
    return FileResponse(path, media_type="video/mp4", filename=filename)