import os
import json
import hashlib
import secrets
import threading
import time
from datetime import datetime, timezone
from flask import (Flask, render_template, redirect, url_for,
                   session, request, jsonify)
from dotenv import load_dotenv

from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from utils import get_gemini_client, parse_gmail_message, summarize_email

# ─── App Setup ─────────────────────────────────────────────────────────────────

load_dotenv()
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"   # Allow HTTP for local dev
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"    # Allow scope changes (Gmail API propagation delay)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key-change-me")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

CLIENT_SECRETS_FILE = "credentials.json"

# In-memory store for summaries (per session key → list of summary dicts)
_summaries_store: dict[str, list] = {}
_polling_threads: dict[str, threading.Thread] = {}
_polling_active: dict[str, bool] = {}
_store_lock = threading.Lock()


# ─── OAuth Helpers ─────────────────────────────────────────────────────────────

def _get_client_config() -> dict | None:
    """Build OAuth client config from env vars OR credentials.json."""
    client_id     = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()

    if client_id and client_secret:
        # Env-var method — no JSON file needed
        return {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost:5000/oauth2callback"],
            }
        }
    return None   # fall back to credentials.json


def build_flow():
    config = _get_client_config()
    redirect = url_for("oauth2callback", _external=True)
    if config:
        return Flow.from_client_config(config, scopes=SCOPES, redirect_uri=redirect)
    # Fall back to credentials.json file
    return Flow.from_client_secrets_file(CLIENT_SECRETS_FILE, scopes=SCOPES, redirect_uri=redirect)


# ─── PKCE Helpers ──────────────────────────────────────────────────────────────

def _pkce_pair() -> tuple[str, str]:
    """Generate a (code_verifier, code_challenge) pair for PKCE."""
    verifier = secrets.token_urlsafe(96)          # 128-char URL-safe string
    digest   = hashlib.sha256(verifier.encode()).digest()
    import base64
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def creds_from_session() -> Credentials | None:
    token_data = session.get("credentials")
    if not token_data:
        return None
    creds = Credentials(**token_data)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        session["credentials"] = creds_to_dict(creds)
    return creds


def creds_to_dict(creds: Credentials) -> dict:
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }


def get_gmail_service(creds):
    return build("gmail", "v1", credentials=creds)


def get_user_info(creds):
    svc = build("oauth2", "v2", credentials=creds)
    return svc.userinfo().get().execute()


# ─── Email Fetching ────────────────────────────────────────────────────────────

def fetch_and_summarize(creds_dict: dict, session_key: str,
                        query: str = "is:unread", max_results: int = 10):
    """Fetch emails, summarize them, store results. Thread-safe."""
    try:
        creds = Credentials(**creds_dict)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())

        gmail = get_gmail_service(creds)
        client = get_gemini_client()

        result = gmail.users().messages().list(
            userId="me", q=query, maxResults=max_results
        ).execute()

        messages = result.get("messages", [])
        summaries = []

        for i, msg_ref in enumerate(messages):
            msg = gmail.users().messages().get(
                userId="me", id=msg_ref["id"], format="full"
            ).execute()

            email_data = parse_gmail_message(msg, client)
            summary_text = summarize_email(email_data, client)

            summaries.append({
                "id": email_data["id"],
                "subject": email_data["subject"],
                "sender": email_data["sender"],
                "date": email_data["date"],
                "summary": summary_text,
                "attachments": [a["filename"] for a in email_data["attachments"]],
                "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "status": "success",
            })

            # Avoid burst rate-limiting: pause 1s between Gemini calls
            if i < len(messages) - 1:
                time.sleep(1)

        with _store_lock:
            # Merge: keep old entries not in new batch, prepend new ones
            existing = _summaries_store.get(session_key, [])
            existing_ids = {s["id"] for s in summaries}
            kept_old = [s for s in existing if s["id"] not in existing_ids]
            _summaries_store[session_key] = summaries + kept_old

        return {"ok": True, "count": len(summaries)}

    except Exception as e:
        return {"ok": False, "error": str(e)}


