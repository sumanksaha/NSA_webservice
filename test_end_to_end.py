import os
import sys
os.chdir('C:\\github\\NSA_webservice')
sys.path.insert(0, 'C:\\github\\NSA_webservice')

from app import create_app
from app.extensions import db
from app.models import Sample, FSO, Bill
from app.bill_generator.utils import get_billable_samples
from datetime import datetime

app = create_app()

with app.app_context():
    # Clear existing data
    db.drop_all()
    db.create_all()
    
    # Create FSO
    fso = FSO(fso_name='Test FSO')
    db.session.add(fso)
    db.session.commit()
    print("[OK] FSO created")
    
    # Create samples
    sample1 = Sample(
        sample_code='S001',
        sample_name='Test Enforcement',
        sample_type='enforcement',
        fso_name='Test FSO',
        collection_date='2026-01-15',
        retailer_name='Retailer A',
        price='100.50',
        billed=False
    )
    sample2 = Sample(
        sample_code='S002',
        sample_name='Test Surveillance',
        sample_type='surveillance',
        fso_name='Test FSO',
        collection_date='2026-01-15',
        retailer_name='Retailer B',
        price='200.75',
        billed=False
    )
    db.session.add_all([sample1, sample2])
    db.session.commit()
    print("[OK] Samples created")
    
    # Test get_billable_samples
    result = get_billable_samples('2026-01-15', '2026-01-15')
    print(f"\nget_billable_samples result:")
    print(f"  enforcement_no: {result['enforcement_no']} (expected: 1)")
    print(f"  enforcement_price: {result['enforcement_price']} (expected: 100.50)")
    print(f"  surveillance_no: {result['surveillance_no']} (expected: 1)")
    print(f"  surveillance_price: {result['surveillance_price']} (expected: 200.75)")
    print(f"  samples count: {len(result['samples'])} (expected: 2)")
    
    if result['enforcement_no'] == 1 and result['surveillance_no'] == 1:
        print("\n[PASS] Step 4: get_billable_samples end-to-end test")
    else:
        print("\n[FAIL] Step 4: get_billable_samples returned wrong counts")
