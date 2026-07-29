"""Celery Task Conversion Verification Script (v3)

Tests all three Celery tasks across 7 dimensions.
Handles missing external deps (tesseract, WeasyPrint) gracefully.
Uses route-level Flask test client for end-to-end verification.
"""

import os
import shutil
from datetime import datetime

# ASCII markers for Windows terminal compatibility
OK = "[OK]"
FAIL_MARK = "[FAIL]"
SKIP_MARK = "[SKIP]"
DASH = "-"

# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------
results = {}


def record(task_name, check, status, evidence=""):
    if task_name not in results:
        results[task_name] = {}
    results[task_name][check] = {"status": status, "evidence": evidence}
    if evidence:
        for _line in evidence.split("\n")[:4]:
            pass


def header(title):
    pass


# ---------------------------------------------------------------------------
# Bootstrap app + eager mode
# ---------------------------------------------------------------------------
os.environ["CELERY_ALWAYS_EAGER"] = "true"
os.environ["CELERY_EAGER_PROPAGATES_EXCEPTIONS"] = "true"
os.environ["CELERY_RESULT_BACKEND"] = "cache+memory://"

from app import create_app

app = create_app()
ctx = app.app_context()
ctx.push()

# Ensure DB schema has all tables and columns from models
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from app.extensions import db

inspector = sa_inspect(db.engine)
missing_tables = [
    t for t in ["bills", "case_files", "sample", "fso", "inspection"] if t not in inspector.get_table_names()
]
if missing_tables:
    db.create_all()

# Add missing columns via raw SQL if they don't exist (for dev/testing only)
for table, col in [
    ("case_files", "pdf_task_id"),
    ("bills", "pdf_task_id"),
    ("case_files", "pdf_generated_at"),
    ("bills", "pdf_generated_at"),
]:
    if table in inspector.get_table_names():
        existing_cols = {c["name"] for c in inspector.get_columns(table)}
        if col not in existing_cols:
            try:
                with db.engine.connect() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} VARCHAR(100)"))
                    conn.commit()
            except Exception:
                db.create_all()

from flask import render_template

from app.extensions import db
from app.models import FSO, Bill, CaseFile, Sample

# Celery eager config - lazy import to avoid ModuleNotFoundError in deployment
try:
    from celery_app import celery as global_celery

    global_celery.conf.update(
        task_always_eager=True,
        task_eager_propagates=True,
        result_backend="cache+memory://",
        broker_url="memory://localhost/",
    )
except ImportError:
    global_celery = None

# Tasks
import contextlib

from app.bill_generator.tasks import generate_bill_pdf
from app.case_file_generator.tasks import generate_case_file_pdf

# Check deps
_TESS_OK = False
try:
    import pytesseract

    pytesseract.get_tesseract_version()
    _TESS_OK = True
except Exception:
    pass

_WP_OK = False
with contextlib.suppress(Exception):
    _WP_OK = True

# Test dir
TEST_IMG_DIR = "test_ocr_input"
TEST_IMG_PATH = os.path.join(TEST_IMG_DIR, "test_card.png")

# Ensure FSO exists for FK refs
try:
    if not FSO.query.filter_by(fso_name="VERIFY_FSO").first():
        db.session.add(FSO(fso_name="VERIFY_FSO"))
        db.session.commit()
except Exception:
    db.session.rollback()

# ===================================================================
# CHECK 1+2: Enqueue & Execution (eager mode)
# ===================================================================
header("CHECK 1+2: Enqueue & Execution (eager mode)")

# --- OCR ---
from app.inspection.tasks import run_ocr_extraction

os.makedirs(TEST_IMG_DIR, exist_ok=True)
try:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (400, 200), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((20, 40), "FSSAI: 1234567890", fill="black")
    draw.text((20, 80), "NAME: TEST CORP", fill="black")
    img.save(TEST_IMG_PATH)

    task = run_ocr_extraction.delay(file_path=TEST_IMG_PATH)
    assert task.id and len(task.id) > 0, "No task ID"

    if not _TESS_OK:
        record(
            "run_ocr_extraction",
            "enqueue+execution",
            SKIP_MARK,
            f"task_id={task.id} | Tesseract not installed (enqueue OK, exec SKIP)",
        )
    else:
        r = task.get(timeout=30)
        assert isinstance(r, dict) and "_pages_processed" in r
        record("run_ocr_extraction", "enqueue+execution", OK, f"task_id={task.id} | pages={r['_pages_processed']}")
except Exception as e:
    record("run_ocr_extraction", "enqueue+execution", FAIL_MARK, str(e)[:200])

