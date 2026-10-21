"""Ruler versioning rail (WF-0 — see ``Ruler_Fix_Workflow.md``).

Every report that publishes a correctness figure under the rebuilt ruler must
emit this package's publication header. The header carries the ruler version,
the reference-bundle checksum, the threshold table, and the boot rules — so a
number without its measurement context cannot circulate.

Discipline (design review ``Evaluator_V4_Binary_Ruler_Design_Review.md`` §5.1):

- **R2:** the reference bundle is frozen and versioned; a ruler change is a
  version bump plus a reference-bundle checksum change, never an in-place edit.
- **R4:** every report shows v1/v2/v4 side by side; the v1 token-overlap binary
  stays frozen as control.

The reference bundle itself is built by WF-4
(``evaluation/build_references_v4.py``). Until then the header reports the
bundle as ABSENT — explicitly, never silently (WF-1 gate discipline).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

#: Identity of the ruler this package governs.
RULER_ID = "evaluator-v4"

#: Current ruler version. Semantics: ``v4.x-dev`` until WF-8/WF-9 acceptance
#: (determinism + re-baseline) promotes it; each calibration or reference
#: change bumps this per the boot rules below. WF-5 landed bundle-backed
#: lexicon bans (bundle v1.1.1) → v4.1. WF-6 landed the widening merge
#: (bundle v1.2.1) plus the two-shape C2 (frozen max-per-ref control vs
#: reference-SET union; primary selected on holdout) → v4.2. WF-8 + WF-9
#: acceptance landed (determinism byte-identical, baseline frozen) → the
#: ``-dev`` suffix is retired; the ruler is promoted to a numbered version.
#: Live measurements under it additionally carry boot rule 6 (3 replicates).
RULER_VERSION = "v4.2"

#: The frozen multi-reference bundle WF-4 will own (versioned, checksummed).
REFERENCE_BUNDLE_PATH = PROJECT_ROOT / "evaluation" / "evaluator_v4_references.json"

#: Boot rules a publishing report must carry verbatim (R4 + hard-won measured
#: constraints from the v1→v3 lineage, plus the G9 replicate discipline).
BOOT_RULES: tuple[str, ...] = (
    "A ruler change is a version bump plus a reference-bundle checksum change; never in-place.",
    "The v1 token-overlap binary stays frozen as control; reports show v1/v2/v4 side by side.",
    "Thresholds calibrate against the completed human tabulation only — never against LLM answers.",
    "Crediting is per-answer; cross-arm best-arm roll-ups belong to the report layer only.",
    "Near-threshold band counts publish next to every binary figure.",
    "A live-LLM arm scored under this ruler requires 3 replicates and a paired gate: "
    "CLAIM must improve on the calibration cohort while the holdout stays untouched.",
)

#: Report-layer alias for the two derived binaries a v4 report must publish
#: (design review §5, Layer 2). Pooled single-value correctness is prohibited.
REQUIRED_BINARY_KEYS = ("credit_binary", "claim_binary")

#: Human partial-credit scale: ``full_review_tabulation`` ``human_correct`` ∈
#: {yes, partial, no} → {1.0, 0.5, 0.0} (acceptance A7).
HUMAN_PARTIAL_CREDIT_SCALE = {"yes": 1.0, "partial": 0.5, "no": 0.0}

_PENDING_CALIBRATION = "pending (WF-3 calibrates against full_review_tabulation.json)"


class RulerPublicationError(ValueError):
    """Raised when a report claims a ruler lineage but omits required context."""


def reference_bundle_checksum(
    path: str | Path | None = None,
) -> dict[str, Any]:
    """Checksum of the frozen reference bundle (or an explicit ABSENT mark).

    Pass an alternate ``path`` for tests; default is the live WF-4 bundle.
    """
    resolved = Path(path) if path is not None else REFERENCE_BUNDLE_PATH
    if not resolved.exists():
        return {
            "path": str(resolved),
            "sha256": None,
            "status": "absent (WF-4 pending) — ruler reports must NOT calibrate references",
        }
    digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    return {"path": str(resolved), "sha256": digest, "status": "frozen"}


def publication_header(
    thresholds: dict[str, Any] | None = None,
    reference_bundle: str | Path | None = None,
) -> dict[str, Any]:
    """The header every ruler-governed report embeds and prints.

    ``reference_bundle`` overrides the bundle path (tests, sidecar drafts).
    ``thresholds`` carries the calibrated component table once WF-3 lands;
    before that it is reported as pending rather than defaulted to 0.5 (F4).
    """
    return {
        "ruler_id": RULER_ID,
        "ruler_version": RULER_VERSION,
        "reference_bundle": reference_bundle_checksum(reference_bundle),
        "thresholds": dict(thresholds) if thresholds else {"calibration": _PENDING_CALIBRATION},
        "boot_rules": list(BOOT_RULES),
        "required_binary_keys": list(REQUIRED_BINARY_KEYS),
        "human_partial_credit_scale": dict(HUMAN_PARTIAL_CREDIT_SCALE),
    }


def format_publication_header(header: dict[str, Any] | None = None) -> str:
    """Render the header as the text block reports print above their tables."""
    h = header if header is not None else publication_header()
    bundle = h.get("reference_bundle") or {}
    digest = bundle.get("sha256")
    digest_text = digest[:16] + "…" if digest else "ABSENT"
    thresholds = h.get("thresholds") or {}
    lines = [
        "=" * 72,
        f"RULER {h.get('ruler_id')} {h.get('ruler_version')} — measurement context (boot rules below)",
        f"reference bundle: {bundle.get('status')}  sha256={digest_text}",
        "thresholds: " + "; ".join(f"{k}={v}" for k, v in sorted(thresholds.items())),
        "derivative binaries published per-answer: " + ", ".join(REQUIRED_BINARY_KEYS),
        "human partial-credit scale: " + (", ".join(f"{k}={v}" for k, v in sorted(HUMAN_PARTIAL_CREDIT_SCALE.items()))),
        "boot rules:",
    ]
    lines += [f"  {i}. {rule}" for i, rule in enumerate(boot_rules_of(h), start=1)]
    lines.append("=" * 72)
    return "\n".join(lines)


def boot_rules_of(header: dict[str, Any]) -> list[str]:
    """The boot rules a specific header carries (filterable for test asserts)."""
    return list(header.get("boot_rules") or BOOT_RULES)


def validate_header(header: Any) -> None:
    """Raise unless *header* is a publication header a report may publish.

    Report writers call this before writing anything ruler-governed; the test
    suite calls it against every report format the workflow adds.
    """
    if not isinstance(header, dict):
        raise RulerPublicationError(f"publication header must be a dict, got {type(header).__name__}")
    for key in ("ruler_id", "ruler_version", "reference_bundle", "boot_rules", "thresholds"):
        if key not in header:
            raise RulerPublicationError(f"publication header missing required key: {key!r}")
    if header.get("ruler_id") != RULER_ID:
        raise RulerPublicationError(
            f"ruler_id mismatch: expected {RULER_ID!r}, got {header.get('ruler_id')!r} "
            "(claimed lineage does not match this rail)",
        )
    if not str(header.get("ruler_version") or "").startswith("v"):
        raise RulerPublicationError(f"ruler_version must match ^v: got {header.get('ruler_version')!r}")
    bundle = header.get("reference_bundle") or {}
    if not isinstance(bundle, dict) or "status" not in bundle:
        raise RulerPublicationError("reference_bundle must carry a status (frozen/absent), not a bare hash")
    if bundle.get("status") == "frozen" and not bundle.get("sha256"):
        raise RulerPublicationError("a frozen reference bundle must carry its sha256")
    rules = header.get("boot_rules") or []
    missing = [r for r in BOOT_RULES if r not in rules]
    if missing:
        raise RulerPublicationError(f"boot rules incomplete; missing: {missing!r}")


def write_json_with_header(
    out_path: str | Path,
    body: dict[str, Any],
    *,
    ruler_report: bool = True,
    thresholds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write *body* as JSON with the publication header embedded + echoed to stdout.

    Returns the header so callers can return/assert it. When ``ruler_report``
    is False the header is still embedded (provenance) but the boot-rule echo
    is skipped — for report *pieces* nested inside a larger governed report.
    """
    header = publication_header(thresholds=thresholds)
    validate_header(header)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"publication_header": header, **body}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    import sys

    if ruler_report:
        sys.stdout.write(format_publication_header(header) + "\n")
    return header
