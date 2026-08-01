import os
import re

modules = ["case_file_generator", "adjudication", "bill_generator"]

for module in modules:
    filepath = f"app\\{module}\\routes.py"
    if os.path.exists(filepath):
        with open(filepath) as f:
            content = f.read()
            # Find all route decorators
            routes = re.findall(r"@\w+\.route\([^)]+\)", content)
            for _route in routes:
                pass

            # Find all function definitions that are routes
            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                if "@" in line and "route" in line:
                    # Get the next line which should be the function def
                    if i < len(lines):
                        next_line = lines[i].strip()
                        if next_line.startswith("def "):
                            pass
    else:
        pass