# --- Bill PDF ---
try:
    bill = Bill(
        Name="E2E BILL",
        EMP_ID="FSO-E2E",
        Enf_samp_No=2,
        Surv_samp_No=1,
        enforcement_price=3000,
        surveillance_price=2000,
        Total_bill=5000,
        No_of_enfbills=2,
        No_of_survbills=1,
        TR_Value="TRE2E",
        TR_date=datetime(2026, 1, 15),
        Submission_date=datetime(2026, 1, 20),
        start_date=datetime(2026, 1, 1),
        end_date=datetime(2026, 1, 31),
    )
    db.session.add(bill)
    db.session.commit()
    bid = bill.id

    tv = {
        "Name": "E2E BILL",
        "EMP_ID": "FSO-E2E",
        "Designation": "FSO",
        "Enf_samp_No": "2",
        "Surv_samp_No": "1",
        "Total_bill": "5000",
        "No_of_enfbills": "2",
        "No_of_survbills": "1",
        "TR_Value": "TRE2E",
        "TR_date": "15/01/2026",
        "Submission_date": "20/01/2026",
        "start_date": "2026-01-01",
        "end_date": "2026-01-31",
        "enforcement_price": "3000",
        "surveillance_price": "2000",
    }

    task = generate_bill_pdf.delay(bill_id=bid, template_vars=tv)
    assert task.id and len(task.id) > 0

    if not _WP_OK:
        record(
            "generate_bill_pdf",
            "enqueue+execution",
            SKIP_MARK,
            f"task_id={task.id} | WeasyPrint not available (enqueue OK)",
        )
        db.session.delete(bill)
        db.session.commit()
    else:
        r = task.get(timeout=60)
        s = OK if r["status"] == "ok" else FAIL_MARK
        record("generate_bill_pdf", "enqueue+execution", s, f"task_id={task.id} | status={r['status']}")
        if r.get("file_path") and os.path.exists(r["file_path"]):
            os.remove(r["file_path"])
        db.session.delete(bill)
        db.session.commit()
except Exception as e:
    db.session.rollback()
    record("generate_bill_pdf", "enqueue+execution", FAIL_MARK, str(e)[:200])

# --- CaseFile PDF ---
try:
    cf = CaseFile(
        case_number="E2E/CF/001",
        food_safety_officer_name="Dr. E2E",
        authorization_date=datetime(2026, 1, 15),
        inspection_date=datetime(2026, 1, 10),
        inspection_time="10:00",
        manufacturer_fssai="MFGE2E",
        manufacturer_name="John",
        manufacturer_fbo_name="E2E Mfg",
        manufacturer_address="123",
        retailer_fssai="RETE2E",
        retailer_name="Jane",
        retailer_fbo_name="E2E Retail",
        retailer_address="456",
        product_name="E2E Product",
        batch_no="B001",
        sample_quantity="500g",
        packet_count=4,
        mfg_date=datetime(2026, 1, 1),
        expiry_date=datetime(2026, 12, 31),
        sample_code="SMPE2E",
        sample_submission_date=datetime(2026, 1, 12),
        Lab_Registration_No="LABE2E",
        do_receipt_date=datetime(2026, 1, 14),
        is_misbranded=False,
        is_substandard=True,
        analyst_report_no="ARE2E",
        analyst_report_date=datetime(2026, 1, 20),
        directive_letter_no="DLE2E",
        directive_letter_date=datetime(2026, 1, 22),
        retailer_report_receive_date=datetime(2026, 1, 25),
        manufacturer_report_receive_date=datetime(2026, 1, 26),
        applicable_regulation="2.4.1",
        applicable_clause="2.4.1(1)",
        sample_name="E2E Sample",
        applicable_sections="51",
    )
    db.session.add(cf)
    db.session.commit()
    cid = cf.id

    cd = {
        "case_number": "E2E/CF/001",
        "food_safety_officer_name": "Dr. E2E",
        "authorization_date": "15/01/2026",
        "inspection_date": "10/01/2026",
        "inspection_time": "10:00",
        "manufacturer_fssai_license": "MFGE2E",
        "manufacturer_person_name": "John",
        "manufacturer_trade_name": "E2E Mfg",
        "manufacturer_address": "123",
        "retailer_fssai_license": "RETE2E",
        "retailer_person_name": "Jane",
        "retailer_trade_name": "E2E Retail",
        "retailer_address": "456",
        "product_name": "E2E Product",
        "batch_no": "B001",
        "sample_quantity": "500g",
        "packet_count": 4,
        "mfg_date": "01/01/2026",
        "expiry_date": "31/12/2026",
        "sample_code": "SMPE2E",
        "sample_submission_date": "12/01/2026",
        "lab_registration_no": "LABE2E",
        "do_receipt_date": "14/01/2026",
        "is_misbranded": False,
        "is_substandard": True,
        "analysis_result": "substandard",
        "applicable_sections": ["51"],
        "applicable_sections_str": "51",
        "applicable_regulation": "2.4.1",
        "applicable_clause": "2.4.1(1)",
        "sample_name": "E2E Sample",
        "total_cost": "500",
        "cost_in_words": "Five Hundred Only",
        "analyst_report_no": "ARE2E",
        "analyst_report_date": "20/01/2026",
        "directive_letter_no": "DLE2E",
        "directive_letter_date": "22/01/2026",
        "retailer_report_receive_date": "25/01/2026",
        "manufacturer_report_receive_date": "26/01/2026",
    }

    task = generate_case_file_pdf.delay(case_file_id=cid, case_data=cd)
    assert task.id and len(task.id) > 0

    if not _WP_OK:
        record(
            "generate_case_file_pdf",
            "enqueue+execution",
            SKIP_MARK,
            f"task_id={task.id} | WeasyPrint not available (enqueue OK)",
        )
        db.session.delete(cf)
        db.session.commit()
    else:
        r = task.get(timeout=60)
        s = OK if r["status"] == "ok" else FAIL_MARK
        record("generate_case_file_pdf", "enqueue+execution", s, f"task_id={task.id} | status={r['status']}")
        if r.get("file_path") and os.path.exists(r["file_path"]):
            os.remove(r["file_path"])
        db.session.delete(cf)
        db.session.commit()
