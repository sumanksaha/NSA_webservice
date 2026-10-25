with open("app/case_file_generator/templates/case_file_generator/Prohibition_order.adoc", encoding="utf-8") as f:
    lines = f.readlines()
print("TOTAL LINES:", len(lines))
for i in range(74, 98):
    if i < len(lines):
        print(f"{i+1}: {lines[i]}")
