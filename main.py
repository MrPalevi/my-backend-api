from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from yt_dlp import YoutubeDL
import os
import uuid
import shutil

app = FastAPI()

# ============================
# CORS untuk Flutter / Android
# ============================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================
# Request Models
# ============================
class DownloadRequest(BaseModel):
    url: str
    platform: str

class VideoSizeRequest(BaseModel):
    url: str
    platform: str

class PreviewRequest(BaseModel):
    url: str
    platform: str

# ============================
# Endpoint Download Video
# ============================
@app.post("/api/download")
async def download_video(req: DownloadRequest):
    unique_id = str(uuid.uuid4())[:8]
    output_dir = "downloads"
    os.makedirs(output_dir, exist_ok=True)
    output_template = f"{output_dir}/{unique_id}.%(ext)s"

    # Cek ffmpeg
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        if os.path.exists("C:/ffmpeg/bin/ffmpeg.exe"):
            ffmpeg_path = "C:/ffmpeg/bin/ffmpeg.exe"
        elif os.path.exists("C:/ffmpeg/bin"):
            ffmpeg_path = "C:/ffmpeg/bin"
        else:
            raise HTTPException(500, detail="ffmpeg tidak ditemukan di PATH sistem")

    ydl_opts = {
        "format": "bestvideo+bestaudio/best",
        "outtmpl": output_template,
        "merge_output_format": "mp4",
        "quiet": True,
        "noplaylist": True,
        "ffmpeg_location": ffmpeg_path,
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(req.url, download=True)
            temp_path = ydl.prepare_filename(info)
            final_file = f"{output_dir}/{unique_id}.mp4"

            if os.path.exists(temp_path) and temp_path != final_file:
                try:
                    os.rename(temp_path, final_file)
                except:
                    shutil.copy(temp_path, final_file)
                    os.remove(temp_path)

            # Hapus file tambahan jika ada
            for ext in ["webm", "mkv", "f4v"]:
                extra = f"{output_dir}/{unique_id}.{ext}"
                if os.path.exists(extra):
                    os.remove(extra)

            return {
                "file_endpoint": f"/api/file/{unique_id}.mp4",
                "filename": f"{unique_id}.mp4"
            }

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ============================
# Endpoint Serve File
# ============================
@app.get("/api/file/{filename}")
async def get_file(filename: str):
    file_path = os.path.join("downloads", filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        file_path,
        media_type="video/mp4",
        filename=filename
    )

# ============================
# Endpoint Video Sizes
# ============================
@app.post("/api/video-sizes")
def get_video_sizes(req: VideoSizeRequest):
    """
    Mengembalikan ukuran video (MB) untuk kualitas 360p, 720p, 1080p, auto
    """
    try:
        ydl_opts = {
            "format": "bestvideo+bestaudio/best",
            "noplaylist": True,
            "quiet": True,
        }
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(req.url, download=False)
            formats = info.get("formats", [])

            sizes = {"360p": "–", "720p": "–", "1080p": "–", "auto": "–"}

            for f in formats:
                height = f.get("height")
                filesize = f.get("filesize") or f.get("filesize_approx")
                if not height or not filesize:
                    continue

                size_str = f"{round(filesize / 1024 / 1024, 1)} MB"

                if height <= 360:
                    sizes["360p"] = size_str
                elif height <= 720:
                    sizes["720p"] = size_str
                elif height <= 1080:
                    sizes["1080p"] = size_str

            # auto = ambil filesize terbesar dari info utama
            best_size = info.get("filesize") or info.get("filesize_approx")
            if best_size:
                sizes["auto"] = f"{round(best_size / 1024 / 1024, 1)} MB"

            return sizes

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ============================
# Endpoint Preview Video
# ============================
@app.post("/api/preview")
def get_preview(req: PreviewRequest):
    """
    Mengembalikan URL video sementara untuk preview tanpa download
    """
    try:
        ydl_opts = {
            "format": "bestvideo+bestaudio/best",
            "noplaylist": True,
            "quiet": True,
        }
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(req.url, download=False)
            video_url = info.get("url")
            if not video_url:
                raise HTTPException(status_code=404, detail="Preview URL tidak tersedia")

            return {"preview_url": video_url}

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
