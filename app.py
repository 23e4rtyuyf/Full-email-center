import os
import json
import base64
import time
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

os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"  # dev only; remove in production with HTTPS

SCOPES = ["https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/userinfo.email", "openid"]
CLIENT_SECRETS_FILE = "credentials.json"


def get_flow():
    return Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=url_for("oauth2callback", _external=True),
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
    flow = get_flow()
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    session["credentials"] = credentials_to_dict(creds)

    # Fetch user email
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

    if count < 1 or count > 100:
        return jsonify({"success": False, "error": "Send count must be between 1 and 100."}), 400

    recipients = [r.strip() for r in recipients_raw.replace("\n", ",").split(",") if r.strip()]
    if not recipients:
        return jsonify({"success": False, "error": "No valid recipients found."}), 400

    sender = session.get("user_email", "me")

    try:
        service = build("gmail", "v1", credentials=creds)
    except Exception as e:
        return jsonify({"success": False, "error": f"Failed to connect to Gmail: {e}"}), 500

    results = []
    total_sent = 0
    total_failed = 0

    for recipient in recipients:
        for i in range(count):
            try:
                msg = build_message(sender, recipient, subject, body)
                service.users().messages().send(userId="me", body=msg).execute()
                total_sent += 1
                results.append({"recipient": recipient, "attempt": i + 1, "status": "sent"})
                # Brief pause to avoid Gmail rate limits
                if (total_sent) % 10 == 0:
                    time.sleep(1)
            except HttpError as e:
                total_failed += 1
                results.append({"recipient": recipient, "attempt": i + 1, "status": "failed", "error": str(e)})
            except Exception as e:
                total_failed += 1
                results.append({"recipient": recipient, "attempt": i + 1, "status": "failed", "error": str(e)})

    return jsonify({
        "success": True,
        "total_sent": total_sent,
        "total_failed": total_failed,
        "results": results,
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000)
