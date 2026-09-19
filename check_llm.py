import sys, os

sys.path.insert(0, r"C:\github\NSA_webservice")

from dotenv import load_dotenv

load_dotenv(r"C:\github\NSA_webservice/.env", override=True)

# Check env values
stub_val = os.environ.get("RAG_USE_STUB_LLM", "")
print(f"RAG_USE_STUB_LLM: {stub_val}")
print(f'RAG_USE_STUB_LLM == "true": {stub_val.lower() == "true"}')

# Check GroundedLLMClient
from app.rag.generation.llm_client import GroundedLLMClient

client = GroundedLLMClient()
mode = "stub" if client.use_stub else "live"
print(f"LLM mode: {mode}")
print(f"LLM model: {client.model}")
print(f"Base URL: {client._base_url}")
print(f"API key present: {bool(client._api_key)}")
