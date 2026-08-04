"""Tests for the Phase 7 dynamic Table of Contents generator.

Covers:
1. Heading extraction (h1-h6): document order, nested markup, empty-skip,
   hierarchical numbering, level-jump numbering, anchor IDs
2. Nested TOC HTML generation: balanced <ol>/<li> nesting, siblings,
   multi-level jumps (h1 -> h3 -> h1), numbered labels
3. Heading annotation: id injection, existing-id and empty-heading skips
4. Full annotate_html pass: placeholder injection, no-op cases,
   defensive failure on broken HTML
5. generate_toc_data: JSON-safe report keys/values including ``tag``
6. Annexure / appendix marker detection: flagging markers, rejecting
   plurals/descriptive titles, badge rendering, report counts
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from app.toc_generator import TocEntry, TocGeneratorEngine, generate_toc_data

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_nesting_balanced(html: str) -> None:
    """Assert all <ol>/<li> tags are correctly nested (LIFO matched)."""
    stack: list[str] = []
    for closing, tag in re.findall(r"<(/?)(ol|li)\b[^>]*>", html):
        if closing:
            assert stack and stack[-1] == tag, (
                f"Unexpected </{tag}> with " f"{stack[-1] if stack else 'empty stack'} still open"
            )
            stack.pop()
        else:
            stack.append(tag)
    assert stack == [], f"Unclosed tags: {stack}"


class _TreeBuilder(HTMLParser):
    """Build a small DOM tree (ol/li only) for structural assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.root: dict = {"tag": "root", "text": "", "children": []}
        self._stack: list[dict] = [self.root]

    def handle_starttag(self, tag: str, attrs) -> None:
        node = {"tag": tag, "text": "", "children": []}
        self._stack[-1]["children"].append(node)
        if tag in ("ol", "li"):
            self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("ol", "li"):
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        self._stack[-1]["text"] += data

    @staticmethod
    def lis(node: dict) -> list[dict]:
        return [c for c in node["children"] if c["tag"] == "li"]

    @staticmethod
    def ols(node: dict) -> list[dict]:
        return [c for c in node["children"] if c["tag"] == "ol"]


def _toc_tree(html_doc: str) -> dict:
    """Extract entries from ``html_doc`` and return the parsed TOC tree."""
    engine = TocGeneratorEngine()
    toc_html = engine.build_toc_html(engine.extract_toc(html_doc))
    builder = _TreeBuilder()
    builder.feed(toc_html)
    builder.close()
    return builder.root


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


class TestExtraction:
    def test_extracts_headings_in_document_order(self):
        html = "<h1>Intro</h1><p>body</p><h2>Background</h2><h3>Legal</h3>"
        entries = TocGeneratorEngine().extract_toc(html)
        assert [e.text for e in entries] == ["Intro", "Background", "Legal"]
        assert [e.level for e in entries] == [1, 2, 3]
        assert [e.tag for e in entries] == ["h1", "h2", "h3"]

    def test_extracts_text_from_nested_markup(self):
        html = "<h1><span>Intro</span> <em>Part</em></h1>"
        entries = TocGeneratorEngine().extract_toc(html)
        assert entries[0].text == "Intro Part"

    def test_skips_empty_headings(self):
        html = "<h1></h1><h2>Real</h2>"
        entries = TocGeneratorEngine().extract_toc(html)
        assert len(entries) == 1
        assert entries[0].text == "Real"

    def test_empty_and_malformed_input_return_no_entries(self):
        engine = TocGeneratorEngine()
        assert engine.extract_toc("") == []
        assert engine.extract_toc(None) == []
        assert engine.extract_toc("<h1>never closed") == []

    def test_hierarchical_numbering(self):
        html = "<h1>A</h1><h2>B</h2><h3>C</h3><h2>D</h2><h3>E</h3><h1>F</h1>"
        entries = TocGeneratorEngine().extract_toc(html)
        assert [e.number for e in entries] == ["1", "1.1", "1.1.1", "1.2", "1.2.1", "2"]

    def test_numbering_with_level_jump(self):
        html = "<h1>A</h1><h3>Deep</h3><h1>B</h1>"
        entries = TocGeneratorEngine().extract_toc(html)
        assert [e.number for e in entries] == ["1", "1.0.1", "2"]

    def test_unique_anchor_ids_and_hrefs(self):
        html = "<h1>A</h1><h2>B</h2>"
        entries = TocGeneratorEngine().extract_toc(html)
        assert [e.heading_id for e in entries] == ["toc-1", "toc-2"]
        assert [e.href for e in entries] == ["#toc-1", "#toc-2"]


