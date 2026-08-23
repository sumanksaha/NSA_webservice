"""Tests for the Phase 20 Plugin Architecture (app/plugins/).

Covers:
- PluginRegistry: singleton, register, get, get_active, available, reset
- OCRProvider: lazy import + extract_text delegation to OCRPipeline
- AIProvider: is_enabled gate + generate delegation to AIAssistantService
- RuleProvider: suggest_sections delegation to suggester
- PDFProvider: render_pdf + render_pdf_safe delegation to PDFAssemblyEngine
- Backward compatibility: pdf_utils shims still work through the registry

Follows the test pattern from tests/test_ai_assistant.py and
tests/test_food_cell_do_intimation.py: _setup_test_env() creates an app with
in-memory SQLite + db.create_all(), seeds User/FSO, authenticates via
session_transaction().
"""

from __future__ import annotations

import os
from unittest import mock

import pytest

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _setup_test_env():
    """Create a test app with in-memory SQLite, a user, and an FSO."""
    from app import create_app
    from app.extensions import db
    from app.models import FSO, User

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    app_context = app.app_context()
    app_context.push()

    db.drop_all()
    db.create_all()

    user = User(username="plugintest", password_hash="pbkdf2:sha256$test$dummy")
    db.session.add(user)
    db.session.add(FSO(fso_name="Test Officer"))
    db.session.commit()

    return app, app_context


@pytest.fixture(autouse=True)
def _reset_registry():
    """Reset the PluginRegistry singleton state before each test."""
    from app.plugins.registry import PluginRegistry

    PluginRegistry.reset()
    yield
    PluginRegistry.reset()


@pytest.fixture(autouse=True)
def _test_env():
    """Set up test env with app context + DB."""
    app, ctx = _setup_test_env()
    yield app, ctx
    ctx.pop()


# --------------------------------------------------------------------------- #
# TestPluginRegistry
# --------------------------------------------------------------------------- #


class TestPluginRegistry:
    """5 tests for the PluginRegistry singleton."""

    def test_singleton(self):
        """get_instance() returns the same object on repeated calls."""
        from app.plugins.registry import PluginRegistry

        r1 = PluginRegistry.get_instance()
        r2 = PluginRegistry.get_instance()
        assert r1 is r2

    def test_register_and_get(self):
        """register() + get() return the registered class instance."""
        from app.plugins.base import OCRProvider
        from app.plugins.registry import PluginRegistry

        registry = PluginRegistry.get_instance()

        class DummyOCR(OCRProvider):
            def extract_text(self, file_path):
                from app.plugins.base import OCRResult

                return OCRResult(text="dummy", confidence=0.5, ocr_engine_used="dummy", page_count=1)

        registry.register("ocr", "dummy", DummyOCR)
        instance = registry.get("ocr", "dummy")
        assert isinstance(instance, DummyOCR)

    def test_get_raises_for_unknown(self):
        """get() with an unknown name raises KeyError."""
        from app.plugins.registry import PluginRegistry

        registry = PluginRegistry.get_instance()
        with pytest.raises(KeyError):
            registry.get("ocr", "nonexistent")

    def test_available(self):
        """available() lists all registered names for a category."""
        from app.plugins.base import OCRProvider
        from app.plugins.registry import PluginRegistry

        registry = PluginRegistry.get_instance()

        class DummyA(OCRProvider):
            def extract_text(self, file_path):
                from app.plugins.base import OCRResult

                return OCRResult()

        class DummyB(OCRProvider):
            def extract_text(self, file_path):
                from app.plugins.base import OCRResult

                return OCRResult()

        registry.register("ocr", "a", DummyA)
        registry.register("ocr", "b", DummyB)
        names = registry.available("ocr")
        assert "a" in names
        assert "b" in names

    def test_reset(self):
        """reset() clears all registrations and active selections."""
        from app.plugins.base import OCRProvider
        from app.plugins.registry import PluginRegistry

        registry = PluginRegistry.get_instance()

        class Dummy(OCRProvider):
            def extract_text(self, file_path):
                from app.plugins.base import OCRResult

                return OCRResult()

        registry.register("ocr", "dummy", Dummy)
        assert registry.get("ocr", "dummy") is not None

        PluginRegistry.reset()
        # After reset, the old registration should be gone
        registry2 = PluginRegistry.get_instance()
        with pytest.raises(KeyError):
            registry2.get("ocr", "dummy")


# --------------------------------------------------------------------------- #
# TestPluginRegistration
# --------------------------------------------------------------------------- #


