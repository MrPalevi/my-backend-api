import os
import uuid
import shutil

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from yt_dlp import YoutubeDL


# ======================================================
# PATHS & DIRECTORIES
# ======================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


# ======================================================
# FASTAPI INITIALIZATION
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


class Mp3Request(BaseModel):
    url: str


class RenameRequest(BaseModel):
    old_name: str
    new_name: str


# ======================================================
# ROOT
# ======================================================

@app.get("/")
def root():
    return {"status": "backend is running"}


# ======================================================
# MP3 PREVIEW: TITLE + THUMBNAIL
# ======================================================

@app.post("/api/mp3/preview")
async def api_mp3_preview(data: Mp3Request):
    try:
        with YoutubeDL({"quiet": True}) as ydl:
            info = ydl.extract_info(data.url, download=False)

        return {
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail")
        }

    except Exception as e:
        raise HTTPException(500, f"MP3 preview error: {e}")


# ======================================================
# VIDEO PREVIEW (thumbnail)
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
# VIDEO DOWNLOAD (with quality)
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
            "merge_output_format": "mp4",
        }

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(data.url, download=True)
            final_path = ydl.prepare_filename(info)

        filename = os.path.basename(final_path)

        return {
            "file_endpoint": f"/api/file/{filename}",
            "filename": filename
        }

    except Exception as e:
        raise HTTPException(500, f"Download error: {e}")


# ======================================================
# DOWNLOAD MP3 (single video only)
# ======================================================

@app.post("/api/download-mp3")
async def api_download_mp3(data: Mp3Request):
    try:
        url = data.url

        uid = uuid.uuid4().hex
        output_template = os.path.join(DOWNLOAD_DIR, f"{uid}.%(ext)s")

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": output_template,
            "quiet": True,
            "noplaylist": True,  # ❗ hanya 1 video, bukan playlist
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        # Path final MP3
        final_mp3_path = os.path.join(DOWNLOAD_DIR, f"{uid}.mp3")
        filename = f"{uid}.mp3"

        return {
            "status": "success",
            "filename": filename,
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "filesize": info.get("filesize"),
            "duration": info.get("duration"),
            "file_endpoint": f"/api/file/{filename}"
        }

    except Exception as e:
        raise HTTPException(500, f"MP3 download error: {e}")


# ======================================================
# RENAME MP3
# ======================================================

@app.post("/api/mp3/rename")
async def api_mp3_rename(data: RenameRequest):
    old_path = os.path.join(DOWNLOAD_DIR, data.old_name)
    new_path = os.path.join(DOWNLOAD_DIR, data.new_name)

    if not os.path.exists(old_path):
        raise HTTPException(404, "Old file not found")

    os.rename(old_path, new_path)

    return {
        "status": "success",
        "filename": data.new_name,
        "file_endpoint": f"/api/file/{data.new_name}"
    }


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
