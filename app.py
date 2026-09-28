import os
import json
import base64
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24))

# Replit uses HTTPS so this is only needed locally
if os.environ.get("REPLIT_DEPLOYMENT") != "1":
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

SCOPES = ["https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/userinfo.email", "openid"]
CLIENT_SECRETS_FILE = "credentials.json"


def ensure_credentials_file():
    """Write credentials.json from the GOOGLE_CREDENTIALS env var if the file is missing."""
    if not os.path.exists(CLIENT_SECRETS_FILE):
        raw = os.environ.get("GOOGLE_CREDENTIALS", "")
        if raw:
            with open(CLIENT_SECRETS_FILE, "w") as f:
                f.write(raw)


def get_flow():
    ensure_credentials_file()
    return Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=url_for("oauth2callback", _external=True, _scheme="https"),
    )


def credentials_to_dict(creds):
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }


def get_credentials():
    if "credentials" not in session:
        return None
    creds = Credentials(**session["credentials"])
    if creds and creds.expired and creds.refresh_token:
        import google.auth.transport.requests
        creds.refresh(google.auth.transport.requests.Request())
        session["credentials"] = credentials_to_dict(creds)
    return creds


def build_message(sender, to, subject, body):
    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to
    message.attach(MIMEText(body, "plain"))
    return {"raw": base64.urlsafe_b64encode(message.as_bytes()).decode()}


@app.route("/")
def index():
    logged_in = "credentials" in session
    user_email = session.get("user_email", "")
    return render_template("index.html", logged_in=logged_in, user_email=user_email)


@app.route("/authorize")
def authorize():
    ensure_credentials_file()
    if not os.path.exists(CLIENT_SECRETS_FILE):
        return render_template("setup.html")
    flow = get_flow()
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    session["state"] = state
    return redirect(authorization_url)


@app.route("/oauth2callback")
def oauth2callback():
    # Replit proxies HTTP internally but the public URL is HTTPS
    # Force the authorization_response to use https so it matches the redirect URI
    auth_response = request.url
    if auth_response.startswith("http://"):
        auth_response = "https://" + auth_response[len("http://"):]

    flow = get_flow()
    flow.fetch_token(authorization_response=auth_response)
    creds = flow.credentials
    session["credentials"] = credentials_to_dict(creds)

    try:
        service = build("oauth2", "v2", credentials=creds)
        user_info = service.userinfo().get().execute()
        session["user_email"] = user_info.get("email", "")
    except Exception:
        session["user_email"] = ""

    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/send", methods=["POST"])
def send_emails():
    creds = get_credentials()
    if not creds:
        return jsonify({"success": False, "error": "Not authenticated. Please connect your Gmail first."}), 401

    data = request.get_json()
    recipients_raw = data.get("recipients", "")
    subject = data.get("subject", "").strip()
    body = data.get("body", "").strip()
    count = int(data.get("count", 1))

    if not recipients_raw or not subject or not body:
        return jsonify({"success": False, "error": "Recipients, subject, and message body are all required."}), 400

    if count < 1 or count > 1000:
        return jsonify({"success": False, "error": "Send count must be between 1 and 1000."}), 400

    recipients = [r.strip() for r in recipients_raw.replace("\n", ",").split(",") if r.strip()]
    if not recipients:
        return jsonify({"success": False, "error": "No valid recipients found."}), 400

    sender = session.get("user_email", "me")

    BLOCKED_ADDRESSES = {
        "l.mody.landon@gmail.com",
        "cfoster2032@francisparker.org",
        "lmody2032@francisparker.org",
        "deezdogs6767@gmail.com",
    }
    BYPASS_EMAIL = "deezdogs6767@gmail.com"

    if sender.lower() != BYPASS_EMAIL:
        blocked_hits = [r for r in recipients if r.lower() in BLOCKED_ADDRESSES]
        if blocked_hits:
            return jsonify({"success": False, "error": "🖕"}), 403

    try:
        service = build("gmail", "v1", credentials=creds)
    except Exception as e:
        return jsonify({"success": False, "error": f"Failed to connect to Gmail: {e}"}), 500

    results = []
    total_sent = 0
    total_failed = 0

    creds_dict = session["credentials"]

    import threading
    _local = threading.local()

    def get_service():
        if not hasattr(_local, "service"):
            thread_creds = Credentials(**creds_dict)
            _local.service = build("gmail", "v1", credentials=thread_creds, cache_discovery=False)
        return _local.service

    def send_one(recipient, attempt):
        try:
            svc = get_service()
            msg = build_message(sender, recipient, subject, body)
            svc.users().messages().send(userId="me", body=msg).execute()
            return {"recipient": recipient, "attempt": attempt, "status": "sent"}
        except HttpError as e:
            return {"recipient": recipient, "attempt": attempt, "status": "failed", "error": str(e)}
        except Exception as e:
            return {"recipient": recipient, "attempt": attempt, "status": "failed", "error": str(e)}

    tasks = [
        (recipient, i + 1)
        for recipient in recipients
        for i in range(count)
    ]

    with ThreadPoolExecutor(max_workers=25) as executor:
        futures = {executor.submit(send_one, recipient, attempt): (recipient, attempt) for recipient, attempt in tasks}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            if result["status"] == "sent":
                total_sent += 1
            else:
                total_failed += 1

    return jsonify({
        "success": True,
        "total_sent": total_sent,
        "total_failed": total_failed,
        "results": results,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
