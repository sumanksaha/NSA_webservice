import sys, os, ssl
sys.path.insert(0, r'C:\github\NSA_webservice')

from dotenv import load_dotenv
load_dotenv(r'C:\github\NSA_webservice/.env', override=True)
os.environ['RAG_USE_STUB_LLM'] = 'false'

import httpx
import json

api_key = os.environ.get('OPENROUTER_API_KEY', '')
model = os.environ.get('RAG_LLM_MODEL', 'poolside/laguna-s-2.1:free')
base_url = "https://openrouter.ai/api/v1"

print(f"Model: {model}")
print(f"API key length: {len(api_key)}")

# Test with verify=False to work around self-signed cert
try:
    url = base_url + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://nsa-webservice.local",
        "X-Title": "NSA Webservice RAG Eval",
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a legal assistant."},
            {"role": "user", "content": "What is the capital of France? One sentence."},
        ],
        "temperature": 0.1,
        "max_tokens": 100,
    }
    with httpx.Client(timeout=30.0, verify=False) as client:
        resp = client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
        choice = data["choices"][0]
        text = choice.get("content") or choice.get("message", {}).get("content", "")
        print(f"API CALL: SUCCESS (status {resp.status_code})")
        print(f"Model: {data.get('model', 'N/A')}")
        usage = data.get("usage", {})
        print(f"Usage: prompt={usage.get('prompt_tokens')}, completion={usage.get('completion_tokens')}")
        print(f"Response: {text[:100]}")
except Exception as e:
    print(f"API CALL: ERROR - {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