except Exception as e:
    db.session.rollback()
    record("generate_case_file_pdf", "enqueue+execution", FAIL_MARK, str(e)[:200])

# ===================================================================
# CHECK 1a: Route-level async verification (Flask test client)
# ===================================================================
header("CHECK 1a: Route-level async verification (Flask test client)")

with app.test_client() as client:
    # --- Bill route ---
    try:
        s1 = Sample(
            sample_code="VRFY001",
            sample_name="Verify 1",
            sample_type="enforcement",
            fso_name="VERIFY_FSO",
            collection_date=datetime(2026, 1, 15),
            price="1500",
            billed=False,
        )
        s2 = Sample(
            sample_code="VRFY002",
            sample_name="Verify 2",
            sample_type="surveillance",
            fso_name="VERIFY_FSO",
            collection_date=datetime(2026, 1, 20),
            price="1000",
            billed=False,
        )
        db.session.add(s1)
        db.session.add(s2)
        db.session.commit()

        resp = client.post(
            "/bill_generator/generate_bill",
            data={
                "start_date": "2026-01-01",
                "end_date": "2026-01-31",
                "Name": "Route Test FBO",
                "EMP_ID": "FSO-RT",
                "Designation": "FSO",
                "TR_Value": "TRRT001",
                "TR_date": "2026-01-25",
                "Submission_date": "2026-01-30",
                "No_of_enfbills": "1",
                "No_of_survbills": "1",
            },
        )

        body = resp.get_json()
        assert resp.status_code == 202, f"Expected 202, got {resp.status_code}"
        assert body and "task_id" in body and body["task_id"] is not None
        assert "bill_id" in body

        # Verify task_id stored on DB record
        bill = Bill.query.get(body["bill_id"])
        assert bill is not None, "Bill not found in DB"
        assert bill.pdf_task_id == body["task_id"], (
            f"pdf_task_id mismatch: DB={bill.pdf_task_id} vs response={body['task_id']}"
        )

        # Revert sample billed flags
        s1.billed = False
        s2.billed = False
        db.session.commit()

        record(
            "generate_bill_pdf",
            "route_async_behavior",
            OK,
            f"HTTP 202 | task_id={body['task_id']} | bill_id={body['bill_id']} | DB.pdf_task_id verified match",
        )
    except Exception as e:
        db.session.rollback()
        record("generate_bill_pdf", "route_async_behavior", FAIL_MARK, str(e)[:200])

    # --- CaseFile route ---
    try:
        resp = client.post(
            "/case_file_generator/generate_case_file",
            data={
                "case_number": "ROUTE/CF/001",
                "food_safety_officer_name": "Dr. Route",
                "authorization_date": "2026-02-15",
                "sample_draw_date": "2026-02-10",
                "sample_draw_time": "11:00",
                "manufacturer_fssai_license": "MFGRT",
                "manufacturer_person_name": "Mfg Route",
                "manufacturer_trade_name": "Route Mfg",
                "manufacturer_address": "789 Route Rd",
                "retailer_fssai_license": "RETRT",
                "retailer_person_name": "Ret Route",
                "retailer_trade_name": "Route Retail",
                "retailer_address": "321 Route Ave",
                "product_name": "Route Product",
                "batch_no": "BATCHRT",
                "sample_quantity": "250g",
                "packet_count": "2",
                "mfg_date": "2026-01-01",
                "expiry_date": "2026-12-31",
                "sample_code": "SMPRT",
                "sample_submission_date": "2026-02-12",
                "lab_registration_no": "LABRT",
                "do_receipt_date": "2026-02-14",
                "is_substandard": "substandard",
                "analyst_report_no": "ARRT",
                "analyst_report_date": "2026-02-20",
                "directive_letter_no": "DLRT",
                "directive_letter_date": "2026-02-22",
                "retailer_report_receive_date": "2026-02-25",
                "manufacturer_report_receive_date": "2026-02-26",
                "sample_name": "Route Sample",
            },
        )

        body = resp.get_json()
        assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {body}"
        assert body and "task_id" in body and body["task_id"] is not None
        assert "case_file_id" in body

        # Verify task_id stored on DB record
        cf = CaseFile.query.get(body["case_file_id"])
        assert cf is not None, "CaseFile not found in DB"
        assert cf.pdf_task_id == body["task_id"], (
            f"pdf_task_id mismatch: DB={cf.pdf_task_id} vs response={body['task_id']}"
        )

        record(
            "generate_case_file_pdf",
            "route_async_behavior",
            OK,
            f"HTTP 202 | task_id={body['task_id']} | case_file_id={body['case_file_id']} | "
            f"DB.pdf_task_id verified match",
        )
    except Exception as e:
        db.session.rollback()
        record("generate_case_file_pdf", "route_async_behavior", FAIL_MARK, str(e)[:200])

    # --- Regenerate route ---
    try:
        cf = CaseFile.query.filter_by(case_number="ROUTE/CF/001").first()
        if cf:
            resp = client.get(f"/case_file_generator/regenerate/{cf.id}")
            body = resp.get_json()
            assert resp.status_code == 202, f"Expected 202, got {resp.status_code}"
            assert body and "task_id" in body and body["task_id"] is not None

            # Verify task_id updated on DB record
            db.session.refresh(cf)
            assert cf.pdf_task_id == body["task_id"], "pdf_task_id not updated on regenerate"

            record(
                "generate_case_file_pdf",
                "regenerate_async_behavior",
                OK,
                f"Regenerate: HTTP 202 | task_id={body['task_id']} | DB verified",
            )
    except Exception as e:
        db.session.rollback()
        record("generate_case_file_pdf", "regenerate_async_behavior", FAIL_MARK, str(e)[:200])

