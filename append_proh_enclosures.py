"""Append enclosures + signature to Prohibition_order.adoc."""
import sys

sys.stdout.reconfigure(encoding="utf-8")

path = "app/case_file_generator/templates/case_file_generator/Prohibition_order.adoc"
with open(path, "a", encoding="utf-8") as f:
    f.write('''
[.justify]
In accordance with the applicable legal framework and the directions issued by the Government of West Bengal, Health and Family Welfare Department, Food Safety Branch, vide Memo No: 258/HF/CES/IA-23/2019 dated 29/04/2025, the following documents are attached herewith (marked E):

Now, all the relevant documents regarding the collection of the sample **{{ sample_name }}** of {{ packet_count }} sealed packets{% if not retailer_cum_manufacturer %} (Batch No - {{ batch_no }}, Date of Manufacture {{ mfg_date }}, Date of Expiry {{ expiry_date }}){% endif %} are attached herewith (marked F) for the following:
d. Initiation of Prohibition on Sale of Unsafe food.
e. Grant of Sanction for Prosecution,
f. Initiation of Product Recall, if applicable.

*Enclosures:*
. Attachment A — copy of {{ directive_letter_no }} dated {{ directive_letter_date }} and Food Analyst Report No. {{ analyst_report_no }} dated {{ analyst_report_date }}, duly received by the representative of {{ retailer_name }} on {{ retailer_report_receive_date }}.
. Attachment B — Entry of {{ manufacturer_name }} ({{ manufacturer_fbo_name }}), {{ manufacturer_address }}, Lic No - {{ manufacturer_fssai }}.
. Attachment C — Reply of the manufacturer dated {{ manufacturer_report_receive_date }}.
. Attachment D — Draft prohibition order.
. Attachment E — Memo No: 258/HF/CES/IA-23/2019 dated 29/04/2025.
. Attachment F — Documents regarding the collection of the sample of {{ sample_name }} vide Sample Code {{ sample_code }} dated {{ inspection_date }}.
  i. Copy of Form of Notice to the FBO (Form VA dated {{ inspection_date }}).
  ii. Copy of Form of Memorandum to the Food Analyst (Form VI dated {{ inspection_date }}).
  iii. Copy of sample coupon form dated {{ inspection_date }}.
  iv. Copy of Lab &amp; DO picon book dated {{ inspection_date }}.

[.signature]
{{ food_safety_officer_name }}
Food Safety Officer
Kolkata Municipal Corporation

[.header]
Date - {{ authorization_date }}
''')
print("appended enclosures to", path)
