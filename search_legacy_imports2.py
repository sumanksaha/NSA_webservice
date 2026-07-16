import os
import re

# Search for imports that would point to the old root-level modules
# The old structure had: case_file_generator/, bill_generator/, adjudication/
# at the root level, not under app/

# Patterns for bare imports (without app. prefix)
bare_import_patterns = [
    r'^from (case_file_generator|bill_generator|adjudication)\b',
    r'^import (case_file_generator|bill_generator|adjudication)\b',
]

# Also check for imports that might be in the middle of lines
inline_patterns = [
    r'from (case_file_generator|bill_generator|adjudication)\b',
    r'import (case_file_generator|bill_generator|adjudication)\b',
]

# Skip these files (our own search scripts)
skip_files = ['search_imports.py', 'search_legacy_imports.py', 'search_legacy_imports2.py']

results = []

for root, dirs, files in os.walk('.'):
    # Skip .git and __pycache__ and instance
    dirs[:] = [d for d in dirs if d not in ['.git', '__pycache__', 'instance']]
    for file in files:
        if file.endswith('.py') and file not in skip_files:
            filepath = os.path.join(root, file)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                    lines = content.split('\n')
                    for i, line in enumerate(lines, 1):
                        stripped = line.strip()
                        # Skip empty lines and comments
                        if not stripped or stripped.startswith('#'):
                            continue
                        
                        # Check for bare imports
                        for pattern in bare_import_patterns:
                            if re.search(pattern, stripped):
                                # Make sure it's not part of app.*
                                if 'app.' not in stripped:
                                    results.append((filepath, i, stripped))
                                    break
                        
                        # Check for inline imports that aren't part of app.*
                        for pattern in inline_patterns:
                            if re.search(pattern, stripped) and 'app.' not in stripped:
                                # Avoid double-counting
                                already_added = False
                                for r in results:
                                    if r[0] == filepath and r[1] == i:
                                        already_added = True
                                        break
                                if not already_added:
                                    results.append((filepath, i, stripped))
                                break
            except Exception as e:
                pass

if results:
    print("=== LEGACY IMPORTS FOUND (pointing to removed root-level paths) ===")
    for filepath, line_num, line in results:
        print(f"{filepath}:{line_num}: {line}")
else:
    print("=== NO LEGACY IMPORTS FOUND ===")
