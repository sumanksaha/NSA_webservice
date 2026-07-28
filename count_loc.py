import glob

# Count lines of Python code
py_files = glob.glob("C:\\github\\NSA_webservice\\**\\*.py", recursive=True)
total_lines = 0
for f in py_files:
    try:
        with open(f, encoding="utf-8", errors="ignore") as file:
            lines = file.readlines()
            # Count non-empty, non-comment lines
            code_lines = [l for l in lines if l.strip() and not l.strip().startswith("#")]
            total_lines += len(code_lines)
    except Exception as e:
        print(f"Error reading {f}: {e}")

print(f"Total Python files: {len(py_files)}")
print(f"Total lines of Python code: {total_lines}")

# Also count all lines including comments and blank
all_lines = 0
for f in py_files:
    try:
        with open(f, encoding="utf-8", errors="ignore") as file:
            all_lines += len(file.readlines())
    except Exception:
        pass

print(f"Total lines (including comments/blank): {all_lines}")
