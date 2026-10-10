@echo off
cd /d C:/github/NSA_webservice
call .venv/Scripts/python.exe -m pytest tests/test_food_intent_retrieval.py tests/test_rag_generation_characterization.py -q -p no:cacheprovider --tb=short