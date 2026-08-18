# Corpus Identity Coverage — Evaluation & Path to Complete

> **Status:** P1 (document_title) + P2 (L7 propagation) **implemented and applied
> live (2026-08-18)**. Tooling: `evaluation/coverage_audit.py` (repeatable
> audit, JSON at `out/cache/coverage_audit.json`).
> **Headline (post-P1/P2):** 58.0% of all chunks / **82.4% of substantive
> (hl≥2)** chunks carry retrievable identity (up from 51.3% / 71.6%). The
> remaining gap is 2,989 substantive chunks — rule docs (1,414), BNS
> space-stripped OCR (715), Nutraceuticals bilingual (399) — all requiring
> re-ingestion (P3/P4), not payload-side fixes.

---

## 1. What "coverage" means here (method)

A chunk is **identified** when it carries the identity its document class needs
for retrieval (G8 semantics):

| Document type | Identity field | Notes |
|---|---|---|
| `act` | `section_number` | Act sections |
| `regulation` / `rule` / `notification` / `circular` | `clause_number` | Dotted regulation clause (e.g. `2.4.15`) — Act sections on non-act chunks are noise (stripped, G8) |
| `unknown` | either | 1 chunk |

Coverage is reported on **substantive** chunks (`hierarchy_level >= 2`): the
10,361 hl1 chunks are headers, page fragments, form labels and OCR residue
(including *reversed* text in Food Additives) that semantically carry no
identity (G6). Counting them as "missing" overstated the gap ~2×.

`evaluation/coverage_audit.py` computes everything below reproducibly
(`--live` refreshes the frozen cache; JSON written to
`out/cache/coverage_audit.json`).

## 2. Current state (post-P1/P2, verified live, 2026-08-18)

| Metric | Value |
|---|---|
| Chunks | 27,351 |
| Identified (all) | 15,852 (58.0%) |
| **Identified (substantive hl≥2)** | **14,001 / 16,990 (82.4%)** |
| hl1 floor | 10,361 (37.9%) |

**By collection (substantive):** commercial 99.9% · wb_state 99.9% · fssai
86.9% · animal 42.7% · env 41.5% · **criminal 10.6%**.

**By document type (substantive):** act 93.9% · regulation 77.1% · notification
48.4% · **rule 1.7% (24/1,378)** · circular 0%.

**Gap buckets (4,824 unidentified substantive chunks):**

| Bucket | Chunks | Meaning / remediation |
|---|---|---|
| `paren_fragment` | 4,025 | `(a)`/`(1)` body fragments that never repeat the identity → **fillable by propagation** |
| `prose` | 494 | plain continuation prose → fillable by propagation (same section/clause context) |
| `gazette_header` | 237 | "40 THE GAZETTE OF INDIA : EXTRAORDINARY" page headers → propagation-fillable or leave |
| `dotted_unstamped` | 61 | dotted clause the guard/derive missed → chunker/backfill rule gap |
| `rule_header` | 4 | dot-less rule headings → rule-identity pass |
| `stripped_ocr` / `short_noise` | 3 | space-stripped OCR / fragments |

**Root causes by document class** (per-document audit, worst offenders):

