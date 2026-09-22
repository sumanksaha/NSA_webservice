"""Unification tests: one adjudication context builder, one form-date helper.

``renderer.build_adjudication_context`` duplicated
``routes._prepare_adjudication_context`` (same section reads, same derive
calls, same date normalisation) plus ``compilation_date``. The renderer
must delegate instead of duplicating; ``form_date`` must live in exactly
one module.
"""

from __future__ import annotations

from datetime import datetime
from unittest import mock

from tests.test_preview_adjudication import VALID_FORM


class TestRendererDelegates:
    def test_build_adjudication_context_delegates_to_routes(self):
        from app.adjudication import routes as adj_routes
        from app.document_viewer import renderer

        with mock.patch.object(
            adj_routes, "_prepare_adjudication_context", wraps=adj_routes._prepare_adjudication_context
        ) as spy:
            ctx = renderer.build_adjudication_context(dict(VALID_FORM))
        assert spy.call_count == 1
        assert ctx["compilation_date"]

    def test_delegated_context_matches_routes_output(self):
        from app.adjudication.routes import _prepare_adjudication_context
        from app.document_viewer import renderer

        form = dict(VALID_FORM)
        assert renderer.build_adjudication_context(form) == {
            **_prepare_adjudication_context(dict(VALID_FORM)),
            "compilation_date": renderer.build_adjudication_context(dict(VALID_FORM))["compilation_date"],
        }


class TestFormDate:
    def test_shared_helper(self):
        from app.utils.filters import form_date

        assert form_date(datetime(2026, 1, 10)) == "2026-01-10"
        assert form_date("2026-01-10T00:00:00") == "2026-01-10"
        assert form_date("") == ""
        assert form_date(None) == ""

    def test_no_local_duplicates_remain(self):
        import app.adjudication.routes as adj_routes
        import app.case_file_generator.routes as case_routes

        assert not hasattr(adj_routes, "_form_date")
        assert not hasattr(case_routes, "_form_date")
