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
# UTIL: FFMPEG
# Back4App Container → ffmpeg sudah tersedia jika
# Anda install via Dockerfile "apt-get install ffmpeg"
# ======================================================

def get_ffmpeg():
    p = shutil.which("ffmpeg")
    if p:
        return p
    raise HTTPException(500, "FFmpeg tidak ditemukan di server.")

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
# PROXY (opsional)
# ======================================================

PROXY = "https://video-proxy.fly.dev"
USE_PROXY = False

def make_ydl_opts(req: DownloadReq, output_template):
    ffmpeg = get_ffmpeg()

    ydl_opts = {
        "quiet": True,
        "noplaylist": True,
        "merge_output_format": "mp4",
        "ffmpeg_location": ffmpeg,
        "outtmpl": output_template,
    }

    if USE_PROXY:
        ydl_opts["proxy"] = PROXY

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
# PROCESS DOWNLOAD
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

    # rename output
    if os.path.exists(original_path) and original_path != final_file:
        shutil.move(original_path, final_file)

    # OPTIONAL compression
    if req.compress:
        compressed = f"downloads/{uid}_c.mp4"
        ffmpeg = get_ffmpeg()
        os.system(f'{ffmpeg} -i "{final_file}" -vcodec libx264 -crf 28 -preset fast "{compressed}"')
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
async def api_preview(data: PreviewRequest):
    try:
        url, platform = data.url, data.platform

        # Ambil metadata
        with YoutubeDL({"quiet": True}) as ydl:
            info = ydl.extract_info(url, download=False)
            preview_url = info.get("thumbnail")

        return {"preview_url": preview_url}
    except Exception as e:
        raise HTTPException(500, f"Preview gagal: {e}")

@app.post("/api/video-sizes")
async def api_video_sizes(data: SizeRequest):
    try:
        url, platform = data.url, data.platform

        ydl_opts = {"quiet": True, "skip_download": True}
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        formats = info.get("formats", [])
        sizes = {
            "auto": info.get("filesize") or "–",
            "360p": "–",
            "720p": "–",
            "1080p": "–",
        }

        for fmt in formats:
            q = fmt.get("height")
            if q == 360: sizes["360p"] = fmt.get("filesize") or "–"
            if q == 720: sizes["720p"] = fmt.get("filesize") or "–"
            if q == 1080: sizes["1080p"] = fmt.get("filesize") or "–"

        return sizes
    except Exception as e:
        raise HTTPException(500, f"Gagal size: {e}")

@app.post("/api/download")
async def api_download(data: DownloadRequest):
    try:
        url = data.url
        quality = data.quality or "auto"

        # Pilih kualitas
        ydl_opts = {
            "format": "best" if quality == "auto" else f"bestvideo[height<={quality.replace('p','')}]+bestaudio/best",
            "outtmpl": "downloads/%(id)s-%(resolution)s.%(ext)s",
        }

        os.makedirs("downloads", exist_ok=True)

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)

        endpoint = f"/api/file/{os.path.basename(filename)}"
        return {
            "file_endpoint": endpoint,
            "filename": os.path.basename(filename),
        }

    except Exception as e:
        raise HTTPException(500, f"Download gagal: {e}")

@app.get("/api/status/{task_id}")
def status(task_id: str):
    result = results.get(task_id)
    if result is None:
        return {"status": "processing", "queue": download_queue.qsize()}
    return {"status": "done", "result": result}

@app.get("/api/file/{filename}")
async def api_file(filename: str):
    filepath = f"downloads/{filename}"
    if not os.path.exists(filepath):
        raise HTTPException(404, "File tidak ditemukan")
    return FileResponse(filepath, filename=filename)

# ======================================================
# BACK4APP ENTRYPOINT (penting)
# ======================================================

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
