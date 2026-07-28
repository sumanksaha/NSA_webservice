import os
import re

# Search for imports that would point to the old root-level modules
# The old structure had: case_file_generator/, bill_generator/, adjudication/
# at the root level, not under app/

search_patterns = [
    r"from case_file_generator",
    r"import case_file_generator",
    r"from bill_generator",
    r"import bill_generator",
    r"from adjudication",
    r"import adjudication",
]

# But we need to exclude the new app.* imports
exclude_patterns = [
    r"from app\.case_file_generator",
    r"from app\.bill_generator",
    r"from app\.adjudication",
    r"import app\.case_file_generator",
    r"import app\.bill_generator",
    r"import app\.adjudication",
]

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
                    lines = content.split("\n")
                    for i, line in enumerate(lines, 1):
                        # Check if line matches any search pattern
                        is_legacy = False
                        for pattern in search_patterns:
                            if re.search(pattern, line):
                                is_legacy = True
                                break

                        if is_legacy:
                            # Check if it's excluded (new app.* imports)
                            is_excluded = False
                            for pattern in exclude_patterns:
                                if re.search(pattern, line):
                                    is_excluded = True
                                    break

                            if not is_excluded:
                                results.append((filepath, i, line.strip()))
            except Exception:
                pass

if results:
    print("=== LEGACY IMPORTS FOUND (pointing to removed paths) ===")
    for filepath, line_num, line in results:
        print(f"{filepath}:{line_num}: {line}")
else:
    print("=== NO LEGACY IMPORTS FOUND ===")
