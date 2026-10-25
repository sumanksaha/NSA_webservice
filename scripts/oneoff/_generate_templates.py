import json
import random

# Load gold provisions
g = json.load(open("benchmark/gold_provisions_v1.0.json"))
provisions = g["provisions"]
print(f"Loaded {len(provisions)} gold provisions")

# Also load the full corpus - check if there's a larger set
# From the doc: "The corpus contains 1,861 provisions plus a gold registry of 99 provisions"
# Let's look for the full corpus

# First, explore domain distribution
domains = set(p["domain"] for p in provisions)
print(f"\nDomains: {domains}")

# Section distribution
sections = set(p["section"] for p in provisions)
print(f"Sections: {sorted(sections)[:20]}...")

# Act distribution
acts = set(p["act"] for p in provisions)
print(f"Unique acts: {len(acts)}")

# Build template generators
# Template 1: "Under Section X, what is [permitted/prohibited] regarding Y?"
# Template 2: "What is the maximum [limit] under Section X?"
# Template 3: "According to Section X of [act], what action is required for [actor]?"
# Template 4: "What does Section X state about [topic]?"


def make_question_1(p):
    """Under Section X, what is permitted/prohibited regarding Y?"""
    # Use the title as Y, or create a topic from domain
    topic = p["title"] if p["title"] else f"matter under {p['domain']}"
    action = random.choice(["is permitted", "is prohibited", "is allowed", "is restricted"])
    return f"Under Section {p['section']} of the {p['act']}, what {action} regarding {topic}?"


def make_question_2(p):
    """What is the maximum limit under Section X?"""
    # Look for numeric limits in title or create generic
    limit_terms = ["fine", "penalty", "imprisonment term", "fine amount", "prison term"]
    limit = random.choice(limit_terms)
    return f"What is the maximum {limit} under Section {p['section']} of the {p['act']}?"


def make_question_3(p):
    """According to Section X of [act], what action is required for [actor]?"""
    actor = random.choice(["a person", "an entity", "a company", "an organization", "any individual"])
    return f"According to Section {p['section']} of the {p['act']}, what action is required for {actor}?"


def make_question_4(p):
    """What does Section X state about [topic]?"""
    topic = p["title"] if p["title"] else f"the {p['domain']} provisions"
    return f"What does Section {p['section']} state about {topic}?"


# Generate questions for all 99 provisions
templates = [make_question_1, make_question_2, make_question_3, make_question_4]

generated = []
for p in provisions:
    q = random.choice(templates)(p)
    generated.append({
        "qid": p["id"],
        "question": q,
        "section": p["section"],
        "act": p["act"],
        "title": p["title"],
        "domain": p["domain"],
        "expected_answer_type": "text",  # would be filled by human
        "template_used": [t.__name__ for t in templates].index(
            [
                t.__name__
                for t in templates
                if t.__code__.co_name == make_question_1.__code__.co_name
                or t.__code__.co_name == make_question_2.__code__.co_name
                or t.__code__.co_name == make_question_3.__code__.co_name
                or t.__code__.co_name == make_question_4.__code__.co_name
            ][0],
        ),
    })

# Actually let's just tag which template was used more carefully
template_names = ["make_question_1", "make_question_2", "make_question_3", "make_question_4"]
for p in provisions:
    t = random.choice(template_names)
    func = globals()[t]
    q = func(p)
    generated.append({
        "qid": p["id"],
        "question": q,
        "section": p["section"],
        "act": p["act"],
        "title": p["title"],
        "domain": p["domain"],
        "template": t,
    })

# Shuffle
random.shuffle(generated)

# Output
out = {
    "version": "template-generation-1",
    "generated": len(generated),
    "template_names": template_names,
    "questions": generated,
}

with open("benchmark/gold_provisions_templates_v1.json", "w") as f:
    json.dump(out, f, indent=2)

print(f"\nWrote {len(generated)} templated questions to benchmark/gold_provisions_templates_v1.json")

# Show some examples
print("\n--- Examples ---")
for i, q in enumerate(generated[:8]):
    print(f"{i + 1}. Q: {q['question'][:120]}...")
    print(f"   A: qid={q['qid']}, section={q['section']}, act={q['act']}, template={q.get('template')}")
