# Modal inference for the NSA legal RAG stack

Hosts the two models the Render free tier can't run locally (torch + weights
> 512 MB RAM):

| Endpoint | Model | Purpose |
| --- | --- | --- |
| `POST /rerank` | fine-tuned legal CE from Modal Volume `nsa-ce-models` (`/models/legal_ce_v2_K500`) | CE head of the ensemble reranker |
| `POST /embed` | `all-mpnet-base-v2` (768-dim, baked into the image) | query-side dense embeddings |
| `GET /healthz` | — | reports which CE dir / embed model are loaded |

The three URLs are **one app** (`nsa-legal-inference`) with three web
endpoints; they are stable across redeploys, so Render env vars never change.

## One-time setup (already done if you followed the chat)

1. **Modal account** — sign up at modal.com, verify phone, add a card to unlock
   the $30/month free credits, and set a **monthly spend limit of $30** in
   Settings so it can never exceed the credit.
2. **CLI auth** — `pip install modal` then `modal setup` (browser auth).
3. **Volume** — `modal volume create nsa-ce-models` (holds the CE checkpoints;
   created 2026-09-19).

> The `hf-token` Secret from the original deploy is no longer used — the CE
> is read from the Volume and `all-mpnet-base-v2` is not gated.

## Ship a new cross-encoder

```bash
# 1. stage ONLY the inference files (never train_state.pt / tokenized_cache.pt)
mkdir -p /tmp/ce_stage/<model_name>
cp evaluation/out/models/<model_name>/{config.json,model.safetensors,tokenizer.json,tokenizer_config.json} /tmp/ce_stage/<model_name>/

# 2. upload to the Volume (Git Bash on Windows: MSYS_NO_PATHCONV=1 stops the
#    shell rewriting the remote path; PYTHONUTF8=1 avoids a cosmetic ✓ encoding error)
cd /tmp/ce_stage
MSYS_NO_PATHCONV=1 PYTHONUTF8=1 modal volume put nsa-ce-models <model_name> /<model_name> --force
modal volume ls nsa-ce-models /<model_name>   # expect the 4 files

# 3. point CE_MODEL_NAME in app.py at <model_name> (if it changed), then
cd modal_deploy && modal deploy app.py

# 4. a warm container from the PREVIOUS version keeps serving for up to
#    scaledown_window (10 min). Stop it so the swap is immediate, then check:
modal container list
modal container stop <container_id> --yes
curl https://sumanksaha--healthz.modal.run     # expect "rerank": "/models/<model_name>"

# 5. remove a superseded checkpoint (optional)
MSYS_NO_PATHCONV=1 modal volume rm nsa-ce-models /<old_model_name> -r
```

Re-uploading into the **same** directory name with `--force` swaps the weights
without any code change — containers read the Volume at start-up — but running
containers keep the old weights until they are stopped (step 4) or scale to zero.

## Deploy

```bash
cd modal_deploy
modal deploy app.py
```

The first build takes a few minutes (torch + embed model baked into the image;
the CE is *not* in the image). The deploy prints three URLs (one per web endpoint).

## Deployed

```
https://sumanksaha--rerank.modal.run
https://sumanksaha--embed.modal.run
https://sumanksaha--healthz.modal.run
```

- **2026-08-16** — first deploy; CE pulled from the HF Hub
  (`sumanksaha/Foodmultidomain`) at image build. Verified: `/embed` 768-dim;
  `/rerank` ranked "Section 50: General penalty for unsafe food" #1 for the
  penalty query at −0.82 (parity with local checkpoint −0.821).
- **2026-09-19** — CE moved to Modal Volume `nsa-ce-models:/legal_ce_v2_K500`
  (retrained 2026-09-18, margin loss + curriculum, val loss 0.718). The HF
  Hub copy is no longer read by the deployment.

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
after use via `scaledown_window`).
