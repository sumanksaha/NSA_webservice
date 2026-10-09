import os

# Find JSON files with "provision" in name
for root, dirs, files in os.walk("."):
    for f in files:
        if f.endswith(".json") and "provision" in f.lower():
            print(os.path.join(root, f))

# Also check docs for provision-related content
for root, dirs, files in os.walk("docs"):
    for f in files:
        if f.endswith(".md") and ("provision" in f.lower() or "registry" in f.lower()):
            print(os.path.join(root, f))
