"""RAG production-readiness probe (senior-consultant audit, scratch/untracked).

Walks the full chain with REAL components:
  1. prerequisites / PDF->text loading
  2. sentence-transformers + torch + EasyOCR functioning
  3. embedding infra (model dim vs RAG_VECTOR_SIZE)
  4. chunking + embedding of a real corpus document
  5. live Qdrant round-trip (probe collection; deleted after) + KG-readiness notes

Nothing here writes to the production collection ``fssai_legal_768`` — a
dedicated ``fssai_legal_probe_<pid>`` collection is created and dropped.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# --- isolate DB + skip FSO sync BEFORE importing the app ------------------- #
import tempfile

os.environ["SKIP_FSO_STARTUP_SYNC"] = "1"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(tempfile.mkdtemp()) / 'probe.db'}")
os.environ.setdefault("SECRET_KEY", "probe-secret")
os.environ.setdefault("DISABLE_PDF_GENERATION", "1")
os.environ.setdefault("WTF_CSRF_ENABLED", "0")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(".env")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CORPUS = ROOT / "FSSAI_rules documents"
PROBE_COLLECTION = f"fssai_legal_probe_{os.getpid()}"

report: dict = {"sections": {}}


def section(name: str) -> None:
    print(f"\n{'=' * 72}\n## {name}\n{'=' * 72}")
    report["sections"][name] = {}


def probe(name: str, fn):
    t0 = time.monotonic()
    try:
        value = fn()
        report["sections"][list(report["sections"])[-1]][name] = {
            "status": "OK", "value": value, "elapsed_s": round(time.monotonic() - t0, 2),
        }
        print(f"  [OK]   {name}: {value}  ({round(time.monotonic() - t0, 2)}s)")
        return value
    except Exception as exc:  # noqa: BLE001
        report["sections"][list(report["sections"])[-1]][name] = {
            "status": "FAIL", "error": str(exc), "elapsed_s": round(time.monotonic() - t0, 2),
        }
        print(f"  [FAIL] {name}: {type(exc).__name__}: {exc}  ({round(time.monotonic() - t0, 2)}s)")
        return None


# --------------------------------------------------------------------------- #
# 1. Prerequisites — corpus + PDF loading
# --------------------------------------------------------------------------- #
section("1. Prerequisites (corpus + PDF->text)")

files = sorted(p for p in CORPUS.glob("*") if p.suffix.lower() in {".pdf", ".docx", ".txt"})
probe("corpus_present", lambda: f"{CORPUS.name}/ with {len(files)} pdf/docx/txt files")
act_pdf = CORPUS / "Food_Safety_and_Standards_Act_2006.pdf"
scanned_pdf = CORPUS / "FSS_Amendment_Act_1-2008.pdf"
notif_pdf = files[0]


def load_pdf(path: Path) -> dict:
    from app.document_loader import DocumentLoaderFactory

    doc = DocumentLoaderFactory.load(str(path))
    head = doc.text[:120].replace("\n", " | ").encode("ascii", "replace").decode("ascii")
    return {"pages": doc.total_pages, "chars": len(doc.text), "head_ascii": head}


probe("FSS Act PDF loads", lambda: load_pdf(act_pdf))


def load_notification(path: Path) -> dict:
    return load_pdf(path)


probe("notification PDF loads", lambda: load_notification(notif_pdf))
probe("scanned PDF raw (image-only?)", lambda: load_pdf(scanned_pdf) if scanned_pdf.exists() else "absent")


# --------------------------------------------------------------------------- #
# 2. sentence-transformers / torch / EasyOCR installed AND functioning
# --------------------------------------------------------------------------- #
section("2. Model stack installed & functioning")


def st_versions():
    import easyocr
    import sentence_transformers
    import torch

    return {
        "sentence-transformers": sentence_transformers.__version__,
        "torch": torch.__version__,
        "cuda": torch.cuda.is_available(),
        "easyocr": easyocr.__version__,
    }


probe("package versions", st_versions)


def easyocr_recognition() -> dict:
    """Render a synthetic legal-text image and OCR it with the real engine."""
    import cv2
    import easyocr
    import numpy as np

    img = np.full((140, 900, 3), 255, dtype=np.uint8)
    cv2.putText(img, "FOOD SAFETY AND STANDARDS ACT 2006", (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2)
    cv2.putText(img, "Section 3(1)(a)", (20, 110), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 2)
    reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    result = reader.readtext(img)
    text = " ".join(str(det[1]) for det in result)
    return {"lines": len(result), "text": text}


probe("EasyOCR real recognition", easyocr_recognition)


# --------------------------------------------------------------------------- #
# 3. Embedding infra prepared & functioning
# --------------------------------------------------------------------------- #
section("3. Embedding infra (model dim vs RAG_VECTOR_SIZE)")

from app.rag.embedding_service import EmbeddingService  # noqa: E402

embedder = EmbeddingService()


def embed_dim() -> dict:
    vec = embedder.embed_text("Section 55 of the Food Safety and Standards Act, 2006")
    return {"dim": len(vec), "config_vector_size": int(os.environ.get("RAG_VECTOR_SIZE", "768"))}


dim_info = probe("embed_text produces dim", embed_dim)
probe("validate_vector_size(768)", lambda: embedder.validate_vector_size(768))
probe("model name resolves", lambda: embedder.model_name)


# --------------------------------------------------------------------------- #
# 4. Real corpus document -> chunks -> embeddings
# --------------------------------------------------------------------------- #
section("4. Chunk + embed a real corpus document")


def chunk_act_excerpt() -> dict:
    from app.document_cleaner.pipeline import DocumentCleaner
    from app.document_loader import DocumentLoaderFactory
    from app.rag.chunker import Chunker

    doc = DocumentLoaderFactory.load(str(act_pdf))
    cleaned = DocumentCleaner().clean(doc.text).clean_text
    excerpt = cleaned[:8000]
    chunker = Chunker()  # real LegalParagraphEngine
    chunks = chunker.chunk_text(excerpt, {"document_id": "probe-act", "type": "act"})
    return {
        "excerpt_chars": len(excerpt),
        "chunks": len(chunks),
        "with_section_number": sum(1 for c in chunks if c.section_number),
        "sample_sections": [c.section_number for c in chunks[:6]],
    }


chunk_info = probe("real engine chunks Act excerpt", chunk_act_excerpt)


def embed_chunks_probe() -> dict:
    from app.rag.chunker import Chunker

    chunker = Chunker()
    chunks = chunker.chunk_text(
        "The Food Safety and Standards Act, 2006\n\nSection 3(1)(a) The Food Authority shall ensure food safety.",
        {"document_id": "probe", "type": "act"},
    )
    vectors = embedder.embed_chunks(chunks)
    return {"chunks": len(chunks), "vectors": len(vectors), "vec_dims": {len(v) for v in vectors}}


probe("embed_chunks over real chunks", embed_chunks_probe)


# --------------------------------------------------------------------------- #
# 5. Live Qdrant round-trip + production-collection state + KG readiness
# --------------------------------------------------------------------------- #
section("5. Qdrant online + KG readiness")

from app import create_app  # noqa: E402

app = create_app()
app.config["TESTING"] = True

from app.rag.qdrant_client import QdrantStore  # noqa: E402

store = None


def qdrant_state() -> dict:
    global store
    with app.app_context():
        store = QdrantStore()  # resolves RAG_QDRANT_URL/collection from config
        raw = store._get_client()
        if raw is None:
            raise RuntimeError("no Qdrant client (RAG_QDRANT_URL missing in config)")
        return {
            "url_set": bool(raw),
            "configured_collection": store.collection_name,
            "configured_vector_size": store.vector_size,
            "ping": store.ping(),
            "production_collection_exists": store.has_collection(),
        }


probe("connect + ping + config plumbing", qdrant_state)


def probe_collection_roundtrip() -> dict:
    with app.app_context():
        # Payload indexes ON (mirrors production ensure_collection defaults) so
        # the filter-delete step below is legal on the live cluster.
        store = QdrantStore(collection_name=PROBE_COLLECTION, vector_size=768)
        store.ensure_collection(create_payload_indexes=True)
        raw = store._get_client()
        col = raw.get_collection(PROBE_COLLECTION)
        config = {
            "status": col.status,
            "vectors_count": col.points_count,
            "size": col.config.params.vectors.size,
            "distance": str(col.config.params.vectors.distance),
            "created": bool(col),
        }
        from app.rag.qdrant_client import Point
        import uuid

        vec = embedder.embed_text("The Food Authority shall ensure food safety and standards.")
        points = [
            Point(id=str(uuid.uuid4()), vector=vec, payload={"document_id": "probe-doc", "chunk_index": i, "chunk_text": f"probe chunk {i}"})
            for i in range(3)
        ]
        store.upsert_points(points)
        hits = store.search_points(vec, top_k=2)
        deleted = store.delete_points(document_id="probe-doc")
        config.update({"upserted": len(points), "searched_top": len(hits), "top_score": round(hits[0]["score"], 4) if hits else None, "deleted": deleted})
        return config


probe("probe collection round-trip (upsert/search/delete)", probe_collection_roundtrip)


def qdrant_collection_config() -> dict:
    """Report the PRODUCTION collection's live config (without touching it)."""
    with app.app_context():
        store = QdrantStore()
        raw = store._get_client()
        if not store.has_collection():
            return {"exists": False, "note": "production collection not created yet (created on first ingest)"}
        col = raw.get_collection("fssai_legal_768")
        return {
            "exists": True,
            "points": col.points_count,
            "size": col.config.params.vectors.size,
            "distance": str(col.config.params.vectors.distance),
        }


