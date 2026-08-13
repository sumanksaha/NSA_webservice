# FSSAI_REINGEST_PLAN.md

**P1-4 remediation — rebuild `fssai_legal_768` from the local DB (12,819 chunks, full §5.1 metadata)**

- **Status:** ✅ **EXECUTED 2026-08-11** — this plan was carried out as written (dry-run first, then
  `--delete-collection`, then stamp). Verified results: **12,819 points, 29/29 docs, `act_name` 100%,
  reconcile matched 12,819 / failed 0 / unexplained 0**, Qdrant total 27,343 = Neo4j 27,343. The
  sections below remain as the plan + execution record; live evidence: `reports/fssai_reingest_run.log`,
  `CORPUS_IDENTITY_REPORT.md` §8.
- **Prepared:** 2026-08-11
- **Source of truth:** `CORPUS_IDENTITY_REPORT.md` (2026-08-11) — the live `fssai_legal_768`
  collection holds **1,100 stale points from a different DB snapshot** (14/29 docs, content
  99.9% identical to current-DB chunks, UUIDs regenerated). The current corpus is the local
  DB: **29 documents / 12,819 `LegalChunk` rows**, which the Neo4j KG already mirrors 1:1.
- **Design decision (locked):** the rebuild is **DB → Qdrant payload reconstruction with
  identity preservation** — NOT a PDF re-ingest. Re-chunking the PDFs would mint **fresh
  UUIDs** and break the Neo4j↔Qdrant join (`Chunk.chunk_id` / `qdrant_point_id` =
  `LegalChunk.id`), recreating the exact identity gap this remediation exists to close.
  The DB rows carry the full text and a complete §5.1 `metadata_json` (verified
  `LegalChunk.text == metadata_json.chunk_text`, 0 nulls), so points are rebuilt 1:1.

---

## 0. Why this is safe / why it is needed

| Question | Answer |
| --- | --- |
| What breaks today? | FSSAI dense/hybrid retrieval sees 1,100/12,819 chunks (8.6% coverage); `act_name` 100% missing; `provision_id`/`instrument_id`/`status` absent (points carry unknown-document UUIDs, so the identity stamper could only add `legal_domain`). |
| What is preserved? | `chunk_id = LegalChunk.id` (the identity Neo4j uses), `content_hash`, `document_id` (current DB UUIDs), section numbers, all cached §5.1 enrichment. |
| What changes in the DB? | **Nothing.** `LegalChunk.qdrant_point_id == id` is already self-referential — after the rebuild it becomes *true* by construction. No DB writes. |
| What changes in Neo4j? | **Nothing.** The KG already references the DB chunk ids; the collection rebuild makes the graph-side join resolvable from the payload side. |
| What about the after_flush hook? | `register_legal_chunk_hooks()` is a **manual opt-in, NOT wired in `create_app()`** — no double-upsert risk from DB writes. Do not arm it during/after this work. |
| Rollback? | §7 — the stale collection is exported (with vectors) before deletion; restoring it is a single re-create + re-upsert from the backup JSON. |

---

## 1. Prerequisites (pre-flight checks)

1. **Back up the local DB** (identity authority — cheap insurance):
   ```bash
   cd /c/github/NSA_webservice
   cp instance/app.db instance/app.db.pre_fssai_reingest_$(date +%Y%m%d_%H%M%S)
   ```
2. **Confirm the environment** (all four must be set — the extract script already proved them):
   ```bash
   python -c "import os; [print(k, '=', 'SET' if os.environ.get(k) else 'UNSET') for k in ['RAG_QDRANT_URL','RAG_QDRANT_API_KEY','NEO4J_URI','NEO4J_USERNAME','NEO4J_PASSWORD']]"
   ```
   (`.env` is loaded by the script via `load_dotenv()`; on this host all are set.)
3. **Confirm the current bad state** (baseline — should read exactly this):
   ```bash
   python corpus_identity_reconcile.py 2>&1 | head -12
   # expect: TOTAL row  matched=14524  failed=11222  |  FOOD_SAFETY row matched=0 failed=11222
   python - <<'EOF'
   import os
   from qdrant_client import QdrantClient
   c = QdrantClient(url=os.environ['RAG_QDRANT_URL'], api_key=os.environ.get('RAG_QDRANT_API_KEY') or None)
   print('fssai_legal_768 points BEFORE:', c.get_collection('fssai_legal_768').points_count)
   print('sparse declared:', bool(getattr(c.get_collection('fssai_legal_768').config.params, 'sparse_vectors', None)))
   EOF
   # expect: points BEFORE: 1100   (sparse: True on this cluster — see NEO4J_QDRANT_AUDIT §3)
   ```

