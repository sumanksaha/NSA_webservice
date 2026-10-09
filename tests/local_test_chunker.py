import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

START = time.monotonic()

try:
    from app.rag.chunker import (
        Chunk,
        _extract_markdown_section_title,
        _markdown_heading_level,
        _propagate_sections,
    )
    print(f"imports OK after {time.monotonic() - START:.2f}s", file=sys.stderr)

    assert _extract_markdown_section_title("## Seizure") == "Seizure"
    assert _extract_markdown_section_title("## Sampling") == "Sampling"
    assert _extract_markdown_section_title("# FSO seizure & sampling workflow") == "FSO seizure & sampling workflow"
    assert _extract_markdown_section_title("body text") is None
    assert _markdown_heading_level("## Seizure") == 2
    assert _markdown_heading_level("## Sampling") == 2
    assert _markdown_heading_level("body text") == 0
    assert _markdown_heading_level("# Heading") == 1

    chunks = [
        Chunk(chunk_id="h1", document_id="d1", chunk_index=0, chunk_text="## Seizure", section_title="Seizure", section_number=None, hierarchy_level=0),
        Chunk(chunk_id="b1", document_id="d1", chunk_index=1, chunk_text="When food safety officer seizes an item, he provides receipt in form II, regulation 2.3.1.", section_title=None, section_number=None, hierarchy_level=0),
        Chunk(chunk_id="b2", document_id="d1", chunk_index=2, chunk_text="order in form III, regulation 2.3.2(1).", section_title=None, section_number=None, hierarchy_level=0),
        Chunk(chunk_id="h2", document_id="d1", chunk_index=3, chunk_text="## Sampling", section_title="Sampling", section_number=None, hierarchy_level=0),
        Chunk(chunk_id="b3", document_id="d1", chunk_index=4, chunk_text="FSO provides notice in Form V, regulation 2.4.1.3.", section_title=None, section_number=None, hierarchy_level=0),
    ]
    _inherited = _propagate_sections(chunks)
    assert _inherited == 2, f"expected 2 inherited, got {_inherited}"
    assert chunks[1].section_title == "Seizure", chunks[1].section_title
    assert chunks[2].section_title == "Seizure", chunks[2].section_title
    assert chunks[3].section_title == "Sampling", chunks[3].section_title
    assert chunks[4].section_title == "Sampling", chunks[4].section_title
    assert chunks[0].section_title == "Seizure"

    chunks2 = [
        Chunk(chunk_id="s1", document_id="d2", chunk_index=0, chunk_text="5. Compensation", section_title="Compensation", section_number="5", hierarchy_level=3),
        Chunk(chunk_id="c1", document_id="d2", chunk_index=1, chunk_text="Some body text that continues the section.", section_title=None, section_number=None, hierarchy_level=0),
    ]
    _inherited2 = _propagate_sections(chunks2)
    assert _inherited2 == 1
    assert chunks2[1].section_title == "Compensation"

    print("ALL CHUNKER TESTS PASSED")
except Exception:
    traceback.print_exc()
    raise SystemExit(1) from None