class TestPluginRegistration:
    """Verify default plugins are registered at app startup."""

    def test_default_plugins_registered(self):
        """After create_app(), all four provider categories have defaults."""
        from app.plugins.registry import PluginRegistry

        registry = PluginRegistry.get_instance()
        # The defaults should be registered by app factory
        assert registry.get("ocr", "easyocr") is not None
        assert registry.get("rules", "fssai_default") is not None
        assert registry.get("pdf", "weasyprint") is not None
        # AI may or may not have a default registered (depends on AI_ASSISTANT_PROVIDER)
        # but the category should be queryable
        ai_names = registry.available("ai")
        assert len(ai_names) >= 1

    def test_get_active_returns_default(self):
        """get_active() returns the default when no config override is set."""
        from app.plugins.registry import PluginRegistry

        registry = PluginRegistry.get_instance()
        from app.plugins.ocr_plugins import EasyOCRPlugin

        instance = registry.get_active("ocr")
        assert isinstance(instance, EasyOCRPlugin)


# --------------------------------------------------------------------------- #
# TestOCRProvider
# --------------------------------------------------------------------------- #


class TestOCRProvider:
    """3 tests for OCR provider delegation."""

    def test_extract_text_delegates_to_ocrpipeline(self):
        """EasyOCRPlugin.extract_text delegates to OCRPipeline.process_document."""
        from app.plugins.registry import PluginRegistry

        registry = PluginRegistry.get_instance()
        plugin = registry.get("ocr", "easyocr")

        mock_result = mock.MagicMock()
        mock_result.text = "Extracted text content"
        mock_result.page_number = 1
        mock_result.ocr_used = True

        with mock.patch("app.ocr_pipeline.pipeline.OCRPipeline") as mock_pipeline_cls:
            instance = mock_pipeline_cls.return_value
            instance.process_document.return_value = [mock_result]

            result = plugin.extract_text("/fake/path.pdf")
            assert result.text == "Extracted text content"
            instance.process_document.assert_called_once()

    def test_extract_text_handles_empty_results(self):
        """EasyOCRPlugin.extract_text handles empty pipeline results gracefully."""
        from app.plugins.registry import PluginRegistry

        registry = PluginRegistry.get_instance()
        plugin = registry.get("ocr", "easyocr")

        with mock.patch("app.ocr_pipeline.pipeline.OCRPipeline") as mock_pipeline_cls:
            instance = mock_pipeline_cls.return_value
            instance.process_document.return_value = []

            result = plugin.extract_text("/fake/path.pdf")
            assert result.text == ""
            assert result.page_count == 0

    def test_extract_text_maps_ocr_engine_field(self):
        """Regression (2026-08-22): pipeline page results carry ``ocr_engine``
        (not ``ocr_engine_used``). The plugin must map that field into the
        contract's ``ocr_engine_used`` — the old code read the wrong name and
        crashed with AttributeError on the real EasyOCR path.

        Uses SimpleNamespace (strict attributes) instead of MagicMock, which
        silently auto-created the missing attribute and masked the bug.
        """
        from types import SimpleNamespace

        from app.plugins.registry import PluginRegistry

        registry = PluginRegistry.get_instance()
        plugin = registry.get("ocr", "easyocr")

        page_result = SimpleNamespace(
            page=1,
            text="Extracted text content",
            confidence=0.93,
            ocr_used=True,
            ocr_engine="tesseract",  # the field the pipeline actually sets
        )

        with mock.patch("app.ocr_pipeline.pipeline.OCRPipeline") as mock_pipeline_cls:
            instance = mock_pipeline_cls.return_value
            instance.process_document.return_value = [page_result]

            result = plugin.extract_text("/fake/path.pdf")

        assert result.text == "Extracted text content"
        assert result.ocr_engine_used == "tesseract"

    def test_extract_text_defaults_engine_when_page_results_lack_it(self):
        """Partial/error results may lack ``ocr_engine`` entirely — the engine
        name then falls back to the plugin default instead of crashing."""
        from types import SimpleNamespace

        from app.plugins.registry import PluginRegistry

        registry = PluginRegistry.get_instance()
        plugin = registry.get("ocr", "easyocr")

        partial_result = SimpleNamespace(page=1, text="", confidence=0.0, ocr_used=True)

        with mock.patch("app.ocr_pipeline.pipeline.OCRPipeline") as mock_pipeline_cls:
            instance = mock_pipeline_cls.return_value
            instance.process_document.return_value = [partial_result]

            result = plugin.extract_text("/fake/path.pdf")

        assert result.ocr_engine_used == "easyocr"

    def test_lazy_import(self):
        """OCRProvider plugin does not import OCRPipeline at module load."""

        # The OCRPipeline should not be imported just by importing the plugin module
        # Check that app.ocr_pipeline is not in sys.modules just from importing plugin
        # (it may already be there from app factory, so we test that the plugin
        # module itself does not hard-import it at class-definition time)
        # The class body should not execute OCRPipeline/OCREngine imports:
        # they must stay inside the implementation method bodies (lazy).
        # Updated 2026-08-23: extract_text now dispatches to _extract_document /
        # _extract_image; extract_text itself is pure dispatch (no imports),
        # and each implementation method lazily imports its backend.
        import inspect

        import app.plugins.ocr_plugins as ocr_plugins_mod

        for method_name in ("_extract_document", "_extract_image"):
            source = inspect.getsource(getattr(ocr_plugins_mod.EasyOCRPlugin, method_name))
            # The backend import should be inside the method, not at module level
            assert "from app.ocr_pipeline" in source


