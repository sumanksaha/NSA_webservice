import json
import sys

# Step 1: Read current overlay
with open("evaluation/evaluator_v2_overlay.json", encoding="utf-8") as f:
    overlay = json.load(f)

existing = overlay.setdefault("widened_conclusions", {})

# Step 2: Add entries one at a time (will be run as separate commands)
sys.exit(0)  # Stop here; entries added via subsequent commands
