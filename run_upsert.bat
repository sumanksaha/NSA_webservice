@echo off
REM Run upsert script in background
echo Starting upsert process...
cd /d C:\github\NSA_webservice
python scripts/upsert_kmc_csv_to_supabase.py --batch-size 2000 > upsert_output.log 2>&1
echo Upsert completed. Check upsert_output.log for details