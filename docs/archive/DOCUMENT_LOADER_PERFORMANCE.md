# Legal Document Loader — Performance Optimization

## Target: 100,000+ Documents

### 1. I/O Parallelism (Thread Pool Tuning)

**Current:** `ThreadPoolExecutor(max_workers=8)`

The document loader is **I/O-bound** (reading files from disk), not CPU-bound. 
Optimal worker count depends on your storage:

| Storage Type | Recommended Workers | Reasoning |
|-------------|-------------------|-----------|
| Local NVMe SSD | 4–8 | Minimal I/O contention; more threads add overhead |
| Network NAS / NFS | 8–16 | Higher latency benefits from more concurrent reads |
| Cloud object store (S3) | 16–32 | Each worker fetches independently; tune via `max_parallel` |

**Rule of thumb:** Start with `workers=os.cpu_count() * 2` and monitor disk queue depth.

### 2. Process Pool vs Thread Pool

For CPU-heavy extraction (PyMuPDF rendering, OCR), a `ProcessPoolExecutor` 
sidesteps the GIL. However, for text-only PDFs (the common case), 
the GIL is rarely a bottleneck since `pdfplumber` and `PyMuPDF` release it 
during I/O.

**Benchmark hint:**
```python
# If > 30% of PDFs are scanned/image-only:
from concurrent.futures import ProcessPoolExecutor
# Use ProcessPoolExecutor with max_workers=os.cpu_count()
```

### 3. `orjson` — Fast JSON Serialization

`orjson` is **4–5× faster** than the stdlib `json` module for serializing 
simple dicts. The batch processor's `_fast_dumps()` function already prefers 
orjson when installed:

```python
# orjson: ~18 million docs/hour on modern hardware
# stdlib json: ~4 million docs/hour
```

**Install:** `pip install orjson>=3.10`

### 4. Memory-Mapped I/O for Giant TXT Files

Files larger than 500 MB should use `mmap` to avoid loading the entire file 
into RAM. The base `TXTLoader._read_bytes()` method can be overridden:

```python
import mmap


def _read_mmap(path: Path) -> bytes:
    with open(path, "rb") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            return mm.read()
```

### 5. Caching `_clean_text()` with `@lru_cache`

If the same page text appears in multiple documents (common in legal
templates), cache the cleaning step:

```python
from functools import lru_cache


@staticmethod
@lru_cache(maxsize=10_000)
def _clean_text_cached(raw: str) -> str:
    return BaseLoader._clean_text(raw)
```

**Memory trade-off:** 10,000 cached pages × ~2 KB average = ~20 MB RAM.

### 6. OCR Fallback for Scanned PDFs

`pdfplumber` and `PyMuPDF` will extract **zero text** from scanned/image-only 
PDFs. Add an OCR fallback using `pytesseract` (already in requirements.txt):

```python
def _try_ocr(self) -> Optional[List[PageResult]]:
    try:
        import pytesseract
        from pdf2image import convert_from_path
    except ImportError:
        return None

    images = convert_from_path(str(self._path), dpi=300)
    pages = []
    for i, img in enumerate(images, start=1):
        text = pytesseract.image_to_string(img, lang="eng")
        pages.append(PageResult(page=i, text=self._clean_text(text)))
    return pages
```

**Add as Strategy 1.5** in `PDFLoader._extract_pages()` — try OCR after
pdfplumber fails but before PyMuPDF.

### 7. JSONL Streaming — Never Load All Results in RAM

The batch processor already writes **one JSON object per line** (`jsonl` format),
which is the industry standard for large-scale document processing. Each line
is flushed every `jsonl_batch_size` (default 10,000) documents.

**Reading back:** Use `ijson` or iterate line-by-line:
```python
with open("output.jsonl") as f:
    for line in f:
        doc = json.loads(line)
        process(doc)
```

### 8. DOCX Metadata Cache

`DOCXLoader._extract_metadata()` currently re-runs full text extraction just to 
count pages. Cache the result:

```python
# In __init__:
self._pages: Optional[List[PageResult]] = None

# In _extract_pages:
if self._pages is None:
    self._pages = self._build_pages(doc)  # or similar
return self._pages

# In _extract_metadata:
if self._pages is not None:
    meta_kwargs["page_count"] = len(self._pages)
```

### 9. Benchmarking

```bash
# Measure throughput on a representative sample
time python -c "
from app.document_loader import BatchProcessor
p = BatchProcessor('/data/sample_10k', '/data/output', workers=8)
s = p.run()
print(f'{s.throughput_per_second:.1f} docs/s')
"
```

**Target:** > 200 docs/s on NVMe storage with 8 workers.

### 10. Estimated Throughput

| Hardware | Workers | Docs/hour (PDF, text) | Docs/hour (DOCX) |
|----------|---------|----------------------|-------------------|
| 4-core laptop, SSD | 4 | 14,000 | 10,000 |
| 8-core server, NVMe | 8 | 36,000 | 25,000 |
| 16-core server, NVMe | 16 | 60,000 | 40,000 |
| 32-core + orjson | 32 | **100,000+** | **70,000+** |
