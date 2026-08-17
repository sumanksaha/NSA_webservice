"""RAG generation sub-package — Phase 2 deliverable (Grounded Generation).

Exports the grounded-generation components so callers can do::

    from app.rag.generation import GroundedGenerationService, ContextBuilder

Phase 2 builds the pipeline: RetrievedChunks → LLM context → grounded LLM
response with citations → validated / sanitised RAGResponse.

Each component follows a documented reuse pattern:

- ``ContextBuilder``      → ``DocumentSaveCoordinator`` orchestration pattern (R1)
- ``PromptTemplate``      → ``AIAssistantService`` PROMPTS dict pattern (R3)
- ``GroundedLLMClient``   → ``AIAssistantService`` httpx client pattern (R3)
- ``CitationTracker``     → ``CrossReferenceEngine`` reference extraction (R2)
- ``ResponseSanitizer``   → ``score_field`` confidence pattern (R2)
- ``GenerationLogger``    → ``RetrievalLogger`` + ``compute_hash`` (R0/R1)
"""

from app.rag.generation.citation_tracker import CitationTracker
from app.rag.generation.context_builder import BuiltContext, ContextBuilder
from app.rag.generation.grounded_service import GroundedGenerationService
from app.rag.generation.llm_client import GroundedLLMClient, GroundedLLMResponse
from app.rag.generation.logger import GenerationLogger
from app.rag.generation.prompt_template import PromptTemplate
from app.rag.generation.sanitizer import ResponseSanitizer, SanitizedResponse

__all__ = [
    "BuiltContext",
    "CitationTracker",
    "ContextBuilder",
    "GenerationLogger",
    "GroundedGenerationService",
    "GroundedLLMClient",
    "GroundedLLMResponse",
    "PromptTemplate",
    "ResponseSanitizer",
    "SanitizedResponse",
]
