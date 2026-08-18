# Implementation Plan: Plugin Architecture (Phase 20)

> **Phase:** 20 — Plugin Architecture  
> **Status:** ✅ Complete (2026-08-18) — 23/23 tests passing, lint clean, backward-compat verified  
> **Priority:** Low  
> **Source:** `task.md` §Phase 20 (line 637+), `plan.md` §1 (pending phase)  
> **Architecture doc:** `specs/tech-architecture/tech-stack.md` §12–14

---

## Goal & Rationale

**Goal:** Decouple core services (OCR processing, AI processing, Rule suggestion, PDF generation) behind formal provider interfaces to enable dynamic plugin registration and third-party extensions.

**Rationale:** Currently, four core services are hardcoded and tightly coupled:

1. **OCR** — `app/ocr_pipeline/ocr_engine.py` `OCREngine` (EasyOCR → PaddleOCR fallback) is directly imported by `app/services/ocr_extraction.py`
2. **AI** — `app/ai_assistant/service.py` `AIAssistantService` hardcodes the OpenRouter/OpenAI provider logic
3. **Rules/Sections** — `app/utils/suggester.py` `suggest_sections` is a plain function imported directly by `app/adjudication/routes.py` and `app/validation/data_assembler.py`
4. **PDF** — `app/pdf_assembly/engine.py` `PDFAssemblyEngine` uses WeasyPrint directly, called via `app/pdf_assembly/__init__.py` shims

While there are _existing_ ABC patterns (`app/document_loader/base.py` `BaseLoader` + `DocumentLoaderFactory`), `app/metadata_extractor/extractors/base.py` `BaseExtractor`, and `app/validation/rules.py` `BaseRule`), these are internal abstraction patterns, not a true plugin registry. Phase 20 introduces a **registry-based plugin system** that allows swapping providers at runtime via configuration, with zero behavioral change for current implementations.

**Key constraint:** The existing codebase pattern of lazy imports + graceful degradation must be preserved. Plugins should never be hard imports at module load time.

---

## Architecture

### Design Pattern: Registry + Factory + Lazy Loading

The plugin system follows a **registry-factory pattern** consistent with existing patterns:

```
┌─────────────────────────────────────────────────────────────┐
│                      Plugin Registry                        │
│  (singleton, app/plugins/registry.py)                       │
│                                                              │
│  register("ocr", "easyocr", OCREasyOCRPlugin)               │
│  register("ai", "openrouter", AIOOpenRouterPlugin)          │
│  register("rules", "fssai_default", RuleSuggesterPlugin)    │
│  register("pdf", "weasyprint", PDFWeasyPrintPlugin)          │
│                                                              │
│  get("ocr", "easyocr") → OCREasyOCRPlugin()                  │
│  get_active("ocr") → resolved from config at call-time       │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
   ┌───────────┐       ┌───────────┐       ┌───────────┐
   │  OCR      │       │  AI       │       │  Rules    │
   │ Provider  │       │ Provider  │       │ Provider  │
   │ (ABC)     │       │ (ABC)     │       │ (ABC)     │
   └───────────┘       └───────────┘       └───────────┘
        │                     │                     │
        ▼                     ▼                     ▼
   ┌───────────┐       ┌───────────┐       ┌───────────┐
   │ easyocr   │       │ openrouter│       │ fssai     │
   │ plugin    │       │ plugin    │       │ plugin    │
   └───────────┘       └───────────┘       └───────────┘
```

### Interface Contracts

Each provider interface follows the existing `BaseLoader`/`BaseExtractor` pattern (ABC + `@abstractmethod`):

```python
class OCRProvider(ABC):
    @abstractmethod
    def extract_text(self, file_path: Path | str) -> dict: ...

class AIProvider(ABC):
    @abstractmethod
    def generate(self, prompt: str, **kwargs) -> str: ...

class RuleProvider(ABC):
    @abstractmethod
    def evaluate_rules(self, data: dict) -> list[ValidationResult]: ...

class PDFProvider(ABC):
    @abstractmethod
    def render_pdf(self, html_content: str, **kwargs) -> bytes: ...
```

### Configuration

Plugins are selected via Flask config keys (consistent with the RAG feature-flag pattern):

