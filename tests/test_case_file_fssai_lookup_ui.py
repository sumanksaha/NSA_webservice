"""Regression: FSSAI manufacturer/retailer lookup on the Sample-adjudication page.

``case_file_generator/index.html`` wires two Lookup buttons to
``lookupFssai('manufacturer' | 'retailer')``. The handler resolves every DOM
node through ``document.getElementById``; if any id it reads does not exist in
the rendered page, the very first property access throws a TypeError inside the
async function, which becomes an unhandled promise rejection. Symptom: clicking
Lookup does nothing at all — no spinner, no error box, no autofill.

These tests pin the JS<->DOM id contract so any id drift fails in CI instead of
silently in the browser.
"""

import re

import pytest

TEMPLATE = "case_file_generator/index.html"

# The two lookup types the page's inline JS interpolates into element ids.
LOOKUP_TYPES = ("manufacturer", "retailer")


@pytest.fixture
def app():
    from app import app as flask_app

    return flask_app


def _render_page(app) -> str:
    """Render the Sample-adjudication landing page (same vars as index())."""
    from flask import render_template

    with app.test_request_context("/"):
        return render_template(
            TEMPLATE, cases=[], case_type="case_file", show_archived=False
        )


def _inline_script_blocks(html: str) -> list[str]:
    return re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.S)


def _getelementbyid_ids(html: str) -> set[str]:
    """Every statically-resolvable id read by getElementById in inline JS.

    Plain string literals are returned as-is; template literals like
    ``${type}_fssai`` are interpolated for each lookup type used on the page.
    Dynamic expressions (e.g. ``cb.id + "_card"``) are skipped — they cannot be
    resolved statically.
    """
    ids: set[str] = set()
    for block in _inline_script_blocks(html):
        for match in re.finditer(r"getElementById\(\s*([`'\"])(.+?)\1\s*\)", block):
            raw = match.group(2)
            if "${type}" in raw:
                ids.update(raw.replace("${type}", t) for t in LOOKUP_TYPES)
            elif "${" not in raw:
                ids.add(raw)
    return ids


def test_no_dangling_getelementbyid_ids(app):
    """Every id the inline JS reads must exist in the rendered page."""
    html = _render_page(app)
    ids = _getelementbyid_ids(html)
    assert ids, "sanity: expected inline JS to read element ids"

    missing = sorted(i for i in ids if f'id="{i}"' not in html)
    # tlOpenBtn is a base.html nav button wrapped in an auth conditional; the
    # base template itself guards `if (!picker || !openBtn) return;` before use,
    # so its absence on the un-authed render is a known false positive.
    unexpected = [i for i in missing if i != "tlOpenBtn"]
    assert not unexpected, (
        "inline JS reads ids that do not exist in the page "
        f"(click handlers die with a TypeError): {unexpected}"
    )


@pytest.mark.parametrize("lk_type", LOOKUP_TYPES)
def test_lookup_button_wiring(app, lk_type):
    """Each Lookup button targets an input that both exists and is submitted."""
    html = _render_page(app)

    # The button is wired to the unified lookup handler.
    assert f"lookupFssai('{lk_type}')" in html, f"missing Lookup wiring for {lk_type}"

    # The FSSAI input the JS must read is the same element the form submits
    # (routes read form_data["<type>_fssai"]).
    assert f'name="{lk_type}_fssai"' in html, f"form field {lk_type}_fssai missing"
    assert f'id="{lk_type}_fssai"' in html, f"input id {lk_type}_fssai missing"

    # The handler must resolve that exact input id (contract as fixed).
    assert re.search(
        r'getElementById\(\s*`\$\{type\}_fssai`\s*\)', html
    ), "lookup JS must read the '<type>_fssai' input"
