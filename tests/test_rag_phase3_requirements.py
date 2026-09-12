"""Phase 3 requirement-graph tests: decomposition to answer requirements,
requirement-level benchmark metrics, and per-claim verification semantics.

Pure-function tests — no retrieval backend, no LLM (the planner is the only
dependency and it is deterministic regex-based).  Complements
``tests/test_rag_phase4_measurement.py`` (kind-level benchmark gate) with the
Phase 3 surfaces: AnswerRequirementGraph, requirement-conditioned sufficiency,
per-claim ClaimVerification status, and the RC/AS/DE/EC metrics.
"""

from __future__ import annotations

from app.rag.agent.sufficiency import (
    SufficiencyAssessor,
    task_requirement_id,
)
from app.rag.evidence_task import (
    AnswerRequirement,
    AnswerRequirementGraph,
    ClaimVerification,
    ClaimVerificationStatus,
    EvidenceRequirement,
    EvidenceTask,
    build_claim_verification,
    requirement_graph_from_tasks,
    requirement_to_answer_type,
)
from app.rag.evaluation.benchmark import DecompositionBenchmark
from app.rag.evaluation.decomposition_metrics import (
    atomicity_score,
    decomposition_efficiency,
    evidence_completeness,
    requirement_coverage,
)
from app.rag.evaluation.gold_dataset import GOLD_DECOMPOSITION
from app.rag.planning.query_planner import QueryPlanner


# --------------------------------------------------------------------------- #
# AnswerRequirement / AnswerRequirementGraph model
# --------------------------------------------------------------------------- #
class TestAnswerRequirementModel:
    def test_round_trip_through_dict(self):
        req = AnswerRequirement(
            id="R1",
            type=EvidenceRequirement.PENALTY,
            subject="late filing",
            question="What penalty applies?",
            answer_type="numeric_or_rule",
            evidence_required=["penalty", "fine"],
            mandatory=True,
            conditions=["section 42"],
            jurisdiction="India",
            temporal_scope="2024",
        )
        restored = AnswerRequirement.from_dict(req.to_dict())
        assert restored.id == "R1"
        assert restored.type is EvidenceRequirement.PENALTY
        assert restored.mandatory is True
        assert restored.conditions == ["section 42"]
        assert restored.jurisdiction == "India"

    def test_unknown_type_falls_back_to_provision(self):
        req = AnswerRequirement.from_dict({"id": "R9", "type": "not_a_type", "subject": "s", "question": "q?"})
        assert req.type is EvidenceRequirement.PROVISION

    def test_graph_round_trip_preserves_dependencies_and_source_ids(self):
        graph = AnswerRequirementGraph(
            query="q",
            requirements=[
                AnswerRequirement(id="r1", type=EvidenceRequirement.PROVISION, subject="s1", question="q1?"),
                AnswerRequirement(id="r2", type=EvidenceRequirement.PENALTY, subject="s2", question="q2?"),
            ],
            dependencies=[("r1", "r2")],
            derived_tasks=[
                EvidenceTask(
                    task_id="T1",
                    objective="o",
                    question="q1?",
                    evidence_requirement=EvidenceRequirement.PROVISION,
                    source_requirement_id="r1",
                ),
            ],
        )
        restored = AnswerRequirementGraph.from_dict(graph.to_dict())
        assert [tuple(d) for d in restored.dependencies] == [("r1", "r2")]
        assert restored.derived_tasks[0].source_requirement_id == "r1"
        assert restored.mandatory_ids() == ["r1", "r2"]
        assert restored.requirement_by_id("r2") is restored.requirements[1]

    def test_requirement_graph_from_tasks_bridge(self):
        dep = EvidenceTask(
            task_id="T6",
            objective="o",
            question="q0?",
            evidence_requirement=EvidenceRequirement.PROVISION,
        )
        task = EvidenceTask(
            task_id="T7",
            objective="o",
            question="q?",
            evidence_requirement=EvidenceRequirement.EXCEPTION,
            dependency=["T6"],
        )
        graph = requirement_graph_from_tasks("qq", [dep, task], make_mandatory=False)
        assert graph.requirements[0].id == "T6"
        assert graph.requirements[1].id == "T7"
        assert graph.requirements[1].mandatory is False
        assert graph.dependencies == [("T6", "T7")]

    def test_bridge_ignores_dependencies_outside_the_task_list(self):
        task = EvidenceTask(
            task_id="T7",
            objective="o",
            question="q?",
            evidence_requirement=EvidenceRequirement.EXCEPTION,
            dependency=["T6"],  # T6 not included below — no resolvable edge
        )
        graph = requirement_graph_from_tasks("qq", [task])
        assert graph.dependencies == []


