import json
import os

for root, dirs, files in os.walk("."):
    for f in files:
        if f.endswith(".json"):
            path = os.path.join(root, f)
            try:
                data = json.load(open(path))
                if isinstance(data, dict) and "provisions" in data:
                    provs = data["provisions"]
                    print(f"{path}: dict with provisions list, len={len(provs)}")
                    if provs:
                        print(f"  first: {provs[0]}")
                elif isinstance(data, list) and data and isinstance(data[0], dict):
                    keys = list(data[0].keys())
                    prov_keys = [k for k in keys if k in ("id", "act", "section", "title", "domain")]
                    if prov_keys:
                        print(f"{path}: list of dicts, len={len(data)}, keys={keys[:8]}")
                        if data:
                            print(f"  first: {data[0]}")
            except Exception:
                pass

for root, dirs, files in os.walk("docs"):
    for f in files:
        if f.endswith(".md"):
            path = os.path.join(root, f)
            try:
                text = open(path).read()
                if "provision" in text.lower() and "section" in text.lower():
                    print(f"{path}: has provision+section references")
            except Exception:
                pass
