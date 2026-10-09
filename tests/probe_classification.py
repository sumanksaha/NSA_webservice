import sys
import time
from pathlib import Path

LOG = open(Path(__file__).parent / "probe_classification.log", "w")

def log(*args, **kwargs):
    msg = " ".join(str(a) for a in args)
    print(msg, file=LOG, **kwargs)
    print(msg, file=sys.stderr, **kwargs)

sys.path.insert(0, str(Path(__file__).parent.parent))

START = time.monotonic()
log(f"start {time.ctime()}")

# ------------------------------------------------------------------
# Probe A: workflow query recognizer on paraphrased procedural / form queries
# ------------------------------------------------------------------
log("--- Probe A: workflow_query_recognizer ---")
from app.rag.query_understanding.workflow_query_recognizer import (
    WorkflowRecognition,
    recognize_workflow_query,
)

q_appeal = "How can the FBO appeal to the designated officer?"
q_form = "Which form can the FBO use to appeal?"
q_bare_form = "Form VIII designated officer"
q_general = "What is FSSAI?"

for q in [q_appeal, q_form, q_bare_form, q_general]:
    r = recognize_workflow_query(q)
    log(f"query={q!r} -> type={type(r).__name__}")
    if isinstance(r, WorkflowRecognition):
        log(f"    form_mentioned={r.form_mentioned}, matched_patterns={r.matched_patterns}")
        log(f"    action_keywords={r.action_keywords}, procedure_keywords={r.procedure_keywords}")

# ------------------------------------------------------------------
# Probe B: query_classifier classify + food_query_understanding intent
# ------------------------------------------------------------------
log("--- Probe B: query_classifier + FoodQueryUnderstanding ---")
from app.rag.retrieval.food_query_understanding import FoodQueryUnderstanding, detect_food_intent
from app.rag.retrieval.query_classifier import QueryClassifier

for q in [q_appeal, q_form, q_bare_form, "Define ginger", "What is regulation 2.3.1 about?", "How should samples be drawn?"]:
    qc = QueryClassifier()
    cls = qc.classify(q)
    fqu = FoodQueryUnderstanding.from_query(q)
    intent, conf = detect_food_intent(q)
    log(f"query={q!r}")
    log(f"    QueryType={cls}, FoodIntent={intent} (conf={conf:.2f}), entity={fqu.entity}, target={fqu.target}")

# ------------------------------------------------------------------
# Probe C: form alias detection
# ------------------------------------------------------------------
log("--- Probe C: form_references detect_form ---")
from app.rag.retrieval.form_references import detect_form, form_query

for q in [
    "Which form for appeal",
    "receipt form",
    "order in form",
    "bond form",
    "notice form",
    "memorandum to analyst",
    "analyst report form",
    "appeal form",
    "Which form can the FBO use to appeal?",
]:
    form = detect_form(q)
    fq = form_query(q)
    log(f"query={q!r} -> detect_form={form!r}, form_query_lexical={fq!r}")

# ------------------------------------------------------------------
# Probe D: identifier detect_clause / identifier_query
# ------------------------------------------------------------------
log("--- Probe D: identifier ---")
from app.rag.retrieval.identifier import detect_clause, identifier_query

for q in ["regulation 2.4.6", "2.4.6", "food additive 2.9.11", "clause 2.3.1"]:
    log(f"query={q!r} -> detect_clause={detect_clause(q)!r}, identifier_query={identifier_query(q)}")

# ------------------------------------------------------------------
# Probe E: query_planner plan
# ------------------------------------------------------------------
log("--- Probe E: query_planner ---")
from app.rag.planning.query_planner import QueryPlanner

q = "How can the FBO appeal to the designated officer?"
planner = QueryPlanner()
dec = planner.plan(q)
log(f"query={q!r}")
log(f"    intent={dec.intent}, complexity={dec.complexity}, evidence_requirements={[e.value for e in dec.evidence_requirements]}")
for t in dec.tasks:
    log(f"    task_id={t.task_id}, req={t.source_requirement_id}, objective={t.objective!r}, evidence_req={t.evidence_requirement}")

# ------------------------------------------------------------------
# Probe F: food_answer system prompt routing (intent -> prompt)
# ------------------------------------------------------------------
log("--- Probe F: food_answer build_food_system_prompt ---")
from app.rag.generation.food_answer import build_food_system_prompt

for intent in ["procedure", "definition", "general_information", "sampling", "compliance"]:
    sys_prompt = build_food_system_prompt(intent)
    log(f"intent={intent!r} -> prompt starts: {sys_prompt[:120]!r}...")

log(f"\nDone in {time.monotonic() - START:.2f}s")