# --------------------------------------------------------------------------- #
# Planner: requirement identity + decomposition
# --------------------------------------------------------------------------- #
class TestPlannerRequirementIdentity:
    def test_every_task_carries_source_requirement_id(self):
        plan = QueryPlanner().plan("What is the penalty for operating without a licence under the FSS Act?")
        assert plan.requirement_graph is not None
        for task in plan.tasks:
            assert task.source_requirement_id
            assert task.source_requirement_id in plan.requirement_graph.requirement_ids()
            # The magic-string marker is gone from retrieval-scoping entities.
            assert not any(e.startswith("requirement_id:") for e in task.entities)

    def test_task_dependencies_mirror_into_requirement_dependencies(self):
        plan = QueryPlanner().plan(
            "What is the penalty for operating without a licence under the FSS Act, and who can initiate action?"
        )
        graph = plan.requirement_graph
        dep_task = next(t for t in plan.tasks if t.dependency)
        assert dep_task.source_requirement_id
        dep_req = next(
            t.source_requirement_id for t in plan.tasks if t.task_id in dep_task.dependency
        )
        assert (dep_req, dep_task.source_requirement_id) in graph.dependencies

    def test_serialized_tasks_keep_source_id(self):
        plan = QueryPlanner().plan("What does Section 42 prohibit and what is the penalty?")
        task = plan.tasks[0]
        restored = EvidenceTask.from_dict(task.to_dict())
        assert restored.source_requirement_id == task.source_requirement_id


class TestPlannerSecondaryRequirements:
    """Phase 3 torture-test gaps: secondary requirements must be extracted."""

    def test_temporal_query_yields_amendment_requirement(self):
        plan = QueryPlanner().plan("Was Section 12 of the FSS Act amended after 2020?")
        types = [r.type for r in plan.requirement_graph.requirements]
        assert EvidenceRequirement.PROVISION in types
        assert EvidenceRequirement.AMENDMENT in types

    def test_temporal_before_query_yields_amendment_requirement(self):
        plan = QueryPlanner().plan(
            "What was the applicable penalty for substandard food before the 2021 amendment?"
        )
        types = [r.type for r in plan.requirement_graph.requirements]
        assert EvidenceRequirement.PENALTY in types
        assert EvidenceRequirement.AMENDMENT in types

    def test_enforcement_query_yields_authority_requirement(self):
        plan = QueryPlanner().plan(
            "What is prohibited under Section 31 of the FSS Act, who can enforce it, "
            "what is the penalty, and what exceptions apply?"
        )
        types = [r.type for r in plan.requirement_graph.requirements]
        assert EvidenceRequirement.AUTHORITY in types
        assert EvidenceRequirement.PENALTY in types
        assert EvidenceRequirement.EXCEPTION in types
        # One task per requirement — the SIMPLE collapse must not merge them.
        assert len(plan.tasks) == len(plan.requirement_graph.requirements)

    def test_multi_hop_rule_query_typed_cross_reference(self):
        plan = QueryPlanner().plan(
            "Which provision authorizes the recall order prescribed by Rule 2.3.1 "
            "of the FSS Regulations, and what penalty follows from non-compliance?"
        )
        types = [r.type for r in plan.requirement_graph.requirements]
        assert EvidenceRequirement.CROSS_REFERENCE in types
        assert EvidenceRequirement.PENALTY in types
        penalty_task = next(
            t for t in plan.tasks if t.evidence_requirement is EvidenceRequirement.PENALTY
        )
        assert penalty_task.dependency, "penalty resolves through the cross-reference"

    def test_permission_query_yields_definition_and_exception(self):
        plan = QueryPlanner().plan("Does Section 58 of the FSS Act permit the sale of handmade cosmetics?")
        types = [r.type for r in plan.requirement_graph.requirements]
        assert EvidenceRequirement.DEFINITION in types
        assert EvidenceRequirement.EXCEPTION in types

    def test_yes_no_offence_question_yields_fact_application(self):
        plan = QueryPlanner().plan(
            "A food business operator continued operations after his licence was "
            "suspended. Has he committed an offence under the FSS Act?"
        )
        types = [r.type for r in plan.requirement_graph.requirements]
        assert EvidenceRequirement.FACT_APPLICATION in types

    def test_simple_lookup_stays_single_task(self):
        plan = QueryPlanner().plan("What is Section 12?")
        assert len(plan.tasks) == 1
        assert len(plan.requirement_graph.requirements) == 1


