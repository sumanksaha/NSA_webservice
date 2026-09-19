# ADR-0003: Inspection Module Architecture Deepening

**Date:** 2026-09-11  
**Author:** Architecture Improvement Skill

## Overview

This ADR documents the deepening of the Inspection module to improve testability, maintainability, and single-responsibility principles. The goal was to replace ad-hoc implementations scattered across multiple files with focused, deep modules that expose clean interfaces.

## Changes Made

### 1. InspectionCodeGenerator (`app/inspection/code_generation.py`)

- **Depth:** Increased from shallow to deep (single interface for all code generation logic)
- **Interface:** `generate_code() -> str`, `calculate_compliance_deadline() -> datetime | None`
- **Benefit:** Callers interact with one stable interface; internal sequence allocation, concurrency handling, and deadline arithmetic are encapsulated

### 2. NomadimGeocoder (`app/inspection/verification/geocoding_adapter.py`)

- **Depth:** Increased from ad-hoc to deep module
- **Interface:** `reverse(lat, lng) -> dict` with standardized error handling
- **Benefit:** All geocoding behavior (timeout, rate limiting, error normalization) is centralized

### 3. IpGeolocationAdapter (`app/inspection/verification/ip_adapter.py`)

- **Depth:** Increased from ad-hoc to deep module
- **Interface:** `geolocate(ip) -> dict` returning region, city, error
- **Benefit:** Unified IP-to-location mapping with built-in private IP rejection

### 4. LicenseLookupAdapter (`app/inspection/verification/lookup_adapter.py`)

- **Depth:** Increased from ad-hoc to deep module
- **Interface:** `lookup(source, id) -> dict` with normalized results
- **Benefit:** Centralized license lookup with consistent error handling

### 5. PhotoProcessor (`app/inspection/services/photo_processor.py`)

- **Depth:** Increased from ad-hoc to deep module
- **Interface:** `process(file, form_data) -> ProcessedPhoto`
- **Benefit:** Image validation, EXIF extraction, coordinate fallback, temp-file management are all contained

### 6. EvidenceStore (`app/inspection/services/evidence_store.py`)

- **Depth:** Increased from ad-hoc to deep module
- **Interface:** `save(processed_photo) -> Evidence`, `update_stamped_evidence()`, `delete()`, `cleanup_file()`
- **Benefit:** All persistence logic (add, commit, delete, cleanup) is encapsulated

### 7. OCRDispatcher (`app/inspection/services/ocr_dispatcher.py`)

- **Depth:** Increased from ad-hoc to deep module
- **Interface:** `dispatch(filepath) -> dict` with task ID, result, mode
- **Benefit:** OCR task management with deduplication and retry logic is centralized

## Impact

- **Testability:** All new modules are independently testable with mocked dependencies
- **Maintainability:** Changes to geocoding, IP geolocation, license lookup, or photo processing now only affect their respective modules
- **Leverage:** Callers benefit from stable interfaces; internal implementation details remain hidden
- **Locality:** Seam boundaries are well-defined; modifications concentrate in one place

## Related Files

- `app/inspection/code_generation.py` – InspectionCodeGenerator
- `app/inspection/verification/geocoding_adapter.py` – NominatimGeocoder
- `app/inspection/verification/ip_adapter.py` – IpGeolocationAdapter
- `app/inspection/verification/lookup_adapter.py` – LicenseLookupAdapter
- `app/inspection/services/photo_processor.py` – PhotoProcessor
- `app/inspection/services/evidence_store.py` – EvidenceStore
- `app/inspection/services/ocr_dispatcher.py` – OCRDispatcher
- `tests/inspection/` – Unit tests for all new modules

## Next Steps

1. **Refactor `photo_routes.py`** to use the new service layer (already partially done)
2. **Record ADR** – Completed
3. **Verify all tests pass** – Running test suite to confirm