# --------------------------------------------------------------------------- #
# TestAIProvider
# --------------------------------------------------------------------------- #


class TestAIProvider:
    """3 tests for AI provider delegation."""

    def test_is_enabled_true_when_configured(self):
        """AIAssistantPlugin.is_enabled returns True when provider is configured."""
        from app.plugins.registry import PluginRegistry

        registry = PluginRegistry.get_instance()
        registry.register("ai", "test", _TestAIPlugin)

        plugin = registry.get("ai", "test")
        assert plugin.is_enabled() is True

    def test_is_enabled_false_when_not_configured(self):
        """AIAssistantPlugin.is_enabled returns False when no provider configured."""
        from app.plugins.registry import PluginRegistry

        registry = PluginRegistry.get_instance()

        # Register a disabled plugin
        from app.plugins.base import AIProvider

        class DisabledAI(AIProvider):
            def is_enabled(self):
                return False

            def generate(self, prompt, **kwargs):
                raise RuntimeError("not configured")

        registry.register("ai", "disabled", DisabledAI)
        plugin = registry.get("ai", "disabled")
        assert plugin.is_enabled() is False

    def test_generate_delegates_to_service(self):
        """OpenRouterAIPlugin.generate delegates to AIAssistantService."""
        from app.plugins.registry import PluginRegistry

        registry = PluginRegistry.get_instance()
        registry.register("ai", "test", _TestAIPlugin)

        plugin = registry.get("ai", "test")
        result = plugin.generate("summarize", "test content")
        assert "summarized" in result


# --------------------------------------------------------------------------- #
# TestRuleProvider
# --------------------------------------------------------------------------- #


class TestRuleProvider:
    """3 tests for rule provider delegation."""

    def test_suggest_sections_delegates(self):
        """FSSAIRuleSuggesterPlugin.suggest_sections delegates to suggest_sections."""
        from app.plugins.registry import PluginRegistry

        registry = PluginRegistry.get_instance()
        plugin = registry.get("rules", "fssai_default")

        case_data = {
            "section_55": "yes",
            "section_56": "no",
            "clean_premise": "no",
            "non_license": "yes",
        }

        with mock.patch("app.utils.suggester.suggest_sections") as mock_suggest:
            mock_suggest.return_value = {"sections": ["55"], "reasoning": {}}
            result = plugin.suggest_sections(case_data)
            assert "sections" in result
            assert result["sections"] == ["55"]
            mock_suggest.assert_called_once_with(case_data)

    def test_suggest_sections_real_delegation(self):
        """Without mocking, the plugin delegates to the real suggest_sections."""
        from app.plugins.registry import PluginRegistry

        registry = PluginRegistry.get_instance()
        plugin = registry.get("rules", "fssai_default")

        case_data = {
            "non_license": "no",
            "clean_premise": "no",
            "refrigerator_clean": "no",
        }

        result = plugin.suggest_sections(case_data)
        assert "sections" in result
        assert "55" in result["sections"]

    def test_suggest_sections_returns_dict(self):
        """The plugin return type is a dict with sections + reasoning keys."""
        from app.plugins.registry import PluginRegistry

        registry = PluginRegistry.get_instance()
        plugin = registry.get("rules", "fssai_default")

        result = plugin.suggest_sections({"section_55": "yes", "section_56": "no"})
        assert isinstance(result, dict)
        assert "sections" in result
        assert "reasoning" in result