---

## 2. Ordering (the exact sequence)

```
STEP 0  Pre-flight checks          (§1)                     [read-only]
STEP 1  Export stale collection    (§3, cmd 1)              [read  + write local file]
STEP 2  Rebuild from DB            (§3, cmd 2)              [WRITES Qdrant — the remediation]
STEP 3  Stamp identity fields      (§3, cmd 3)              [WRITES Qdrant payloads]
STEP 4  Verify                     (§4)                     [read-only]
STEP 5  Re-run reconciliation      (§4, cmd 7)              [read-only]
STEP 6  Update docs/status         (§6)                     [doc-only]
```

> **Gate:** Steps 2–3 must be run **one at a time**, not chained with `&&`, so each
> step's output is inspected before the next write. If STEP 2 reports `ok:false`,
> **stop** — restore the backup (§7) and investigate before any stamping.

---

## 3. Exact commands

### 3.1 STEP 1 — export the stale collection (recoverable rollback point)

The committed helper ``scripts/export_fssai_backup.py`` performs the read-only
scroll-with-vectors export (it also JSON-serialises Qdrant ``SparseVector``
objects, which a naive ``json.dump`` would fail on):

```bash
cd /c/github/NSA_webservice
python scripts/export_fssai_backup.py
# expect: BACKUP reports\fssai_legal_768_pre_reingest_backup.json: 1100 points (with vectors)
```

> ⚠️ Do NOT use a naive inline export: the collection declares BOTH dense
> (768 Cosine) and sparse (``text_sparse`` IDF) vectors, and ``json.dump``
> fails on Qdrant's ``SparseVector`` objects (observed 2026-08-11). The helper
> script stores sparse vectors as ``{"_sparse": {"indices": [...], "values":
> [...]}}`` so the §7 rollback can faithfully reconstruct them.

**Rollback restore (reverse of ``safe_vector``, §7):**

```python
def restore_vector(v):
    if isinstance(v, dict) and "_sparse" in v:
        s = v["_sparse"]
        return models.SparseVector(indices=s["indices"], values=s["values"])
    if isinstance(v, dict):
        return {k: restore_vector(x) for k, x in v.items()}
    if isinstance(v, list):
        return [restore_vector(x) for x in v]
    return v
```

### 3.2 STEP 2 — rebuild `fssai_legal_768` from the DB

The command below is `scripts/reingest_fssai_from_db.py` (the file ships with this plan —
see §8; it is written to the repo but is **inert until invoked**):

```bash
cd /c/github/NSA_webservice
python scripts/reingest_fssai_from_db.py --delete-collection --dry-run     # 1st: plan only, NO writes
python scripts/reingest_fssai_from_db.py --delete-collection                # 2nd: execute
```

**Script safety guards (built in):**

- **FSS-scope guard** — refuses to run if any loaded document is not recognisably
  FSS-family (URI/title markers), so a foreign document can never be stamped with
  the FSS `act_name` and enter `fssai_legal_768`.
- **Backup guard** — `--delete-collection` refuses to run unless the STEP-1 export
  (`reports/fssai_legal_768_pre_reingest_backup.json`) exists, making the
  pre-rebuild collection always recoverable (§7).
- **Corpus-size warning** — prints a stderr warning (does not abort) if the loaded
  corpus deviates from the audited baseline (29 docs / 12,819 chunks), e.g. after
  a legitimate future FSSAI ingest.

What the script does, in order:

1. **Loads the corpus** (one Flask app context, same pattern as
   `KGCorpusIngestionEngine._fss_corpus`): 29 `LegalDocument` rows + 12,819
   `LegalChunk` rows, reading `lc.text` (full text) as the authoritative
   `chunk_text`.
