"""Audio e video: metadati con ffprobe; per i video un fotogramma descritto dal modello visivo."""

from __future__ import annotations

from pathlib import Path

from .. import shell
from .base import ExtractContext, Extraction, Extractor

def frame_prompt() -> str:
    from ..i18n import language_name

    return (f"This is a frame from a video. Describe the scene in {language_name()} in 1-2 sentences: "
            "place, subjects, activity. Do not identify people by name.")


class MediaExtractor(Extractor):
    name = "media"
    priority = 50
    extensions = frozenset({".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".aiff", ".opus",
                            ".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".wmv", ".mpg", ".mpeg"})

    async def available(self, ctx: ExtractContext) -> bool:
        return shell.which("ffprobe") is not None

    async def extract(self, path: Path, ctx: ExtractContext) -> Extraction | None:
        probe = await shell.ffprobe(path)
        if not probe:
            return None
        fmt = probe.get("format", {})
        tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
        meta: dict = {}
        try:
            meta["duration_s"] = round(float(fmt.get("duration", 0)), 1)
        except ValueError:
            pass
        video = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"
                      and s.get("disposition", {}).get("attached_pic") != 1), None)
        if video:
            meta["resolution"] = f"{video.get('width')}x{video.get('height')}"
            meta["video_codec"] = video.get("codec_name")
        for k in ("title", "artist", "album", "album_artist", "genre", "date", "comment",
                  "creation_time", "location", "com.apple.quicktime.location.iso6709",
                  "com.apple.quicktime.model"):
            if tags.get(k):
                meta[k.split(".")[-1]] = tags[k]
        text = "\n".join(f"{k}: {v}" for k, v in meta.items())
        description = ""
        engine = self.name
        if video and shell.which("ffmpeg") and await ctx.vision_ok():
            frame = ctx.tmpdir / f"frame-{abs(hash(str(path)))}.jpg"
            at = max(0.0, float(meta.get("duration_s", 0)) / 3)
            await shell.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{at:.1f}", "-i", str(path),
                             "-frames:v", "1", "-vf", "scale=1024:-2", str(frame)], timeout=60)
            if frame.exists():
                try:
                    description = await ctx.ai.describe_image(frame, frame_prompt())  # type: ignore[union-attr]
                    engine += "+vision"
                except Exception:
                    pass
                frame.unlink(missing_ok=True)
        return Extraction(text=text, description=description, metadata=meta, engine=engine)