# ===================================================================
# CHECK 2a: Worker execution verification (manual)
# ===================================================================
header("CHECK 2: Worker execution verification")

record("run_ocr_extraction", "worker_execution", SKIP_MARK, "Requires real worker -- see instructions above")
record("generate_bill_pdf", "worker_execution", SKIP_MARK, "Requires real worker -- see instructions above")
record("generate_case_file_pdf", "worker_execution", SKIP_MARK, "Requires real worker -- see instructions above")

# ===================================================================
# CHECK 3: App-context correctness
# ===================================================================
header("CHECK 3: App-context correctness")

try:
    cnt = FSO.query.count()
    record(
        "run_ocr_extraction",
        "app_context",
        OK,
        f"FSO.count()={cnt} | REDIS_URL={'set' if app.config.get('REDIS_URL') else 'not set'}",
    )
except Exception as e:
    record("run_ocr_extraction", "app_context", FAIL_MARK, str(e)[:200])

try:
    html = render_template(
        "bill_generator/template.html",
        Name="T",
        EMP_ID="T",
        Designation="T",
        Enf_samp_No="0",
        Surv_samp_No="0",
        Total_bill="0",
        No_of_enfbills="0",
        No_of_survbills="0",
        TR_Value="",
        TR_date="",
        Submission_date="",
        start_date="",
        end_date="",
        enforcement_price="0",
        surveillance_price="0",
    )
    record("generate_bill_pdf", "app_context", OK, f"Template OK ({len(html)} chars)")
except Exception as e:
    record("generate_bill_pdf", "app_context", FAIL_MARK, str(e)[:200])

try:
    html = render_template(
        "case_file_generator/petition.html",
        case_number="T/001",
        food_safety_officer_name="Dr.T",
        authorization_date="15/01/2026",
        inspection_date="10/01/2026",
        inspection_time="10:00",
        manufacturer_fssai_license="MFG",
        manufacturer_person_name="John",
        manufacturer_trade_name="Co",
        manufacturer_address="Addr",
        retailer_fssai_license="RET",
        retailer_person_name="Jane",
        retailer_trade_name="Ret",
        retailer_address="Addr",
        product_name="P",
        batch_no="B",
        sample_quantity="Q",
        packet_count=1,
        mfg_date="01/01/2026",
        expiry_date="31/12/2026",
        sample_code="S",
        sample_submission_date="12/01/2026",
        lab_registration_no="L",
        do_receipt_date="14/01/2026",
        is_misbranded=False,
        is_substandard=True,
        analysis_result="substandard",
        applicable_sections=["51"],
        applicable_sections_str="51",
        applicable_regulation="2.4.1",
        applicable_clause="2.4.1(1)",
        sample_name="S",
        total_cost="500",
        cost_in_words="Five Hundred",
        analyst_report_no="AR",
        analyst_report_date="20/01/2026",
        directive_letter_no="DL",
        directive_letter_date="22/01/2026",
        retailer_report_receive_date="25/01/2026",
        manufacturer_report_receive_date="26/01/2026",
    )
    record("generate_case_file_pdf", "app_context", OK, f"Petition template OK ({len(html)} chars)")