# --------------------------------------------------------------------------- #
# task_requirement_id (field + legacy fallback)
# --------------------------------------------------------------------------- #
class TestTaskRequirementId:
    def test_reads_first_class_field(self):
        task = EvidenceTask(
            task_id="T1",
            objective="o",
            question="q?",
            evidence_requirement=EvidenceRequirement.PENALTY,
            source_requirement_id="r2",
        )
        assert task_requirement_id(task) == "r2"

    def test_legacy_entity_marker_fallback(self):
        task = EvidenceTask(
            task_id="T1",
            objective="o",
            question="q?",
            evidence_requirement=EvidenceRequirement.PENALTY,
            entities=["requirement_id:r5"],
        )
        assert task_requirement_id(task) == "r5"

    def test_none_when_absent(self):
        task = EvidenceTask(
            task_id="T1",
            objective="o",
            question="q?",
            evidence_requirement=EvidenceRequirement.PENALTY,
        )
        assert task_requirement_id(task) is None


# --------------------------------------------------------------------------- #
# Sufficiency: verdicts stamped with requirement ids
# --------------------------------------------------------------------------- #
class TestSufficiencyRequirementStamp:
    def test_verdicts_carry_requirement_id(self):
        plan = QueryPlanner().plan("What does Section 42 prohibit and what is the penalty?")
        assessor = SufficiencyAssessor()
        chunk = {
            "text": "Section 42 provides the penalty is Rs 5,00,000.",
            "score": 0.8,
            "section_number": "42",
            "document_type": "act",
            "chunk_id": "k1",
        }
        verdicts = [assessor.assess_task(t, [chunk]) for t in plan.tasks]
        for verdict in verdicts:
            assert verdict.requirement_id
        assert {v.requirement_id for v in verdicts} == set(
            plan.requirement_graph.requirement_ids()
        )


# --------------------------------------------------------------------------- #
# build_claim_verification: per-claim signal semantics
# --------------------------------------------------------------------------- #
def _claim(cid: str, chunks: list[str]) -> dict:
    return {"claim_id": cid, "text": f"claim {cid}", "chunk_ids": list(chunks)}


def _verdict(chunks: list[str], *, verified: bool = True, confidence: float = 0.95) -> dict:
    return {
        "verified": verified,
        "confidence": confidence,
        "supporting_chunks": list(chunks),
    }