```python
app.config["OCR_PROVIDER"] = os.environ.get("OCR_PROVIDER", "easyocr")
app.config["AI_PROVIDER"] = os.environ.get("AI_PROVIDER", "openrouter")
app.config["RULES_PROVIDER"] = os.environ.get("RULES_PROVIDER", "fssai_default")
app.config["PDF_PROVIDER"] = os.environ.get("PDF_PROVIDER", "weasyprint")
```

### Lazy Loading Strategy

- Plugins are registered at app factory time via `register_default_plugins()` called in `create_app()`
- Each plugin wraps its underlying implementation with a **lazy import** (consistent with `app/food_cell/services.py` pattern)
- `PluginRegistry.get()` returns the plugin instance; the provider's method performs the lazy import
- Tests inject plugins via `PluginRegistry.register()` directly (no config changes needed)

---

## In Scope

1. **Plugin registry** — `app/plugins/__init__.py`, `app/plugins/registry.py`
2. **Base provider interfaces** — `app/plugins/base.py` (OCR, AI, Rule, PDF providers)
3. **Default plugin implementations** wrapping existing services:
    - `app/plugins/ocr_plugins.py` — wraps `OCREngine` from `app/ocr_pipeline/ocr_engine.py`
    - `app/plugins/ai_plugins.py` — wraps `AIAssistantService` (or `GroundedLLMClient` for RAG)
    - `app/plugins/rule_plugins.py` — wraps `suggest_sections` from `app/utils/suggester.py`
    - `app/plugins/pdf_plugins.py` — wraps `PDFAssemblyEngine` from `app/pdf_assembly/engine.py`
4. **App factory integration** — register default plugins in `app/__init__.py::create_app()`
5. **Refactoring** — update callers to use `PluginRegistry.get()` instead of direct imports:
    - `app/services/ocr_extraction.py` → use `OCRProvider` instead of `OCRPipeline` directly
    - `app/adjudication/routes.py` + `app/validation/data_assembler.py` → use `RuleProvider`
    - `app/case_file_generator/tasks.py`, `app/food_cell/renderer.py` → use `PDFProvider`
    - `app/ai_assistant/routes.py` → use `AIProvider`
6. **Tests** — `tests/test_plugins.py` (registration, retrieval, lazy loading, active provider resolution, end-to-end delegation)

## Out of Scope

1. **Third-party plugin discovery** via `importlib.metadata` entry points — not needed yet (single deployment; all plugins are first-party)
2. **Plugin packaging/distribution** — no separate plugin packages; all plugins live in `app/plugins/`
3. **Dynamic plugin loading from disk** at runtime — plugins are registered at app startup only
4. **Plugin configuration UI** — selection happens via environment variables only
5. **Replacing the RAG retrieval stack** (DenseRetriever, HybridRetriever) — these already have a loose provider contract via constructor injection (`client`, `encoder`); they are not part of Phase 20's scope
6. **Replacing the Neo4j graph service** — `app/services/neo4j_graph.py` has its own gate (`neo4j_configured()`)

---

## File Targets