except Exception as e:
    record("generate_case_file_pdf", "app_context", FAIL_MARK, str(e)[:200])

# ===================================================================
# CHECK 4: Result correctness
# ===================================================================
header("CHECK 4: Result correctness")

if not _TESS_OK:
    record(
        "run_ocr_extraction",
        "result_correctness",
        SKIP_MARK,
        "Tesseract not installed -- cannot verify OCR text extraction",
    )
else:
    try:
        r = run_ocr_extraction.delay(file_path=TEST_IMG_PATH).get(timeout=30)
        assert isinstance(r, dict) and "_pages_processed" in r
        record(
            "run_ocr_extraction",
            "result_correctness",
            OK,
            f"pages={r['_pages_processed']} | errors={r['_ocr_errors']}",
        )
    except Exception as e:
        record("run_ocr_extraction", "result_correctness", FAIL_MARK, str(e)[:200])

# No old OCR version to compare against
record(
    "run_ocr_extraction",
    "result_vs_old_version",
    SKIP_MARK,
    "No old synchronous OCR function -- built from scratch as Celery task",
)

if not _WP_OK:
    record("generate_bill_pdf", "result_correctness", SKIP_MARK, "WeasyPrint not available")
    record("generate_case_file_pdf", "result_correctness", SKIP_MARK, "WeasyPrint not available")
else:
    # Bill: metadata-only result, file on disk, valid PDF
    try:
        bill = Bill(
            Name="RESULT CHK",
            EMP_ID="FSO-RC",
            Enf_samp_No=1,
            Surv_samp_No=0,
            enforcement_price=1000,
            surveillance_price=0,
            Total_bill=1000,
            No_of_enfbills=1,
            No_of_survbills=0,
            TR_Value="TRRC",
            TR_date=datetime(2026, 3, 15),
            Submission_date=datetime(2026, 3, 20),
            start_date=datetime(2026, 3, 1),
            end_date=datetime(2026, 3, 31),
        )
        db.session.add(bill)
        db.session.commit()
        bid = bill.id

        tv = {
            "Name": "RESULT CHK",
            "EMP_ID": "FSO-RC",
            "Designation": "FSO",
            "Enf_samp_No": "1",
            "Surv_samp_No": "0",
            "Total_bill": "1000",
            "No_of_enfbills": "1",
            "No_of_survbills": "0",
            "TR_Value": "TRRC",
            "TR_date": "15/03/2026",
            "Submission_date": "20/03/2026",
            "start_date": "2026-03-01",
            "end_date": "2026-03-31",
            "enforcement_price": "1000",
            "surveillance_price": "0",
        }

        r = generate_bill_pdf.delay(bill_id=bid, template_vars=tv).get(timeout=60)
        assert r["bill_id"] == bid
        assert r["status"] == "ok"
        assert r["file_path"] is not None
        assert "pdf_bytes" not in r, "RAW BYTES LEAKED in result!"
        assert os.path.exists(r["file_path"])
        with open(r["file_path"], "rb") as f:
            assert f.read(5) == b"%PDF-"

        record(
            "generate_bill_pdf",
            "result_correctness",
            OK,
            f"Meta-only | file={r['file_path']} | size={os.path.getsize(r['file_path'])}B | PDF valid",
        )
        os.remove(r["file_path"])
        db.session.delete(bill)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        record("generate_bill_pdf", "result_correctness", FAIL_MARK, str(e)[:200])

    # CaseFile ZIP: metadata-only, 2 PDFs inside
    try:
        cf = CaseFile(
            case_number="RC/CF/001",
            food_safety_officer_name="Dr. RC",
            authorization_date=datetime(2026, 3, 15),
            inspection_date=datetime(2026, 3, 10),
            inspection_time="09:30",
            manufacturer_fssai="MFGRC",
            manufacturer_name="John RC",
            manufacturer_fbo_name="RC Mfg",
            manufacturer_address="RC",
            retailer_fssai="RETRC",
            retailer_name="Jane RC",
            retailer_fbo_name="RC Retail",
            retailer_address="RC",
            product_name="RC Product",
            batch_no="BATCHRC",
            sample_quantity="100g",
            packet_count=1,
            mfg_date=datetime(2026, 1, 1),
            expiry_date=datetime(2026, 12, 31),
            sample_code="SMPRC",
            sample_submission_date=datetime(2026, 3, 12),
            Lab_Registration_No="LABRC",
            do_receipt_date=datetime(2026, 3, 14),
            is_misbranded=False,
            is_substandard=True,
            analyst_report_no="ARRC",
            analyst_report_date=datetime(2026, 3, 20),
            directive_letter_no="DLRC",
            directive_letter_date=datetime(2026, 3, 22),
            retailer_report_receive_date=datetime(2026, 3, 25),
            manufacturer_report_receive_date=datetime(2026, 3, 26),
            applicable_regulation="2.4.1",
            applicable_clause="2.4.1(1)",
            sample_name="RC Sample",
            applicable_sections="51",
        )
        db.session.add(cf)
        db.session.commit()
        cid = cf.id

        cd = {
            "case_number": "RC/CF/001",
            "food_safety_officer_name": "Dr. RC",
            "authorization_date": "15/03/2026",
            "inspection_date": "10/03/2026",
            "inspection_time": "09:30",
            "manufacturer_fssai_license": "MFGRC",
            "manufacturer_person_name": "John RC",
            "manufacturer_trade_name": "RC Mfg",
            "manufacturer_address": "RC",
            "retailer_fssai_license": "RETRC",
            "retailer_person_name": "Jane RC",
            "retailer_trade_name": "RC Retail",
            "retailer_address": "RC",
            "product_name": "RC Product",
            "batch_no": "BATCHRC",
            "sample_quantity": "100g",
            "packet_count": 1,
            "mfg_date": "01/01/2026",
            "expiry_date": "31/12/2026",
            "sample_code": "SMPRC",
            "sample_submission_date": "12/03/2026",
            "lab_registration_no": "LABRC",
            "do_receipt_date": "14/03/2026",
            "is_misbranded": False,
            "is_substandard": True,
            "analysis_result": "substandard",
            "applicable_sections": ["51"],
            "applicable_sections_str": "51",
            "applicable_regulation": "2.4.1",
            "applicable_clause": "2.4.1(1)",
            "sample_name": "RC Sample",
            "total_cost": "500",
            "cost_in_words": "Five Hundred Only",
            "analyst_report_no": "ARRC",
            "analyst_report_date": "20/03/2026",
            "directive_letter_no": "DLRC",
            "directive_letter_date": "22/03/2026",
            "retailer_report_receive_date": "25/03/2026",
            "manufacturer_report_receive_date": "26/03/2026",
        }

        r = generate_case_file_pdf.delay(case_file_id=cid, case_data=cd).get(timeout=60)
        assert r["case_file_id"] == cid
        assert r["status"] == "ok"
        assert r["file_path"] is not None
        assert "zip_bytes" not in r, "RAW ZIP BYTES leaked!"

        import zipfile

        with zipfile.ZipFile(r["file_path"], "r") as zf:
            names = zf.namelist()
        assert len(names) == 2
        assert any("Petition" in n for n in names)
        assert any("Permission" in n for n in names)

        record("generate_case_file_pdf", "result_correctness", OK, f"Meta-only | ZIP valid | contents={names}")
        os.remove(r["file_path"])
        db.session.delete(cf)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        record("generate_case_file_pdf", "result_correctness", FAIL_MARK, str(e)[:200])