class TestPerClaimVerification:
    def test_authority_is_per_claim_not_global(self):
        claims = [_claim("C1", ["a"]), _claim("C2", ["b"])]
        verifs = [_verdict(["a"]), _verdict(["b"])]
        out = build_claim_verification(
            claims, verifs, chunk_authority={"a": 1.0, "b": 0.5}
        )
        assert out[0].authority_score == 1.0
        assert out[0].status is ClaimVerificationStatus.SUPPORTED
        assert out[1].authority_score == 0.5
        assert out[1].status is ClaimVerificationStatus.PARTIALLY_SUPPORTED

    def test_contradiction_not_guilty_by_association(self):
        """A chunk in an unrelated contradiction pair must not taint a claim."""
        claims = [_claim("C1", ["a"])]
        verifs = [_verdict(["a"])]
        pairs = [{"chunk_a": "a", "chunk_b": "c", "kind": "numeric", "values": ["5", "10"]}]
        out = build_claim_verification(
            claims, verifs, chunk_authority={"a": 1.0, "c": 1.0}, contradictions=pairs
        )
        assert out[0].status is ClaimVerificationStatus.SUPPORTED
        assert out[0].contradictions == []

    def test_contradiction_when_both_sides_are_own_evidence(self):
        claims = [_claim("C3", ["a", "c"])]
        verifs = [_verdict(["a", "c"])]
        pairs = [{"chunk_a": "a", "chunk_b": "c", "kind": "numeric", "values": ["5", "10"]}]
        out = build_claim_verification(
            claims, verifs, chunk_authority={"a": 1.0, "c": 1.0}, contradictions=pairs
        )
        assert out[0].status is ClaimVerificationStatus.CONTRADICTED
        assert len(out[0].contradictions) == 1

    def test_unsupported_with_own_contradiction_is_contradicted(self):
        claims = [_claim("C1", ["a", "c"])]
        verifs = [_verdict(["a", "c"], verified=False, confidence=0.2)]
        pairs = [{"chunk_a": "a", "chunk_b": "c", "kind": "numeric", "values": ["5", "10"]}]
        out = build_claim_verification(claims, verifs, contradictions=pairs)
        assert out[0].status is ClaimVerificationStatus.CONTRADICTED

    def test_temporal_cap_on_repealed_evidence(self):
        claims = [_claim("C1", ["a"])]
        verifs = [_verdict(["a"])]
        out = build_claim_verification(
            claims, verifs, chunk_authority={"a": 1.0}, temporally_invalid_ids={"a"}
        )
        assert out[0].status is ClaimVerificationStatus.PARTIALLY_SUPPORTED
        assert out[0].temporal_valid is False

    def test_temporal_valid_when_evidence_clean(self):
        claims = [_claim("C1", ["a"])]
        verifs = [_verdict(["a"])]
        out = build_claim_verification(
            claims, verifs, chunk_authority={"a": 1.0}, temporally_invalid_ids={"z"}
        )
        assert out[0].temporal_valid is True
        assert out[0].status is ClaimVerificationStatus.SUPPORTED

    def test_neutral_authority_floor_without_metadata(self):
        """Claims with no authority metadata score the 0.5 neutral floor."""
        claims = [_claim("C1", ["a"])]
        verifs = [_verdict(["a"])]
        out = build_claim_verification(claims, verifs)
        assert out[0].authority_score == 0.5
        assert out[0].status is ClaimVerificationStatus.PARTIALLY_SUPPORTED

    def test_claims_and_verifications_serialize(self):
        out = build_claim_verification(
            [_claim("C1", ["a"])],
            [_verdict(["a"])],
            chunk_authority={"a": 0.99},
        )
        assert isinstance(out[0], ClaimVerification)
        d = out[0].to_dict()
        assert d["status"] == "SUPPORTED"
        assert d["claim_id"] == "C1"


# --------------------------------------------------------------------------- #
# Requirement-level metrics
# --------------------------------------------------------------------------- #
def _graph(reqs: list[tuple[str, str, str, bool]]) -> AnswerRequirementGraph:
    """Build a graph from (id, type, subject, mandatory) tuples."""
    return AnswerRequirementGraph(
        query="q",
        requirements=[
            AnswerRequirement(
                id=rid,
                type=EvidenceRequirement(rtype),
                subject=subj,
                question=f"{subj}?",
                mandatory=mand,
            )
            for rid, rtype, subj, mand in reqs
        ],
        dependencies=[],
    )


class TestRequirementCoverage:
    def test_order_insensitive_type_matching(self):
        gold = [
            {"id": "R1", "type": "penalty", "subject": "late filing"},
            {"id": "R2", "type": "provision", "subject": "late filing"},
        ]
        graph = _graph(
            [
                ("r2", "provision", "late filing", True),
                ("r1", "penalty", "late filing", True),
            ]
        )
        assert requirement_coverage(graph, gold) == 1.0

    def test_distinct_subjects_require_separate_predictions(self):
        gold = [
            {"id": "R1", "type": "condition", "subject": "small food businesses"},
            {"id": "R2", "type": "condition", "subject": "large food manufacturers"},
        ]
        # Only one side found — a same-type duplicate cannot satisfy both.
        graph = _graph(
            [
                ("r1", "condition", "small food businesses", True),
                ("r1", "condition", "small food businesses", True),
            ]
        )
        assert requirement_coverage(graph, gold) == 0.5

    def test_type_mismatch_does_not_match(self):
        gold = [{"id": "R1", "type": "amendment", "subject": "Section 12"}]
        graph = _graph([("r1", "provision", "Section 12", True)])
        assert requirement_coverage(graph, gold) == 0.0

    def test_no_gold_scores_one(self):
        graph = _graph([("r1", "provision", "s", True)])
        assert requirement_coverage(graph, []) == 1.0


