"""
app.py – MailMind Flask application entry point.
Handles OAuth flow and core page routes only.
All feature routes are registered via Blueprints.
"""
import os
import base64
import hashlib
import secrets
import traceback

from flask import Flask, render_template, redirect, url_for, session, request
from dotenv import load_dotenv

# ─── Auth & Helpers ───────────────────────────────────────────────────────────

from auth_helpers import (
    build_flow, creds_from_session, creds_to_dict,
    get_user_info, REDIRECT_URI,
)

# ─── App Setup ────────────────────────────────────────────────────────────────

load_dotenv()
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"]  = "1"

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key-change-me")
# Allow uploads up to 500 MB (for meeting video recordings)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024

# ─── Blueprint Registration ───────────────────────────────────────────────────

from routes.email_routes   import email_bp
from routes.meeting_routes import meeting_bp

app.register_blueprint(email_bp)
app.register_blueprint(meeting_bp)

# ─── Server-side PKCE Store ───────────────────────────────────────────────────

_pkce_verifier_store: dict[str, str] = {}


def _pkce_pair() -> tuple[str, str]:
    verifier  = secrets.token_urlsafe(64)
    digest    = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# ─── Core Page Routes ─────────────────────────────────────────────────────────

@app.route("/")
def index():
    creds     = creds_from_session()
    user_info = None
    if creds:
        try:
            user_info = get_user_info(creds)
        except Exception:
            pass
    return render_template("index.html", user_info=user_info)


@app.route("/authorize")
def authorize():
    from auth_helpers import _get_client_config, CLIENT_SECRETS_FILE
    config = _get_client_config()
    if not config and not os.path.exists(CLIENT_SECRETS_FILE):
        return render_template("setup.html")

    flow = build_flow()
    verifier, challenge = _pkce_pair()

    auth_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        code_challenge=challenge,
        code_challenge_method="S256",
    )

    _pkce_verifier_store[state] = verifier
    if len(_pkce_verifier_store) > 50:
        oldest = next(iter(_pkce_verifier_store))
        _pkce_verifier_store.pop(oldest, None)

    return redirect(auth_url)


@app.route("/oauth2callback")
def oauth2callback():
    state         = request.args.get("state", "")
    code_verifier = _pkce_verifier_store.pop(state, None)

    if not code_verifier:
        return redirect(url_for("authorize"))

    flow = build_flow()
    flow.fetch_token(
        authorization_response=request.url,
        code_verifier=code_verifier,
    )
    creds = flow.credentials

    granted = set(creds.scopes or [])
    required = {
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
    }
    if not required.issubset(granted):
        print(f"[WARN] Some Gmail scopes not granted. Got: {granted}")

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
    from routes.email_routes import _polling_active
    sk = session.get("session_key")
    if sk:
        _polling_active[sk] = False
    session.clear()
    return redirect(url_for("index"))


# ─── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=False)
