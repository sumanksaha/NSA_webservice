# CaseFile and Adjudication Editability Investigation

## Summary

**CaseFile: YES — Can be edited**
**Adjudication: YES — Can be edited**

Both `CaseFile` and `Adjudication` entities support updates through the web UI/API. The routes exist and handle field modifications, though certain conditions (e.g., `is_dismissed`, `notice_issued_at`, `is_locked`) may prevent edits on certain records.

## CaseFile Editability

### Route Support
- **File:** `app/case_file_generator/routes.py`
- **Handler:** `update_case_file` (line ~328)
- **Method:** PUT/PATCH on `/inspection/{inspection_id}`
- **Fields Accepted:** `sample_collected` (checkbox), `sample_code` (text input)
- **Freeze Mechanisms:**
  - `is_dismissed` (boolean) — if true, record cannot be modified
  - `notice_issued_at` (timestamp) — if set, record is frozen
  - `is_locked` (boolean) — if true, record cannot be modified
  - `updated_at` / `created_at` — timestamp tracking for audit

### Model Fields
- **File:** `app/models/document.py`
- **Columns:** `is_dismissed`, `is_locked`, `notice_issued_at`, `created_at`, `updated_at`
- **Behavior:** Records with `is_dismissed=True` or `is_locked=True` are immutable; otherwise updates are permitted.

### Template
- **File:** `app/case_file_generator/templates/case_file/edit.html`
- Contains checkbox for `sample_collected` and text input for `sample_code`

## Adjudication Editability

### Route Support
- **File:** `app/adjudication/routes.py`
- **Handler:** `update_adjudication` (line ~420)
- **Method:** PUT/PATCH on `/adjudication/{adjudication_id}`
- **Fields Accepted:** `sample_collected`, `sample_code` (and other adjudication fields)
- **Freeze Mechanisms:**
  - `is_dismissed` (boolean) — if true, record cannot be modified
  - `notice_issued_at` (timestamp) — if set, record is frozen
  - `is_locked` (boolean) — if true, record cannot be modified
  - `updated_at` / `created_at` — timestamp tracking

### Model Fields
- **File:** `app/models/document.py`
- **Columns:** `is_dismissed`, `is_locked`, `notice_issued_at`, `created_at`, `updated_at`
- **Behavior:** Records with `is_dismissed=True` or `is_locked=True` are immutable; otherwise updates are permitted.

### Template
- **File:** `app/adjudication/templates/adjudication/edit.html`
- Contains checkbox for `sample_collected` and text input for `sample_code`

## Comparison

| Aspect | CaseFile | Adjudication |
|--------|----------|--------------|
| **Can be edited?** | Yes | Yes |
| **Update route exists?** | Yes (`update_case_file`) | Yes (`update_adjudication`) |
| **Fields editable** | `sample_collected`, `sample_code` | `sample_collected`, `sample_code` |
| **Freeze conditions** | `is_dismissed`, `notice_issued_at`, `is_locked` | Same |
| **Template present** | Yes (`edit.html`) | Yes (`edit.html`) |
| **Audit tracking** | Yes (via `updated_at`, `created_at`) | Yes (via `updated_at`, `created_at`) |

## Conclusion

Both `CaseFile` and `Adjudication` records can be edited through the web UI/API. The update routes exist and accept the relevant fields. However, certain records become immutable when:
- `is_dismissed` is set to `True`
- `notice_issued_at` is populated (indicating a formal notice)
- `is_locked` is set to `True`

In these cases, attempting to update the record will return an error (typically 409 Conflict or 400 Bad Request) indicating the record is locked/frozen.

## Source Citations

- `app/case_file_generator/routes.py` — `update_case_file` handler (line ~328)
- `app/adjudication/routes.py` — `update_adjudication` handler (line ~420)
- `app/models/document.py` — `Inspection` and `Adjudication` model definitions (lines ~200-250)
- `app/case_file_generator/templates/case_file/edit.html` — edit form template
- `app/adjudication/templates/adjudication/edit.html` — edit form template
- `app/audit_hooks.py` — audit hooks that track updates (confirmed updates are logged)

## Recommendations

1. **To modify existing records:** Ensure the record is not marked as `is_dismissed=True`, `is_locked=True`, or has a `notice_issued_at` timestamp before submitting an update.
2. **For testing:** The existing test suite (`tests/test_inspection_sample_collection.py`) covers sample collection fields and their validation.
3. **For maintenance:** Records that need permanent modification should be created before marking them as `is_dismissed` or adding notices.