"""
routes/email_routes.py
Flask Blueprint for all email-related API endpoints.
Moved from app.py to keep the main module lean.
"""
import os
import time
import threading
import traceback

from flask import Blueprint, jsonify, request, session
from auth_helpers import creds_from_session, creds_to_dict, get_gmail_service
from utils import get_gemini_client, parse_gmail_message, summarize_email, draft_reply

email_bp = Blueprint("email", __name__)

# ─── In-Memory Email Store ─────────────────────────────────────────────────────

_summaries_store: dict[str, list] = {}
_polling_threads: dict[str, threading.Thread] = {}
_polling_active:  dict[str, bool] = {}
_store_lock = threading.Lock()


# ─── Service: Fetch & Summarize ───────────────────────────────────────────────

def fetch_and_summarize(creds_dict: dict, session_key: str,
                        query: str = "is:unread", max_results: int = 10):
    """Fetch emails, summarize with Gemini, merge into store. Thread-safe."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        creds = Credentials(**creds_dict)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())

        gmail  = get_gmail_service(creds)
        client = get_gemini_client()

        result   = gmail.users().messages().list(
            userId="me", q=query, maxResults=max_results
        ).execute()
        messages = result.get("messages", [])
        summaries = []

        for i, msg_ref in enumerate(messages):
            msg = gmail.users().messages().get(
                userId="me", id=msg_ref["id"], format="full"
            ).execute()
            email_data   = parse_gmail_message(msg, client)
            summary_text = summarize_email(email_data, client)

            summaries.append({
                "id":          email_data["id"],
                "subject":     email_data["subject"],
                "sender":      email_data["sender"],
                "date":        email_data["date"],
                "summary":     summary_text,
                "attachments": [a["filename"] for a in email_data["attachments"]],
                "fetched_at":  time.strftime("%Y-%m-%d %H:%M:%S"),
                "status":      "success",
            })

            if i < len(messages) - 1:
                time.sleep(1)   # avoid burst rate-limiting

        with _store_lock:
            existing     = _summaries_store.get(session_key, [])
            existing_ids = {s["id"] for s in summaries}
            kept_old     = [s for s in existing if s["id"] not in existing_ids]
            _summaries_store[session_key] = summaries + kept_old

        return {"ok": True, "count": len(summaries)}

    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": str(e)}


def start_polling(creds_dict: dict, session_key: str, interval_minutes: int = 5):
    _polling_active[session_key] = True

    def poll():
        while _polling_active.get(session_key, False):
            fetch_and_summarize(creds_dict, session_key)
            for _ in range(interval_minutes * 60):
                if not _polling_active.get(session_key, False):
                    break
                time.sleep(1)

    t = threading.Thread(target=poll, daemon=True)
    _polling_threads[session_key] = t
    t.start()


# ─── Routes ───────────────────────────────────────────────────────────────────

@email_bp.route("/api/fetch", methods=["POST"])
def api_fetch():
    creds = creds_from_session()
    if not creds:
        return jsonify({"error": "Not authenticated"}), 401

    data        = request.get_json() or {}
    query       = data.get("query", "is:unread")
    max_results = min(int(data.get("max_results", 10)), 25)
    sk          = session.setdefault("session_key", os.urandom(8).hex())

    result = fetch_and_summarize(creds_to_dict(creds), sk, query, max_results)
    return jsonify(result)


@email_bp.route("/api/summaries")
def api_summaries():
    sk = session.get("session_key", "")
    with _store_lock:
        data = _summaries_store.get(sk, [])
    return jsonify({"summaries": data, "count": len(data)})


@email_bp.route("/api/poll/start", methods=["POST"])
def api_poll_start():
    creds = creds_from_session()
    if not creds:
        return jsonify({"error": "Not authenticated"}), 401

    data     = request.get_json() or {}
    interval = max(1, int(data.get("interval_minutes", 5)))
    sk       = session.setdefault("session_key", os.urandom(8).hex())

    _polling_active[sk] = False
    time.sleep(0.1)
    start_polling(creds_to_dict(creds), sk, interval)
    return jsonify({"ok": True, "interval_minutes": interval})


@email_bp.route("/api/poll/stop", methods=["POST"])
def api_poll_stop():
    sk = session.get("session_key", "")
    _polling_active[sk] = False
    return jsonify({"ok": True})


@email_bp.route("/api/poll/status")
def api_poll_status():
    sk     = session.get("session_key", "")
    active = _polling_active.get(sk, False)
    return jsonify({"polling": active})


@email_bp.route("/api/clear", methods=["POST"])
def api_clear():
    sk = session.get("session_key", "")
    with _store_lock:
        _summaries_store[sk] = []
    return jsonify({"ok": True})


@email_bp.route("/api/config/gemini", methods=["POST"])
def api_config_gemini():
    data = request.get_json() or {}
    key  = data.get("api_key", "").strip()
    if not key:
        return jsonify({"error": "No key provided"}), 400

    os.environ["GEMINI_API_KEY"] = key
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    lines    = []
    replaced = False
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith("GEMINI_API_KEY="):
                    lines.append(f"GEMINI_API_KEY={key}\n")
                    replaced = True
                else:
                    lines.append(line)
    if not replaced:
        lines.append(f"GEMINI_API_KEY={key}\n")
    with open(env_path, "w") as f:
        f.writelines(lines)
    return jsonify({"ok": True})


@email_bp.route("/api/draft", methods=["POST"])
def api_draft():
    creds = creds_from_session()
    if not creds:
        return jsonify({"error": "Not authenticated"}), 401

    data       = request.get_json() or {}
    subject    = data.get("subject", "")
    sender     = data.get("sender", "")
    summary    = data.get("summary", "")
    body_hint  = data.get("body_hint", "")

    client     = get_gemini_client()
    draft_text = draft_reply(subject, sender, summary, body_hint, client)

    import re, base64
    from email.mime.text import MIMEText

    m        = re.search(r"<(.+?)>", sender)
    to_addr  = m.group(1) if m else sender
    draft_id = None
    gmail_url = None
    try:
        gmail   = get_gmail_service(creds)
        msg     = MIMEText(draft_text)
        msg["to"]      = to_addr
        msg["subject"] = f"Re: {subject}"
        raw   = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        draft = gmail.users().drafts().create(
            userId="me", body={"message": {"raw": raw}}
        ).execute()
        draft_id  = draft["id"]
        gmail_url = f"https://mail.google.com/mail/#drafts/{draft_id}"
    except Exception:
        pass

    return jsonify({"ok": True, "draft_text": draft_text,
                    "draft_id": draft_id, "gmail_url": gmail_url})


@email_bp.route("/api/send", methods=["POST"])
def api_send():
    """Send an email via Gmail API. Always returns JSON."""
    try:
        creds = creds_from_session()
        if not creds:
            return jsonify({"ok": False,
                            "error": "Not authenticated — please log in again"}), 401

        data    = request.get_json() or {}
        to      = data.get("to", "").strip()
        cc      = data.get("cc", "").strip()
        bcc     = data.get("bcc", "").strip()
        subject = data.get("subject", "").strip()
        body    = data.get("body", "").strip()

        if not to:
            return jsonify({"ok": False, "error": "Recipient (To) is required"}), 400

        import base64
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart()
        msg["To"]      = to
        msg["Subject"] = subject
        if cc:  msg["Cc"]  = cc
        if bcc: msg["Bcc"] = bcc
        msg.attach(MIMEText(body, "plain", "utf-8"))

        raw    = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        gmail  = get_gmail_service(creds)
        result = gmail.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()
        return jsonify({"ok": True, "message_id": result.get("id", "")})

    except Exception as e:
        traceback.print_exc()
        err = str(e)
        if "insufficientPermissions" in err or "Insufficient Permission" in err:
            return jsonify({"ok": False,
                            "error": "Send permission denied — log out, revoke app at "
                                     "myaccount.google.com/permissions, then log back in"}), 403
        return jsonify({"ok": False, "error": err}), 500