2. **Builds §5.1 payloads per chunk** from `metadata_json` (deep copy), then
   overrides the authoritative keys from the row: `chunk_id = lc.id`,
   `document_id`, `chunk_text = lc.text`, `content_hash`, `section_number`,
   `chunk_index`, `chunk_char_count`, `word_count`, `created_at`. Adds
   `act_name` per document (`"Food Safety and Standards Act, 2006"` for every
   FSS-family document — matching `app/rag/enrichment/deterministic.py`
   `legal_act_of`'s FSS-family resolution; the FSS Act itself carries its own
   title).
3. **Rebuilds the collection**: `--delete-collection` calls
   `client.delete_collection("fssai_legal_768")` then
   `QdrantIndexer(collection_name="fssai_legal_768").ensure_collection()`,
   which recreates it with dense 768 + `text_sparse` (IDF) when
   `RAG_ENABLE_SPARSE` is on — identical config to the other 5 collections.
4. **Embeds + upserts per document** via `indexer.sync_payloads(payloads)` —
   the exact production path (`make_ingestion_pipeline` → `QdrantIndexer`),
   which embeds with `sentence-transformers/all-mpnet-base-v2` and upserts in
   **100-point batches with a per-batch retry** (`UPSERT_BATCH_SIZE = 100` —
   the fix for the 2026-08-09 connection-reset failures documented in
   `ingest_run.log`).
5. **Reports per-document `chunk_count` / `points_upserted` / `ok`** and exits
   1 if any document fails (resumable: re-run without `--delete-collection`
   to append/complete missing documents; `--only <doc-id>` restricts).

Timing estimate (from `docs/INGESTION_READINESS.md` measured rates on this CPU):
dense embedding 212–315 ms/chunk × 12,819 ≈ **45–67 min**; sparse BM25 adds
~2–7 min; upserts ~5 min. **Plan for ~1 h 15 m wall clock.** Run with
`RAG_ENABLE_SPARSE=true` (current default) so the rebuilt collection carries
the same dense + sparse layout as the other collections.

### 3.3 STEP 3 — stamp the four identity fields

After the points exist with **current-DB document_ids**, the identity registry
resolves them (its `doc_identity_map()` keys on current DB document ids — this
is exactly why the stale points only ever got domain-level stamps):

```bash
cd /c/github/NSA_webservice
python scripts/stamp_qdrant_payload_identity.py --collection fssai_legal_768 --dry-run
python scripts/stamp_qdrant_payload_identity.py --collection fssai_legal_768
```

Expected after stamping: `provision_id` on section-bearing chunks
(`{INSTRUMENT}_SEC_{n}` matching Neo4j), `instrument_id`, `legal_domain =
FOOD_SAFETY`, `status` on all 29 documents' points; the 24 keyword payload
indexes re-reported as already-existing.

---

## 4. Verification queries (STEP 4 — all read-only)

Run in this order; every check must pass before FSSAI benchmarking resumes.

