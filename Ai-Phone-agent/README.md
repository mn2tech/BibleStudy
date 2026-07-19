# NM2TECH AI Phone Agent

Phase 1 MVP — an AI receptionist that answers incoming Twilio voice calls, converses with callers using OpenAI, and saves call transcripts and summaries.

## Features

- Answers incoming calls with a branded AI greeting
- Multi-turn voice conversation via Twilio `<Gather>` speech recognition
- OpenAI-powered responses using NM2TECH business knowledge
- Transfer to a human when caller says "human", "representative", "agent", or "transfer"
- Leave-a-message flow (collects name, phone, message)
- Demo scheduling flow (collects name, business, phone, preferred time, email)
- Post-call AI summary
- SQLite locally, Postgres/Supabase in production
- Deploy-ready for Railway and Render

## Project Structure

```
app/
  main.py          # FastAPI app, health & calls API
  config.py        # Environment settings
  ai_agent.py      # OpenAI logic, intent detection
  twilio_routes.py # Twilio webhooks & TwiML
  database.py      # SQLAlchemy setup & helpers
  models.py        # ORM + Pydantic schemas
requirements.txt
.env.example
Procfile
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/twilio/voice` | Incoming call webhook (TwiML greeting) |
| POST | `/twilio/gather` | Speech result webhook (AI reply) |
| POST | `/twilio/status` | Call status callback (generates summary) |
| GET | `/calls` | List recent calls |
| GET | `/calls/{call_sid}` | Get call details + transcript |

## Quick Start (Local)

### 1. Clone and install

```bash
cd Ai-Phone-agent
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your keys:

- `OPENAI_API_KEY` — from [OpenAI Platform](https://platform.openai.com/api-keys)
- `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` — from [Twilio Console](https://console.twilio.com)
- `TWILIO_PHONE_NUMBER` — your Twilio phone number
- `TRANSFER_PHONE_NUMBER` — number to dial when caller asks for a human
- `BASE_URL` — your public URL (ngrok URL during local dev)

### 3. Run the server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Visit http://localhost:8000/health to confirm it is running.

## Local Testing with ngrok

Twilio needs a public HTTPS URL to send webhooks. Use [ngrok](https://ngrok.com/) to tunnel your local server.

### 1. Start the app

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 2. Start ngrok

```bash
ngrok http 8000
```

Copy the HTTPS URL (e.g. `https://abc123.ngrok-free.app`).

### 3. Update BASE_URL

Set in `.env`:

```
BASE_URL=https://abc123.ngrok-free.app
```

Restart the app after changing `.env`.

### 4. Configure Twilio webhooks

In the [Twilio Console](https://console.twilio.com) → **Phone Numbers** → select your number:

| Setting | URL |
|---------|-----|
| **A call comes in** | `https://abc123.ngrok-free.app/twilio/voice` (HTTP POST) |
| **Call status changes** | `https://abc123.ngrok-free.app/twilio/status` (HTTP POST) |

Save the number configuration.

### 5. Call your Twilio number

Dial your Twilio phone number from any phone. You should hear:

> "Hello, thank you for calling NM2TECH AI Phone Agent demo..."

Try asking about pricing, say "leave a message", or say "transfer" to test those flows.

## Twilio Webhook Configuration (Production)

When deployed to Railway or Render, set `BASE_URL` to your production domain and configure the same webhooks:

- **Voice URL:** `https://your-app.railway.app/twilio/voice`
- **Status callback:** `https://your-app.railway.app/twilio/status`

## Example curl Commands

### Health check

```bash
curl http://localhost:8000/health
```

### Simulate incoming call (TwiML greeting)

```bash
curl -X POST http://localhost:8000/twilio/voice \
  -d "CallSid=CAtest123" \
  -d "From=+15551234567" \
  -d "To=+15559876543"
```

### Simulate speech input

```bash
curl -X POST http://localhost:8000/twilio/gather \
  -d "CallSid=CAtest123" \
  -d "From=+15551234567" \
  -d "SpeechResult=What are your pricing plans?"
```

### Simulate call completed (triggers summary)

```bash
curl -X POST http://localhost:8000/twilio/status \
  -d "CallSid=CAtest123" \
  -d "CallStatus=completed" \
  -d "From=+15551234567"
```

### List calls

```bash
curl http://localhost:8000/calls
```

### Get call details

```bash
curl http://localhost:8000/calls/CAtest123
```

## Deploy to Railway

1. Push this repo to GitHub.
2. Create a new project on [Railway](https://railway.app).
3. Add a **PostgreSQL** plugin (optional — SQLite works for demos but Postgres is recommended).
4. Set environment variables from `.env.example`.
5. Set `DATABASE_URL` to the Postgres connection string Railway provides.
6. Set `BASE_URL` to your Railway public URL.
7. Railway uses the `Procfile` automatically.

## Deploy to Render

1. Create a new **Web Service** on [Render](https://render.com).
2. Connect your GitHub repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5. Add environment variables from `.env.example`.
6. Optionally add a Render Postgres database and set `DATABASE_URL`.

## Call Flow

```
Caller dials Twilio number
        │
        ▼
POST /twilio/voice  →  Greeting + Gather
        │
        ▼
Caller speaks  →  POST /twilio/gather
        │
        ├── Transfer intent?  →  Dial TRANSFER_PHONE_NUMBER
        ├── Message intent?   →  Collect name / phone / message
        ├── Demo intent?      →  Collect demo details
        └── Otherwise         →  OpenAI reply + Gather (loop)
        │
        ▼
Call ends  →  POST /twilio/status  →  Save AI summary
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes | OpenAI API key |
| `TWILIO_ACCOUNT_SID` | Yes | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | Yes | Twilio auth token |
| `TWILIO_PHONE_NUMBER` | Yes | Your Twilio number |
| `TRANSFER_PHONE_NUMBER` | Yes | Human transfer destination |
| `DATABASE_URL` | No | Defaults to SQLite locally |
| `BASE_URL` | Yes | Public app URL for Twilio callbacks |

## Troubleshooting

- **No speech detected:** Speak clearly after the greeting pause. The agent will retry once, then offer to take a message.
- **OpenAI errors:** Check `OPENAI_API_KEY` and account billing. The agent falls back to a generic message if OpenAI is unavailable.
- **Transfer not working:** Verify `TRANSFER_PHONE_NUMBER` is set and in E.164 format (`+1...`).
- **Webhooks not hitting your app:** Confirm ngrok is running and `BASE_URL` matches the ngrok HTTPS URL. Check Twilio debugger in the console.

## License

MIT — NM2TECH LLC demo project.