class TestAtomicityAndEfficiency:
    def test_atomic_tasks_score_one(self):
        tasks = [
            EvidenceTask(
                task_id="T1",
                objective="o",
                question="What penalty applies to late filing?",
                evidence_requirement=EvidenceRequirement.PENALTY,
            )
        ]
        assert atomicity_score(tasks) == 1.0

    def test_conjunctive_question_is_multi_claim(self):
        tasks = [
            EvidenceTask(
                task_id="T1",
                objective="o",
                question="What is prohibited and what is the penalty?",
                evidence_requirement=EvidenceRequirement.PROVISION,
            )
        ]
        assert atomicity_score(tasks) == 0.0

    def test_empty_task_list_scores_one(self):
        assert atomicity_score([]) == 1.0

    def test_efficiency_penalizes_extra_requirements(self):
        gold = [{"id": "R1", "type": "provision", "subject": "s"}]
        graph = _graph(
            [
                ("r1", "provision", "s", True),
                ("r2", "scope", "unrelated scope", True),
            ]
        )
        assert decomposition_efficiency(graph, gold) == 0.5


class TestEvidenceCompleteness:
    def test_conservative_zero_without_data(self):
        graph = _graph([("r1", "provision", "s", True)])
        assert evidence_completeness(graph, None) == 0.0

    def test_counts_only_sufficient_mandatory(self):
        graph = _graph(
            [
                ("r1", "provision", "s", True),
                ("r2", "penalty", "p", True),
                ("r3", "scope", "x", False),
            ]
        )
        assert evidence_completeness(graph, {"r1": True}) == 0.5
        assert evidence_completeness(graph, {"r1": True, "r2": True, "r3": True}) == 1.0

    def test_no_mandatory_scores_one(self):
        graph = _graph([("r1", "scope", "s", False)])
        assert evidence_completeness(graph, None) == 1.0


# --------------------------------------------------------------------------- #
# Benchmark end-to-end (requirement-level section)
# --------------------------------------------------------------------------- #
class TestBenchmarkRequirementLevel:
    def test_full_pipeline_scores_one_over_gold_set(self):
        planner = QueryPlanner()
        bench = DecompositionBenchmark()
        suff: dict[str, dict[str, bool]] = {}
        for entry in GOLD_DECOMPOSITION:
            bench.add_gold_entry(entry)
            plan = planner.plan(entry["query"])
            bench.record_prediction(entry["query"], plan)
            assert plan.requirement_graph is not None
            suff[entry["query"]] = {r.id: True for r in plan.requirement_graph.requirements}

        report = bench.evaluate(sufficiency_map=suff)
        rl = report["requirement_level"]
        assert rl["total_scored_queries"] == len(GOLD_DECOMPOSITION)
        assert rl["requirement_coverage"] == 1.0
        assert rl["atomicity_score"] == 1.0
        assert rl["decomposition_efficiency"] == 1.0
        assert rl["evidence_completeness"] == 1.0

    def test_ec_is_zero_without_sufficiency_map(self):
        planner = QueryPlanner()
        bench = DecompositionBenchmark()
        for entry in GOLD_DECOMPOSITION:
            bench.add_gold_entry(entry)
            bench.record_prediction(entry["query"], planner.plan(entry["query"]))
        rl = bench.evaluate()["requirement_level"]
        assert rl["evidence_completeness"] == 0.0

    def test_per_query_breakdown_present(self):
        planner = QueryPlanner()
        bench = DecompositionBenchmark()
        for entry in GOLD_DECOMPOSITION[:1]:
            bench.add_gold_entry(entry)
            bench.record_prediction(entry["query"], planner.plan(entry["query"]))
        rl = bench.evaluate()["requirement_level"]
        assert rl["per_query"]
        assert rl["per_query"][0]["gold_count"] == 2
