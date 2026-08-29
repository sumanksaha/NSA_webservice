"""Run upsert script in background with output to file."""
import subprocess
import sys

# Run the script and capture output
result = subprocess.run(
    [sys.executable, 'C:/github/NSA_webservice/scripts/upsert_kmc_csv_to_supabase.py', '--batch-size', '5000'],
    capture_output=True,
    text=True,
    timeout=600  # 10 minutes max
)

# Write output to file
with open('C:/github/NSA_webservice/upsert_output.log', 'w') as f:
    f.write("STDOUT:\n")
    f.write(result.stdout)
    f.write("\n\nSTDERR:\n")
    f.write(result.stderr)
    f.write(f"\n\nReturn code: {result.returncode}\n")

print(f"Process completed with return code: {result.returncode}")
print(f"Output written to upsert_output.log")
print("\n--- STDOUT (last 50 lines) ---")
for line in result.stdout.split('\n')[-50:]:
    print(line)
if result.stderr:
    print("\n--- STDERR (last 20 lines) ---")
    for line in result.stderr.split('\n')[-20:]:
        print(line)
