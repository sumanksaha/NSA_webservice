import py_compile

p = "evaluation/evaluate_qa_sample.py"
try:
    py_compile.compile(p, cfile=p + "c", doraise=True)
    print("COMPILED_OK")
except py_compile.PyCompileError as e:
    print("COMPILE_FAIL:", e)
lines = sum(1 for _ in open(p))
print("lines:", lines)