# ---------------------------------------------------------------------------
# TOC HTML generation
# ---------------------------------------------------------------------------


class TestBuildTocHtml:
    def test_empty_entries_returns_empty_list(self):
        assert TocGeneratorEngine().build_toc_html([]) == '<ol class="toc-list"></ol>'

    def test_single_entry(self):
        html = TocGeneratorEngine().build_toc_html(TocGeneratorEngine().extract_toc("<h1>Title</h1>"))
        _assert_nesting_balanced(html)
        assert 'class="toc-list"' in html
        assert 'href="#toc-1"' in html
        assert "Title" in html
        assert '<span class="toc-number">1</span>' in html

    def test_nested_structure_for_heading_chain(self):
        html_doc = "<h1>A</h1><h2>B</h2><h3>C</h3><h2>D</h2><h3>E</h3><h1>F</h1>"
        toc_html = TocGeneratorEngine().build_toc_html(TocGeneratorEngine().extract_toc(html_doc))
        _assert_nesting_balanced(toc_html)

        root = _toc_tree(html_doc)["children"][0]
        assert root["tag"] == "ol"
        lis = _TreeBuilder.lis(root)
        assert len(lis) == 2  # A and F are top-level siblings
        li_a, li_f = lis
        assert "A" in li_a["text"]
        assert "F" in li_f["text"]

        # A contains one sub-list holding B and D as siblings
        sub_a = _TreeBuilder.ols(li_a)[0]
        assert len(_TreeBuilder.lis(sub_a)) == 2
        li_b, li_d = _TreeBuilder.lis(sub_a)
        # ... and each of B and D contains its own single-item sub-list
        assert "C" in _TreeBuilder.lis(_TreeBuilder.ols(li_b)[0])[0]["text"]
        assert "E" in _TreeBuilder.lis(_TreeBuilder.ols(li_d)[0])[0]["text"]

    def test_multi_level_jump_keeps_toplevel_siblings(self):
        # Regression test: h1 -> h3 -> h1 previously emitted malformed HTML
        # where the extra closing </ol> closed the root list early and nested
        # the second h1 inside the first.
        html_doc = "<h1>A</h1><h3>Deep</h3><h1>B</h1>"
        toc_html = TocGeneratorEngine().build_toc_html(TocGeneratorEngine().extract_toc(html_doc))
        _assert_nesting_balanced(toc_html)

        root = _toc_tree(html_doc)["children"][0]
        lis = _TreeBuilder.lis(root)
        assert len(lis) == 2  # B is a top-level sibling, not nested in A
        assert "A" in lis[0]["text"]
        assert "B" in lis[1]["text"]
        # Deep hangs directly under A's single sub-list; B is a leaf
        assert "Deep" in _TreeBuilder.lis(_TreeBuilder.ols(lis[0])[0])[0]["text"]
        assert _TreeBuilder.ols(lis[1]) == []

    def test_three_level_jump_keeps_nesting_valid(self):
        # Jump of 3 (h1 -> h4 -> h1) must also produce balanced, correctly
        # nested output with the second h1 as a top-level sibling.
        html_doc = "<h1>A</h1><h4>Deep</h4><h1>B</h1>"
        toc_html = TocGeneratorEngine().build_toc_html(TocGeneratorEngine().extract_toc(html_doc))
        _assert_nesting_balanced(toc_html)

        root = _toc_tree(html_doc)["children"][0]
        lis = _TreeBuilder.lis(root)
        assert len(lis) == 2
        assert "Deep" in _TreeBuilder.lis(_TreeBuilder.ols(lis[0])[0])[0]["text"]
        assert _TreeBuilder.ols(lis[1]) == []

    def test_same_level_siblings_share_sub_list(self):
        html_doc = "<h1>A</h1><h2>B</h2><h2>B2</h2>"
        toc_html = TocGeneratorEngine().build_toc_html(TocGeneratorEngine().extract_toc(html_doc))
        _assert_nesting_balanced(toc_html)

        root = _toc_tree(html_doc)["children"][0]
        sub = _TreeBuilder.ols(_TreeBuilder.lis(root)[0])[0]
        sub_lis = _TreeBuilder.lis(sub)
        assert len(sub_lis) == 2
        assert "B" in sub_lis[0]["text"]
        assert "B2" in sub_lis[1]["text"]

    def test_labels_include_number_and_level_class(self):
        toc_html = TocGeneratorEngine().build_toc_html(TocGeneratorEngine().extract_toc("<h1>A</h1><h2>B</h2>"))
        assert 'class="toc-item level-1"' in toc_html
        assert 'class="toc-item level-2"' in toc_html
        assert '<span class="toc-number">1.1</span>' in toc_html


