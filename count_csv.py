import csv

with open('C:/github/NSA_webservice/db/kmc_license_issued.csv', encoding='utf-8', errors='replace') as f:
    n_license = sum(1 for r in csv.reader(f)) - 1
with open('C:/github/NSA_webservice/db/kmc_registration_issued.csv', encoding='utf-8', errors='replace') as f:
    r = sum(1 for r in csv.reader(f)) - 1

print(f"License CSV rows: {n_license}")
print(f"Registration CSV rows: {r}")
