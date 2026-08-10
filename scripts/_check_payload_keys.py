"""Check payload keys and enrichment field presence."""
from dotenv import load_dotenv
load_dotenv(dotenv_path='.env')
from app import create_app
app = create_app()
with app.app_context():
    from app.rag.qdrant_client import QdrantStore
    store = QdrantStore()
    pts = store.scroll_points(limit=5)
    keys = set()
    for p in pts:
        keys.update(p['payload'].keys())
    print('payload keys present in collection:')
    for k in sorted(keys):
        print(f'  {k}')

    enrichment = {
        'citations', 'references', 'entities', 'quality_score',
        'document_classification', 'document_type', 'document_authority',
        'section_number'
    }
    missing = enrichment - keys
    present = enrichment & keys
    print(f'\nEnrichment fields PRESENT: {present}')
    print(f'Enrichment fields MISSING: {missing}')