"""Form-reference retrieval arm: detection precision and gating.

The arm (commits 092e8d5 / 09b3084) was live via ``cfg.identifier_route`` and
misfired on ordinary statutory questions.  Measured on the real detector:

    "When must the FSO serve notice on the business operator?" -> "Form V"
    "What is Section X of the Act?"                            -> "Form X"

The first came from a six-word subject-keyword guess; the second from matching
any standalone roman numeral case-insensitively.  Both fed a 20-40 chunk list
into RRF against the vector pool, where a short auxiliary list outranks gold
by construction (the KG-fusion failure mode, analysis §1).

These tests pin explicit-mention-only detection, the single flag, the
procedure-query gate, and the pool-depth cap.
"""

from __future__ import annotations

import pytest

from app.rag.retrieval.form_references import FORM_NAMES, detect_form, form_query
from app.rag.retrieval.query_understanding import understand


class TestDetectFormIsExplicitOnly:
    @pytest.mark.parametrize(
        "query,expected",
        [
            ("What is Form V?", "v"),
            ("Is there a bond under Form IV?", "iv"),
            ("Form VII covers the analyst report", "vii"),
            ("Which form does the FSO give in Form VIII?", "viii"),
            ("form ii receipt", "ii"),
            ("What is Form IX?", "ix"),
        ],
    )
    def test_explicit_mentions_detected(self, query, expected):
        assert detect_form(query) == expected

    def test_first_form_wins_when_two_are_named(self):
        """'Forms V and VIII' asks about the primary form, not the trailing one."""
        assert detect_form("Forms V and VIII") == "v"

    def test_empty_and_none_return_none(self):
        assert detect_form("") is None
        assert detect_form(None) is None

    # --- the two defects this change removed -------------------------------- #

    @pytest.mark.parametrize(
        "query",
        [
            "When must the FSO serve notice on the business operator?",
            "How does the analyst receive the memorandum?",
            "Explain the appellate remedy",
            "What happens to the bond if it is not executed?",
            "What is the procedure during seizure?",
        ],
    )
    def test_subject_keywords_do_not_imply_a_form(self, query):
        """The keyword arm guessed a form from six common English words.

        Those words carry no information about which form is meant, so
        guessing was always wrong; here it was also common.
        """
        assert detect_form(query) is None

    @pytest.mark.parametrize(
        "query",
        [
            "What is Section X of the Act?",
            "What is Section V of the IPC?",
            "What is section vi?",
            "Explain clause ix",
        ],
    )
    def test_bare_roman_numerals_are_not_forms(self, query):
        """Case-insensitive bare numerals matched any standalone v/x/ix."""
        assert detect_form(query) is None

    def test_asking_which_form_is_not_naming_one(self):
        """'Which form ...?' carries no form number — the old cue arm guessed."""
        assert detect_form("Which form is issued when goods are seized?") is None


class TestFormNamesTable:
    #: The FSSAI framework defines Forms II-VIII (module docstring).  ix/x are
    #: detectable but unnamed, so they exercise the fallback.
    DEFINED = ("ii", "iii", "iv", "v", "vi", "vii", "viii")

    def test_every_defined_form_has_a_canonical_name(self):
        for token in self.DEFINED:
            assert token in FORM_NAMES, f"Form {token} has no canonical description"
            assert FORM_NAMES[token].startswith(f"Form {token.upper()}")

    def test_every_detectable_form_yields_a_usable_query(self):
        """Undetectable-nameless forms fall back to the bare label.

        ``ix``/``x`` are detectable but have no canonical description, so
        ``form_query`` must still produce a query rather than None or "".
        """
        from app.rag.retrieval.form_references import _DETECTABLE_FORMS

        for token in _DETECTABLE_FORMS:
            got = form_query(f"Which is Form {token.upper()}?")
            assert got, f"Form {token} produced no query"
            assert f"Form {token.upper()}" in got

    def test_single_table_no_alias_inconsistency(self):
        """The old _FORM_CUES said analyst->viia while _FORM_NAMES said vii.

        Two tables meant the same query produced two different form numbers
        depending on which path fired.
        """
        assert FORM_NAMES["vii"] == "Form VII (analyst report)"
        assert "viia" not in FORM_NAMES
        assert "analyst report" in form_query("Which form does Form VII use?")

    def test_form_query_carries_label_and_description(self):
        got = form_query("Which form does the FSO give in Form VIII?")
        assert got == "Form VIII (appeal to designated officer)"
        assert got.startswith("Form VIII")


class TestArmIsGated:
    def test_flag_defaults_off(self):
        from app.shared.config import cfg

        assert cfg.form_arm is False

    def test_form_arm_is_its_own_setting(self):
        """The arm used to ride on ``cfg.identifier_route``.

        Borrowing another lever's flag meant the form arm could not be A/B'd
        or disabled on its own, and disabling the identifier arm silently
        removed form retrieval too.  Pin that they are independent knobs.
        """
        from app.shared.config import cfg

        assert hasattr(cfg, "form_arm")
        assert hasattr(cfg, "identifier_route")
        assert cfg.form_arm is False
        assert cfg.identifier_route is True  # the two must be able to differ

    def test_understanding_exposes_form_query_independently_of_the_flag(self):
        """Detection is unconditional; arming is a separate decision.

        Keeping them separate is what lets the A/B turn the arm on without
        changing what the detector thinks.
        """
        u = understand("Which form does the FSO give in Form VIII?")
        assert u.form_query == "Form VIII (appeal to designated officer)"

    def test_non_form_procedure_query_has_no_form_query(self):
        u = understand("How to get FSSAI registration?")
        assert u.form_query is None


class TestArmPoolDepth:
    def test_form_arm_does_not_outsize_the_vector_pool(self, monkeypatch):
        """RRF is rank-based: a deeper auxiliary list wins by construction.

        The arm asked for ``max(top_k * 2, 20)`` while dense/sparse fetched
        ``top_k`` — so every form-arm item entered near the top of its own
        list and outranked vector results below that depth.
        """
        import inspect

        from app.rag.retrieval.hybrid_retriever import HybridRetriever

        src = inspect.getsource(HybridRetriever.retrieve)
        assert "form_result = self.sparse.retrieve(form_query, top_k=top_k, filters=None)" in src
        assert "form_query, top_k=max(" not in src

    def test_form_arm_failure_is_contained(self):
        """A dead sparse backend must not fail the query."""
        import inspect

        from app.rag.retrieval.hybrid_retriever import HybridRetriever

        src = inspect.getsource(HybridRetriever.retrieve)
        assert "HybridRetriever: form arm failed" in src