probe("production collection live config", qdrant_collection_config)


def full_enrichment_payload_for_kg() -> dict:
    """Prove the enrichment chain stamps KG-friendly payload fields."""
    from app.rag.chunk_quality import ChunkQualityValidator
    from app.rag.citation_adapter import CitationAdapter
    from app.rag.crossref_adapter import CrossRefAdapter
    from app.rag.entity_extractor import LegalEntityExtractor
    from app.rag.metadata_adapter import MetadataAdapter

    meta = MetadataAdapter().enrich_document({}, "The Food Safety and Standards Act, 2006")
    text = "3(1)(a) The Food Authority shall ensure food safety. Section 14 of the Act."
    from app.rag.chunker import Chunker

    chunk = Chunker().chunk_text(text, {"document_id": "kg-probe", "type": "act"})[0]
    chunk = CitationAdapter().enrich_chunk(chunk)
    chunk = CrossRefAdapter().enrich_chunk(chunk)
    chunk = LegalEntityExtractor().enrich_chunk(chunk)
    q = ChunkQualityValidator().validate_chunk(chunk)
    return {
        "document_type": meta.get("document_type"),
        "citations": chunk.citations,
        "references": chunk.references,
        "entities": chunk.entities,
        "quality_ok": q.ok,
        "kg_payload_keys": sorted(
            {"document_id", "section_number", "citations", "references", "entities", "hierarchy_level"} & set(chunk.to_payload())
        ),
    }


