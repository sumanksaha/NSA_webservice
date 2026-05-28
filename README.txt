
INSTALL

pip install -r requirements.txt
playwright install chromium

RUN

uvicorn app:app --reload

DEPLOY TO RENDER

1 Push folder to GitHub
2 Create Render Web Service
3 Render auto-detects render.yaml
4 Deploy

URL:
http://127.0.0.1:8000
