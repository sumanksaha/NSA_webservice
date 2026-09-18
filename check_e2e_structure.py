import json

with open('evaluation/out/ceiling_v5/e2e_eval_v2.json') as f:
    data = json.load(f)

print("Top-level keys:", list(data.keys()))
print()
for key in data:
    val = data[key]
    if isinstance(val, dict):
        if key == "llm_generation":
            print(f"  llm_generation keys: {list(val.keys())}")
            if 'aggregate' in val:
                for m in val['aggregate']:
                    print(f"    {m}: {list(val['aggregate'][m].keys())[:10]}")
        elif key == "failure_classification":
            print(f"  failure_classification keys: {list(val.keys())[:5]}")
            if 'per_question' in val:
                print(f"    per_question has {len(val['per_question'])} entries")
                # Show one example
                for k, v in list(val['per_question'].items())[:1]:
                    print(f"    Example: {k}: {list(v.keys())}")
        elif key == "query_decomposition":
            print(f"  query_decomposition: {list(val.keys())}")
            if 'per_question' in val:
                print(f"    per_question has {len(val['per_question'])} entries")
        else:
            print(f"  {key}: {list(val.keys())[:5] if len(str(val)) > 200 else val}")
    elif isinstance(val, list):
        print(f"  {key}: list of {len(val)} items")
    elif isinstance(val, str):
        print(f"  {key}: {val[:100]}")
    else:
        print(f"  {key}: {type(val).__name__} = {val}")