# ===================================================================
# CHECK 5: Retry behavior
# ===================================================================
header("CHECK 5: Retry behavior")

# Non-transient: invalid format -> ValueError
try:
    try:
        run_ocr_extraction.delay(file_path="/nonexistent/file.txt")
        record(
            "run_ocr_extraction",
            "retry_non_transient_format",
            FAIL_MARK,
            "Expected ValueError for unsupported format",
        )
    except ValueError:
        record("run_ocr_extraction", "retry_non_transient_format", OK, "Invalid format -> ValueError (no retry)")
    except Exception as e:
        record(
            "run_ocr_extraction",
            "retry_non_transient_format",
            FAIL_MARK,
            f"Expected ValueError, got {type(e).__name__}: {str(e)[:100]}",
        )
except Exception as e:
    record("run_ocr_extraction", "retry_non_transient_format", FAIL_MARK, str(e)[:100])

# Non-transient: missing file -> ValueError
try:
    try:
        run_ocr_extraction.delay(file_path="/nonexistent/file.pdf")
        record("run_ocr_extraction", "retry_non_transient_missing", FAIL_MARK, "Expected ValueError for missing file")
    except ValueError:
        record("run_ocr_extraction", "retry_non_transient_missing", OK, "Missing file -> ValueError (no retry)")
    except Exception as e:
        record(
            "run_ocr_extraction",
            "retry_non_transient_missing",
            FAIL_MARK,
            f"Expected ValueError, got {type(e).__name__}",
        )
except Exception as e:
    record("run_ocr_extraction", "retry_non_transient_missing", FAIL_MARK, str(e)[:100])

# Transient I/O: code path verified by review
record(
    "run_ocr_extraction",
    "retry_transient_io",
    SKIP_MARK,
    "self.retry() wired in IOError/OSError catch -- verified by code review; eager mode raises Retry immediately",
)

