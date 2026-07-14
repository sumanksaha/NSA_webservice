from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader
from datetime import datetime
from weasyprint import HTML
import zipfile
import os
import httpx
import ssl
import re
import json
import sqlite3
from pydantic import BaseModel

from suggester import suggest_sections

app = FastAPI()

templates = Jinja2Templates(directory="templates")
SECTION_MD_PATH = "fss_sections.md"
env = Environment(loader=FileSystemLoader("templates"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

os.makedirs(OUTPUT_DIR, exist_ok=True)

# FSSAI License/Registration lookup DBs
DB_DIR = os.path.join(BASE_DIR, "db")
LICENSE_DB_PATH = os.path.join(DB_DIR, "license_data.db")
REGISTRATION_DB_PATH = os.path.join(DB_DIR, "registration_data.db")

CHECKLIST = [
    'clean_premise', 'refrigerator_clean', 'proper_attire',
    'proper_covered_utensil', 'date_tag', 'veg_nonveg_separation',
    'food_segregation', 'license_display', 'artificial_colour',
    'Expired_item', 'Pest_report', 'Water_report'
]

RULES = {
    'clean_premise': (
        "Unclean Premises",
        "The premises were found inadequately maintained and unhygienic."
    ),
    'refrigerator_clean': (
        "Improper Refrigerator Maintenance",
        "Refrigeration facilities were found unclean."
    ),
    'proper_attire': (
        "Improper Protective Attire",
        "Food handlers lacked prescribed attire."
    ),
    'proper_covered_utensil': (
        "Improper Covering of Food",
        "Food and utensils were uncovered."
    ),
    'date_tag': (
        "Absence of Date Tagging",
        "Stored food items lacked traceability."
    ),
    'veg_nonveg_separation': (
        "Improper Veg/Non-Veg Separation",
        "Segregation not maintained."
    ),
    'food_segregation': (
        "Improper Food Segregation",
        "Risk of cross contamination."
    ),
    'license_display': (
        "Improper License Display",
        "License not prominently displayed."
    ),
    'Expired_item': (
        "Expired Items",
        "Expired items present."
    ),
    'Pest_report': (
        "Pest Control Report Missing",
        "Routine pest control not documented."
    ),
    'Water_report': (
        "Water Test Report Missing",
        "Potable water testing unavailable."
    )
}

class LicenseLookupRequest(BaseModel):
    license_no: str


class FssaiLookupRequest(BaseModel):
    license_no: str


def lookup_fssai(license_no: str):
    """
    Look up an FSSAI License/Registration number.
    Numbers starting with '1' are Registration-category FBOs -> license_data.db.
    Numbers starting with '2' are License-category FBOs -> registration_data.db.
    Returns a dict with companyName/fullAddress/expiryDate/source, or None if
    not found / prefix not recognized.
    """
    if not license_no:
        return None, "License/Registration number is required."

    prefix = license_no[0]
    if prefix == '1':
        db_path, table, col, source = LICENSE_DB_PATH, "license_records", "license_no", "license_data"
    elif prefix == '2':
        db_path, table, col, source = REGISTRATION_DB_PATH, "registration_records", "registration_no", "registration_data"
    else:
        return None, "Unrecognized License/Registration number prefix (expected to start with 1 or 2)."

    if not os.path.exists(db_path):
        return None, f"Lookup database not found: {os.path.basename(db_path)}."

    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"SELECT company_name, full_address, expiry_date FROM {table} WHERE {col} = ?",
            (license_no,)
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return None, "License/Registration number not found."

    return {
        "companyName": row["company_name"],
        "fullAddress": row["full_address"],
        "expiryDate": row["expiry_date"],
        "source": source,
    }, None

def fdate(v):
    try:
        dt = datetime.strptime(v, "%Y-%m-%d")
        return dt.strftime("%d %B %Y")
    except:
        return v

async def lookup_ce(license_no: str):
    ctx = ssl.create_default_context()
    ctx.set_ciphers("DEFAULT@SECLEVEL=1")
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    async with httpx.AsyncClient(timeout=10, verify=ctx) as client:
        await client.get(
            "https://www.kmcgov.in/KMCPortal/jsp/TradeLicenseInformation.jsp"
        )
        resp = await client.post(
            "https://www.kmcgov.in/KMCPortal/LicenseInformationAction.do?passedParam=searchResult",
            data={"searchLicenseNo": license_no},
            headers={
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.kmcgov.in/KMCPortal/jsp/TradeLicenseInformation.jsp",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
  },
        )
        
        raw_text = resp.text
        # KMC's endpoint returns JSON with unquoted keys — fix before parsing
        fixed_text = re.sub(r'([{,])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:', r'\1"\2":', raw_text)
        try:
            data = json.loads(fixed_text)
        except json.JSONDecodeError as e:
            print(f"JSON repair failed: {e}")  # TEMP DEBUG
            print(f"Fixed text (first 500 chars): {fixed_text[:500]}")  # TEMP DEBUG
            return None

    if not data.get("success"):
        return None
    try:
        rows = data["licenseNo"][0]
        identity = rows[0]
    except (KeyError, IndexError):
        return None

    fee_heads = [{"section": r.get("sectionCode"), "amount": r.get("demandAmount")} for r in rows]
    return {"identity": identity, "fee_heads": fee_heads, "is_closed": bool(identity.get("licClosingDate"))}



@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    context = {
        "request": request,
        "checklist": CHECKLIST
    }

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=context
    )

@app.post("/lookup_ce")
async def lookup_ce_route(payload: LicenseLookupRequest):
    license_no = payload.license_no.strip()
    if not license_no:
        return JSONResponse({"error": "License number is required."}, status_code=400)
    try:
        result = await lookup_ce(license_no)
    except httpx.RequestError as e:
        print(f"KMC request failed: {type(e).__name__}: {e}")  # TEMP DEBUG
        return JSONResponse({"error": "Could not reach KMC portal. Try again."}, status_code=502)
    if not result:
        return JSONResponse({"error": "License not found."}, status_code=404)
    return result


@app.post("/lookup_fssai")
async def lookup_fssai_route(payload: FssaiLookupRequest):
    license_no = payload.license_no.strip()
    result, error = lookup_fssai(license_no)
    if error:
        status_code = 400 if "required" in error or "prefix" in error else 404
        return JSONResponse({"error": error}, status_code=status_code)
    return {"identity": result}


@app.post("/suggest_sections")
async def suggest_sections_route(request: Request):
    form = await request.form()
    return suggest_sections(dict(form))


@app.post("/generate_all")
async def generate_all(request: Request):

    form = await request.form()
    data = dict(form)

    is_pre_authorization = str(data.get('pre_authorization', 'no')).strip().lower() == 'yes'

    date_fields = [
        'First_inspection_date',
        'Complaint_date',
        'inspection_date',
    ]
    if not is_pre_authorization:
        date_fields.append('authorization_date')

    for k in date_fields:
        data[k] = fdate(data.get(k, ''))

    if is_pre_authorization:
        # Authorization hasn't been granted yet in this case — the permission
        # memo requests it, so don't carry a stray/blank authorization_date
        # into the render context.
        data.pop('authorization_date', None)

    data['compilation_date'] = datetime.today().strftime(
        "%d %B %Y"
    )

    violations = []

    for k, (title, obs) in RULES.items():
        if data.get(k) == 'no':
            violations.append({
                'title': title,
                'Observation': obs
            })

    if data.get('artificial_colour') == 'yes':
        violations.append({
            'title': 'Use of Artificial Colours',
            'Observation':
            'Artificial colours were reportedly used in food preparation.'
        })

    if data.get('Expired_item') == 'yes':
        violations.append({
            'title': 'Expired Items Present',
            'Observation':
            'Expired food items were found on the premises.'
        })

    data['violations'] = violations

    outputs = []

    if is_pre_authorization:
        # Permission not yet granted: generate only the permission-request
        # memo (which asks the Designated Officer for authorization).
        templates_to_generate = [
            (
                "Legal_NonsampleAdjudication_Template.html",
                "Permission_Letter"
            ),
        ]
    else:
        # Permission already granted (authorization_date is known):
        # generate only the petition, which cites that date.
        if not data.get('authorization_date'):
            return JSONResponse(
                {"error": "authorization_date is required when Pre-Authorization Case is not ticked."},
                status_code=400,
            )
        templates_to_generate = [
            (
                "template_nonsample_petition.html",
                "Petition"
            ),
        ]

    for tpl, prefix in templates_to_generate:

        template = env.get_template(tpl)
        rendered = template.render(**data)

        htmlf = os.path.join(
            OUTPUT_DIR,
            f"{prefix}.html"
        )

        pdff = os.path.join(
            OUTPUT_DIR,
            f"{prefix}.pdf"
        )

        with open(
            htmlf,
            "w",
            encoding="utf-8"
        ) as f:
            f.write(rendered)

        # WEASYPRINT PDF GENERATION
        HTML(
            filename=htmlf
        ).write_pdf(
            pdff
        )

        outputs.extend([
            htmlf,
            pdff
        ])

    zip_prefix = "PermissionLetter" if is_pre_authorization else "Petition"
    zipname = os.path.join(
        OUTPUT_DIR,
        f"{zip_prefix}_Final.zip"
    )

    with zipfile.ZipFile(
        zipname,
        'w'
    ) as z:
        for f in outputs:
            z.write(
                f,
                arcname=os.path.basename(f)
            )

    return FileResponse(
        zipname,
        filename=f"{zip_prefix}_Final.zip",
        media_type="application/zip"
    )
