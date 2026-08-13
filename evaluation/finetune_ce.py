"""Fine-tune the legal cross-encoder on mined (gold, hard-negative) pairs.

Reads ``evaluation/out/cache/ce_training_pairs.jsonl`` (produced by
``mine_ce_pairs.py`` from frozen multi-route caches — no live retrieval),
and fine-tunes ``cross-encoder/ms-marco-MiniLM-L-6-v2`` on
(query, chunk_text) -> label, where label=1 for gold-covering pool chunks
(positives) and label=0 for same-family wrong-section / high-rank non-gold
chunks (hard negatives).

Output: evaluation/out/models/legal_ce_v1/ — a drop-in for the production
``Reranker`` via ``RAG_RERANKER_MODEL`` (the Reranker class takes any local
path or HF model id as ``model_name``).

CPU-friendly: ~2.1k pairs, 4 epochs, batch 32 => a couple of minutes.
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

PAIRS = PROJECT_ROOT / "evaluation" / "out" / "cache" / "ce_training_pairs.jsonl"
OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "models" / "legal_ce_v1"
BASE_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

EPOCHS = 4
BATCH_SIZE = 32
LR = 2e-5


def main() -> int:
    import torch
    from torch.utils.data import DataLoader
    from transformers import (
        AutoModelForSequenceClassification,
        AutoTokenizer,
        get_linear_schedule_with_warmup,
    )

    pairs = []
    with open(PAIRS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            q = rec["query"]
            for p in rec["positives"]:
                pairs.append((q, p["text"], 1))
            for n in rec["negatives"]:
                pairs.append((q, n["text"], 0))

    labels = Counter(label for _, _, label in pairs)
    print(f"loaded {len(pairs)} pairs: positives={labels[1]} negatives={labels[0]}", flush=True)

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    model = AutoModelForSequenceClassification.from_pretrained(BASE_MODEL, num_labels=1)
    model.train()

    # Encode (query, text) once up-front: all pairs are short relative to
    # max_length and this avoids re-tokenizing per epoch.
    enc = tokenizer(
        [p[0] for p in pairs],
        [p[1] for p in pairs],
        padding=True,
        truncation=True,
        max_length=512,
        return_tensors="pt",
    )
    ys = torch.tensor([p[2] for p in pairs], dtype=torch.float32).unsqueeze(1)

    steps_per_epoch = max(len(pairs) // BATCH_SIZE, 1)
    total_steps = EPOCHS * steps_per_epoch
    optimizer = torch.optim.AdamW(model.parameters(), lr=LR)
    scheduler = get_linear_schedule_with_warmup(
        optimizer,
        num_warmup_steps=int(0.1 * total_steps),
        num_training_steps=total_steps,
    )
    loss_fn = torch.nn.BCEWithLogitsLoss()

    n = len(pairs)
    order = torch.randperm(n)
    t0 = time.time()
    for epoch in range(EPOCHS):
        epoch_loss = 0.0
        steps = 0
        order = torch.randperm(n)
        for i in range(0, n, BATCH_SIZE):
            idx = order[i : i + BATCH_SIZE]
            batch = {k: v[idx] for k, v in enc.items()}
            logits = model(**batch).logits
            loss = loss_fn(logits, ys[idx])
            loss.backward()
            optimizer.step()
            scheduler.step()
            optimizer.zero_grad()
            epoch_loss += loss.item() * len(idx)
            steps += 1
        print(
            f"epoch {epoch + 1}/{EPOCHS} loss={epoch_loss / max(n, 1):.4f} "
            f"({steps} steps, {time.time() - t0:.0f}s elapsed)",
            flush=True,
        )
    elapsed = time.time() - t0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(OUT_DIR.as_posix())
    tokenizer.save_pretrained(OUT_DIR.as_posix())
    summary = {
        "base_model": BASE_MODEL,
        "pairs": len(pairs),
        "positives": labels[1],
        "negatives": labels[0],
        "epochs": EPOCHS,
        "batch_size": BATCH_SIZE,
        "lr": LR,
        "max_length": 512,
        "elapsed_seconds": round(elapsed, 1),
        "out_dir": OUT_DIR.as_posix(),
        "notes": "drop-in via RAG_RERANKER_MODEL=<out_dir>",
    }
    (PROJECT_ROOT / "evaluation" / "out" / "models" / "ce_finetune_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
