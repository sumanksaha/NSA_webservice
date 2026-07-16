import os
from jinja2 import Environment, FileSystemLoader
from num2words import num2words
from datetime import datetime

env = Environment(loader=FileSystemLoader('templates'))


def to_words_filter(number):
    try:
        number = float(number)

        # Remove decimal if whole number
        if number.is_integer():
            number = int(number)

        return num2words(number, lang='en_IN').capitalize()

    except Exception:
        return str(number)


def format_date_indian(date_str):
    try:
        dt = datetime.strptime(date_str, "%d/%m/%Y")

        day = dt.day
        # Ordinal suffix logic
        if 10 <= day % 100 <= 20:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")

        return f"{day}{suffix} {dt.strftime('%B')} {dt.year}"

    except Exception:
        return date_str


env.filters['to_words'] = to_words_filter
env.filters['format_date'] = format_date_indian

data_context = {
    "Name": "Suman Kumar Saha",
    "EMP_ID": "81276",
    "Designation": "Food Safety Officer",
    "Enf_samp_No": 8,
    "Surv_samp_No": 7,
    "Total_samp_No": 15,
    "Total_bill": "6025.55",
    "No_of_enfbills": "3",
    "No_of_survbills": "1",
    "TR_Value": "912",
    "TR_date": format_date_indian("15/05/2026"),
    "Submission_date": format_date_indian("15/05/2026"),
}


def generate_report(data):
    # Load template from templates/reimbursement_report.html
    jinja_template = env.get_template('reimbursement_report.html')

    # Render the HTML
    rendered_html = jinja_template.render(data)

    # Write to file
    filename = f"Inspection_Report_{data['Name'].replace(' ', '_')}.html"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(rendered_html)

    print(f"✅ Success: Report generated as {filename}")


if __name__ == "__main__":
    generate_report(data_context)
