# Bulk Email Center

Send bulk emails through your own Gmail account via a clean web UI.

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env          # edit FLASK_SECRET_KEY
python app.py
```

Open **http://localhost:5000** in your browser.

## Google Setup (one-time)

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and create/select a project.
2. Enable the **Gmail API** under *APIs & Services → Library*.
3. Create an **OAuth 2.0 Client ID** under *APIs & Services → Credentials*:
   - Type: **Web application**
   - Redirect URI: `http://localhost:5000/oauth2callback`
4. Download the JSON, rename it `credentials.json`, and place it in this folder.
5. Restart the app and click **Sign in with Google**.

## Features

- Sign in with your own Google account (OAuth2 — no password stored)
- Compose subject + message body
- Enter one or many recipient addresses (comma or newline separated)
- Drag a slider to set how many times to send per recipient (1–100)
- Live send log with per-email sent/failed status
