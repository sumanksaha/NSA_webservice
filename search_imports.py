import os

search_terms = ["case_file_generator", "bill_generator", "adjudication"]
results = []

for root, dirs, files in os.walk("."):
    # Skip .git and __pycache__
    dirs[:] = [d for d in dirs if d not in [".git", "__pycache__", "instance"]]
    for file in files:
        if file.endswith(".py"):
            filepath = os.path.join(root, file)
            try:
                with open(filepath, encoding="utf-8") as f:
                    content = f.read()
                    for term in search_terms:
                        if term in content:
                            # Find line numbers
                            lines = content.split("\n")
                            for i, line in enumerate(lines, 1):
                                if term in line and ("import" in line or "from" in line):
                                    results.append((filepath, i, line.strip()))
            except Exception:
                pass

if results:
    for filepath, _line_num, line in results:
        pass
else:
    pass
