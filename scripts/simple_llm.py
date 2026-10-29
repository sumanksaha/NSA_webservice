"""Simple LLM query→answer CLI (no RAG).

Usage:
    python scripts/simple_llm.py "Your question here" [--system "..."] [--temperature 0.2] [--max-tokens 512]

Requires (for live calls): LLM_ENABLED=true MAX_LLM_CALLS=<n> OPENROUTER_API_KEY=<key>
Otherwise returns the deterministic stub.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from app.llm.simple_complete import SimpleLLMClient  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Simple LLM query→answer (no RAG).")
    parser.add_argument("query", help="User question")
    parser.add_argument("--system", default=None, help="System prompt override")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--model", default=None, help="Model slug (must be allow-listed)")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    client = SimpleLLMClient(model=args.model)
    print(
        client.complete(
            args.query,
            system_prompt=args.system,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
