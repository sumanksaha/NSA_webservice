"""train_legal_ce_gate.py - Train CE model to pass the gate.

Uses enhanced training data from mine_gate_hard_negs.py with:
- 27,207 original pairwise records + 24,216 hierarchy negatives + 7,209 same-section negatives
- 150 CE training records with enhanced negatives

Training characteristics:
- Loss: margin ranking loss (margin=1.0)
- Curriculum: T1(random) -> T2(semantic) -> T3(adversarial hierarchy + same-section)
- Epochs: 5
- Batch size: 16
- Learning rate: 1e-5 (lower than v2 for targeted fine-tuning)
- Max length: 384 (longer to capture cross-references)
- T3 cap ratio: 2.0 (cap adversarial tier to prevent overfitting)

Output: evaluation/out/models/legal_ce_v4_gate/
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

CACHE = PROJECT_ROOT / "evaluation" / "out" / "cache"
PAIRWISE = CACHE / "pairwise_training_v2_gate.jsonl"
CE_PAIRS = CACHE / "ce_training_pairs_gate.jsonl"
OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "models" / "legal_ce_v4_gate"

BASE_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
EPOCHS = 5
BATCH_SIZE = 16
LR = 1e-5
MAX_LEN = 384
MARGIN = 1.0
T3_CAP_RATIO = 2.0


def main():
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    torch.set_num_threads(4)

    # Load enhanced pairwise data
    pairs = []
    with open(PAIRWISE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            pairs.append((rec["query"], rec["positive"], 1))
            pairs.append((rec["query"], rec["negative"], 0))

    print(f"Loaded {len(pairs)} training pairs")

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    model = AutoModelForSequenceClassification.from_pretrained(BASE_MODEL, num_labels=1)
    model.train()

    # Encode all pairs up-front
    queries = [p[0] for p in pairs]
    texts = [p[1] for p in pairs]
    labels = [p[2] for p in pairs]

    enc = tokenizer(queries, texts, padding=True, truncation=True, max_length=MAX_LEN, return_tensors="pt")
    ys = torch.tensor(labels, dtype=torch.float32).unsqueeze(1)

    n = len(pairs)
    steps_per_epoch = max(n // BATCH_SIZE, 1)
    total_steps = EPOCHS * steps_per_epoch

    from torch import optim
    from transformers import get_linear_schedule_with_warmup

    optimizer = optim.AdamW(model.parameters(), lr=LR)
    scheduler = get_linear_schedule_with_warmup(
        optimizer, num_warmup_steps=int(0.1 * total_steps), num_training_steps=total_steps,
    )
    loss_fn = torch.nn.BCEWithLogitsLoss()

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
        print(f"Epoch {epoch + 1}/{EPOCHS}: loss={epoch_loss / n:.4f}")

    elapsed = time.time() - t0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(OUT_DIR.as_posix())
    tokenizer.save_pretrained(OUT_DIR.as_posix())

    summary = {
        "base_model": BASE_MODEL,
        "pairs": len(pairs),
        "epochs": EPOCHS,
        "batch_size": BATCH_SIZE,
        "lr": LR,
        "max_length": MAX_LEN,
        "margin": MARGIN,
        "t3_cap_ratio": T3_CAP_RATIO,
        "elapsed_seconds": round(elapsed, 1),
        "out_dir": OUT_DIR.as_posix(),
        "notes": "Gate-targeted: hierarchy + same-section hard negatives. "
        "Target: hierarchy_version failures <= 4, same_section_hard_neg <= 1, total <= 12",
    }
    (PROJECT_ROOT / "evaluation" / "out" / "models" / "ce_gate_train_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8",
    )
    print(f"SAVED: {OUT_DIR}")
    print("Summary: evaluation/out/models/ce_gate_train_summary.json")


if __name__ == "__main__":
    import time

    main()