| File                               | Action                | Purpose                                                                                       |
| ---------------------------------- | --------------------- | --------------------------------------------------------------------------------------------- |
| `app/plugins/__init__.py`          | **Create**            | Package init, exports, `register_default_plugins()`                                           |
| `app/plugins/base.py`              | **Create**            | Abstract base provider classes (`OCRProvider`, `AIProvider`, `RuleProvider`, `PDFProvider`)   |
| `app/plugins/registry.py`          | **Create**            | `PluginRegistry` singleton with `register()`, `get()`, `get_active()`, `available()`          |
| `app/plugins/ocr_plugins.py`       | **Create**            | `EasyOCRPlugin` wrapping `OCRPipeline`, `PaddleOCRPlugin`, `TesseractPlugin`                  |
| `app/plugins/ai_plugins.py`        | **Create**            | `OpenRouterAIPlugin`, `OpenAIPlugin` wrapping `AIAssistantService`                            |
| `app/plugins/rule_plugins.py`      | **Create**            | `FSSAIRuleSuggesterPlugin` wrapping `suggest_sections`                                        |
| `app/plugins/pdf_plugins.py`       | **Create**            | `WeasyPrintPDFPlugin` wrapping `PDFAssemblyEngine`                                            |
| `app/__init__.py`                  | **Modify**            | Call `register_default_plugins()` in `create_app()`                                           |
| `app/services/ocr_extraction.py`   | **Modify**            | Replace `from app.ocr_pipeline import OCRPipeline` with `PluginRegistry.get("ocr")`           |
| `app/adjudication/routes.py`       | **Modify**            | Replace `from app.utils.suggester import suggest_sections` with `PluginRegistry.get("rules")` |
| `app/validation/data_assembler.py` | **Modify**            | Replace direct suggester import with `PluginRegistry.get("rules")`                            |
| `app/case_file_generator/tasks.py` | **Modify**            | Replace `from app.pdf_assembly import PDFAssemblyEngine` with `PluginRegistry.get("pdf")`     |
| `app/food_cell/renderer.py`        | **Modify**            | Same PDF provider swap                                                                        |
| `app/ai_assistant/routes.py`       | **Modify**            | Replace `AIAssistantService()` direct instantiation with `PluginRegistry.get("ai")`           |
| `app/rag/generation/llm_client.py` | **Modify** (optional) | Wrap `GroundedLLMClient` as `AIProvider` plugin                                               |
| `tests/test_plugins.py`            | **Create**            | 20+ tests covering all plugin interfaces                                                      |
| `.env.example`                     | **Modify**            | Add `OCR_PROVIDER`, `AI_PROVIDER`, `RULES_PROVIDER`, `PDF_PROVIDER` env vars                  |

---

## Implementation Steps

### Step 1: Plugin Base Interfaces (`app/plugins/base.py`)

Create the four abstract provider classes following the `BaseLoader`/`BaseRule` pattern:

```python
from abc import ABC, abstractmethod
from pathlib import Path
from dataclasses import dataclass
from typing import Any

@dataclass
class OCRResult:
    text: str
    confidence: float
    ocr_engine_used: str
    page_count: int

class OCRProvider(ABC):
    field_name: str = ""
    @abstractmethod
    def extract_text(self, file_path: str | Path) -> OCRResult: ...

class AIProvider(ABC):
    @abstractmethod
    def generate(self, prompt: str, **kwargs) -> str: ...
    @abstractmethod
    def is_enabled(self) -> bool: ...

class RuleProvider(ABC):
    @abstractmethod
    def suggest_sections(self, case_data: dict) -> dict: ...

class PDFProvider(ABC):
    @abstractmethod
    def render_pdf(self, html_content: str, **kwargs) -> bytes: ...
    @abstractmethod
    def render_pdf_safe(self, html_content: str, **kwargs) -> tuple[bytes | None, str | None]: ...
```

### Step 2: Plugin Registry (`app/plugins/registry.py`)

```python
class PluginRegistry:
    _instance = None
    _plugins: dict[str, dict[str, type]]  # {category: {name: cls}}
    _active: dict[str, str]  # {category: active_name}

    @classmethod
    def get_instance(cls) -> PluginRegistry: ...  # singleton
    def register(self, category: str, name: str, cls: type) -> None: ...
    def get(self, category: str, name: str | None = None) -> Any: ...  # instance
    def get_active(self, category: str) -> Any: ...  # reads config
    def available(self, category: str) -> list[str]: ...
```

The `get_active()` method reads `current_app.config[f"{CATEGORY}_PROVIDER"]` (or env var), falling back to the default plugin name. This mirrors the `DenseRetriever._get_encoder()` pattern of resolving from `current_app.config` lazily.

### Step 3: Default Plugin Implementations

Each plugin wraps existing services with lazy imports:

```python
# app/plugins/ocr_plugins.py
class EasyOCRPlugin(OCRProvider):
    def extract_text(self, file_path):
        from app.ocr_pipeline import OCRPipeline  # lazy
        pipeline = OCRPipeline(languages=["english", "hindi"])
        results = pipeline.process_document(str(file_path))
        # ... wrap result
        return OCRResult(text=..., confidence=..., ...)
```

