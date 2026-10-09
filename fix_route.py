"""Insert the closing ''' after line 62 of insert_route.py."""
with open("insert_route.py", encoding="utf-8") as f:
    lines = f.readlines()
# Insert closing ''' for the outer r""" string right after line 62 ('''
lines.insert(62, '"""\n')
with open("insert_route.py", "w", encoding="utf-8") as f:
    f.writelines(lines)
print("fixed; total lines now:", len(lines))
