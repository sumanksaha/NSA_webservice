import csv
import os

os.chdir('/github/NSA_webservice')

# Inspect kmc_license_issued.csv
print("=== kmc_license_issued.csv ===")
with open('db/kmc_license_issued.csv', 'r', encoding='utf-8', errors='replace') as f:
    reader = csv.reader(f)
    headers = next(reader)
    print(f"Columns ({len(headers)}): {headers}")
    for i, row in enumerate(reader):
        if i < 3:
            print(f"Row {i}: {row[:10]}{'...' if len(row) > 10 else ''}")
        else:
            break

# Count rows
with open('db/kmc_license_issued.csv', 'r', encoding='utf-8', errors='replace') as f:
    lines = sum(1 for _ in f) - 1
    print(f"Total data rows: {lines}")

# Inspect kmc_registration_issued.csv
print("\n=== kmc_registration_issued.csv ===")
with open('db/kmc_registration_issued.csv', 'r', encoding='utf-8', errors='replace') as f:
    reader = csv.reader(f)
    headers = next(reader)
    print(f"Columns ({len(headers)}): {headers}")
    for i, row in enumerate(reader):
        if i < 3:
            print(f"Row {i}: {row[:10]}{'...' if len(row) > 10 else ''}")
        else:
            break

# Count rows
with open('db/kmc_registration_issued.csv', 'r', encoding='utf-8', errors='replace') as f:
    lines = sum(1 for _ in f) - 1
    print(f"Total data rows: {lines}")