# ---------------------------------------------------------------------------
# Heading annotation
# ---------------------------------------------------------------------------


class TestAnnotateHeadings:
    def test_adds_ids_to_headings(self):
        html = "<h1>Intro</h1><h2>Background</h2>"
        entries = TocGeneratorEngine().extract_toc(html)
        result = TocGeneratorEngine().annotate_headings(html, entries)
        assert 'id="toc-1"' in result
        assert 'id="toc-2"' in result

    def test_skips_headings_with_existing_ids(self):
        html = '<h1 id="custom">Intro</h1>'
        entries = TocGeneratorEngine().extract_toc(html)
        result = TocGeneratorEngine().annotate_headings(html, entries)
        assert 'id="custom"' in result
        assert 'id="toc-' not in result

    def test_skips_empty_headings(self):
        html = "<h1></h1><h2>Real</h2>"
        entries = TocGeneratorEngine().extract_toc(html)
        result = TocGeneratorEngine().annotate_headings(html, entries)
        # only the non-empty h2 receives the next id (toc-1)
        assert 'id="toc-1"' in result
        assert result.count("id=") == 1

    def test_unchanged_without_entries(self):
        html = "<h1>Intro</h1>"
        assert TocGeneratorEngine().annotate_headings(html, []) == html


# ---------------------------------------------------------------------------
# Full annotation pass
# ---------------------------------------------------------------------------


class TestAnnotateHtml:
    def test_injects_toc_into_placeholder(self):
        html = "<div data-toc></div><h1>Intro</h1><h2>Background</h2>"
        result = TocGeneratorEngine().annotate_html(html)
        assert '<nav class="toc-nav"' in result
        assert 'aria-label="Table of Contents"' in result
        assert 'class="toc-list"' in result
        assert "data-toc" not in result
        assert 'id="toc-1"' in result
        # The injected <nav> wraps the nested TOC, so the whole result must
        # still be structurally valid.
        _assert_nesting_balanced(result)

    def test_no_placeholder_still_annotates_headings(self):
        html = "<h1>Intro</h1><h2>Background</h2>"
        result = TocGeneratorEngine().annotate_html(html)
        assert '<nav class="toc-nav"' not in result
        assert 'id="toc-1"' in result

    def test_no_headings_returns_input_unchanged(self):
        html = "<div data-toc></div><p>No headings here</p>"
        assert TocGeneratorEngine().annotate_html(html) == html

    def test_empty_input_returns_input_unchanged(self):
        assert TocGeneratorEngine().annotate_html("") == ""

    def test_broken_html_does_not_raise(self):
        result = TocGeneratorEngine().annotate_html("<div data-toc></div><h1>Unclosed")
        assert isinstance(result, str)
        assert "Unclosed" in result


# ---------------------------------------------------------------------------
# JSON-safe report
# ---------------------------------------------------------------------------


