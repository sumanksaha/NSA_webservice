"""Test full RAG pipeline end-to-end with stub LLM."""
import sys, json, os
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv(dotenv_path='.env')
os.environ['RAG_USE_STUB_LLM'] = 'true'

from app import create_app
app = create_app()
with app.app_context():
    app.config['RAG_USE_STUB_LLM'] = True
    from app.rag.tasks import run_generation_pipeline

    print('=== FULL PIPELINE (stub LLM) ===')
    result = run_generation_pipeline(
        query='what is the penalty for selling contaminated food under FSS Act',
        top_k=5
    )
    print('query_type:', result.get('query_type'))
    ans = result.get('answer', '')
    print('answer:', json.dumps(ans[:600]))
    print('groundedness:', result.get('groundedness_score'),
          '| hallucinated:', result.get('hallucination_detected'))
    print('hallucinated_claims:', result.get('hallucinated_claims', []))
    print('error:', result.get('error'))