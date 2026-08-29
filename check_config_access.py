import sys, os, re
sys.stdout.reconfigure(encoding='utf-8')
root = 'app'
# All setting keys
settings = set()
with open('app/shared/config.py', encoding='utf-8') as f:
    for line in f:
        m = re.match(r'\s*Setting\(\s*"(\w+)"', line)
        if m:
            settings.add(m.group(1))

# Find ALL direct config accesses (current_app.config, app.config, etc.)
pat = re.compile(r'(?:current_app|app)\.config(?:\.get\(|\[)\s*\(?\s*["\'](\w+)["\']')
for dirpath, _, files in os.walk(root):
    for fname in files:
        if not fname.endswith('.py'):
            continue
        fpath = os.path.join(dirpath, fname)
        with open(fpath, encoding='utf-8', errors='replace') as f:
            for i, line in enumerate(f, 1):
                for m in pat.finditer(line):
                    key = m.group(1)
                    if key in settings:
                        print(f"{fpath}:{i}: {line.strip()[:140]}")