class TestGenerateTocData:
    def test_report_keys(self):
        html = "<div data-toc></div><h1>A</h1><h2>B</h2>"
        data = TocGeneratorEngine().generate_toc_data(html)
        assert set(data) == {
            "entries",
            "total_headings",
            "total_annexures",
            "max_depth",
            "has_toc_placeholder",
        }

    def test_report_annexure_counts(self):
        html = "<h1>ANNEXURE A</h1><h2>GROUNDS</h2><h1>APPENDIX I</h1>"
        data = TocGeneratorEngine().generate_toc_data(html)
        assert data["total_annexures"] == 2
        assert [e["is_annexure"] for e in data["entries"]] == [True, False, True]

    def test_report_values(self):
        html = "<div data-toc></div><h1>A</h1><h2>B</h2><h3>C</h3>"
        data = TocGeneratorEngine().generate_toc_data(html)
        assert data["total_headings"] == 3
        assert data["max_depth"] == 3
        assert data["has_toc_placeholder"] is True
        assert [e["number"] for e in data["entries"]] == ["1", "1.1", "1.1.1"]

    def test_entry_fields_include_tag(self):
        html = "<h1>A</h1><h2>B</h2>"
        data = TocGeneratorEngine().generate_toc_data(html)
        first, second = data["entries"]
        assert first["tag"] == "h1"
        assert second["tag"] == "h2"
        assert first["heading_id"] == "toc-1"
        assert first["href"] == "#toc-1"
        assert first["level"] == 1

    def test_no_placeholder_flag(self):
        data = TocGeneratorEngine().generate_toc_data("<h1>A</h1>")
        assert data["has_toc_placeholder"] is False

    def test_empty_html(self):
        data = TocGeneratorEngine().generate_toc_data("")
        assert data["total_headings"] == 0
        assert data["max_depth"] == 0
        assert data["entries"] == []

    def test_module_level_wrapper(self):
        data = generate_toc_data("<h1>A</h1><h2>B</h2>")
        assert data["total_headings"] == 2

    def test_toc_entry_dataclass_defaults(self):
        entry = TocEntry(level=1, text="X", heading_id="toc-1")
        assert entry.level == 1
        assert entry.text == "X"
        assert entry.heading_id == "toc-1"
        assert entry.number == ""
        assert entry.href == ""
        assert entry.tag == ""
        assert entry.is_annexure is False


# ---------------------------------------------------------------------------
# Annexure / appendix marker detection
# ---------------------------------------------------------------------------


class TestAnnexureDetection:
    def test_flags_annexure_markers(self):
        html = (
            "<h1>ANNEXURE A</h1>"
            "<h2>ANNEXURE - B</h2>"
            "<h3>APPENDIX I</h3>"
            "<h1>ANNEXURE 1</h1>"
            "<h2>Annexure</h2>"
        )
        entries = TocGeneratorEngine().extract_toc(html)
        assert [e.text for e in entries] == [
            "ANNEXURE A",
            "ANNEXURE - B",
            "APPENDIX I",
            "ANNEXURE 1",
            "Annexure",
        ]
        assert all(e.is_annexure for e in entries)

    def test_flags_bracketed_roman_marker(self):
        entries = TocGeneratorEngine().extract_toc("<h2>APPENDIX [I]</h2>")
        assert len(entries) == 1
        assert entries[0].is_annexure is True

    def test_flags_short_identifiers(self):
        # Multi-digit and two-letter identifiers must be flagged (regression
        # guard for the open-ended [a-z0-9]+ bug that also matched the word
        # in "Annexure Management").
        entries = TocGeneratorEngine().extract_toc("<h1>ANNEXURE 10</h1><h2>ANNEXURE AB</h2>")
        assert [e.text for e in entries] == ["ANNEXURE 10", "ANNEXURE AB"]
        assert all(e.is_annexure for e in entries)

    def test_does_not_flag_regular_headings(self):
        html = "<h1>STATEMENT OF FACTS</h1>" "<h2>GROUNDS</h2>" "<h3>PRAYER</h3>" "<h2>Photographic Evidence</h2>"
        entries = TocGeneratorEngine().extract_toc(html)
        assert entries
        assert all(not e.is_annexure for e in entries)

    def test_does_not_flag_plurals_or_titles(self):
        # "ANNEXURES" is a section that lists annexures, not an annexure
        # itself; "Annexure Management" is UI copy, not a marker.
        html = "<h1>ANNEXURES</h1>" "<h2>LIST OF ANNEXURES</h2>" "<h3>Annexure Management</h3>" "<h4>APPENDICES</h4>"
        entries = TocGeneratorEngine().extract_toc(html)
        assert len(entries) == 4
        assert all(not e.is_annexure for e in entries)

    def test_build_toc_html_adds_class_and_badge(self):
        html_doc = "<h1>ANNEXURE A</h1><h2>GROUNDS</h2>"
        toc_html = TocGeneratorEngine().build_toc_html(TocGeneratorEngine().extract_toc(html_doc))
        _assert_nesting_balanced(toc_html)
        assert 'class="toc-item level-1 toc-annexure"' in toc_html
        assert '<span class="toc-annexure-badge">Annexure</span>' in toc_html
        # The regular heading has no badge or annexure class.
        assert 'class="toc-item level-2"' in toc_html
        assert toc_html.count("toc-annexure-badge") == 1

    def test_annotate_html_includes_annexure_badge(self):
        html = "<div data-toc></div>" "<h1>ANNEXURE A</h1>" "<h1>STATEMENT OF FACTS</h1>"
        result = TocGeneratorEngine().annotate_html(html)
        _assert_nesting_balanced(result)
        assert 'class="toc-item level-1 toc-annexure"' in result
        assert '<span class="toc-annexure-badge">Annexure</span>' in result
        assert 'id="toc-1"' in result