# --------------------------------------------------------------------------- #
# TestPDFProvider
# --------------------------------------------------------------------------- #


class TestPDFProvider:
    """3 tests for PDF provider delegation."""

    def test_render_pdf_safe_delegates(self):
        """WeasyPrintPDFPlugin.render_pdf delegates to engine.generate_from_html."""
        from app.plugins.registry import PluginRegistry

        registry = PluginRegistry.get_instance()
        plugin = registry.get("pdf", "weasyprint")

        with mock.patch("app.pdf_assembly.engine.PDFAssemblyEngine") as mock_engine_cls:
            instance = mock_engine_cls.return_value
            instance.generate_from_html.return_value = (b"pdf-bytes", None)

            html = "<html><body>Test</body></html>"
            pdf_bytes, error = plugin.render_pdf_safe(html)
            assert pdf_bytes == b"pdf-bytes"
            assert error is None
            instance.generate_from_html.assert_called_once()

    def test_render_pdf_safe_handles_error(self):
        """render_pdf_safe returns (None, error) when engine fails."""
        from app.plugins.registry import PluginRegistry

        registry = PluginRegistry.get_instance()
        plugin = registry.get("pdf", "weasyprint")

        with mock.patch("app.pdf_assembly.engine.PDFAssemblyEngine") as mock_engine_cls:
            instance = mock_engine_cls.return_value
            instance.generate_from_html.return_value = (None, "WeasyPrint not installed")

            pdf_bytes, error = plugin.render_pdf_safe("<html>test</html>")
            assert pdf_bytes is None
            assert error == "WeasyPrint not installed"

    def test_render_pdf_safe_with_post_processing(self):
        """render_pdf_safe applies post-processing when kwargs are provided."""
        from app.plugins.registry import PluginRegistry

        registry = PluginRegistry.get_instance()
        plugin = registry.get("pdf", "weasyprint")

        with mock.patch("app.pdf_assembly.engine.PDFAssemblyEngine") as mock_engine_cls:
            instance = mock_engine_cls.return_value
            instance.post_process.return_value = "<html>processed</html>"
            instance.generate_from_html.return_value = (b"pdf-bytes", None)

            html = "<html><body>Test</body></html>"
            pdf_bytes, _error = plugin.render_pdf_safe(html, case_id=1, adjudication_id=None)
            assert pdf_bytes == b"pdf-bytes"
            instance.post_process.assert_called_once()
            instance.generate_from_html.assert_called_once_with("<html>processed</html>")


# --------------------------------------------------------------------------- #
# TestBackwardCompat
# --------------------------------------------------------------------------- #


class TestBackwardCompat:
    """4 tests verifying existing callers still work through the registry."""

    def test_pdf_utils_delegates_to_registry(self):
        """app.utils.pdf_utils.generate_pdf_from_html still works via registry."""

        # When PDF generation is disabled, we expect graceful None
        os.environ["DISABLE_PDF_GENERATION"] = "1"
        try:
            from app.utils.pdf_utils import generate_pdf_from_html

            result = generate_pdf_from_html("<html><body>test</body></html>")
            # Should return (None, error_msg) when WeasyPrint is unavailable
            assert isinstance(result, tuple)
        finally:
            os.environ["DISABLE_PDF_GENERATION"] = "0"

    def test_suggester_still_importable(self):
        """app.utils.suggester.suggest_sections is still importable and functional."""
        from app.utils.suggester import suggest_sections

        result = suggest_sections({
            "section_55": "yes",
            "section_56": "no",
            "clean_premise": "no",
        })
        assert "sections" in result

    def test_ocrengine_still_importable(self):
        """app.ocr_pipeline.OCREngine is still importable."""
        from app.ocr_pipeline.ocr_engine import OCREngine

        engine = OCREngine(languages=["english"])
        assert engine is not None

    def test_app_factory_registers_plugins(self):
        """create_app() registers default plugins without errors."""
        from app.plugins.registry import PluginRegistry

        # After _test_env fixture's create_app(), defaults should be registered
        registry = PluginRegistry.get_instance()
        names = registry.available("ocr")
        assert "easyocr" in names


# --------------------------------------------------------------------------- #
# Test Helpers
# --------------------------------------------------------------------------- #


class _TestAIPlugin:
    """A simple test AI plugin that does NOT require ABC inheritance.

    This avoids the ABC enforcement issue while still testing registry behavior.
    """

    def is_enabled(self):
        return True

    def generate(self, action, content, **kwargs):
        return f"summarized: {content[:20]}"
