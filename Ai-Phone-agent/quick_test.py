import httpx

BASE = "http://127.0.0.1:8000"
print("=== Quick Test ===\n")

# 1 Health
r = httpx.get(f"{BASE}/health", timeout=10)
print(f"[1] Health: {r.status_code} {r.json()}")

# 2 Greeting
r = httpx.post(f"{BASE}/twilio/voice", data={"CallSid": "test-run", "From": "+12404836963", "To": "+13012982874"}, timeout=30)
print(f"[2] Greeting: {r.status_code} - {'Play' in r.text or 'Say' in r.text}")

# 3 Pricing FAQ (fast path)
r = httpx.post(f"{BASE}/twilio/gather", data={"CallSid": "test-run", "From": "+12404836963", "SpeechResult": "What are your prices?"}, timeout=30)
has_price = "99" in r.text or "ninety-nine" in r.text.lower()
print(f"[3] Pricing FAQ: {r.status_code} - matched={has_price}")

# 4 Demo list
r = httpx.get(f"{BASE}/demos", timeout=10)
data = r.json()
print(f"[4] Demo requests saved: {data['total']}")

print("\n=== Ready for live call: (301) 298-2874 ===")
