@echo off
cd /d C:\github\NSA_webservice
python -m evaluation.train_legal_ce_v2 --fresh --save-every 50 > evaluation\out\models\legal_ce_v2_K500\train.log 2>&1