```python
# app/plugins/rule_plugins.py
class FSSAIRuleSuggesterPlugin(RuleProvider):
    def suggest_sections(self, case_data):
        from app.utils.suggester import suggest_sections  # lazy
        return suggest_sections(case_data)  # returns {"sections": [...], "reasoning": {...}}
```

### Step 4: App Factory Integration

In `app/__init__.py::create_app()`:

```python
# At the top with other imports:
from app.plugins import register_default_plugins

# Inside create_app(), after blueprint registration:
register_default_plugins()
```

### Step 5: Refactor Callers

For each caller, replace direct imports with registry lookups. **Backward compatibility:** `app/utils/pdf_utils.py` continues to delegate to `PDFAssemblyEngine`, which internally uses `PluginRegistry.get("pdf")` — this preserves all existing `pdf_utils.generate_pdf_from_html()` callers.

### Step 6: Tests

| Test Group           | Tests  | Focus                                                                    |
| -------------------- | ------ | ------------------------------------------------------------------------ |
| `TestPluginRegistry` | 5      | register, get, get_active, available, singleton                          |
| `TestOCRProvider`    | 3      | lazy import, extract_text delegation, fallback                           |
| `TestAIProvider`     | 3      | is_enabled gate, generate delegation                                     |
| `TestRuleProvider`   | 3      | suggest_sections delegation                                              |
| `TestPDFProvider`    | 3      | render_pdf + render_pdf_safe delegation                                  |
| `TestBackwardCompat` | 4      | existing callers (pdf_utils shims, suggester, ocr_extraction) still work |
| **Total**            | **21** |                                                                          |

---

## Acceptance Criteria

1. ✅ All four provider ABCs defined in `app/plugins/base.py`
2. ✅ `PluginRegistry` singleton with register/get/get_active/available
3. ✅ Default plugins registered at app startup without errors
4. ✅ All callers refactored to use `PluginRegistry.get_active()`:
    - `app/services/ocr_extraction.py` → `OCRProvider.extract_text`
    - `app/adjudication/routes.py` → `RuleProvider.suggest_sections`
    - `app/validation/data_assembler.py` → `RuleProvider.suggest_sections`
    - `app/case_file_generator/tasks.py` → `PDFProvider.get_engine()`
    - `app/food_cell/renderer.py` → `PDFProvider.render_pdf_safe()`
    - `app/ai_assistant/routes.py` → `AIProvider` (with `__getattr__` proxy)
    - `app/utils/pdf_utils.py` → backward-compatible shim
5. ✅ Lazy imports preserved — `import app.plugins` works without `easyocr`/`sentence_transformers` installed
6. ✅ Existing tests pass unchanged (pre-existing `test_validation.py` failures from uncommitted `engine.py` changes, not plugins)
7. ✅ New `tests/test_plugins.py` passes (23 tests)
8. ✅ Plugin selection works via env vars (`OCR_PROVIDER`, `AI_PROVIDER`, `RULES_PROVIDER`, `PDF_PROVIDER`)
9. ✅ `.env.example` documents the 4 provider selection env vars

---

## Risks & Mitigations

| Risk                                           | Mitigation                                                                                                        |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Breaking existing callers during refactor      | Use backward-compatible shims (e.g., `pdf_utils.py` delegates to `PDFAssemblyEngine` which uses `PluginRegistry`) |
| Lazy import failures when optional deps absent | Follow existing pattern: `try/except ImportError` + graceful degradation with logged warning                      |
| Plugin registry state leaking across tests     | `conftest.py` fixture resets the singleton before each test; `PluginRegistry._plugins.clear()` in teardown        |
| Config key name collisions                     | Use `PLUGIN_` prefix for all internal keys: `PLUGIN_OCR_PROVIDER`, etc.                                           |

## Dependencies

- **Internal:** `app/ocr_pipeline/ocr_engine.py`, `app/ai_assistant/service.py`, `app/utils/suggester.py`, `app/pdf_assembly/engine.py`
- **External:** None new — all providers wrap existing dependencies
- **Python:** `abc` (stdlib), `pathlib` (stdlib), existing `pydantic`, `flask`

## Test Command

```bash
pytest tests/test_plugins.py -v  # new plugin tests
pytest tests/ -k "suggester or ocr or pdf or ai_assistant" -v  # regression on callers
```
