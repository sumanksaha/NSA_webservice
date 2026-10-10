import sys
sys.path.insert(0, ".")

# Canonical definition site and both consumers import cleanly.
from evaluation.split_policy import split_census, answer_eval_qids
from evaluation.benchmark import load_questions
import tests.test_split_policy as tsp

census = split_census()
assert isinstance(census, dict), census
assert set(census) == {"dev", "validation", "holdout"}, census
assert len(census["dev"]) == 100
assert len(census["validation"]) == 30
assert len(census["holdout"]) == 20
assert sum(map(len, census.values())) == 150

# The two consumers agree with the canonical census.
assert tsp._census() == census
assert tsp._question_ids_by_split("dev") == census["dev"]
assert tsp._question_ids_by_split("validation") == census["validation"]
assert tsp._question_ids_by_split("holdout") == census["holdout"]

# The policy module's answer-eval slice matches census["validation"].
assert set(answer_eval_qids()) == set(census["validation"])
assert len(answer_eval_qids()) == 30

# No duplicate qids anywhere in the census.
all_qids = [q for v in census.values() for q in v]
assert len(all_qids) == len(set(all_qids)), "census has duplicates"

# load_questions() now honours split= and defaults to the full frozen set.
assert len(load_questions()) == 150
assert len(load_questions(split="validation")) == 30
assert len(load_questions(split=["validation", "holdout"])) == 50
assert load_questions(split="nope") == []
assert load_questions(split=[]) == []
assert all(q.split == "validation" for q in load_questions(split="validation"))
assert set(q.question_id for q in load_questions(split="validation")) == set(census["validation"])

print("split_census cleanup smoke test OK", flush=True)
