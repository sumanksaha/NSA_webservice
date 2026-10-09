"""2.6 — Targeted Retry (Intelligence Layer).

Replace generic query expansion with failure-aware targeted retrieval.
When verification fails, diagnose *what evidence is missing* and target
retrieval for that specific gap.

ponytail: deterministic targeting based on failure classification.
Upgrade path: learned targeting if taxonomy proves insufficient.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.rag.planning.failure_classifier import FailureClassifier, RetrievalFailure

logger = logging.getLogger(__name__)

#: Arms understood by retrieve_node.
ARM_SPARSE_IDENTIFIER = "sparse_identifier"
ARM_DEFINITION = "definition"
ARM_HIERARCHY = "hierarchy"
ARM_KG_PATHS = "kg_paths"
ARM_HYBRID = "hybrid"
ARM_NONE = "none"

#: Pseudo-tag suffixes that must never appear in an emitted query (RAG-TR-001 §4 hard rule).
_PSEUDO_TAG_RE = re.compile(r"\(\s*(identifier|definition|temporal|hierarchy|authority|case_law|expand)\s*\)", re.IGNORECASE)


@dataclass(frozen=True)
class TargetPlan:
    """Structured retry plan (RAG-TR-001 §3.1). JSON-safe via to_dict()."""

    query: str
    arm: str = ARM_HYBRID
    filters: dict[str, Any] | None = None
    top_k_override: int | None = None
    strategy: str = "dense_expansion"
    failures: tuple[str, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "arm": self.arm,
            "filters": self.filters,
            "top_k_override": self.top_k_override,
            "strategy": self.strategy,
            "failures": list(self.failures),
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TargetPlan:
        return cls(
            query=str(data.get("query") or ""),
            arm=str(data.get("arm") or ARM_HYBRID),
            filters=data.get("filters"),
            top_k_override=data.get("top_k_override"),
            strategy=str(data.get("strategy") or "dense_expansion"),
            failures=tuple(str(f) for f in (data.get("failures") or [])),
            meta=dict(data.get("meta") or {}),
        )

if TYPE_CHECKING:
    from app.rag.planning.failure_classifier import RetrievalFailure


class TargetedRetryPlanner:
    """Plan targeted retrieval queries based on verification failures."""

    def __init__(self) -> None:
        pass

    def target_plan(
        self,
        query: str,
        failures: list[Any],
        query_type: str,
        context: dict | None = None,
    ) -> TargetPlan:
        """Build a structured retry plan (RAG-TR-001 section 3.1/4)."""
        ctx = context or {}
        if not failures:
            return TargetPlan(query=query, arm=ARM_HYBRID, strategy="dense_expansion", failures=())
        failure = failures[0]
        strategy = FailureClassifier().recovery_strategy(failure)  # type: ignore[arg-type]
        fail_codes = tuple(str(f) for f in failures)
        top_k = ctx.get("top_k")
        try:
            top_k_int: int | None = int(top_k) if top_k is not None else None
        except (TypeError, ValueError):
            top_k_int = None
        if strategy == "identifier_search":
            plan = self._build_identifier(query, fail_codes, top_k_int)
        elif strategy == "collection_reroute":
            plan = self._build_collection(query, ctx, fail_codes)
        elif strategy == "definition_search":
            plan = self._build_definition(query, ctx, fail_codes)
        elif strategy == "hierarchy_graph":
            plan = self._build_hierarchy(query, ctx, fail_codes)
        elif strategy in ("kg_traversal", "kg_reasoning"):
            plan = self._build_kg(query, ctx, fail_codes, strategy)
        elif strategy == "abstain":
            plan = TargetPlan(query=query, arm=ARM_NONE, strategy=strategy, failures=fail_codes)
        else:
            # Unknown strategy: explicit no-op plan, never a silent query echo
            logger.warning("Unknown retry strategy %r for failures %r — emitting unmapped no-op plan", strategy, fail_codes)
            plan = TargetPlan(query=query, arm=ARM_NONE, strategy="unmapped", failures=fail_codes, meta={"unmapped_strategy": strategy})
        self._check_plan_invariant(query, plan)
        return plan

    def _check_plan_invariant(self, query: str, plan: TargetPlan) -> None:
        """Plan-time invariant: non-empty failures must not silently echo the query.

        A retry plan with ``failures=()`` may echo the input query.  A plan with
        non-empty failures must produce a different query *unless* it declares
        itself an explicit no-op (``arm="none"`` abstain, or ``strategy=
        "unmapped"``) — those are labelled refusals to re-query, not silent
        echoes, and are the two cases SPEC-2 explicitly permits.
        """
        if not plan.failures or plan.query != query:
            return
        if plan.arm == ARM_NONE or plan.strategy == "unmapped":
            return
        logger.warning(
            "Plan invariant violation: failures=%r but plan_query == input query %r",
            plan.failures,
            query,
        )

    def _legacy_target_query(self, query: str, failures: list[Any], query_type: str, context: dict) -> str:
        """Pre-V2 string behavior without deprecation warning (legacy path).

        NOTE: this path keeps its own dispatch and silently echoes the input
        query for strategies it does not handle (``collection_reroute``,
        ``temporal_retrieval``, ``authority_retrieval``, ``kg_*``).  Collapsing
        it onto :meth:`target_plan` was tried and **reverted**: ``
        RAG_TARGETED_RETRY_V2`` defaults to False, so this is the production
        path and the change would have altered served behaviour without an
        adoption gate.  The SPEC-2 fixes are therefore live only under that
        flag.  See docs/adr/0010-shadow-verifier-threshold-calibration.md §2.6.
        """
        if not failures:
            return query
        strategy = FailureClassifier().recovery_strategy(failures[0])  # type: ignore[arg-type]
        if strategy == "identifier_search":
            return self._target_identifier(query)
        if strategy == "hierarchy_graph":
            return self._target_hierarchy(query)
        if strategy == "definition_search":
            return self._target_definition(query)
        return query

    def target_query(
        self,
        query: str,
        failures: list[RetrievalFailure],
        query_type: str,
        context: dict,
    ) -> str:
        """Legacy string API - delegates to target_plan (deprecated shim)."""
        try:
            import warnings as _w

            _w.warn("target_query is deprecated; use target_plan", DeprecationWarning, stacklevel=2)
        except Exception:
            pass
        return self.target_plan(query, failures, query_type, context).query

    # --- Real builders (RAG-TR-001 section 4) ---
    def _build_identifier(self, query: str, failures: tuple[str, ...], top_k: int | None) -> TargetPlan:
        try:
            from app.rag.retrieval.identifier import identifier_query
        except Exception:
            return self._degrade(query, failures, "identifier_search", "import_failed")
        try:
            lex, meta = identifier_query(query)
        except Exception:
            return self._degrade(query, failures, "identifier_search", "extract_failed")
        if not lex:
            return self._degrade(query, failures, "identifier_search", "none")
        override = (top_k * 2) if top_k else None
        clean = {k: v for k, v in meta.items() if v is not None}
        return TargetPlan(query=lex, arm=ARM_SPARSE_IDENTIFIER, top_k_override=override, strategy="identifier_search", failures=failures, meta=clean)

    def _degrade(self, query: str, failures: tuple[str, ...], strategy: str, reason: str) -> TargetPlan:
        """Explicit abstain when a builder cannot produce a targeted query.

        SPEC-2: a non-empty-failure plan must never silently echo the input
        query.  Echoing wastes a retrieval round and reads as "we tried"; an
        abstain says "no targeting token was recoverable, do not re-query".
        """
        logger.debug("Degrading %s retry to abstain: %s", strategy, reason)
        return TargetPlan(
            query=query,
            arm=ARM_NONE,
            strategy=strategy,
            failures=failures,
            meta={"degraded": reason},
        )

    def _build_collection(self, query: str, ctx: dict, failures: tuple[str, ...]) -> TargetPlan:
        """Build a collection-targeted query for collection_reroute failures.

        Unlike the old identity no-op, this extracts collection-scoped terms
        (act name, section identifiers) from the failure payload and context
        to construct a query that targets the collection's namespace.
        """
        from app.rag.retrieval.identifier import detect_act, detect_section

        act = detect_act(query)
        section, _sub = detect_section(query)
        # Extract collection-scoped terms from context chunks
        collection_terms: list[str] = []
        for chunk in (ctx.get("chunks") or []):
            if not isinstance(chunk, dict):
                continue
            title = str(chunk.get("document_title") or "")
            if title and title not in collection_terms:
                collection_terms.append(title)
        # Build a collection-scoped query
        parts: list[str] = []
        if act:
            parts.append(act)
        if section:
            parts.append(f"Section {section}")
        if collection_terms:
            parts.append(" ".join(collection_terms[:3]))
        if not parts:
            # No targeting tokens available — route to abstain
            return TargetPlan(query=query, arm=ARM_NONE, strategy="collection_reroute", failures=failures, meta={"collection_target": "unresolvable"})
        collection_query = " ".join(parts)
        return TargetPlan(query=collection_query, arm=ARM_SPARSE_IDENTIFIER, strategy="collection_reroute", failures=failures, meta={"act": act, "section": section, "collection_terms": collection_terms[:3]})

    def _mine_defined_term(self, ctx: dict) -> str | None:
        for chunk in (ctx.get("chunks") or []):
            if not isinstance(chunk, dict):
                continue
            try:
                from app.rag.retrieval.legal_identity import detect_provision_type
            except Exception:
                break
            try:
                if detect_provision_type(str(chunk.get("text") or "")) == "definition":
                    blob = str(chunk.get("document_title") or "") + " " + str(chunk.get("text") or "")[:300]
                    m = re.search(r"[\"']([^\"']{3,60})[\"']", blob)
                    if m:
                        return m.group(1).strip()
            except Exception:
                continue
        answer = str(ctx.get("answer") or "")
        m = re.search(r"[\"']([^\"']{3,60})[\"']\s+means\b", answer, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return None

    def _build_definition(self, query: str, ctx: dict, failures: tuple[str, ...]) -> TargetPlan:
        term = self._mine_defined_term(ctx)
        if not term:
            return self._degrade(query, failures, "definition_search", "no_defined_term")
        return TargetPlan(query=f'"{term} means" OR "definition of {term}"', arm=ARM_DEFINITION, strategy="definition_search", failures=failures, meta={"term": term})

    def _build_hierarchy(self, query: str, ctx: dict, failures: tuple[str, ...]) -> TargetPlan:
        from app.rag.retrieval.identifier import detect_act, detect_section

        act = detect_act(query)
        section, _sub = detect_section(query)
        adjacent: list[str] = []
        if section:
            try:
                from app.rag.retrieval.legal_hierarchy import section_base

                base = section_base(section)
                digits = re.search(r"\d+", base or section)
                if digits:
                    n = int(digits.group(0))
                    adjacent = [str(max(1, n - 1)), str(n + 1)]
            except Exception:
                adjacent = []
        expanded: list[str] = []
        try:
            chunks = ctx.get("chunks") or []
            if chunks:
                from app.rag.retrieval.reference_graph import expand_candidates
                from app.rag.retrieval.result import RetrievedChunk

                objs = [RetrievedChunk(chunk_id=c.get("chunk_id", ""), text=c.get("text", ""), score=float(c.get("score") or 0)) for c in chunks if isinstance(c, dict)]
                expanded = list(expand_candidates(objs, top_k=10, depth=1))[:10]
        except Exception as exc:
            logger.debug("hierarchy expansion failed: %s", exc)
        if act and section:
            q = f"{act} Section {section} exception proviso notwithstanding"
        elif section:
            q = f"Section {section} exception proviso notwithstanding"
        else:
            return self._degrade(query, failures, "hierarchy_graph", "no_section_token")
        if adjacent:
            q += " OR Section " + " OR Section ".join(adjacent)
        return TargetPlan(query=q, arm=ARM_HIERARCHY, strategy="hierarchy_graph", failures=failures, meta={"act": act, "section": section, "adjacent": adjacent, "expanded_candidates": expanded})

    def _build_kg(self, query: str, ctx: dict, failures: tuple[str, ...], strategy: str) -> TargetPlan:
        targets: list[str] = []
        for item in (ctx.get("kg_paths") or []):
            try:
                if isinstance(item, str) and item.strip():
                    targets.append(item.strip())
                elif isinstance(item, dict):
                    steps = item.get("steps") or []
                    if steps and str(steps[0]).strip():
                        targets.append(str(steps[0]).strip())
                else:
                    steps = getattr(item, "steps", []) or []
                    if steps and str(steps[0]).strip():
                        targets.append(str(steps[0]).strip())
            except Exception:
                continue
        seen: list[str] = []
        for t in targets:
            if t not in seen:
                seen.append(t)
        if not seen:
            return self._degrade(query, failures, strategy, "no_kg_targets")
        sec_qs = " OR ".join(f"Section {t}" if not t.lower().startswith("section") else t for t in seen[:5])
        return TargetPlan(query=f"{query} {sec_qs}".strip(), arm=ARM_KG_PATHS, strategy=strategy, failures=failures, meta={"kg_hint": seen[0], "kg_targets": seen[:5]})

    # Legacy thin fallbacks (no pseudo-tags).
    def _target_identifier(self, query: str) -> str:
        return self._build_identifier(query, (), None).query

    def _target_collection(self, query: str) -> str:
        return query

    def _target_temporal(self, query: str) -> str:
        return query

    def _target_hierarchy(self, query: str) -> str:
        return self._build_hierarchy(query, {}, ()).query

    def _target_definition(self, query: str) -> str:
        return self._build_definition(query, {}, ()).query
