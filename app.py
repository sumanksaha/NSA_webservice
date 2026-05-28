```python
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader
from datetime import datetime
from weasyprint import HTML
import zipfile
import os

app = FastAPI()

templates = Jinja2Templates(directory="templates")
env = Environment(loader=FileSystemLoader("templates"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

os.makedirs(OUTPUT_DIR, exist_ok=True)

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


def fdate(v):
    try:
        dt = datetime.strptime(v, "%Y-%m-%d")
        return dt.strftime("%d %B %Y")
    except:
        return v


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    context = {
        "request": request,
        "checklist": CHECKLIST
    }

    return templates.TemplateResponse(
        "index.html",
        context
    )


@app.post("/generate_all")
async def generate_all(request: Request):

    form = await request.form()
    data = dict(form)

    for k in [
        'First_inspection_date',
        'Complaint_date',
        'inspection_date',
        'authorization_date'
    ]:
        data[k] = fdate(data.get(k, ''))

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

    data['violations'] = violations

    outputs = []

    templates_to_generate = [
        (
            "Legal_NonsampleAdjudication_Template.html",
            "Permission_Letter"
        ),
        (
            "template_nonsample_petition.html",
            "Petition"
        )
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

    zipname = os.path.join(
        OUTPUT_DIR,
        "CasePack_Final.zip"
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
        filename="CasePack_Final.zip",
        media_type="application/zip"
    )
```