| Document | Type | Subst. | Identified | Root cause |
|---|---|---|---|---|
| FSS Amendment Act 3-2023 | act | 1,799 | 808 (45%) | **Zero extractable section headers** — the amendment's own numbering is absent from the chunk text; identity = *referenced* principal-Act section via in-text cross-refs (`In section 34, for sub-section (3)…` → sec=34). 991 fragments unfilled |
| SWM Rules 2026 | rule | 913 | 0 | Rule headings merged into table rows / dot-less (`4 Rural areas 1 With a population…`); form tables with cid-encoded glyphs |
| Bharatiya Nyaya Sanhita 2023 | act | 800 | 85 | **Space-stripped OCR text** (`Whoeverisengaged,orhired,oroffers…`) — every spaced regex fails; the 85 stamps are mostly cross-ref/gazette noise |
| Nutraceuticals Regulations | regulation | 399 | 0 | Romanized-Hindi bilingual text (`2- ifjHkk"kk,a%`); no dotted markers |
| FSS Amendment Act 2-2011 | act | 603 | 215 | Same as Amendment 3 |
| LLP Act 2008 (consolidated) | act | 395 | 86 | Headers exist (`50. Prosecution.`) but **mis-stamped** — in-text cross-refs (`…under section 49`) win over the leading header number; bodies unfilled |
| PCA Rules | rule | 187 | 0 | Form-based rules; no clean headers |
| Specific Relief Act | act | 161 | 12 | Same as LLP (consolidated edition with amendment footnotes) |
| Food Fortification / PWM / Organic / WB-infection | rule/reg | ~230 | 24 | No extractable headers |
| Food Additives Regulations | regulation | 1,295 | 1,288 (99%) | **Substantive content essentially complete**; the 22% headline is a 4,618-chunk hl1 floor of reversed-text/table OCR residue |

**Additional finding — `document_title`:** 12,820 chunks lack a title;
**12,819 are recoverable** from `document_uri` filenames
(`Food_Additives_Regulations-4.pdf` → `Food Additives Regulations`). G8
finding 6 confirmed.

## 3. Path to complete (quantified, in ROI order)

### P1 — `document_title` backfill · ✅ done (2026-08-18, applied live)
`scripts/backfill_document_title.py` derives `document_title` from
`document_uri` basenames (extension + `#fragment` stripped, `_` → space,
leading junk digits dropped when a capital word follows). **Applied live:
12,819 fills** (29 documents titled; 14,531 → 27,350 of 27,351 — one point
has no `document_uri`); DB mirrored (`LegalDocument.title` 29 +
`LegalChunk.metadata_json` 12,819). Never overwrites; identity-preserving.

### P2 — L7 propagation · ✅ done (2026-08-18, applied live) · substantive 71.6% → **82.4%**
`scripts/backfill_payload_identity.py` now runs a third mechanism after L4/L5:
`derive_l7` (header-trust correction + amendment anchors), plus an L1/L2/L3
**act-only gate** (see below). **Applied live: 2,075 updates** (L4 19 +
L5 180 + L7 correction 42 + L7 propagation 1,834); section coverage
11,243 → 13,318; commercial 92.1% → 99.9%, fssai 65.7% → 86.9%, act docs
78.6% → 93.9%. Spot-verified: LLP `50. Prosecution` 49 → 50; Amendment-3
fragments filled with their referenced section. **Latent bug fixed en route:**
L1/L2/L3 would have *re-stamped the stripped noise* — the 1,624 reg/rule/
notification chunks carry `provision_id` values like
`FSS_FOOD_ADDITIVES_REGULATIONS_…_SEC_41` built from the old page-number
stamps, and L1 derives `section_number` straight from `provision_id`.
`derive_section` is now gated to `document_type == "act"` (sections are Act
identity; non-act identity is `clause_number`, G8) — the re-stamp count went
1,624 → 0 with no act-chunk loss.

**Convergence (2026-08-18, second apply):** the first L7 apply produced 31
L4-vs-L7 disagreements — page-number/TOC fragments the ``N Word`` (space)
header form had filled (``3 THE AIR …``, ``2 Stoppage in transit``), plus two
dot-form cases (``39D.``/``76A.``). Fixed: the space form was dropped from
``_HEADER_TRUST_RE`` (29/31 conflicts were space-form), the L4 override loop
now runs **last** (its range-validated analysis wins over L5/L7 stamps in the
same apply), and L7 corrections skip L4-verified chunks. **Re-run is fully
idempotent: 0 changes** (verified).
The 4,025 `paren_fragment` + 494 `prose` chunks are exactly what
header-anchored propagation fills — but only when a boundary exists. Today
L5/L6 boundaries require an **L4/L6-verified header**, which these documents
do not have (amendments: zero headers; LLP/SR: headers exist but L4 does not
verify them). Simulated recovery (probe, doc-order propagation, never
overwrite, hl≥2 only):