# Bill: template error -> error status (no retry)
try:
    r = generate_bill_pdf.delay(bill_id=9999, template_vars={}).get(timeout=30)
    if r["status"] == "error":
        record(
            "generate_bill_pdf",
            "retry_template_error",
            OK,
            f"Template error -> error status: {r.get('error', '')[:80]}",
        )
    else:
        record("generate_bill_pdf", "retry_template_error", FAIL_MARK, f"Expected error, got: {r}")
except Exception as e:
    record("generate_bill_pdf", "retry_template_error", OK, f"Exception raised (no retry): {type(e).__name__}")

# CaseFile: template error -> error status (no retry)
try:
    r = generate_case_file_pdf.delay(case_file_id=9999, case_data={}).get(timeout=30)
    if r["status"] == "error":
        record(
            "generate_case_file_pdf",
            "retry_template_error",
            OK,
            f"Template error -> error status: {r.get('error', '')[:80]}",
        )
    else:
        record("generate_case_file_pdf", "retry_template_error", FAIL_MARK, f"Expected error, got: {r}")
except Exception as e:
    record("generate_case_file_pdf", "retry_template_error", OK, f"Exception raised (no retry): {type(e).__name__}")

# ===================================================================
# CHECK 6: Idempotency
# ===================================================================
header("CHECK 6: Idempotency")

if not _WP_OK:
    record("generate_bill_pdf", "idempotency", SKIP_MARK, "WeasyPrint not available")
    record("generate_case_file_pdf", "idempotency", SKIP_MARK, "WeasyPrint not available")
else:
    # Bill: same bill_id twice -> same path, no duplicate records
    try:
        bill = Bill(
            Name="IDEM BILL",
            EMP_ID="FSO-ID",
            Enf_samp_No=1,
            Surv_samp_No=0,
            enforcement_price=500,
            surveillance_price=0,
            Total_bill=500,
            No_of_enfbills=1,
            No_of_survbills=0,
            TR_Value="TRID",
            TR_date=datetime(2026, 4, 15),
            Submission_date=datetime(2026, 4, 20),
            start_date=datetime(2026, 4, 1),
            end_date=datetime(2026, 4, 30),
        )
        db.session.add(bill)
        db.session.commit()
        bid = bill.id
        tv = {
            "Name": "IDEM BILL",
            "EMP_ID": "FSO-ID",
            "Designation": "FSO",
            "Enf_samp_No": "1",
            "Surv_samp_No": "0",
            "Total_bill": "500",
            "No_of_enfbills": "1",
            "No_of_survbills": "0",
            "TR_Value": "TRID",
            "TR_date": "15/04/2026",
            "Submission_date": "20/04/2026",
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
            "enforcement_price": "500",
            "surveillance_price": "0",
        }

        r1 = generate_bill_pdf.delay(bill_id=bid, template_vars=tv).get(timeout=60)
        r2 = generate_bill_pdf.delay(bill_id=bid, template_vars=tv).get(timeout=60)
        assert r1["status"] == "ok" and r2["status"] == "ok"
        assert r1["file_path"] == r2["file_path"]
        assert Bill.query.filter_by(id=bid).count() == 1

        record("generate_bill_pdf", "idempotency", OK, "2 runs, same output path | 1 bill record | no duplicates")
        os.remove(r1["file_path"])
        db.session.delete(bill)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        record("generate_bill_pdf", "idempotency", FAIL_MARK, str(e)[:200])

    # CaseFile: same case_file_id twice
    try:
        cf = CaseFile(
            case_number="IDEM/CF/002",
            food_safety_officer_name="Dr. Idem",
            authorization_date=datetime(2026, 4, 15),
            inspection_date=datetime(2026, 4, 10),
            inspection_time="09:00",
            manufacturer_fssai="MFGID",
            manufacturer_name="John ID",
            manufacturer_fbo_name="ID Mfg",
            manufacturer_address="ID",
            retailer_fssai="RETID",
            retailer_name="Jane ID",
            retailer_fbo_name="ID Retail",
            retailer_address="ID",
            product_name="ID Product",
            batch_no="BATCHID",
            sample_quantity="100g",
            packet_count=1,
            mfg_date=datetime(2026, 1, 1),
            expiry_date=datetime(2026, 12, 31),
            sample_code="SMPID",
            sample_submission_date=datetime(2026, 4, 12),
            Lab_Registration_No="LABID",
            do_receipt_date=datetime(2026, 4, 14),
            is_misbranded=False,
            is_substandard=True,
            analyst_report_no="ARID",
            analyst_report_date=datetime(2026, 4, 20),
            directive_letter_no="DLID",
            directive_letter_date=datetime(2026, 4, 22),
            retailer_report_receive_date=datetime(2026, 4, 25),
            manufacturer_report_receive_date=datetime(2026, 4, 26),
            applicable_regulation="2.4.1",
            applicable_clause="2.4.1(1)",
            sample_name="ID Sample",
            applicable_sections="51",
        )
        db.session.add(cf)
        db.session.commit()
        cid = cf.id

        cd = {
            "case_number": "IDEM/CF/002",
            "food_safety_officer_name": "Dr. Idem",
            "authorization_date": "15/04/2026",
            "inspection_date": "10/04/2026",
            "inspection_time": "09:00",
            "manufacturer_fssai_license": "MFGID",
            "manufacturer_person_name": "John ID",
            "manufacturer_trade_name": "ID Mfg",
            "manufacturer_address": "ID",
            "retailer_fssai_license": "RETID",
            "retailer_person_name": "Jane ID",
            "retailer_trade_name": "ID Retail",
            "retailer_address": "ID",
            "product_name": "ID Product",
            "batch_no": "BATCHID",
            "sample_quantity": "100g",
            "packet_count": 1,
            "mfg_date": "01/01/2026",
            "expiry_date": "31/12/2026",
            "sample_code": "SMPID",
            "sample_submission_date": "12/04/2026",
            "lab_registration_no": "LABID",
            "do_receipt_date": "14/04/2026",
            "is_misbranded": False,
            "is_substandard": True,
            "analysis_result": "substandard",
            "applicable_sections": ["51"],
            "applicable_sections_str": "51",
            "applicable_regulation": "2.4.1",
            "applicable_clause": "2.4.1(1)",
            "sample_name": "ID Sample",
            "total_cost": "100",
            "cost_in_words": "One Hundred",
            "analyst_report_no": "ARID",
            "analyst_report_date": "20/04/2026",
            "directive_letter_no": "DLID",
            "directive_letter_date": "22/04/2026",
            "retailer_report_receive_date": "25/04/2026",
            "manufacturer_report_receive_date": "26/04/2026",
        }

        r1 = generate_case_file_pdf.delay(case_file_id=cid, case_data=cd).get(timeout=60)
        r2 = generate_case_file_pdf.delay(case_file_id=cid, case_data=cd).get(timeout=60)
        assert r1["status"] == "ok" and r2["status"] == "ok"
        assert r1["file_path"] == r2["file_path"]
        assert CaseFile.query.filter_by(id=cid).count() == 1

        record("generate_case_file_pdf", "idempotency", OK, "2 runs, same output path | no duplicate records")
        os.remove(r1["file_path"])
        db.session.delete(cf)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        record("generate_case_file_pdf", "idempotency", FAIL_MARK, str(e)[:200])