```bash
cd /c/github/NSA_webservice

# (a) point count == 12,819 (EXACT count — points_count is approximate)
python - <<'EOF'
import os
from qdrant_client import QdrantClient
c = QdrantClient(url=os.environ['RAG_QDRANT_URL'], api_key=os.environ.get('RAG_QDRANT_API_KEY') or None)
print('points AFTER:', c.count(collection_name='fssai_legal_768', exact=True).count)  # expect 12819
EOF

# (b) identity overlap with the DB: 12,819/12,819 chunk ids present
python corpus_identity_reconcile.py 2>&1 | head -12
#   expect TOTAL row:  matched=27463? NO — matched = Neo4j ∩ Qdrant = 14524 + 12819 = 27343
#   expect FOOD_SAFETY row: matched=12819  failed=0  unexplained=0
#   expect fssai_db_in_fssai_qdrant: 12819 ; fssai_qdrant_not_in_db: 0

# (c) act_name coverage == 100% (was 0%)
python - <<'EOF'
import os
from qdrant_client import QdrantClient
c = QdrantClient(url=os.environ['RAG_QDRANT_URL'], api_key=os.environ.get('RAG_QDRANT_API_KEY') or None)
recs, off, n, with_act = [], None, 0, 0
while True:
    page, off = c.scroll(collection_name='fssai_legal_768', limit=1000, with_payload=True, with_vectors=False, offset=off)
    for r in page:
        n += 1
        if (r.payload or {}).get('act_name'): with_act += 1
    if not off: break
print(f'points={n} with_act_name={with_act} ({100*with_act//n}%)')   # expect 12819 / 12819
EOF

# (d) document coverage == 29 distinct current-DB document ids
python - <<'EOF'
import os, sqlite3
from qdrant_client import QdrantClient
c = QdrantClient(url=os.environ['RAG_QDRANT_URL'], api_key=os.environ.get('RAG_QDRANT_API_KEY') or None)
con = sqlite3.connect('file:instance/app.db?mode=ro', uri=True)
db_ids = {r[0] for r in con.execute('SELECT id FROM legal_document')}
off, qd_ids = None, set()
while True:
    page, off = c.scroll(collection_name='fssai_legal_768', limit=1000, with_payload=True, with_vectors=False, offset=off)
    qd_ids |= {(r.payload or {}).get('document_id') for r in page}
    if not off: break
print('qdrant docs in DB:', len(qd_ids & db_ids), 'of', len(db_ids))   # expect 29 of 29
EOF

# (e) provision linkage: sample provision_ids resolve to Neo4j 1:1
python - <<'EOF'
import os, json
from qdrant_client import QdrantClient
c = QdrantClient(url=os.environ['RAG_QDRANT_URL'], api_key=os.environ.get('RAG_QDRANT_API_KEY') or None)
recs, _ = c.scroll(collection_name='fssai_legal_768', limit=200, with_payload=True, with_vectors=False)
pids = sorted({(r.payload or {}).get('provision_id') for r in recs if (r.payload or {}).get('provision_id')})
print('sample provision_ids:', pids[:10], '... total distinct in sample:', len(pids))
EOF
# then verify a sample against Neo4j: MATCH (p:LegalProvision {provision_id: $pid}) RETURN count(*)

# (f) content_hash parity: every DB content_hash present in the collection
python - <<'EOF'
import os, sqlite3
from qdrant_client import QdrantClient
c = QdrantClient(url=os.environ['RAG_QDRANT_URL'], api_key=os.environ.get('RAG_QDRANT_API_KEY') or None)
con = sqlite3.connect('file:instance/app.db?mode=ro', uri=True)
db_hashes = {r[0] for r in con.execute('SELECT content_hash FROM legal_chunk')}
off, qd_hashes = None, set()
while True:
    page, off = c.scroll(collection_name='fssai_legal_768', limit=1000, with_payload=True, with_vectors=False, offset=off)
    qd_hashes |= {(r.payload or {}).get('content_hash') for r in page}
    if not off: break
print('DB hashes present in collection:', len(db_hashes & qd_hashes), 'of', len(db_hashes))  # expect 12819
EOF
```

**Acceptance criteria (all must hold):**

| # | Check | Expected |
| --- | --- | --- |
| a | exact `count(exact=True)` | 12,819 |
| b | reconcile FOOD_SAFETY `matched` / `failed` / `unexplained` | 12,819 / 0 / 0 |
| b′ | `fssai_db_in_fssai_qdrant` | 12,819 |
| b″ | `fssai_qdrant_not_in_db` | 0 |
| c | `act_name` coverage | 12,819 / 12,819 (100%) |
| d | distinct documents matching DB | 29 / 29 |
| e | `provision_id` sample → Neo4j | 1:1 (60/60 like the 2026-08-11 stamp verification) |
| f | content_hash parity | 12,819 / 12,819 |

---

## 5. Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| Qdrant connection reset on large upserts (recurrence of 2026-08-09) | Medium | Partial document loss | `UPSERT_BATCH_SIZE = 100` + per-batch retry built into `upsert_points`; script is per-document and resumable (`--only`). |
| Embedding OOM (12,819 × 768-d) | Low–Med | Script abort | Per-document batching (largest doc = Food_Additives 5,913 chunks; the production pipeline already handles this size). |
| Identity drift if PDFs re-chunked by mistake | High (if wrong path) | Full identity gap | Design locked to DB→payload rebuild; do NOT run `scripts/_reindex_full_enrichment.py` or `_run_corpus_ingestion.py` for this remediation. |
| Stale 1,100 points accidentally kept | Med | Duplicates in collection | `--delete-collection` removes the collection entirely before rebuild; backup taken first (§3.1). |
| `RAG_ENABLE_SPARSE=false` during run | Low | Dense-only collection | Verify the collection reports `text_sparse` before STEP 3; keep env at current default `true`. |
| Stamp run before points exist | — | No-op | Ordering enforced: STEP 2 completes `ok:true` before STEP 3. |

