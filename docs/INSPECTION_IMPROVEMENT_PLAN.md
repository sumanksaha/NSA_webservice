# Inspection Module — Deepening Opportunities

Surface architectural friction and propose **deepening opportunities** for the inspection module: refactors that turn shallow modules into deep ones. The aim is testability and AI-navigability.

This analysis is informed by the project's domain model and built on the shared design vocabulary from `CONTEXT.md`: **module** (interface + implementation), **interface** (everything a caller must know), **depth** (behaviour per unit of interface), **seam** (where behaviour can vary without editing callers), **adapter** (concrete thing satisfying an interface at a seam), **leverage** (caller benefit of depth), **locality** (maintainer benefit: changes concentrate in one place).

---

## 1. Module Depth Scorecard

| # | Module | Files | LOC | Depth | Target | Done | Forcing Function |
| - | ------ | ----- | --- | ----- | ------ | ---- | ---------------- |
| 1 | **Inspection Utilities** | `inspection_utils.py`, `verification_service.py`, `lookup_routes.py` | ~263 | **1** | 4 | [ ] | Thin wrappers around CodeSequence, coordinator over services, simple lookup endpoints |
| 2 | **Photo Service** | `photo_service.py`, `photo_routes.py` | ~669 | **2** | 4 | [ ] | EXIF extraction, validation, storage, verification, stamping, OCR dispatch all in one service |
| 3 | **Verification Adapters** | `geo_verification.py`, `distance_verification.py`, `ip_verification.py` | ~243 | **2** | 4 | [ ] | Nominatim geocoding, IP geolocation, and license lookup all exposed as direct external calls |
| 4 | **Derived Views** | `derived_views.py` | ~260 | **2** | 3 | [ ] | Open issues, pending action, history share overlapping filter logic |
| 5 | **Inspection Routes** | `inspection_routes.py` | ~451 | **3** | 4 | [ ] | Large orchestrator with 5 callback params, validation logic scattered across create/update |

**Depth scale:** 1 = shallow (interface ≈ implementation), 5 = deep (small interface, large hidden behaviour). Prioritized by **shallowest first** — depth ≤ 2 gets the strongest recommendation.

---

## 2. Deepening Candidates

### Candidate 1: Consolidate Inspection Utilities — ⭐ Highest Priority

**Files:**

- `app/inspection/inspection_utils.py` (106 LOC)
- `app/inspection/verification_service.py` (97 LOC)
- `app/inspection/routes/lookup_routes.py` (60 LOC)

**Problem:**
The inspection module has three thin modules with overlapping concerns:

- `inspection_utils.py` wraps `CodeSequence` for inspection code generation
- `verification_service.py` coordinates external verification calls
- `lookup_routes.py` exposes simple FSSAI/CE license lookup endpoints

These modules are shallow: their interfaces are nearly as complex as their implementations. Understanding the inspection code generation requires bouncing between `inspection_utils.py`, `verification_service.py`, and the `CodeSequence` model.

**Deletion test:** Delete `inspection_utils.py` → the inspection code generation logic reappears inline in `inspection_routes.py`. Delete `verification_service.py` → the verification orchestration reappears in `photo_service.py`. Delete `lookup_routes.py` → the lookup endpoints reappear in `inspection_routes.py`. **All three modules earn their keep, but their interfaces leak complexity.**

**Solution:** Extract a deep module `InspectionCodeGenerator` with a single interface:

```python
# app/inspection/inspection_utils.py (refactored → deep module)
class InspectionCodeGenerator:
    """Generate unique inspection codes with retry logic and audit logging.

    Single interface: generate_code() -> str.
    Replaces the inline CodeSequence logic in inspection_routes.py.
    """
    def generate_code(self) -> str:
        # Atomic sequence logic with retry
        # Database advisory lock (PostgreSQL) or sequence table (SQLite)
        # Audit logging
    
    def calculate_compliance_deadline(self, inspection_date) -> datetime:
        # Business logic for deadline calculation
        # Input validation
```

**Benefits:**

- **Locality:** Code generation logic in one place. Changing the code format requires editing 1 file.
- **Leverage:** 3 call sites simplify to 1-line calls. New inspection types plug in as one more method.
- **Tests:** `test_inspection_code_generator.py` with 5 pure unit tests (no Flask/DB). Existing tests continue through the default constructor.
- **AI-navigability:** Clear boundary — inspection code generation is a single, findable concept.

**Estimated effort:** 2 days
**Tests affected:** 43 inspection tests + 15 photo tests (via verification service)
**Dependency category:** In-process (pure computation, no I/O at the generator level). Testable with mock sequences.

---

### Candidate 2: Extract Verification Adapters — Worth Exploring

**Files:**

- `app/inspection/geo_verification.py` (54 LOC)
- `app/inspection/distance_verification.py` (82 LOC)
- `app/inspection/ip_verification.py` (46 LOC)
- `app/inspection/verification_service.py` (97 LOC)

**Problem:**
The verification module makes direct external service calls without abstraction:

- `reverse_geocode` → Nominatim API (rate-limited, 1 req/sec)
- `ip_geolocate` → IP geolocation service
- `geocode_fbo_address` → Nominatim forward geocoding

These are **four-in-one** modules doing:

1. **Geocoding** (reverse + forward)
2. **IP geolocation**
3. **Distance calculation**
4. **Region matching**

The interface is nearly as complex as the implementation. Each function has its own rate-limiting logic, error handling, and retry strategy — duplicated across 4 modules.

**Deletion test:** Delete `geo_verification.py` → the reverse geocoding logic reappears in `verification_service.py`. Delete `distance_verification.py` → the haversine calculation and geocoding reappear in `photo_service.py`. **The modules earn their keep, but the duplication is a maintainability burden.**

