"""
Version Control Service

Handles version history management for document snapshots, including:
- Creating version snapshots when documents are saved
- Comparing versions (diff functionality)
- Restoring documents to previous versions
- Branch and draft support
- Version metadata tracking (content hash, change summary, etc.)
"""

import difflib
import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from flask import current_app

from app.extensions import db
from app.models import User, Version
from app.utils.document_storage import save_saved_document

logger = logging.getLogger(__name__)

_VALID_DOC_TYPES = ("petition", "permission")


class VersionError(Exception):
    """Base exception for version control errors."""

    pass


class VersionService:
    """Service for managing document versions."""

    def __init__(self):
        # Module-level logger (not ``current_app.logger``) so the module-level
        # ``version_service`` singleton below can be created at import time,
        # outside any Flask application context (e.g. CLI, Celery workers,
        # or during blueprint registration in create_app).
        self.logger = logger

    # ------------------------------------------------------------------ #
    # Version creation
    # ------------------------------------------------------------------ #

    def create_version(
        self,
        case_id: int | None,
        adjudication_id: int | None,
        doc_type: str,
        html_content: str,
        delta_content: dict | None = None,
        user_id: int | None = None,
        change_summary: str | None = None,
        branch_name: str | None = None,
        branch_of: int | None = None,
    ) -> Version:
        """
        Create a new version snapshot.

        Args:
            case_id: CaseFile ID or None for adjudication
            adjudication_id: Adjudication ID or None for case file
            doc_type: 'petition' or 'permission'
            html_content: The HTML content to version
            delta_content: Optional Quill Delta JSON
            user_id: User ID who made the change
            change_summary: Summary of what changed
            branch_name: Branch label, or None for the mainline
            branch_of: ID of the source version a branch was forked from

        Returns:
            The created Version object

        Raises:
            VersionError: If version creation fails
        """
        try:
            # Calculate content hash
            content_hash = self._calculate_content_hash(html_content)

            # Get next version number for this case/adjudication + branch
            version_number = self._get_next_version_number(case_id, adjudication_id, doc_type, branch_name)

            # Create version entry
            version = Version(
                case_id=case_id,
                adjudication_id=adjudication_id,
                doc_type=doc_type,
                version_number=version_number,
                content_hash=content_hash,
                html_snapshot=html_content,
                delta=json.dumps(delta_content) if delta_content else None,
                created_at=datetime.now(UTC),
                user_id=user_id,
                change_summary=change_summary,
                branch_name=branch_name,
                branch_of=branch_of,
            )

            db.session.add(version)
            db.session.commit()

            self.logger.info(
                f"Created version {version_number} for {doc_type} "
                f"(case_id={case_id}, adjudication_id={adjudication_id}, "
                f"branch={branch_name!r})"
            )

            return version

        except Exception as e:
            db.session.rollback()
            self.logger.error(f"Failed to create version: {e}")
            raise VersionError(f"Could not create version: {e}") from e

    def _calculate_content_hash(self, html_content: str) -> str:
        """Calculate SHA256 hash of HTML content."""
        return hashlib.sha256(html_content.encode("utf-8")).hexdigest()

    def _get_next_version_number(
        self,
        case_id: int | None,
        adjudication_id: int | None,
        doc_type: str,
        branch_name: str | None = None,
    ) -> int:
        """Get the next version number for a case/adjudication + branch."""
        query = Version.query.filter_by(doc_type=doc_type, branch_name=branch_name)

        if case_id:
            query = query.filter_by(case_id=case_id)
        elif adjudication_id:
            query = query.filter_by(adjudication_id=adjudication_id)

        max_version = query.order_by(Version.version_number.desc()).first()

        return (max_version.version_number + 1) if max_version else 1

    def create_version_if_changed(
        self,
        case_id: int | None,
        adjudication_id: int | None,
        doc_type: str,
        html_content: str,
        delta_content: dict | None = None,
        user_id: int | None = None,
        change_summary: str | None = None,
        branch_name: str | None = None,
    ) -> Version | None:
        """Create a snapshot only when the content actually changed.

        Compares the SHA-256 hash against the latest stored snapshot for the
        case/doc-type (+ branch), so the debounced autosave path does not spam
        the ``versions`` table with identical snapshots on every keystroke.

        Returns the created ``Version``, or ``None`` when the latest snapshot
        already matches the given content.
        """
        content_hash = self._calculate_content_hash(html_content)
        latest = self.get_case_versions(case_id, adjudication_id, doc_type, branch_name=branch_name)
        if latest and latest[0].content_hash == content_hash:
            return None
        return self.create_version(
            case_id=case_id,
            adjudication_id=adjudication_id,
            doc_type=doc_type,
            html_content=html_content,
            delta_content=delta_content,
            user_id=user_id,
            change_summary=change_summary,
            branch_name=branch_name,
        )

    def get_version(self, case_id: int | None, adjudication_id: int | None, version_id: int) -> Version | None:
        """Get a specific version by ID."""
        return Version.query.filter_by(
            id=version_id,
            case_id=case_id,
            adjudication_id=adjudication_id,
        ).first()

    def get_case_versions(
        self,
        case_id: int | None,
        adjudication_id: int | None,
        doc_type: str,
        branch_name: str | None = None,
    ) -> list[Version]:
        """Get all versions for a case/adjudication (+ branch, newest first).

        ``branch_name=None`` returns the mainline; pass a branch label to get
        that branch's versions only.
        """
        query = Version.query.filter_by(doc_type=doc_type, branch_name=branch_name)

        if case_id:
            query = query.filter_by(case_id=case_id)
        elif adjudication_id:
            query = query.filter_by(adjudication_id=adjudication_id)

        return query.order_by(Version.version_number.desc()).all()

    def get_branches(
        self,
        case_id: int | None,
        adjudication_id: int | None,
        doc_type: str,
    ) -> list[dict]:
        """Return the branch roots (first version of each named branch)."""
        query = Version.query.filter(Version.branch_name.isnot(None), Version.doc_type == doc_type)
        if case_id:
            query = query.filter_by(case_id=case_id)
        elif adjudication_id:
            query = query.filter_by(adjudication_id=adjudication_id)

        roots: dict[str, Version] = {}
        for version in query.order_by(Version.created_at.asc(), Version.id.asc()).all():
            if version.branch_name not in roots:
                roots[version.branch_name] = version
        return [self._version_summary(v) for v in roots.values()]

    # ------------------------------------------------------------------ #
    # Comparison
    # ------------------------------------------------------------------ #

    def compare_versions(
        self,
        case_id: int | None,
        adjudication_id: int | None,
        doc_type: str,
        version_a: int,
        version_b: int,
        branch_name: str | None = None,
    ) -> dict:
        """
        Compare two versions and return diff information.

        Args:
            case_id: CaseFile ID or None
            adjudication_id: Adjudication ID or None
            doc_type: 'petition' or 'permission'
            version_a: First version number
            version_b: Second version number
            branch_name: Branch label, or None for the mainline

        Returns:
            Dict with diff information
        """
        versions = self.get_case_versions(case_id, adjudication_id, doc_type, branch_name=branch_name)

        version_map = {v.version_number: v for v in versions}

        if version_a not in version_map or version_b not in version_map:
            raise VersionError("One or both versions not found")

        version_a_data = version_map[version_a]
        version_b_data = version_map[version_b]

        # Calculate diff
        html_diff = self._diff_html(version_a_data.html_snapshot, version_b_data.html_snapshot)

        return {
            "version_a": self._version_summary(version_a_data),
            "version_b": self._version_summary(version_b_data),
            "diff": {
                "content_changed": html_diff["content_changed"],
                "insertions": html_diff["insertions"],
                "deletions": html_diff["deletions"],
                "word_count_diff": html_diff["word_count_diff"],
                "similarity": html_diff["similarity"],
                "unified": html_diff["unified"],
            },
            "deltas": {
                "version_a_delta": version_a_data.delta,
                "version_b_delta": version_b_data.delta,
            },
        }

    def _diff_html(self, html_a: str, html_b: str) -> dict:
        """Calculate a real word-level diff between two HTML strings.

        Uses :class:`difflib.SequenceMatcher` opcodes so insertions and
        deletions contain the *actual changed words* (with ordering), rather
        than the naive count-based heuristic used previously.
        """
        words_a = (html_a or "").split()
        words_b = (html_b or "").split()

        matcher = difflib.SequenceMatcher(None, words_a, words_b)

        insertions: list[str] = []
        deletions: list[str] = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag in ("replace", "delete"):
                deletions.extend(words_a[i1:i2])
            if tag in ("replace", "insert"):
                insertions.extend(words_b[j1:j2])

        # Line-level unified diff over tag-stripped text for a readable
        # side-by-side preview (the word-level insertions/deletions above are
        # used for the change summary).
        text_a = re.sub(r"<[^>]+>", " ", html_a or "").splitlines()
        text_b = re.sub(r"<[^>]+>", " ", html_b or "").splitlines()

        return {
            "content_changed": matcher.ratio() < 1.0,
            "insertions": insertions,
            "deletions": deletions,
            "word_count_diff": abs(len(words_a) - len(words_b)),
            "similarity": round(matcher.ratio(), 4),
            "unified": "\n".join(
                difflib.unified_diff(
                    text_a,
                    text_b,
                    fromfile="version_a",
                    tofile="version_b",
                    lineterm="",
                )
            ),
        }

    # ------------------------------------------------------------------ #
    # Restore
    # ------------------------------------------------------------------ #

    def restore_version(
        self,
        case_id: int | None,
        adjudication_id: int | None,
        doc_type: str,
        version_id: int,
        user_id: int | None = None,
        change_summary: str | None = None,
    ) -> Version:
        """Restore a document to a specific version.

        Writes the snapshot HTML (+ Delta) back to ``instance/saved/`` using
        the same file naming convention as the document-viewer save path, so
        the restored content becomes the current document (session restore
        picks it up). A new append-only snapshot marked
        "Restored to version N" is recorded so the history is never lost.

        Returns the newly created "restored" Version.
        """
        version = self.get_version(case_id, adjudication_id, version_id)
        if not version:
            raise VersionError(f"Version {version_id} not found")

        # Append a new version recording the restore FIRST (append-only
        # history), then persist the snapshot to disk. If the disk write
        # fails, the appended version is rolled back so history matches
        # disk state.
        summary = change_summary or f"Restored to version {version.version_number}"
        delta_content = json.loads(version.delta) if version.delta else None
        restored = self.create_version(
            case_id=case_id,
            adjudication_id=adjudication_id,
            doc_type=doc_type,
            html_content=version.html_snapshot,
            delta_content=delta_content,
            user_id=user_id,
            change_summary=summary,
            branch_name=version.branch_name,
        )

        try:
            # Persist the restored HTML (+ Delta) as the current saved document.
            self._write_snapshot_to_disk(
                case_id,
                adjudication_id,
                doc_type,
                version.html_snapshot,
                version.delta,
            )
        except Exception:
            db.session.delete(restored)
            db.session.commit()
            raise

        self.logger.info(
            f"Version {version_id} restored for {doc_type} "
            f"(case_id={case_id}, adjudication_id={adjudication_id}) "
            f"by user {user_id}"
        )

        return restored

    def _write_snapshot_to_disk(
        self,
        case_id: int | None,
        adjudication_id: int | None,
        doc_type: str,
        html_snapshot: str,
        delta_text: str | None,
    ) -> str:
        """Persist restored HTML (+ optional Delta) to instance/saved/.

        Mirrors the naming convention used by ``document_viewer`` so the
        ``/saved/<case_id>/<doc_type>`` session-restore endpoint treats the
        restored content as the latest saved document.
        """
        label = case_id if case_id is not None else adjudication_id
        timestamp_str = save_saved_document(
            current_app.instance_path,
            label,
            doc_type,
            html_snapshot or "",
            delta_text or None,
        )
        path = Path(current_app.instance_path) / "saved" / f"{label}_{doc_type}_{timestamp_str}.html"
        self.logger.info("Restored snapshot written to %s", path)
        return str(path)

    # ------------------------------------------------------------------ #
    # Branches
    # ------------------------------------------------------------------ #

    def create_branch(
        self,
        case_id: int | None,
        adjudication_id: int | None,
        doc_type: str,
        from_version: int,
        branch_name: str,
        user_id: int | None = None,
    ) -> dict:
        """Create a branch (draft) from a specific version.

        Persists a branch root version — a snapshot of the source version's
        content with ``branch_name`` set and version numbering restarted at 1,
        isolated from the mainline by the (case, doc_type, branch_name) scope.
        """
        if not branch_name or not str(branch_name).strip():
            raise VersionError("branch_name is required")

        versions = self.get_case_versions(case_id, adjudication_id, doc_type)
        version_map = {v.version_number: v for v in versions}

        if from_version not in version_map:
            raise VersionError(f"Source version {from_version} not found")

        source_version = version_map[from_version]
        delta_content = json.loads(source_version.delta) if source_version.delta else None

        root = self.create_version(
            case_id=case_id,
            adjudication_id=adjudication_id,
            doc_type=doc_type,
            html_content=source_version.html_snapshot,
            delta_content=delta_content,
            user_id=user_id,
            change_summary=(f"Branch '{branch_name}' created from version {from_version}"),
            branch_name=str(branch_name).strip(),
            branch_of=source_version.id,
        )

        self.logger.info(
            f"Branch '{branch_name}' created from version {from_version} "
            f"for {doc_type} (case_id={case_id}, adjudication_id={adjudication_id}) "
            f"by user {user_id}"
        )

        return {
            "branch_name": root.branch_name,
            "branch_root": self._version_summary(root),
            "source_version": self._version_summary(source_version),
            "doc_type": doc_type,
            "case_id": case_id,
            "adjudication_id": adjudication_id,
            "created_by": user_id,
        }

    # ------------------------------------------------------------------ #
    # History / UI helpers
    # ------------------------------------------------------------------ #

    def _version_summary(self, version: Version) -> dict:
        """JSON-safe summary of a Version for the API/UI."""
        user = db.session.get(User, version.user_id) if version.user_id else None
        return {
            "id": version.id,
            "doc_type": version.doc_type,
            "version_number": version.version_number,
            "created_at": version.created_at.isoformat(),
            "created_by": (
                {
                    "id": version.user_id,
                    "username": user.username if user else None,
                }
                if version.user_id
                else None
            ),
            "content_hash": version.content_hash,
            "change_summary": version.change_summary,
            "has_delta": version.delta is not None,
            "branch_name": version.branch_name,
        }

    def get_version_history_ui_data(self, case_id: int | None, adjudication_id: int | None) -> dict:
        """Get version history data formatted for UI display.

        Returns mainline versions grouped by doc_type plus the branch roots.
        """
        result = {"petition": [], "permission": [], "branches": []}

        for doc_type in _VALID_DOC_TYPES:
            versions = self.get_case_versions(case_id, adjudication_id, doc_type)
            result[doc_type] = [self._version_summary(v) for v in versions]

        result["branches"] = [
            branch for doc_type in _VALID_DOC_TYPES for branch in self.get_branches(case_id, adjudication_id, doc_type)
        ]

        return result


# Singleton instance
version_service = VersionService()
