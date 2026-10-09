"""Live LLM probe: ask the real model the appeal question with NO retrieved context.

This reproduces exactly what RAG generation does today: workflow doc is absent
(count of document_type=workflow is 0), so chunks=[] -> but we call the LLM
directly to show the parametric answer vs the gold answer. Budget: 1 LLM call.
"""

import os
import sys
import time
from pathlib import Path

ROOT = Path("C:/github/NSA_webservice")
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)
os.environ["RAG_USE_STUB_LLM"] = "false"  # must be AFTER load_dotenv (it resets to true)
os.environ["RAG_LLM_MAX_ATTEMPTS"] = "1"
os.environ["RAG_LLM_BACKOFF_BASE_S"] = "0.5"
os.environ["RAG_LLM_BACKOFF_MAX_S"] = "2.0"

QUERY = "How can the FBO appeal to the designated officer?"
GOLD = "The FBO can appeal to the designated officer in **Form VIII**, Regulation 2.4.6."

from app.rag.generation.llm_client import GroundedLLMClient

client = GroundedLLMClient()
print(f"model={client.model} stub={client.use_stub}", flush=True)
t = time.time()
resp = client.call(
    system_prompt="You are a food-safety legal assistant. Answer with exact Form numbers and regulation clauses. Cite [n]. If unsure, say so.",
    user_prompt=f"Question: {QUERY}\n\nContext:\n(no retrieved context — workflow document not in corpus)\n\nAnswer:",
    temperature=0.1,
    max_tokens=300,
)
print(f"latency={time.time() - t:.1f}s model={resp.model} error={resp.error}")
print("ANSWER:\n" + (resp.text or "(empty)"))
print("\nGOLD:\n" + GOLD)
import re

FRE = re.compile(r"\bform\s+(ii|iii|iv|v|vi|vii|viia|viii)\b", re.IGNORECASE)
print(f"\nforms: answer={sorted({m.group(1).lower() for m in FRE.finditer(resp.text or '')})} gold=['viii']")
print("usage:", resp.usage)
