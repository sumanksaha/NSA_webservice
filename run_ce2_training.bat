@echo off
cd /d C:\github\NSA_webservice
echo [%date% %time%] Starting CE2 training >> evaluation\out\models\legal_ce_v2_K500\train.log
python -m evaluation.train_legal_ce_v2 --fresh --save-every 50 >> evaluation\out\models\legal_ce_v2_K500\train.log 2>&1
echo [%date% %time%] Training finished with exit code %ERRORLEVEL% >> evaluation\out\models\legal_ce_v2_K500\train.log