def start_polling(creds_dict: dict, session_key: str, interval_minutes: int = 5):
    """Start background polling thread."""
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

@app.route("/")
def index():
    creds = creds_from_session()
    user_info = None
    if creds:
        try:
            user_info = get_user_info(creds)
        except Exception:
            pass
    return render_template("index.html", user_info=user_info)


@app.route("/authorize")
def authorize():
    # Allow access if env vars are set OR credentials.json exists
    config = _get_client_config()
    if not config and not os.path.exists(CLIENT_SECRETS_FILE):
        return render_template("setup.html")
    flow = build_flow()

    # PKCE: generate verifier + challenge (required by new Google OAuth clients)
    verifier, challenge = _pkce_pair()
    session["code_verifier"] = verifier

    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        code_challenge=challenge,
        code_challenge_method="S256",
    )
    session["state"] = state
    return redirect(auth_url)


@app.route("/oauth2callback")
def oauth2callback():
    flow = build_flow()
    # Pass code_verifier so Google can verify the PKCE challenge
    code_verifier = session.pop("code_verifier", None)
    flow.fetch_token(
        authorization_response=request.url,
        code_verifier=code_verifier,
    )
    creds = flow.credentials
    session["credentials"] = creds_to_dict(creds)
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
def dashboard():
    creds = creds_from_session()
    if not creds:
        return redirect(url_for("index"))
    try:
        user_info = get_user_info(creds)
    except Exception:
        user_info = {"email": "Unknown", "name": "User", "picture": ""}
    return render_template("dashboard.html", user_info=user_info)


@app.route("/logout")
def logout():
    sk = session.get("session_key")
    if sk:
        _polling_active[sk] = False
    session.clear()
    return redirect(url_for("index"))


# ─── API Endpoints ─────────────────────────────────────────────────────────────

@app.route("/api/fetch", methods=["POST"])
def api_fetch():
    creds = creds_from_session()
    if not creds:
        return jsonify({"error": "Not authenticated"}), 401

    data = request.get_json() or {}
    query = data.get("query", "is:unread")
    max_results = min(int(data.get("max_results", 10)), 25)

    # Create a stable session key
    sk = session.setdefault("session_key", os.urandom(8).hex())

    result = fetch_and_summarize(creds_to_dict(creds), sk, query, max_results)
    return jsonify(result)


@app.route("/api/summaries")
def api_summaries():
    sk = session.get("session_key", "")
    with _store_lock:
        data = _summaries_store.get(sk, [])
    return jsonify({"summaries": data, "count": len(data)})


@app.route("/api/poll/start", methods=["POST"])
def api_poll_start():
    creds = creds_from_session()
    if not creds:
        return jsonify({"error": "Not authenticated"}), 401

    data = request.get_json() or {}
    interval = max(1, int(data.get("interval_minutes", 5)))

    sk = session.setdefault("session_key", os.urandom(8).hex())

    # Stop existing poller
    _polling_active[sk] = False
    time.sleep(0.1)

    start_polling(creds_to_dict(creds), sk, interval)
    return jsonify({"ok": True, "interval_minutes": interval})


@app.route("/api/poll/stop", methods=["POST"])
def api_poll_stop():
    sk = session.get("session_key", "")
    _polling_active[sk] = False
    return jsonify({"ok": True})


@app.route("/api/poll/status")
def api_poll_status():
    sk = session.get("session_key", "")
    active = _polling_active.get(sk, False)
    return jsonify({"polling": active})


@app.route("/api/clear", methods=["POST"])
def api_clear():
    sk = session.get("session_key", "")
    with _store_lock:
        _summaries_store[sk] = []
    return jsonify({"ok": True})


@app.route("/api/config/gemini", methods=["POST"])
def api_config_gemini():
    """Save Gemini API key to .env at runtime."""
    data = request.get_json() or {}
    key = data.get("api_key", "").strip()
    if not key:
        return jsonify({"error": "No key provided"}), 400
    os.environ["GEMINI_API_KEY"] = key
    # Persist to .env
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    lines = []
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


# ─── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=False)
