import shutil
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import yt_dlp

app = FastAPI()

class DownloadReq(BaseModel):
    url: str
    platform: str

@app.post("/api/download")
def download_video(req: DownloadReq):
    # cek ffmpeg
    ffmpeg_ok = shutil.which("ffmpeg") is not None

    if ffmpeg_ok:
        ydl_opts = {
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
        }
    else:
        ydl_opts = {
            "format": "mp4",              # fallback
            "merge_output_format": "mp4",
            "postprocessors": [],         # tidak merge
            "prefer_ffmpeg": False,
        }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(req.url, download=False)
            return {"download_url": info.get("url"),
                    "ffmpeg": ffmpeg_ok}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
