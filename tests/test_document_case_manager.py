"""DocumentCaseManager: the facade delegates, callbacks inject.

The interface is the test surface — these tests pin that every manager
method crosses its documented seam (lookup / reports / generation /
route registration) with the injected callbacks, so the two blueprint
instantiations (case_file_generator, adjudication) break loudly if the
wiring drifts. No DB: all seams are mocked.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.shared.document_case_manager import DocumentCaseManager, PDFResult


@pytest.fixture()
def callbacks():
    return {
        "model_to_dict_fn": MagicMock(side_effect=lambda inst: {"id": inst.id}),
        "process_form_fn": MagicMock(),
        "validate_form_fn": MagicMock(return_value={}),
        "prepare_context_fn": MagicMock(side_effect=lambda ctx: ctx),
        "templates": {"petition": "petition.html"},
        "apply_update_fn": MagicMock(),
        "form_dict_fn": MagicMock(),
    }


@pytest.fixture()
def manager(callbacks):
    return DocumentCaseManager(
        model=MagicMock(),
        template_dir="case_file_generator",
        bp_name="case_file_generator",
        case_type="case_file",
        **callbacks,
    )


def test_construction_stores_callbacks(manager, callbacks):
    assert manager.case_type == "case_file"
    assert manager.bp_name == "case_file_generator"
    assert manager.templates == {"petition": "petition.html"}
    for key in ("model_to_dict_fn", "process_form_fn", "validate_form_fn", "apply_update_fn", "form_dict_fn"):
        assert getattr(manager, key) is callbacks[key]


def test_construction_defaults(manager):
    bare = DocumentCaseManager(
        model=MagicMock(),
        template_dir="d",
        bp_name="b",
        case_type="case_file",
        model_to_dict_fn=MagicMock(),
        process_form_fn=MagicMock(),
    )
    assert bare.templates == {}
    assert bare.validate_form_fn is None
    assert bare.prepare_context_fn({"a": 1}) == {"a": 1}


def test_pdfresult_success():
    assert PDFResult(b"pdf").success is True
    assert PDFResult(None, error="boom").success is False


def test_get_case_delegates_to_lookup(manager):
    with patch("app.shared.document_case_manager.lookup") as lookup:
        manager.get_case(7)
        lookup.get_case.assert_called_once_with(manager.model, 7)


def test_get_case_by_number_delegates_to_lookup(manager):
    with patch("app.shared.document_case_manager.lookup") as lookup:
        manager.get_case_by_number("SL/WB/001/2024/00001")
        lookup.get_case_by_number.assert_called_once_with(manager.model, "SL/WB/001/2024/00001")


def test_list_cases_orders_newest_first_and_summarizes(manager):
    older, newer = SimpleNamespace(id=1), SimpleNamespace(id=2)
    manager.model.query.order_by.return_value.all.return_value = [newer, older]
    with patch("app.shared.document_case_manager.lookup") as lookup:
        lookup.case_summary.side_effect = lambda _ct, c: {"id": c.id}
        assert manager.list_cases() == [{"id": 2}, {"id": 1}]
        assert lookup.case_summary.call_count == 2


def test_render_editor_delegates_to_reports(manager):
    with patch("app.shared.document_case_manager.reports") as reports:
        manager.render_editor(3)
        reports.render_editor.assert_called_once_with(manager.model, "case_file", "case_file_generator", 3)


def test_xref_and_toc_reports_delegate(manager):
    with patch("app.shared.document_case_manager.reports") as reports:
        manager.xref_report(3, "permission")
        reports.xref_report.assert_called_once_with(manager.model, "case_file", "case_file_generator", 3, "permission")
        manager.toc_report(3)
        reports.toc_report.assert_called_once_with(manager.model, "case_file", "case_file_generator", 3, "petition")


def test_renumber_annexures_delegates(manager):
    with patch("app.shared.document_case_manager.reports") as reports:
        manager.renumber_annexures(3)
        reports.renumber_annexures.assert_called_once_with("case_file", 3)


def test_regenerate_passes_injected_context_fn(manager, callbacks):
    with patch("app.shared.document_case_manager.generation") as generation:
        manager.regenerate(9, context_overrides={"k": "v"})
        generation.regenerate.assert_called_once_with(
            manager.model,
            "case_file",
            callbacks["model_to_dict_fn"],
            callbacks["process_form_fn"],
            callbacks["prepare_context_fn"],
            9,
            {"k": "v"},
        )


def test_generate_case_passes_injected_fns(manager, callbacks):
    form = {"case_number": "SL/WB/001/2024/00001"}
    with patch("app.shared.document_case_manager.generation") as generation:
        manager.generate_case(form)
        generation.generate_case.assert_called_once_with(
            manager.model,
            "case_file",
            callbacks["validate_form_fn"],
            callbacks["process_form_fn"],
            callbacks["prepare_context_fn"],
            form,
        )


def test_register_routes_wires_everything(manager, callbacks):
    bp = MagicMock()
    with patch("app.shared.document_case_manager.register_document_routes") as register:
        manager.register_routes(bp)
        register.assert_called_once_with(
            bp,
            manager.model,
            "case_file",
            "case_file_generator",
            "case_file_generator",
            callbacks["model_to_dict_fn"],
            validate_form_fn=callbacks["validate_form_fn"],
            apply_update_fn=callbacks["apply_update_fn"],
            form_dict_fn=callbacks["form_dict_fn"],
            sheets_module=None,
        )


def test_property_accessors_delegate(manager):
    case = SimpleNamespace(id=1)
    with patch("app.shared.document_case_manager.lookup") as lookup:
        lookup.case_summary.return_value = {"id": 1}
        lookup.get_case_number.return_value = "SL/WB/001/2024/00001"
        lookup.get_fbo_name.return_value = "FBO"
        lookup.get_fso.return_value = None
        assert manager._case_summary(case) == {"id": 1}
        assert manager._get_case_number(case) == "SL/WB/001/2024/00001"
        assert manager._get_fbo_name(case) == "FBO"
        assert manager._get_fso(case) is None
        lookup.case_summary.assert_called_once_with("case_file", case)


def test_sheets_columns_match_lookup_sets(manager):
    from app.shared.document_lookup import sheets_columns

    assert manager._sheets_columns() == sheets_columns("case_file")
    assert "case_number" in manager._sheets_columns()
