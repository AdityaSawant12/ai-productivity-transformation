# MailMind – AI Gmail Summarizer 📧🤖

A beautiful web dashboard that connects to your Gmail, reads new emails (including attachments), and generates intelligent summaries using **Google Gemini AI**.

---

## ✨ Features

| Feature | Details |
|---------|---------|
| 🔐 Gmail OAuth2 | Secure login — your password is never stored |
| 🤖 Gemini AI Summaries | Overview, key points, action items per email |
| 📎 Attachment Support | PDFs, Word docs, Excel sheets, images, text files |
| 🔄 Auto Polling | Checks inbox every N minutes automatically |
| 🔍 Search & Filter | Search summaries, sort by date/subject |
| 🌑 Dark Mode UI | Premium glassmorphism dashboard |

---

## 🚀 Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Google Cloud Setup (One-time)

1. Go to [console.cloud.google.com](https://console.cloud.google.com/)
2. Create a project → Enable **Gmail API** + **Google OAuth2 API**
3. Go to **Credentials → Create OAuth 2.0 Client ID**
4. Choose **Web Application**
5. Add authorized redirect URI: `http://localhost:5000/oauth2callback`
6. Download the JSON → save as **`credentials.json`** in this folder

### 3. Configure Environment

Edit `.env` and add your keys:

```env
GEMINI_API_KEY=your_gemini_api_key_here
FLASK_SECRET_KEY=any_random_secret_string
```

Get your Gemini API key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey)

### 4. Run the App

```bash
python app.py
```

Open → **http://localhost:5000**

---

## 📁 Project Structure

```
Assignment/
├── app.py              # Flask server + API routes
├── utils.py            # Gmail parsing, attachment extraction, Gemini summaries
├── requirements.txt    # Python dependencies
├── .env                # API keys (not committed)
├── credentials.json    # Google OAuth credentials (not committed)
└── templates/
    ├── index.html      # Landing page
    ├── dashboard.html  # Main dashboard
    └── setup.html      # Setup instructions page
```

---

## 🎯 How It Works

```
Gmail API ──► Fetch emails ──► Parse body + attachments
                                        │
                    ┌───────────────────┼────────────────────┐
                    │                   │                    │
                 PDF/DOCX            XLSX              Images
                 (text extract)   (sheet data)    (Gemini vision)
                    │                   │                    │
                    └───────────────────┴────────────────────┘
                                        │
                               Gemini 1.5 Flash
                                        │
                                 Summary Output:
                                 • Overview
                                 • Key Points
                                 • Action Items
                                 • Attachment Summary
```

---

## 🔧 API Reference (Internal)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/fetch` | POST | Fetch & summarize emails |
| `/api/summaries` | GET | Get all cached summaries |
| `/api/poll/start` | POST | Start auto-polling |
| `/api/poll/stop` | POST | Stop auto-polling |
| `/api/poll/status` | GET | Check polling state |
| `/api/clear` | POST | Clear all summaries |
| `/api/config/gemini` | POST | Save Gemini API key |

---

## 📝 Gmail Query Examples

| Query | Description |
|-------|-------------|
| `is:unread` | All unread emails (default) |
| `is:unread newer_than:1d` | Unread from last 24h |
| `from:boss@company.com` | Emails from specific sender |
| `has:attachment is:unread` | Unread with attachments |
| `subject:invoice` | Emails with "invoice" in subject |