# ---------------------------------------------------------------------------
# WeasyPrint PDF bookmarks (navigable outline)
# ---------------------------------------------------------------------------


class TestPdfBookmarks:
    def test_injects_bookmark_css_after_head(self):
        from app.utils.pdf_utils import _inject_bookmark_css

        html = "<html><head><title>T</title></head><body><h1>Intro</h1></body></html>"
        result = _inject_bookmark_css(html)
        assert result.index("<style>") == result.index("<head>") + len("<head>")
        assert "bookmark-level: 1" in result

    def test_injects_bookmark_css_after_html_when_no_head(self):
        from app.utils.pdf_utils import _inject_bookmark_css

        html = "<html><body><h1>Intro</h1></body></html>"
        result = _inject_bookmark_css(html)
        assert result.startswith("<html><style>")
        assert "bookmark-level: 6" in result

    def test_prepends_style_for_bare_fragment(self):
        from app.utils.pdf_utils import _inject_bookmark_css

        result = _inject_bookmark_css("<h2>Section</h2>")
        assert result.startswith("<style>")
        assert "bookmark-level: 2" in result

    def test_all_six_levels_covered(self):
        from app.utils.pdf_utils import _inject_bookmark_css

        result = _inject_bookmark_css("<h1>a</h1>")
        for level in range(1, 7):
            assert f"bookmark-level: {level}" in result

    def test_skips_when_no_headings(self):
        from app.utils.pdf_utils import _inject_bookmark_css

        html = "<html><body><p>No headings</p></body></html>"
        assert _inject_bookmark_css(html) == html

    def test_post_process_injects_bookmarks_with_headings(self):
        from app.utils.pdf_utils import post_process_pdf_html

        html = "<html><head></head><body><h1>Intro</h1><h3>GROUNDS</h3></body></html>"
        result = post_process_pdf_html(html)
        assert "<style>" in result
        assert "bookmark-level: 1" in result
        assert "bookmark-level: 3" in result

    def test_post_process_unchanged_without_headings(self):
        from app.utils.pdf_utils import post_process_pdf_html

        html = "<html><body><p>No headings</p></body></html>"
        assert post_process_pdf_html(html) == html

    def test_bookmark_css_styles_annexure_badge(self):
        from app.utils.pdf_utils import _inject_bookmark_css

        result = _inject_bookmark_css("<h1>ANNEXURE A</h1>")
        assert "bookmark-level: 1" in result
        assert ".toc-annexure-badge" in result
        assert ".toc-annexure a" in result
