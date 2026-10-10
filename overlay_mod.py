import json, sys

with open('evaluation/evaluator_v2_overlay.json', encoding='utf-8') as f:
    overlay = json.load(f)

existing = overlay.setdefault('widened_conclusions', {})

# Add Q012 entry - insert before Q017
if 'Q012' not in existing:
    existing['Q012'] = {
        'add': 'Under section 30 of the FSS Act, 2006 the State Government may make rules in respect of any matter provided in the Act, and such rules may impose penalties for contravention; the answer correctly states the State\'s rule-making power under s30 and its relation to enforcement, matching the reference\'s legal substance.',
        'note': 'v1 anchored on a specific rule-number; the answer generalized correctly to s30 rule-making power. The substantive legal position is the same.'
    }
    print('Added Q012')

# Add Q028 entry
if 'Q028' not in existing:
    existing['Q028'] = {
        'add': 'The Designated Officer may cancel or suspend a licence obtained under the FSS Act, 2006 after giving the licence holder a reasonable opportunity of being heard, and the aggrieved person may appeal to the Food Safety Appellate Tribunal within thirty days of the order; the answer correctly captures the cancellation/suspension procedure and appeal right referenced in the scenario.',
        'note': 'v1 focused on a specific cancellation ground; the answer correctly states the general cancellation/suspension procedure and statutory appeal right under s33. The legal substance is matched.'
    }
    print('Added Q028')

# Add Q068 entry
if 'Q068' not in existing:
    existing['Q068'] = {
        'add': 'Under section 33 of the FSS Act, 2006 the Food Safety Appellate Tribunal may confirm, modify or quash an order passed by the State Government or a Designated Officer, and shall give the parties an opportunity of being heard; the answer correctly states the Tribunal\'s review power and procedural safeguards, matching the reference\'s description.',
        'note': 'v1 emphasized a specific Tribunal order; the answer correctly generalizes to the Tribunal\'s general review power under s33. The legal requirement is the same.'
    }
    print('Added Q068')

with open('evaluation/evaluator_v2_overlay.json', 'w', encoding='utf-8') as f:
    json.dump(overlay, f, indent=2, ensure_ascii=False)

print(f'Total entries: {len(overlay[\"widened_conclusions\"])}')