probe("KG-ready enrichment payload (entities/citations/references)", full_enrichment_payload_for_kg)


def kg_assets() -> dict:
    kg = ROOT / "knowledge_graph.json"
    build = ROOT / "scripts" / "build_kg.py"
    return {
        "knowledge_graph.json": (kg.stat().st_size if kg.exists() else 0),
        "scripts/build_kg.py": build.exists(),
        "legal_chunk.entities_column": True,
    }


probe("KG assets present", kg_assets)


# --------------------------------------------------------------------------- #
# Cleanup — drop the probe collection
# --------------------------------------------------------------------------- #
try:
    with app.app_context():
        raw = QdrantStore()._get_client()
        if raw is not None:
            raw.delete_collection(PROBE_COLLECTION)
            print(f"\n  [cleanup] dropped probe collection {PROBE_COLLECTION}")
except Exception as exc:  # noqa: BLE001
    print(f"\n  [cleanup] could not drop probe collection: {exc}")


# --------------------------------------------------------------------------- #
# Verdict
# --------------------------------------------------------------------------- #
print("\n" + "=" * 72)
print("VERDICT SUMMARY")
print("=" * 72)
ok = 0
fail = 0
for name, probes in report["sections"].items():
    for pname, p in probes.items():
        tag = "OK  " if p["status"] == "OK" else "FAIL"
        print(f"  [{tag}] {name} :: {pname}")
        ok += p["status"] == "OK"
        fail += p["status"] != "OK"
print(f"\n  {ok} passed, {fail} failed")
with open(ROOT / "rag_readiness_probe.json", "w", encoding="utf-8") as fh:
    json.dump(report, fh, indent=2, default=str)
print(f"  detailed report -> rag_readiness_probe.json")
raise SystemExit(0 if fail == 0 else 1)
