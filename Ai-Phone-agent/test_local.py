"""Local test script — simulates Twilio webhooks without a phone call."""

import re
import sys

import httpx

BASE = "http://127.0.0.1:8000"
CALL_SID = "CA_local_test_001"
FROM = "+13012982874"


def say_text(twiml: str) -> str:
    """Extract spoken text or TTS play URL from TwiML."""
    if "<Play>" in twiml:
        plays = re.findall(r"<Play>([^<]+)</Play>", twiml)
        if plays:
            return f"[OpenAI TTS] {plays[0]}"
    parts = re.findall(r"<Say[^>]*>([^<]+)</Say>", twiml)
    return " | ".join(parts) if parts else "(no speech in TwiML)"


def gather_url(twiml: str) -> str:
    match = re.search(r'<Gather action="([^"]+)"', twiml)
    return match.group(1) if match else "(not found)"


def post(path: str, **fields) -> httpx.Response:
    return httpx.post(f"{BASE}{path}", data=fields, timeout=60.0)


def main() -> None:
    print("=" * 60)
    print("NM2TECH AI Phone Agent — Local Test")
    print("=" * 60)

    # 1. Health
    print("\n[1] Health check")
    r = httpx.get(f"{BASE}/health", timeout=10)
    print(f"    Status: {r.status_code}")
    print(f"    Body:   {r.json()}")
    assert r.status_code == 200, "Health check failed"

    # 2. Incoming call (greeting)
    print("\n[2] Incoming call -> /twilio/voice")
    r = post("/twilio/voice", CallSid=CALL_SID, From=FROM, To="+13012982874")
    print(f"    Status:     {r.status_code}")
    print(f"    Gather URL: {gather_url(r.text)}")
    print(f"    Says:       {say_text(r.text)}")
    assert r.status_code == 200
    assert "<Play>" in r.text or "<Say" in r.text

    # 3. Ask about pricing
    print("\n[3] Speech -> /twilio/gather  ('What are your prices?')")
    r = post(
        "/twilio/gather",
        CallSid=CALL_SID,
        From=FROM,
        SpeechResult="What are your prices?",
    )
    print(f"    Status: {r.status_code}")
    print(f"    Says:   {say_text(r.text)}")
    assert r.status_code == 200
    assert "99" in r.text or "pricing" in r.text.lower() or "plan" in r.text.lower() or "configured" in r.text.lower()

    # 4. Transfer intent
    print("\n[4] Speech -> /twilio/gather  ('transfer')")
    r = post(
        "/twilio/gather",
        CallSid="CA_local_test_002",
        From=FROM,
        SpeechResult="I want to transfer to a representative",
    )
    print(f"    Status: {r.status_code}")
    print(f"    Says:   {say_text(r.text)}")
    print(f"    Dial:   {'<Dial>' in r.text}")

    # 5. Call completed
    print("\n[5] Call status -> /twilio/status")
    r = post("/twilio/status", CallSid=CALL_SID, CallStatus="completed", From=FROM)
    print(f"    Status: {r.status_code}  Body: {r.json()}")

    # 6. List calls
    print("\n[6] Saved calls -> /calls")
    r = httpx.get(f"{BASE}/calls", timeout=10)
    data = r.json()
    print(f"    Total calls: {data['total']}")
    for call in data["calls"][:3]:
        print(f"    - {call['call_sid']} | {call['caller_phone']} | turns={call['turn_count']}")

    print("\n" + "=" * 60)
    print("All local tests passed!")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        sys.exit(1)
