"""
services/meeting_service.py
Gemini-powered meeting summarization.
Supports: plain-text transcripts, audio files, video files.
"""
import io
import os
import time

from google import genai
from google.genai import types as genai_types

MODEL_ID = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# Audio / video MIME types supported by Gemini Files API
AUDIO_MIMES = {"audio/mp3", "audio/mpeg", "audio/wav", "audio/x-wav",
               "audio/ogg", "audio/flac", "audio/x-flac", "audio/m4a",
               "audio/x-m4a", "audio/aac", "audio/webm"}
VIDEO_MIMES = {"video/mp4", "video/quicktime", "video/x-msvideo",
               "video/webm", "video/mpeg", "video/3gpp"}


def _meeting_prompt(title: str = "", participants: str = "", date: str = "") -> str:
    meta_lines = []
    if title:        meta_lines.append(f"**Meeting Title:** {title}")
    if date:         meta_lines.append(f"**Date:** {date}")
    if participants: meta_lines.append(f"**Participants:** {participants}")
    meta = ("\n".join(meta_lines) + "\n\n") if meta_lines else ""

    return (
        f"You are an expert meeting analyst and note-taker.\n{meta}"
        "Analyze this meeting and produce a structured summary using the sections below.\n\n"
        "## 📋 Meeting Overview\n"
        "2–3 sentences on the meeting purpose and overall outcome.\n\n"
        "## 🗣️ Key Discussion Points\n"
        "Bullet list of main topics discussed.\n\n"
        "## ✅ Decisions Made\n"
        "Specific decisions/conclusions, or 'No formal decisions recorded.'\n\n"
        "## 📌 Action Items\n"
        "Format each as: **[Person]** — action (by *date* if mentioned). "
        "Or 'No action items identified.'\n\n"
        "## 💡 Key Insights & Important Statements\n"
        "Notable quotes or insights.\n\n"
        "## 🔄 Follow-up & Next Steps\n"
        "Recommended next steps.\n\n"
        "Use clear markdown. Be concise yet comprehensive."
    )


def _retry_generate(client, contents, max_retries: int = 3) -> str:
    wait = 2
    for attempt in range(max_retries):
        try:
            resp = client.models.generate_content(model=MODEL_ID, contents=contents)
            return resp.text
        except Exception as e:
            err = str(e)
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                return (
                    "[Quota exceeded] You've hit the Gemini API daily free-tier limit. "
                    "Wait a few hours or upgrade at https://ai.google.dev/gemini-api/docs/rate-limits"
                )
            if attempt < max_retries - 1 and any(x in err for x in ["503", "UNAVAILABLE"]):
                time.sleep(wait)
                wait *= 2
                continue
            return f"[Summarization failed: {e}]"


def summarize_transcript(transcript: str, title: str, participants: str,
                          date: str, client) -> str:
    """Summarize a plain-text meeting transcript."""
    prompt = _meeting_prompt(title, participants, date)
    full = f"{prompt}\n\n**Meeting Transcript:**\n{transcript[:20000]}"
    return _retry_generate(client, full)


def summarize_file(file_bytes: bytes, filename: str, mime_type: str,
                   title: str, participants: str, date: str, client) -> str:
    """Upload an audio/video file to Gemini Files API and summarize."""
    prompt = _meeting_prompt(title, participants, date)
    uploaded = None
    try:
        uploaded = client.files.upload(
            file=io.BytesIO(file_bytes),
            config=genai_types.UploadFileConfig(
                mime_type=mime_type,
                display_name=filename,
            ),
        )
        result = _retry_generate(client, [uploaded, prompt])
    except Exception as e:
        result = f"[File upload/summarization failed: {e}]"
    finally:
        if uploaded:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass
    return result


def guess_mime(filename: str, provided_mime: str = "") -> str:
    """Return best MIME type guess for a file."""
    if provided_mime and provided_mime != "application/octet-stream":
        return provided_mime
    ext = filename.rsplit(".", 1)[-1].lower()
    return {
        "mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg",
        "flac": "audio/flac", "m4a": "audio/m4a", "aac": "audio/aac",
        "mp4": "video/mp4", "mov": "video/quicktime", "avi": "video/x-msvideo",
        "webm": "video/webm", "mkv": "video/mpeg",
        "txt": "text/plain",
    }.get(ext, "application/octet-stream")
