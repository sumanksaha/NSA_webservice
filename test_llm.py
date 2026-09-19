import sys, os, json

sys.path.insert(0, r"C:\github\NSA_webservice")

from dotenv import load_dotenv

load_dotenv(r"C:\github\NSA_webservice/.env", override=True)

# Override stub mode to use the real API
os.environ["RAG_USE_STUB_LLM"] = "false"

from app.rag.generation.llm_client import GroundedLLMClient, GroundedLLMResponse

client = GroundedLLMClient()
mode = "stub" if client.use_stub else "live"
print(f"LLM mode after override: {mode}")
print(f"API key present: {bool(client._api_key)}")

if not client.use_stub:
    print("Attempting a test API call to OpenRouter...")
    try:
        resp = client.call(
            system_prompt="You are a legal assistant.",
            user_prompt="What is the capital of France? Give a one-sentence answer.",
        )
        if resp.success:
            print(f"TEST API CALL: SUCCESS")
            print(f"Model: {resp.model}")
            print(f"Latency: {resp.latency:.2f}s")
            print(f"Usage: {resp.usage}")
            print(f"Response (first 100 chars): {resp.text[:100]}...")
        else:
            print(f"TEST API CALL: ERROR - {resp.error}")
    except Exception as e:
        print(f"TEST API CALL: EXCEPTION - {type(e).__name__}: {e}")
else:
    print("Still in stub mode — API key not working")
