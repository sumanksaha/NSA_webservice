import re
import os

modules = ['case_file_generator', 'adjudication', 'bill_generator']

for module in modules:
    filepath = f'app\\{module}\\routes.py'
    if os.path.exists(filepath):
        print(f"\n=== ROUTES IN {module} ===")
        with open(filepath, 'r') as f:
            content = f.read()
            # Find all route decorators
            routes = re.findall(r'@\w+\.route\([^)]+\)', content)
            for route in routes:
                print(f"  {route}")
            
            # Find all function definitions that are routes
            lines = content.split('\n')
            for i, line in enumerate(lines, 1):
                if '@' in line and 'route' in line:
                    # Get the next line which should be the function def
                    if i < len(lines):
                        next_line = lines[i].strip()
                        if next_line.startswith('def '):
                            print(f"  {line.strip()} -> {next_line}")
    else:
        print(f"\n=== {module} routes.py not found ===")
