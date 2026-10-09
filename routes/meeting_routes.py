"""
routes/meeting_routes.py
Flask Blueprint for the Meetings feature.
GET  /meetings            – render the meetings page
POST /api/meetings/summarize – summarize transcript or uploaded file
GET  /api/meetings/list   – list stored meeting summaries
POST /api/meetings/clear  – clear all meeting summaries
"""
import os
import uuid
import traceback
import threading
from datetime import datetime

from flask import (Blueprint, render_template, jsonify,
                   request, session, redirect, url_for)

from auth_helpers import creds_from_session, get_user_info
from utils import get_gemini_client
from services.meeting_service import (
    summarize_transcript, summarize_file, guess_mime,
    AUDIO_MIMES, VIDEO_MIMES,
)

meeting_bp = Blueprint("meeting", __name__)

# ─── In-Memory Meeting Store ──────────────────────────────────────────────────

_meeting_store: dict[str, list] = {}
_store_lock = threading.Lock()


# ─── Page ─────────────────────────────────────────────────────────────────────

@meeting_bp.route("/meetings")
def meetings_page():
    creds = creds_from_session()
    if not creds:
        return redirect(url_for("index"))
    try:
        user_info = get_user_info(creds)
    except Exception:
        user_info = {"email": "Unknown", "name": "User", "picture": ""}
    return render_template("meetings.html", user_info=user_info)


# ─── API ──────────────────────────────────────────────────────────────────────

@meeting_bp.route("/api/meetings/summarize", methods=["POST"])
def api_meetings_summarize():
    """Accept a transcript (form field) or file upload, return AI summary."""
    creds = creds_from_session()
    if not creds:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401

    try:
        client = get_gemini_client()

        title        = request.form.get("title", "").strip()
        participants = request.form.get("participants", "").strip()
        date         = request.form.get("date", "").strip()
        mode         = request.form.get("mode", "transcript")  # "transcript" | "file"

        if mode == "transcript":
            transcript = request.form.get("transcript", "").strip()
            if not transcript:
                return jsonify({"ok": False, "error": "Transcript is empty"}), 400
            summary = summarize_transcript(transcript, title, participants, date, client)
            source  = "transcript"
            filename = None

        else:  # file upload
            if "file" not in request.files:
                return jsonify({"ok": False, "error": "No file uploaded"}), 400
            f        = request.files["file"]
            filename = f.filename or "upload"
            raw      = f.read()
            if not raw:
                return jsonify({"ok": False, "error": "Uploaded file is empty"}), 400

            mime_type = guess_mime(filename, f.mimetype or "")

            # Plain-text files — treat as transcript
            if mime_type == "text/plain":
                transcript = raw.decode("utf-8", errors="replace")
                summary    = summarize_transcript(transcript, title, participants,
                                                  date, client)
                source = "text_file"
            elif mime_type in AUDIO_MIMES:
                summary = summarize_file(raw, filename, mime_type,
                                         title, participants, date, client)
                source  = "audio"
            elif mime_type in VIDEO_MIMES:
                summary = summarize_file(raw, filename, mime_type,
                                         title, participants, date, client)
                source  = "video"
            else:
                return jsonify({"ok": False,
                                "error": f"Unsupported file type: {mime_type}. "
                                         "Upload MP3, WAV, M4A, OGG, FLAC, MP4, MOV, AVI, WebM, or TXT."}), 400

        # Store in session
        sk = session.setdefault("session_key", os.urandom(8).hex())
        meeting_obj = {
            "id":           str(uuid.uuid4()),
            "title":        title or f"Meeting {datetime.now().strftime('%b %d, %H:%M')}",
            "participants": participants,
            "date":         date or datetime.now().strftime("%Y-%m-%d"),
            "source":       source,
            "filename":     filename,
            "summary":      summary,
            "created_at":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        with _store_lock:
            _meeting_store.setdefault(sk, []).insert(0, meeting_obj)

        return jsonify({"ok": True, "meeting": meeting_obj})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@meeting_bp.route("/api/meetings/list")
def api_meetings_list():
    sk = session.get("session_key", "")
    with _store_lock:
        data = _meeting_store.get(sk, [])
    return jsonify({"meetings": data, "count": len(data)})


@meeting_bp.route("/api/meetings/clear", methods=["POST"])
def api_meetings_clear():
    sk = session.get("session_key", "")
    with _store_lock:
        _meeting_store[sk] = []
    return jsonify({"ok": True})
