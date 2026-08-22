"""Shared FBO-issue lookup for bill prefill — the one seam both transports use.

``lookup_fbo_issues()`` is the domain function behind the Flask
``GET /bill_generator/lookup_fbo_issues`` route and
``GET /api/v2/bill/lookup-fbo-issues``; each transport reduces to
parse → call → serialize. The prefill schema here (billing-form field names:
``Name``/``EMP_ID``/``Designation`` + sample detail keys) is the canonical
one for the billing flow — note that ``app/adjudication/routes.py`` has its
own, deliberately different adjudication-case schema and is NOT served by
this function.
"""

from __future__ import annotations

import json
from typing import Any


def lookup_fbo_issues(
    session: Any,
    fbo_id: str | None = None,
    issue_id: int | None = None,
) -> list[dict[str, Any]]:
    """Return open/permission_granted FBO issues with billing prefill data.

    Args:
        session: SQLAlchemy ``Session`` — Flask-SQLAlchemy's ``db.session``
            or a standalone session (both speak ``session.query``).
        fbo_id: Look up all issues for this FBO.
        issue_id: Look up one specific issue (wins over ``fbo_id``).

    Returns:
        A list of result dicts sorted by ``created_at`` descending. Each dict
        carries the issue summary plus a source-specific ``prefill`` mapping
        for the bill form (sample / inspection / generic).
    """
    from app.models.issue import FboIssue

    query = session.query(FboIssue).filter(FboIssue.state.in_(["open", "permission_granted"]))

    if issue_id is not None:
        query = query.filter(FboIssue.id == issue_id)
    elif fbo_id:
        query = query.filter(FboIssue.fbo_id == fbo_id)

    issues = query.order_by(FboIssue.created_at.desc()).all()

    result: list[dict[str, Any]] = []
    for issue in issues:
        # Parse detail_json
        detail: Any = None
        if issue.detail_json:
            try:
                detail = json.loads(issue.detail_json)
            except Exception:
                detail = issue.detail_json

        item: dict[str, Any] = {
            "issue_id": issue.id,
            "fbo_id": issue.fbo_id,
            "manufacturer_fbo_id": issue.manufacturer_fbo_id,
            "fbo_name": issue.fbo_name,
            "source_type": issue.source_type,
            "state": issue.state,
            "fso_name": issue.fso_name,
            "created_at": issue.created_at,
            "detail": detail,
        }

        # Source-specific prefill mappings for bill form fields. ``detail``
        # must be a dict — a non-JSON detail_json degrades to a raw string,
        # which would crash ``detail.get`` below (latent bug in the original
        # inline copy); such rows fall back to the generic prefill instead.
        if issue.source_type == "sample" and isinstance(detail, dict):
            item["prefill"] = {
                "Name": issue.fbo_name,  # FBO name as the primary name
                "EMP_ID": issue.fso_name,  # FSO name as default
                "Designation": "Food Safety Officer",
                "sample_code": detail.get("sample_code"),
                "sample_name": detail.get("sample_name"),
                "price": detail.get("price"),
                "sampling_date": detail.get("sampling_date"),
            }
            # If there's a manufacturer, they might be the bill recipient
            if issue.manufacturer_fbo_id:
                item["prefill"]["manufacturer_fbo_id"] = issue.manufacturer_fbo_id
        elif issue.source_type == "inspection" and isinstance(detail, dict):
            item["prefill"] = {
                "Name": issue.fbo_name,
                "EMP_ID": issue.fso_name,
                "Designation": "Food Safety Officer",
                "inspection_details": ", ".join(detail.get("checklist", [])),
            }
        else:
            # Generic prefill
            item["prefill"] = {
                "Name": issue.fbo_name,
                "EMP_ID": issue.fso_name,
                "Designation": "Food Safety Officer",
            }

        result.append(item)

    return result