---

## 6. Post-remediation documentation updates

1. `CORPUS_IDENTITY_REPORT.md` — replace §5 verdict's "remaining open remediation" with
   the completed rebuild; update §6.2 go/no-go to **GO for FSSAI dense retrieval**.
2. `NEO4J_QDRANT_AUDIT_REPORT.md` §0 — flip the P1-4 row to ✅ Resolved.
3. `agents.md` status line — note `fssai_legal_768` = 12,819 points, identity-stamped.
4. Re-run `corpus_identity_reconcile.py` output JSON is the permanent evidence artifact
   (FOOD_SAFETY row: matched 12,819 / failed 0 / unexplained 0).

---

## 7. Rollback

If anything fails after STEP 2/3:

```bash
cd /c/github/NSA_webservice
# 1. restore the local DB backup taken in §1 (if DB was touched — it should not have been)
# 2. restore the stale collection from the STEP-1 export:
python - <<'EOF'
import json, os
from qdrant_client import QdrantClient, models
c = QdrantClient(url=os.environ['RAG_QDRANT_URL'], api_key=os.environ.get('RAG_QDRANT_API_KEY') or None)
data = json.load(open('reports/fssai_legal_768_pre_reingest_backup.json'))
if c.collection_exists('fssai_legal_768'): c.delete_collection('fssai_legal_768')
c.create_collection(collection_name='fssai_legal_768',
    vectors_config=models.VectorParams(size=768, distance=models.Distance.COSINE),
    sparse_vectors_config=models.SparseVectorParams(modifier=models.Modifier.IDF))

def restore_vector(v):
    """Reverse of export_fssai_backup.safe_vector: rebuild SparseVector from JSON."""
    if isinstance(v, dict) and "_sparse" in v:
        s = v["_sparse"]
        return models.SparseVector(indices=s["indices"], values=s["values"])
    if isinstance(v, dict):
        return {k: restore_vector(x) for k, x in v.items()}
    if isinstance(v, list):
        return [restore_vector(x) for x in v]
    return v

pts = [models.PointStruct(id=p['id'], vector=restore_vector(p['vector']), payload=p['payload']) for p in data]
for i in range(0, len(pts), 100):
    c.upsert(collection_name='fssai_legal_768', points=pts[i:i+100])
print('restored', len(pts), 'stale points')
EOF
# 3. re-run corpus_identity_reconcile.py — expect the ORIGINAL baseline (matched=14524, failed=11222)
```

---

## 8. The script (`scripts/reingest_fssai_from_db.py`)

The full, reviewable implementation is committed alongside this plan (**executed 2026-08-11**;
run log: `reports/fssai_reingest_run.log` — 29/29 docs OK, `finished in 1961s ok=True`). Key
invariants it enforces:

- **Strict identity**: `chunk_id` = `LegalChunk.id`; no UUID regeneration anywhere.
- **Authoritative text**: `chunk_text` from `LegalChunk.text`, not the (equivalent,
  but truncated-elsewhere) cached payload.
- **Same embed/upsert path as production**: `QdrantIndexer.sync_payloads` (dense 768 +
  optional sparse, 100-point batches, retry-once).
- **Explicit destructive guard**: `--delete-collection` is required to replace the
  collection; default is append/complete-only. It additionally requires the STEP-1
  rollback export to exist (refuses otherwise).
- **Dry-run**: `--dry-run` builds and validates all payloads and prints per-doc chunk
  counts **without touching Qdrant**.
- **Exit code 1 on any failed document** — the run is resumable (`--only <doc-id>`).

```bash
python scripts/reingest_fssai_from_db.py --help
```

---

*Plan ends. ⛔ Superseded by execution: the collection was rebuilt (12,819 pts) and stamped the same day — see `CORPUS_IDENTITY_REPORT.md` §8 for the executed verification evidence. The STEP-1 backup (`reports/fssai_legal_768_pre_reingest_backup.json`) preserves the pre-rebuild 1,100 points for rollback (§7).*
