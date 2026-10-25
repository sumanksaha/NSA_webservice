"""Patch step3_gated_generation.py to add text-fallback gate."""
import pathlib

fpath = pathlib.Path("evaluation/step3_gated_generation.py")
content = fpath.read_text()

old_covered = '''    def _covered(pair: tuple[str, str]) -> bool:
        fam, sec = pair
        for cid in cited_ids:
            for f, s in payload_to_keys(deps.payload.get(cid, {}), deps.fam_map):
                if f == fam and str(s) == sec:
                    return True
        return False

    required = set(t["unit_pairs"]) | set(needed_pairs)
    uncovered = sorted(f"{f}/{s}" for (f, s) in required if not _covered((f, s))
    rec["context_chunk_count"] = len(cited_ids)
    rec["uncovered_targets"] = uncovered

    if uncovered:
        rec.update(
            verdict="rejected",
            reason="gold_span_still_absent_from_payload",
            note="target text in index but did not survive into the rebuilt context",
        )
        return rec

    rec.update(
        verdict="eligible",
        reason="text_added_to_payload",
        gold_span_in_payload=True,
        new_payload_ids=new_ids,
        context=built.context,
        context_chunk_count=len(cited_ids),
    )
    return rec'''

# The actual file has \r\n line endings, so we need to handle that
# Let's replace line by line
lines = content.split('\n')
# Find the _covered function
for i, line in enumerate(lines):
    if 'def _covered(pair: tuple[str, str]) -> bool:' in line:
        print(f"Found _covered at line {i+1}")
        # Show surrounding lines
        for j in range(i, min(i+25, len(lines))):
            print(f"{j+1:4d}: {lines[j]}")
        break
