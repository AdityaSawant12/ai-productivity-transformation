"""
auth_helpers.py - shared Google OAuth credential utilities.
Imported by app.py and all route blueprints.
"""
import os
from flask import session
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

REDIRECT_URI        = "http://localhost:5000/oauth2callback"
CLIENT_SECRETS_FILE = "credentials.json"


def _get_client_config():
    client_id     = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    if client_id and client_secret:
        return {
            "web": {
                "client_id":     client_id,
                "client_secret": client_secret,
                "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
                "token_uri":     "https://oauth2.googleapis.com/token",
                "redirect_uris": [
                    "http://localhost:5000/oauth2callback",
                    "http://127.0.0.1:5000/oauth2callback",
                ],
            }
        }
    return None


def build_flow():
    config = _get_client_config()
    if config:
        return Flow.from_client_config(config, scopes=SCOPES, redirect_uri=REDIRECT_URI)
    return Flow.from_client_secrets_file(CLIENT_SECRETS_FILE, scopes=SCOPES,
                                         redirect_uri=REDIRECT_URI)


def creds_from_session():
    token_data = session.get("credentials")
    if not token_data:
        return None
    try:
        creds = Credentials(**token_data)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            session["credentials"] = creds_to_dict(creds)
    except Exception as e:
        print(f"[WARN] Token refresh failed: {e}")
        session.pop("credentials", None)
        return None
    return creds


def creds_to_dict(creds):
    return {
        "token":         creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri":     creds.token_uri,
        "client_id":     creds.client_id,
        "client_secret": creds.client_secret,
        "scopes":        creds.scopes,
    }


def get_gmail_service(creds):
    return build("gmail", "v1", credentials=creds)


def get_user_info(creds):
    svc = build("oauth2", "v2", credentials=creds)
    return svc.userinfo().get().execute()