**Solution:** Extract verification adapters:

```python
# app/inspection/verification/geocoding_adapter.py
class NominatimGeocoder:
    """Rate-limited geocoding with circuit breaker pattern.
    
    Single interface: reverse(lat, lng) -> str | None
    Caching layer for recent lookups
    Retry with exponential backoff
    User-Agent management
    """

# app/inspection/verification/ip_adapter.py
class IpGeolocationAdapter:
    """IP-based location detection with fallback.
    
    Single interface: geolocate(ip) -> dict | None
    Cache IP → location mappings
    Multiple provider support (optional)
    """

# app/inspection/verification/lookup_adapter.py
class LicenseLookupAdapter:
    """FSSAI/CE license lookup with retry and caching.
    
    Single interface: lookup(license_number) -> LookupResult
    External API integration
    Response parsing and validation
    """
```

**Benefits:**

- **Locality:** External service logic isolated in one place per service.
- **Testability:** Mock adapters for unit tests. No external calls needed.
- **Leverage:** Shared across inspection routes and photo service.
- **Future-proofing:** Easy to swap providers (e.g., Google Maps for geocoding).

**Estimated effort:** 3-4 days
**Tests affected:** 60 verification tests + 45 photo tests
**Dependency category:** Ports & adapters (external HTTP calls). Testable with mock HTTP responses.

---

### Candidate 3: Deepen Photo Service — Worth Exploring

**Files:**

- `app/inspection/photo_service.py` (504 LOC)
- `app/inspection/routes/photo_routes.py` (165 LOC)

**Problem:**
`InspectionPhotoService` is a **six-in-one** module doing:

1. **EXIF extraction** (`_extract_exif_gps`)
2. **Coordinate fallback** (`_pick_coord`)
3. **File validation** (extension whitelist, size check, PIL verify)
4. **Storage + evidence creation** (temp-file save, Evidence record, DB commit/rollback)
5. **Geo-verification** (dispatch to verification_service)
6. **Image stamping** (`process_and_stamp_image`)
7. **OCR dispatch** (`run_ocr_extraction`)
8. **Audit logging**

The interface is nearly as complex as the implementation. Callers must understand and provide all 8 concerns.

**Deletion test:** Delete the photo service → the photo upload logic reappears in `photo_routes.py`. Delete the EXIF extraction → it reappears in the photo upload handler. **The module earns its keep, but the interface grows with it.**

**Solution:** Split photo service responsibilities:

```python
# app/inspection/services/photo_processor.py
class PhotoProcessor:
    """Core photo processing: EXIF extraction, coordinate fallback.
    
    Single interface: process(file) -> ProcessedPhoto
    Coordinate validation logic
    Temporary file management
    """

# app/inspection/services/evidence_store.py  
class EvidenceStore:
    """Database operations for photo evidence.
    
    Single interface: save(evidence) -> Evidence
    CRUD operations
    Audit logging
    """

# app/inspection/services/ocr_dispatcher.py
class OCRDispatcher:
    """OCR task management with retries and caching.
    
    Single interface: dispatch(file_path) -> OCRResult
    OCR job queue
    Result processing
    """
```

**Benefits:**

- **Locality:** Each service has single responsibility.
- **Testability:** Independent test coverage per service (3-5 tests each).
- **Leverage:** Reusable components across routes.
- **Performance:** Parallel processing opportunities (OCR can run async).

**Estimated effort:** 3-4 days
**Tests affected:** 60 photo tests + 20 evidence tests
**Dependency category:** Local-substitutable (file I/O can use `tmp_path` fixture, DB uses SQLite).

---

## 3. Prioritized Backlog

| Priority | Candidate | Est. Effort | Est. Gain | Risk | Status |
| -------- | --------- | ----------- | --------- | ---- | ------ |
| P0 | Consolidate Inspection Utilities | 2 days | 3 call sites → 1-line calls, −200 LOC | Low | PENDING |
| P1 | Extract Verification Adapters | 3-4 days | Mockable external services, −150 LOC | Low | PENDING |
| P2 | Deepen Photo Service | 3-4 days | 6-in-1 → 3 focused services | Medium | PENDING |
| P3 | Add Unit Test Coverage | 2 days | 80% → 95% coverage | Low | PENDING |

---

## 4. Import-Boundary Check

```bash
# No import-boundaries.json exists in this repo; the project uses Python
# imports (not shell sourcing). The boundary concern is:
# - app/inspection/ → must not depend on app/rag/ (separation of inspection vs RAG)
# - app/shared/  → can be imported by any module (canonical contract)
# - app/rag/     → can depend on app/shared/, app/services/
# - app/food_cell/ → can depend on app/services/, app/utils/
```

The proposed `InspectionCodeGenerator` in `app/inspection/` respects this boundary — it depends only on the `CodeSequence` table (same package) and exposes a protocol that callers in any package can use. No cross-package violations.

---

## 5. Relationship to Existing ADRs

This analysis does not contradict any existing ADRs. ADR-0001 (Atomic bill issuance) and ADR-0002 (RetrievalCache Injection Contract) are in the RAG pipeline and do not affect the inspection module.

---

## 6. Next Steps

1. **Start with Candidate 1** (Consolidate Inspection Utilities) — highest priority, lowest risk, clearest win
2. **Grill through the decision tree** — constraints, dependencies, the shape of the deepened module, what sits behind the seam, what tests survive
3. **Update CONTEXT.md** — add the new `InspectionCodeGenerator` term to the domain glossary
4. **Record an ADR** if the deepening reveals a design decision worth documenting

---

*Analysis generated by the architecture improvement skill. Report at `/tmp/architecture-review-20260610.html`.*
