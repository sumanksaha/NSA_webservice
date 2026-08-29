@echo off
cd /d C:\github\NSA_webservice
set DATABASE_URL=postgresql://postgres.ugvrmjqrumscccrhvcto:fyP4fLbREF8jzpVt@aws-0-ap-southeast-2.pooler.supabase.com:6543/postgres
python upsert_reg.py > upsert_reg.log 2>&1