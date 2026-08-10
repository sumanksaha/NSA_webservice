"""Qdrant vector-store wrapper (Agent A, Phase 1 — §3.1).

Thin, testable wrapper around ``qdrant-client`` exposing the operations the
corpus/embedding pipeline needs: connection + health, collection setup with
payload indexes, and point upsert / search / delete / scroll.

Design notes:
- ``qdrant-client`` is imported **lazily** so the module (and the Flask app)
  boots without it; a pre-built client can be injected via the constructor for
  unit tests (mock-injection pattern from ``app/rag/retrieval/dense_retriever.py``).
- Collection name / vector size read from ``current_app.config``
  (``RAG_QDRANT_COLLECTION``, ``RAG_VECTOR_SIZE``) at call time.
- Filter structures are plain dicts in the same shape as
  ``DenseRetriever._build_filter``; the real client's model types
  (``PointStruct``, ``VectorParams``, ``FilterSelector``, ...) are built
  lazily and fall back to plain dicts when ``qdrant-client`` is absent, so
  tests with mock clients need no optional dependency.
- The ``search_filter`` kwarg name drifted to ``query_filter`` across
  ``qdrant-client`` versions — the active name is detected per client.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Default collection — matches the ``RAG_QDRANT_COLLECTION`` config default
#: consumed by Agent B's ``DenseRetriever`` (``fssai_legal_768``).
DEFAULT_COLLECTION = "fssai_legal_768"

#: Vector names for hybrid collections (dense + BM25 sparse).  Once a
#: collection declares named vectors, ALL vectors must be named — the dense
#: vector lives under ``DENSE_VECTOR_NAME`` and is queried with ``using=``.
DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "text_sparse"

#: Max points per ``upsert`` request.  Qdrant Cloud (and intermediaries such
#: as corporate proxies) drops oversized upsert payloads — observed live
#: 2026-08-09: a single 2523-point hybrid upsert failed with "An existing
#: connection was forcibly closed" after the full request was buffered.
#: Batching keeps each request small (100 points x 768-dim dense + BM25 sparse
#: is a few hundred KB) and makes failures cheap to retry.
UPSERT_BATCH_SIZE = 100

#: Payload fields worth indexing for filtering (§5.1).
DEFAULT_PAYLOAD_INDEX_FIELDS: tuple[str, ...] = (
    "document_id",
    "document_uri",
    "document_type",
    "authority",
    "jurisdiction",
    "state",
    "is_current",
    "chunk_index",
    "section_number",
    "section_title",
    "subsection",
    "hierarchy_level",
)

_DISTANCE_NAMES = {"cosine": "Cosine", "dot": "Dot", "euclid": "Euclid", "euclidean": "Euclid"}


def _detect_search_filter_kwarg(client: Any) -> str:
    """Return the active filter kwarg name for the client's search method.

    ``qdrant-client`` renamed ``query_filter`` -> ``search_filter``; detect
    which one the installed client accepts by inspecting its ``search``
    signature (falls back to ``query_filter`` for ``**kwargs`` doubles).
    """
    try:
        search = getattr(client, "search", None)
        if callable(search):
            params = inspect.signature(search).parameters
            if "search_filter" in params:
                return "search_filter"
    except (TypeError, ValueError):
        pass
    return "query_filter"


def dense_search(
    client: Any,
    *,
    collection_name: str,
    vector: list[float],
    limit: int,
    score_threshold: float | None = None,
    filter_dict: dict[str, Any] | None = None,
    using: str | None = None,
) -> list[Any]:
    """Version-agnostic dense search over a Qdrant client.

    ``qdrant-client`` >= 1.12 renamed ``client.search()`` to
    ``client.query_points()`` (which returns a ``QueryResponse`` with a
    ``.points`` list and takes ``query`` instead of ``query_vector``).  This
    helper calls whichever method the installed client exposes and returns
    the ScoredPoint list in both cases, so ``QdrantStore.search_points`` and
    ``DenseRetriever`` work across client versions (observed breakage on
    1.19.0, 2026-08-09).

    Args:
        client: A ``QdrantClient`` (or test double).
        collection_name: Collection to search.
        vector: Query embedding.
        limit: Maximum number of results.
        score_threshold: Optional minimum similarity score.
        filter_dict: Optional already-built Qdrant filter dict
            (``{"must": [...]}``).
        using: Optional vector name (required when the collection declares
            named vectors, e.g. hybrid collections with sparse vectors).

    Returns:
        List of ScoredPoint-like objects (each with ``id``, ``score``,
        ``payload``).
    """
    search = getattr(client, "search", None)
    if callable(search):
        kwargs: dict[str, Any] = {
            "collection_name": collection_name,
            "query_vector": vector,
            "limit": limit,
            "with_payload": True,
            "with_vectors": False,
        }
        if using:
            kwargs["using"] = using
        if score_threshold is not None:
            kwargs["score_threshold"] = score_threshold
        if filter_dict:
            kwargs[_detect_search_filter_kwarg(client)] = filter_dict
        return list(search(**kwargs) or [])

    # qdrant-client >= 1.12: query_points -> QueryResponse.points
    kwargs = {
        "collection_name": collection_name,
        "query": vector,
        "limit": limit,
        "with_payload": True,
        "with_vectors": False,
    }
    if using:
        kwargs["using"] = using
    if score_threshold is not None:
        kwargs["score_threshold"] = score_threshold
    if filter_dict:
        kwargs["query_filter"] = filter_dict
    response = client.query_points(**kwargs)
    return list(getattr(response, "points", None) or [])


def _sparse_query(sparse_vector: dict[str, list], models: Any | None) -> Any:
    """Convert a JSON-safe ``{indices, values}`` dict into the query object.

    The real client requires ``models.SparseVector`` for sparse queries
    (a raw dict fails with ``Unsupported query type: <class 'dict'>``,
    observed live 2026-08-09 on qdrant-client 1.19); the plain dict is kept
    for the no-qdrant-client test fallback.
    """
    if models is None:
        return sparse_vector
    return models.SparseVector(
        indices=list(sparse_vector.get("indices", [])),
        values=list(sparse_vector.get("values", [])),
    )


def sparse_search(
    client: Any,
    *,
    collection_name: str,
    sparse_vector: dict[str, list],
    limit: int,
    score_threshold: float | None = None,
    filter_dict: dict[str, Any] | None = None,
) -> list[Any]:
    """Search a Qdrant collection by a BM25 sparse vector.

    The sparse query is accepted as a plain ``{"indices": [...], "values":
    [...]}`` dict (JSON-safe) and converted to ``models.SparseVector`` for
    the real client.  Both client generations are handled: ``query_points``
    (>= 1.12, the installed 1.19 path) and the legacy ``search`` method.

    Returns:
        List of ScoredPoint-like objects.
    """
    try:
        from qdrant_client import http as _http  # type: ignore[import-untyped]

        models = _http.models
    except ImportError:
        models = None
    query = _sparse_query(sparse_vector, models)

    query_points = getattr(client, "query_points", None)
    if callable(query_points):
        kwargs: dict[str, Any] = {
            "collection_name": collection_name,
            "query": query,
            "using": SPARSE_VECTOR_NAME,
            "limit": limit,
            "with_payload": True,
            "with_vectors": False,
        }
        if score_threshold is not None:
            kwargs["score_threshold"] = score_threshold
        if filter_dict:
            kwargs["query_filter"] = filter_dict
        response = query_points(**kwargs)
        return list(getattr(response, "points", None) or [])

    search = getattr(client, "search", None)
    if callable(search):
        kwargs = {
            "collection_name": collection_name,
            "query_vector": query,
            "using": SPARSE_VECTOR_NAME,
            "limit": limit,
            "with_payload": True,
            "with_vectors": False,
        }
        if score_threshold is not None:
            kwargs["score_threshold"] = score_threshold
        if filter_dict:
            kwargs[_detect_search_filter_kwarg(client)] = filter_dict
        return list(search(**kwargs) or [])

    raise RuntimeError(
        "client exposes neither query_points nor search; sparse search unavailable"
    )


@dataclass
class Point:
    """A vector point ready for upsert (no qdrant-client types needed).

    When ``sparse_vector`` is set, the point is upserted with NAMED vectors
    (``{"dense": vector, "text_sparse": sparse_vector}``) — required by
    hybrid collections that declare sparse vectors.  Points without a sparse
    vector keep the legacy flat-vector shape for dense-only collections.
    """

    id: str
    vector: list[float]
    payload: dict[str, Any] = field(default_factory=dict)
    sparse_vector: dict[str, list] | None = None


class QdrantStore:
    """Wrapper around a Qdrant collection for the corpus pipeline.

    Args:
        collection_name: Qdrant collection (defaults to the
            ``RAG_QDRANT_COLLECTION`` config value — must match Agent B's
            ``DenseRetriever`` collection).
        vector_size: Vector dimensionality (defaults to ``RAG_VECTOR_SIZE``).
        client: Optional pre-built ``QdrantClient`` (for testing).
    """

    def __init__(
        self,
        collection_name: str | None = None,
        vector_size: int | None = None,
        client: Any | None = None,
    ) -> None:
        self._collection_name = collection_name
        self._vector_size = vector_size
        self._client = client
        self._models: Any | None = None
        #: Cache for :meth:`has_sparse_vectors` (one get_collection call).
        self._has_sparse: bool | None = None

    @property
    def collection_name(self) -> str:
        """Resolve the collection name, reading from config lazily."""
        if self._collection_name:
            return self._collection_name
        try:
            from flask import current_app

            return current_app.config.get("RAG_QDRANT_COLLECTION", DEFAULT_COLLECTION)
        except Exception:  # noqa: BLE001 - outside an app context
            return DEFAULT_COLLECTION

    @property
    def vector_size(self) -> int:
        """Resolve the vector size, reading from config lazily."""
        if self._vector_size:
            return self._vector_size
        try:
            from flask import current_app

            return int(current_app.config.get("RAG_VECTOR_SIZE", 768))
        except Exception:  # noqa: BLE001 - outside an app context
            return 768

    # ------------------------------------------------------------------ #
    # Lazy dependency accessors
    # ------------------------------------------------------------------ #

    def _get_client(self) -> Any | None:
        """Return a QdrantClient, importing qdrant-client lazily.

        Returns ``None`` (with a warning) when the package is missing or no
        ``RAG_QDRANT_URL`` is configured — never raises at import time.
        """
        if self._client is not None:
            return self._client
        try:
            from qdrant_client import QdrantClient  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("QdrantStore: qdrant-client not installed; vector store unavailable.")
            return None
        try:
            from flask import current_app

            url = current_app.config.get("RAG_QDRANT_URL", "")
        except Exception:  # noqa: BLE001 - outside an app context
            url = ""
        if not url:
            logger.warning("QdrantStore: RAG_QDRANT_URL not configured; vector store unavailable.")
            return None
        api_key = current_app.config.get("RAG_QDRANT_API_KEY")
        self._client = QdrantClient(url=url, api_key=api_key)
        return self._client

    def _require_client(self) -> Any:
        """Return the client or raise a descriptive ``RuntimeError``."""
        client = self._get_client()
        if client is None:
            raise RuntimeError(
                "Qdrant is unavailable: qdrant-client is not installed or RAG_QDRANT_URL is not "
                "configured. Install qdrant-client and set RAG_QDRANT_URL to enable the vector store."
            )
        return client

    def _get_models(self) -> Any | None:
        """Return the ``qdrant_client.http.models`` module (or ``None``)."""
        if self._models is None:
            try:
                from qdrant_client import http as _http  # type: ignore[import-untyped]

                self._models = _http.models
            except ImportError:
                self._models = False
        return self._models or None

    @staticmethod
    def _build_filter(filters: dict[str, Any]) -> dict[str, Any]:
        """Convert a flat ``{field: value}`` dict into a Qdrant filter dict.

        Same shape as ``DenseRetriever._build_filter`` (``{"must": [...]}``).
        """
        must = [{"key": key, "match": {"value": value}} for key, value in filters.items()]
        return {"must": must} if must else {}

    @staticmethod
    def _search_filter_kwarg(client: Any) -> str:
        """Return the active filter kwarg name for ``client.search``.

        ``qdrant-client`` renamed ``query_filter`` → ``search_filter``; detect
        which one the installed client accepts.
        """
        return _detect_search_filter_kwarg(client)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def ping(self) -> bool:
        """Health probe — returns ``True`` when Qdrant responds.

        Version-agnostic: ``qdrant-client`` 1.19+ **removed** ``client.ping()``
        (observed 2026-08-09 against the provisioned cloud cluster), so the
        probe falls back to the ``info()`` root endpoint and finally to a
        collection cluster-info call.  This keeps ``/api/rag/health`` and
        ``QdrantIndexer.ping`` working across client versions.
        """
        client = self._get_client()
        if client is None:
            return False
        for method_name in ("ping", "info"):
            method = getattr(client, method_name, None)
            if not callable(method):
                continue
            try:
                return bool(method())
            except Exception as exc:  # noqa: BLE001 - try the next health surface
                logger.warning("QdrantStore.ping (%s) failed: %s", method_name, exc)
        # Last resort: any collection-level call proves the service answers.
        try:
            client.collection_cluster_info(self.collection_name)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("QdrantStore.ping (cluster info) failed: %s", exc)
            return False

    def has_collection(self) -> bool:
        """Whether the configured collection already exists."""
        client = self._get_client()
        if client is None:
            return False
        try:
            return bool(client.collection_exists(self.collection_name))
        except Exception as exc:  # noqa: BLE001
            logger.warning("QdrantStore.has_collection failed: %s", exc)
            return False

    def ensure_collection(self, create_payload_indexes: bool = True, sparse_enabled: bool = False) -> bool:
        """Create the collection (with payload indexes) if it does not exist.

        Args:
            create_payload_indexes: Also create keyword indexes on the
                filterable payload fields (§5.1).
            sparse_enabled: When creating a NEW collection, also declare a
                named BM25 sparse vector (``text_sparse`` with IDF modifier)
                so hybrid retrieval works.  Existing collections are never
                altered (Qdrant sparse config is fixed at creation — to
                enable BM25 on an existing dense-only collection it must be
                recreated and re-indexed).

        Returns:
            ``True`` when the collection exists after this call.
        """
        client = self._require_client()
        try:
            if client.collection_exists(self.collection_name):
                # Existing collections keep their config; refresh the cache
                # so callers detect dense-only collections correctly.
                self._has_sparse = None
                return True
            models = self._get_models()
            if sparse_enabled:
                # Hybrid collections require ALL vectors named: the dense
                # vector lives under DENSE_VECTOR_NAME and the BM25 sparse
                # under SPARSE_VECTOR_NAME (observed live 2026-08-09: an
                # unnamed dense vector + named sparse rejects upserts with
                # "Not existing vector name error: dense").
                vectors_config = (
                    {DENSE_VECTOR_NAME: models.VectorParams(size=self.vector_size, distance=_DISTANCE_NAMES["cosine"])}
                    if models
                    else {DENSE_VECTOR_NAME: {"size": self.vector_size, "distance": "Cosine"}}
                )
            else:
                vectors_config = (
                    models.VectorParams(size=self.vector_size, distance=_DISTANCE_NAMES["cosine"])
                    if models
                    else {"size": self.vector_size, "distance": "Cosine"}
                )
            create_kwargs: dict[str, Any] = {
                "collection_name": self.collection_name,
                "vectors_config": vectors_config,
            }
            if sparse_enabled:
                sparse_config = (
                    models.SparseVectorParams(modifier=models.Modifier.IDF)
                    if models
                    else {"modifier": "idf"}
                )
                create_kwargs["sparse_vectors_config"] = {SPARSE_VECTOR_NAME: sparse_config}
            client.create_collection(**create_kwargs)
            logger.info(
                "QdrantStore: created collection %r (%d dims, sparse=%s)",
                self.collection_name, self.vector_size, bool(sparse_enabled),
            )
            self._has_sparse = bool(sparse_enabled)
            if create_payload_indexes:
                for field_name in DEFAULT_PAYLOAD_INDEX_FIELDS:
                    self.create_payload_index(field_name)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("QdrantStore.ensure_collection failed: %s", exc)
            return False

    def has_sparse_vectors(self) -> bool:
        """Whether the collection declares a sparse vector (BM25 hybrid).

        Cached after the first ``get_collection`` call; returns ``False``
        when the client/collection is unavailable so dense-only and
        unconfigured stores degrade gracefully.
        """
        if self._has_sparse is not None:
            return self._has_sparse
        client = self._get_client()
        if client is None:
            return False
        try:
            info = client.get_collection(self.collection_name)
            params = getattr(getattr(info, "config", None), "params", None)
            sparse = getattr(params, "sparse_vectors", None) if params is not None else None
            self._has_sparse = bool(sparse)
            return self._has_sparse
        except Exception as exc:  # noqa: BLE001 - unconfigured/mock clients
            # Deliberately NOT cached: a transient failure (e.g. cluster
            # restarting) must not permanently mask a sparse-capable
            # collection until the next ensure_collection() call.
            logger.warning("QdrantStore.has_sparse_vectors failed: %s", exc)
            return False

    def create_payload_index(self, field_name: str, field_schema: str = "keyword") -> bool:
        """Index a payload field for filtering."""
        client = self._require_client()
        try:
            client.create_payload_index(
                collection_name=self.collection_name,
                field_name=field_name,
                field_schema=field_schema,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("QdrantStore.create_payload_index(%s) failed: %s", field_name, exc)
            return False

    def upsert_points(self, points: list[Point]) -> int:
        """Batch-upsert points into the collection in small request batches.

        Points are upserted in :data:`UPSERT_BATCH_SIZE` chunks with a single
        per-batch retry, so one dropped/oversized request cannot lose a whole
        document and transient connection failures (observed against Qdrant
        Cloud 2026-08-09 on a 2523-point request) recover on retry.

        Args:
            points: List of :class:`Point` (id + vector + payload).

        Returns:
            Number of points upserted.
        """
        client = self._require_client()
        models = self._get_models()
        upserted = 0
        for start in range(0, len(points), UPSERT_BATCH_SIZE):
            batch = points[start : start + UPSERT_BATCH_SIZE]
            structs = [self._point_struct(p, models) for p in batch]
            try:
                client.upsert(collection_name=self.collection_name, points=structs)
            except Exception as exc:  # noqa: BLE001 - retry once, then surface
                try:
                    client.upsert(collection_name=self.collection_name, points=structs)
                except Exception as exc2:  # noqa: BLE001
                    raise RuntimeError(
                        f"Qdrant upsert batch #{start // UPSERT_BATCH_SIZE} "
                        f"({len(batch)} points) failed after retry: {exc2}"
                    ) from exc2
            upserted += len(batch)
        return upserted

    @staticmethod
    def _point_struct(point: Point, models: Any | None) -> Any:
        """Convert a :class:`Point` into the client's upsert struct shape.

        Hybrid points (with ``sparse_vector``) upsert as NAMED vectors
        ``{"dense": [...], "text_sparse": {indices, values}}`` — required by
        collections that declare sparse vectors.  Plain points keep the
        legacy flat-vector shape for dense-only collections.
        """
        vector: Any = point.vector
        if point.sparse_vector:
            vector = {DENSE_VECTOR_NAME: point.vector, SPARSE_VECTOR_NAME: point.sparse_vector}
        if models:
            return models.PointStruct(id=point.id, vector=vector, payload=point.payload)
        return {"id": point.id, "vector": vector, "payload": point.payload}

    def search_points(
        self,
        vector: list[float],
        top_k: int = 10,
        score_threshold: float | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Dense vector search.

        Args:
            vector: Query embedding.
            top_k: Maximum number of results.
            score_threshold: Minimum similarity score.
            filters: Optional ``{field: value}`` payload filter.

        Returns:
            List of ``{"id", "score", "payload"}`` dicts.
        """
        client = self._require_client()
        filter_dict = self._build_filter(filters) if filters else None
        points = dense_search(
            client,
            collection_name=self.collection_name,
            vector=vector,
            limit=top_k,
            score_threshold=score_threshold,
            filter_dict=filter_dict,
            using=DENSE_VECTOR_NAME if self.has_sparse_vectors() else None,
        )
        return [
            {
                "id": str(p.id),
                "score": float(getattr(p, "score", 0.0) or 0.0),
                "payload": getattr(p, "payload", None) or {},
            }
            for p in (points or [])
        ]

    def search_sparse(
        self,
        sparse_vector: dict[str, list],
        top_k: int = 10,
        score_threshold: float | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """BM25 sparse vector search (``text_sparse`` named vector).

        Args:
            sparse_vector: ``{"indices": [...], "values": [...]}`` (the
                output of :class:`app.rag.sparse_embedding.SparseEmbeddingService`).
            top_k: Maximum number of results.
            score_threshold: Minimum score.
            filters: Optional ``{field: value}`` payload filter.

        Returns:
            List of ``{"id", "score", "payload"}`` dicts.
        """
        client = self._require_client()
        filter_dict = self._build_filter(filters) if filters else None
        points = sparse_search(
            client,
            collection_name=self.collection_name,
            sparse_vector=sparse_vector,
            limit=top_k,
            score_threshold=score_threshold,
            filter_dict=filter_dict,
        )
        return [
            {
                "id": str(p.id),
                "score": float(getattr(p, "score", 0.0) or 0.0),
                "payload": getattr(p, "payload", None) or {},
            }
            for p in (points or [])
        ]

    def hybrid_search(
        self,
        dense_vector: list[float],
        sparse_vector: dict[str, list],
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Server-side hybrid search: dense + BM25 sparse fused via RRF.

        Uses Qdrant ``prefetch`` blocks + ``FusionQuery(Fusion.RRF)`` so the
        fusion happens on the cluster in a single round trip
        (``qdrant-client`` >= 1.12 ``query_points`` API).  Callers must fall
        back to client-side RRF when this raises (legacy clients or
        dense-only collections).

        Args:
            dense_vector: Dense query embedding.
            sparse_vector: ``{"indices": [...], "values": [...]}`` sparse query.
            top_k: Maximum number of fused results.
            filters: Optional ``{field: value}`` payload filter applied to both prefetches.

        Returns:
            List of ``{"id", "score", "payload"}`` dicts, fused and ranked.
        """
        client = self._require_client()
        query_points = getattr(client, "query_points", None)
        if not callable(query_points):
            raise RuntimeError("hybrid_search requires qdrant-client >= 1.12 (query_points)")
        models = self._get_models()
        prefetch_limit = max(top_k * 5, 50)
        prefetch = [
            {
                "query": dense_vector,
                "using": DENSE_VECTOR_NAME,
                "limit": prefetch_limit,
            },
            {
                "query": sparse_vector,
                "using": SPARSE_VECTOR_NAME,
                "limit": prefetch_limit,
            },
        ]
        kwargs: dict[str, Any] = {
            "collection_name": self.collection_name,
            "prefetch": prefetch,
            "query": {"fusion": "rrf"},
            "limit": top_k,
            "with_payload": True,
            "with_vectors": False,
        }
        if models:
            kwargs["prefetch"] = [
                models.Prefetch(query=dense_vector, using=DENSE_VECTOR_NAME, limit=prefetch_limit),
                models.Prefetch(
                    query=_sparse_query(sparse_vector, models),
                    using=SPARSE_VECTOR_NAME,
                    limit=prefetch_limit,
                ),
            ]
            kwargs["query"] = models.FusionQuery(fusion=models.Fusion.RRF)
        if filters:
            kwargs["query_filter"] = self._build_filter(filters)
        response = query_points(**kwargs)
        return [
            {
                "id": str(p.id),
                "score": float(getattr(p, "score", 0.0) or 0.0),
                "payload": getattr(p, "payload", None) or {},
            }
            for p in (getattr(response, "points", None) or [])
        ]

    def delete_points(
        self,
        point_ids: list[str] | None = None,
        document_id: str | None = None,
    ) -> int:
        """Delete points by id and/or by ``document_id`` payload filter.

        Args:
            point_ids: Chunk/point ids to delete.
            document_id: Delete every chunk belonging to this document.

        Returns:
            Number of points targeted for deletion.
        """
        client = self._require_client()
        models = self._get_models()
        if point_ids:
            ids = list(point_ids)
            selector = models.PointIdsList(points=ids) if models else {"points": ids}
            targeted = len(ids)
        elif document_id:
            flt = self._build_filter({"document_id": document_id})
            selector = (
                models.FilterSelector(filter=models.Filter(**flt)) if models else {"filter": flt}
            )
            # Filter deletes report no count from Qdrant — 1 denotes a single
            # delete operation targeted (the docstring's "targeted" semantics).
            targeted = 1
        else:
            raise ValueError("delete_points requires point_ids and/or document_id")
        client.delete(collection_name=self.collection_name, points_selector=selector)
        return targeted

    def scroll_points(
        self,
        limit: int = 100,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Batch-read points (for re-indexing / corpus sync).

        Returns:
            List of ``{"id", "payload"}`` dicts for the first page.
        """
        client = self._require_client()
        kwargs: dict[str, Any] = {
            "collection_name": self.collection_name,
            "limit": limit,
            "with_payload": True,
            "with_vectors": False,
        }
        if filters:
            kwargs["scroll_filter"] = self._build_filter(filters)
        records, _next_offset = client.scroll(**kwargs)
        return [{"id": str(r.id), "payload": getattr(r, "payload", None) or {}} for r in (records or [])]

    def scroll_all(
        self,
        with_vectors: bool = False,
        batch_size: int = 1000,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Paginate through the ENTIRE collection (for backup / re-index).

        Unlike :meth:`scroll_points` (single page), this iterates ``scroll``
        with the returned offset until the collection is exhausted.

        Args:
            with_vectors: Include each point's vector(s) in the output —
                required for a restorable backup.  Named-vector collections
                return ``{"dense": [...], "text_sparse": {indices, values}}``.
            batch_size: Scroll page size.
            filters: Optional ``{field: value}`` payload filter.

        Returns:
            List of ``{"id", "payload", ["vector"]}`` dicts for all points.
        """
        client = self._require_client()
        results: list[dict[str, Any]] = []
        next_offset: Any = None
        pages = 0
        #: Safety cap — a misbehaving client/mock echoing the same truthy
        #: offset forever must not hang the exporter (real qdrant-client
        #: returns None when the collection is exhausted).
        max_pages = 10_000_000 // max(batch_size, 1)
        while True:
            kwargs: dict[str, Any] = {
                "collection_name": self.collection_name,
                "limit": batch_size,
                "with_payload": True,
                "with_vectors": with_vectors,
            }
            if next_offset is not None:
                kwargs["offset"] = next_offset
            if filters:
                kwargs["scroll_filter"] = self._build_filter(filters)
            records, next_offset = client.scroll(**kwargs)
            for r in (records or []):
                item: dict[str, Any] = {
                    "id": str(r.id),
                    "payload": getattr(r, "payload", None) or {},
                }
                if with_vectors:
                    item["vector"] = getattr(r, "vector", None)
                results.append(item)
            pages += 1
            if not next_offset:
                break
            if pages >= max_pages:
                raise RuntimeError(
                    f"scroll_all exceeded {max_pages} pages — possible non-terminating "
                    "offset from the Qdrant client; aborting export."
                )
        return results


# End of qdrant_client.py
