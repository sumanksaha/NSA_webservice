with open("app/case_file_generator/templates/case_file_generator/Unsafe_file.adoc", encoding="utf-8") as f:
    lines = f.readlines()
print("TOTAL LINES:", len(lines))
for i in range(83, 122):
    print(f"{i}: {lines[i]}")
print("--- 180-183 ---")
for i in range(179, 184):
    print(f"{i}: {lines[i]}")
print("--- 184-188 ---")
for i in range(184, 189):
    print(f"{i}: {lines[i]}")
print("--- 218-226 ---")
for i in range(218, 227):
    print(f"{i}: {lines[i]}")
