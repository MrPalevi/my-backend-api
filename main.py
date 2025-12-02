import os
import uuid
import shutil

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from yt_dlp import YoutubeDL

# ======================================================
# PATHS: RAILWAY SAFE
# ======================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ======================================================
# FASTAPI
# ======================================================

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_headers=["*"],
    allow_methods=["*"],
)


# ======================================================
# MODELS
# ======================================================

class PreviewRequest(BaseModel):
    url: str
    platform: str


class SizeRequest(BaseModel):
    url: str
    platform: str


class DownloadRequest(BaseModel):
    url: str
    platform: str
    quality: str = "auto"


# ======================================================
# ROOT
# ======================================================

@app.get("/")
def root():
    return {"status": "backend is running"}


# ======================================================
# PREVIEW
# ======================================================

@app.post("/api/preview")
async def api_preview(data: PreviewRequest):
    try:
        with YoutubeDL({"quiet": True}) as ydl:
            info = ydl.extract_info(data.url, download=False)
        return {"preview_url": info.get("thumbnail")}
    except Exception as e:
        raise HTTPException(500, f"Preview error: {e}")


# ======================================================
# VIDEO SIZE
# ======================================================

@app.post("/api/video-sizes")
async def api_video_sizes(data: SizeRequest):
    try:
        with YoutubeDL({"quiet": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(data.url, download=False)

        formats = info.get("formats", [])

        sizes = {"auto": info.get("filesize") or "–", "360p": "–", "720p": "–", "1080p": "–"}

        for f in formats:
            h = f.get("height")
            if h in (360, 720, 1080):
                sizes[f"{h}p"] = f.get("filesize") or "–"

        return sizes

    except Exception as e:
        raise HTTPException(500, f"Size error: {e}")


# ======================================================
# DOWNLOAD
# ======================================================

@app.post("/api/download")
async def api_download(data: DownloadRequest):
    try:
        quality = data.quality
        if quality == "auto":
            fmt = "best"
        else:
            h = quality.replace("p", "")
            fmt = f"bestvideo[height<={h}]+bestaudio/best"

        uid = str(uuid.uuid4())[:8]
        output_template = os.path.join(DOWNLOAD_DIR, f"{uid}.%(ext)s")

        ydl_opts = {
            "format": fmt,
            "outtmpl": output_template,
            "quiet": True,
            "merge_output_format": "mp4",  # works without ffmpeg on most YT formats
        }

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(data.url, download=True)
            final_path = ydl.prepare_filename(info)

        filename = os.path.basename(final_path)
        return {"file_endpoint": f"/api/file/{filename}", "filename": filename}

    except Exception as e:
        raise HTTPException(500, f"Download error: {e}")


# ======================================================
# SERVE FILE
# ======================================================

@app.get("/api/file/{filename}")
async def api_file(filename: str):
    path = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(404, "File not found")
    return FileResponse(path, filename=filename)


# ======================================================
# ENTRYPOINT
# ======================================================

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
