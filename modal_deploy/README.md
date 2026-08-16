# Modal inference for the NSA legal RAG stack

Hosts the two models the Render free tier can't run locally (torch + weights
> 512 MB RAM):

| Endpoint | Model | Purpose |
| --- | --- | --- |
| `POST /rerank` | `sumanksaha/Foodmultidomain` (gated CE) | CE head of the ensemble reranker |
| `POST /embed` | `all-mpnet-base-v2` (768-dim) | query-side dense embeddings |

## One-time setup (already done if you followed the chat)

1. **Modal account** — sign up at modal.com, verify phone, add a card to unlock
   the $30/month free credits, and set a **monthly spend limit of $30** in
   Settings so it can never exceed the credit.
2. **HF Secret** — Modal → Secrets → create a **Hugging Face** secret named
   `hf-token` with `HF_TOKEN` = the read token (its account must have accepted
   the `sumanksaha/Foodmultidomain` gate).
3. **CLI auth** — `pip install modal` then `modal setup` (browser auth).

## Deploy

```bash
cd modal_deploy
modal deploy app.py
```

The first build takes a few minutes (torch + both models baked into the image).
The deploy prints three URLs (one per web endpoint).

## Deployed (2026-08-16)

```
https://sumanksaha--rerank.modal.run
https://sumanksaha--embed.modal.run
https://sumanksaha--healthz.modal.run
```

Verified live: `/embed` returns 768-dim vectors; `/rerank` ranks
"Section 50: General penalty for unsafe food" #1 for the penalty query with
score −0.82 (matches the local checkpoint's parity reference −0.821).

## Wire into the app

```bash
# .env
RAG_RERANKER_ENDPOINT=https://sumanksaha--rerank.modal.run
RAG_RERANKER_MODE=tei
RAG_EMBED_ENDPOINT=https://sumanksaha--embed.modal.run
RAG_RERANKER_REMOTE_FALLBACK=false
RAG_EMBED_REMOTE_FALLBACK=false
```

`REMOTE_FALLBACK=false` is **required on Render** — a true fallback would lazily
build the local torch model on remote failure and OOM the 512 MB instance.
Remote failures then degrade to sec_act features-only (rerank) / no-dense
(sparse-only) instead.

## Verify

```bash
curl -X POST https://<workspace>--nsa-legal-inference-rerank.modal.run \
  -H "Content-Type: application/json" \
  -d '{"query": "penalty for selling substandard food",
       "texts": ["Section 50: General penalty for unsafe food", "Section 3: Interpretation"]}'
# → [{"index": 0, "score": 4.2}, {"index": 1, "score": -1.1}]

curl -X POST https://<workspace>--nsa-legal-inference-embed.modal.run \
  -H "Content-Type: application/json" \
  -d '{"texts": ["penalty for selling substandard food"]}'
# → {"vectors": [[768 floats]]}
```

## Cost

Free tier gives $30/month credits. A query ≈ 1 s embed + ~2 s rerank of the CE
head ≈ $0.00006 → ~500 K queries/month inside the credit. Containers scale to
zero when idle (cold start ~10-30 s on the next call; kept warm for 10 min
after use via `container_idle_timeout`).