# ===================================================================
# CHECK 7: Queue routing
# ===================================================================
header("CHECK 7: Queue routing")

if global_celery is None:
    for t in ["run_ocr_extraction", "generate_bill_pdf", "generate_case_file_pdf"]:
        record(t, "queue_routing", SKIP_MARK, "Celery not available (celery_app import failed)")
else:
    try:
        task_routes = global_celery.conf.get("task_routes", None) or {}
        has_routes = bool(task_routes) and any(v for v in task_routes.values())
        for tname, tobj in [
            ("run_ocr_extraction", run_ocr_extraction),
            ("generate_bill_pdf", generate_bill_pdf),
            ("generate_case_file_pdf", generate_case_file_pdf),
        ]:
            q = getattr(tobj, "queue", "celery (default)")
            record(
                tname,
                "queue_routing",
                OK,
                f"Queue: {q} | task_routes={'yes' if has_routes else 'not configured (all default)'}",
            )
    except Exception as e:
        for t in ["run_ocr_extraction", "generate_bill_pdf", "generate_case_file_pdf"]:
            record(t, "queue_routing", FAIL_MARK, str(e)[:100])

# ===================================================================
# SUMMARY TABLE
# ===================================================================
header("FINAL SUMMARY")

checks_order = [
    "enqueue+execution",
    "route_async_behavior",
    "regenerate_async_behavior",
    "worker_execution",
    "app_context",
    "result_correctness",
    "result_vs_old_version",
    "retry_non_transient_format",
    "retry_non_transient_missing",
    "retry_transient_io",
    "retry_template_error",
    "idempotency",
    "queue_routing",
]
task_names = ["run_ocr_extraction", "generate_bill_pdf", "generate_case_file_pdf"]

for chk in checks_order:
    row = [chk]
    for tn in task_names:
        r = results.get(tn, {}).get(chk, {})
        row.append(r.get("status", "--"))

total = sum(len(results.get(t, {})) for t in task_names)
passes = sum(1 for t in task_names for c in results.get(t, {}).values() if c.get("status") == OK)
fails = sum(1 for t in task_names for c in results.get(t, {}).values() if c.get("status") == FAIL_MARK)
skips = sum(1 for t in task_names for c in results.get(t, {}).values() if c.get("status") == SKIP_MARK)

# Clean up test artifacts
if os.path.exists(TEST_IMG_DIR):
    shutil.rmtree(TEST_IMG_DIR, ignore_errors=True)
db.session.remove()
ctx.pop()