| Document | +fills |
|---|---|
| FSS Amendment 3 | 988 |
| FSS Amendment 2 | 386 |
| LLP Act | 302 |
| Specific Relief Act | 130 |
| misc regulation docs | ~101 |
| **Total** | **1,907** |

Original design (as implemented in `derive_l7`):

1. **Header-trust fix (L4/L5):** when a chunk's text starts with `N.`, `N)`,
   or `N ` followed by a capital word AND the chunk is stamped, the leading
   number IS the section — in-text cross-references (`…under section 49`)
   must not override it. Fixes LLP/SR mis-stamps (sec=49 for `50.
   Prosecution.`) so boundaries are correct.
2. **Amendment-anchor propagation:** for documents with **zero**
   L4-verified headers, treat any stamped chunk as a running anchor (the
   referenced-section stamp *is* the correct identity for amendment text —
   an amendment to section 34 belongs to section 34). Never overwrite; reset
   at `PART`/`SCHEDULE`/`CHAPTER`.
3. Re-run the existing L5/L6 apply machinery; re-freeze the CE-v2 baseline.

**Excluded from P2:** BNS (criminal) — its 85 stamps are cross-ref/gazette
noise on space-stripped text; propagating them would spread false identity
(+486 simulated, low quality). BNS needs re-extraction (P3).

### P3 — Re-extract the broken-OCR documents (BNS 715 + rule docs ≈ 2,129) · 2–3 days · Medium
Space-stripped text (BNS) and table-merged rule headings (SWM/PCA/PWM) cannot
be repaired payload-side — the chunk text itself is wrong. These need
re-ingestion from the source PDFs with a better extraction strategy
(character-spacing reconstruction for BNS; table-aware layout for the rule
docs). Largest single recovery after P2. **Requires the source PDFs + a
re-ingest run (identity-preserving like `reingest_fssai_from_db.py`).**

### P4 — Nutraceuticals transliteration (≈399 substantive, partial) · 0.5 day · Low ROI
The bilingual Gazette text is Latin-transliterated Hindi with `\d+-` clause
markers. A transliteration pass could extract clause numbers for the English
half (~56 chunks already English-stamped); the Hindi half stays
unidentifiable. Low value (5% of the corpus) — do only if P3 is deferred.

### P5 — Noise-floor handling (10,361 hl1 incl. 4,618 reversed-text Food Additives) · 0.5 day
Report-only today (coverage is measured on substantive chunks — correct
already). Optional future: filter hl1 short/no-alnum/reversed chunks at
ingestion to improve retrieval precision and every downstream coverage %.
The reversed text (`etamatulg muidos-onoM .1` = "1. Mono-sodium glutamate")
is an extraction artifact that only a re-OCR fixes.

## 4. Honest "complete"

- **Without re-ingestion (P1 + P2) — DONE:** substantive identity coverage
  **71.6% → 82.4%** (14,001/16,990) + `document_title` 27,350/27,351
  (100% recoverable). The remaining 17.6% is structurally unfixable
  payload-side (broken-OCR rule docs ~1,414, BNS 715, Nutraceuticals 399,
  gazette/prose residue ~460).
- **With re-ingestion (P3):** ~82.4% → **~92% substantive**, the residual
  being bilingual Nutraceuticals, gazette page headers and the hl1 floor.
- 100% of the *raw* corpus is unattainable: 37.9% is hl1
  headers/fragments/OCR garbage that semantically has no identity — the
  honest metric is substantive coverage, and 83–92% is the real ceiling.

## 5. Recommended sequence

```
P1 (title backfill, do now) → P2 (L7 propagation, next) → re-freeze CE-v2 baseline
→ P3 (re-ingest broken OCR docs) → P4 (transliteration, optional) → P5 (noise filter, optional)
```

Tracked in `evaluation/CV2_IMPROVEMENT_PLAN.md` G8 checklist.
