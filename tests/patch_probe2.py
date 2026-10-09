p = "tests/probe_classification.py"
s = open(p).read()
s = s.replace(", file=sys.stderr)", ", file=sys.stderr)")
s = s.replace('print(f"query={q!r} -> type={type(r).__name__}", file=sys.stderr)',
              'log(f"query={q!r} -> type={type(r).__name__}")')
s = s.replace('        print(f"    form_mentioned={r.form_mentioned}, matched_patterns={r.matched_patterns}", file=sys.stderr)',
              '        log(f"    form_mentioned={r.form_mentioned}, matched_patterns={r.matched_patterns}")')
s = s.replace('        print(f"    action_keywords={r.action_keywords}, procedure_keywords={r.procedure_keywords}", file=sys.stderr)',
              '        log(f"    action_keywords={r.action_keywords}, procedure_keywords={r.procedure_keywords}")')
s = s.replace('    qc = QueryClassifier()\n    cls = qc.classify(q)\n    fqu = FoodQueryUnderstanding.from_query(q)\n    intent, conf = detect_food_intent(q)\n    print(f"query={q!r}", file=sys.stderr)\n    print(f"    QueryType={cls}, FoodIntent={intent} (conf={conf:.2f}), entity={fqu.entity}, target={fqu.target}", file=sys.stderr)',
              '    qc = QueryClassifier()\n    cls = qc.classify(q)\n    fqu = FoodQueryUnderstanding.from_query(q)\n    intent, conf = detect_food_intent(q)\n    log(f"query={q!r}")\n    log(f"    QueryType={cls}, FoodIntent={intent} (conf={conf:.2f}), entity={fqu.entity}, target={fqu.target}")')
s = s.replace('    form = detect_form(q)\n    fq = form_query(q)\n    print(f"query={q!r} -> detect_form={form!r}, form_query_lexical={fq!r}", file=sys.stderr)',
              '    form = detect_form(q)\n    fq = form_query(q)\n    log(f"query={q!r} -> detect_form={form!r}, form_query_lexical={fq!r}")')
s = s.replace('    print(f"query={q!r} -> detect_clause={detect_clause(q)!r}, identifier_query={identifier_query(q)}", file=sys.stderr)',
              '    log(f"query={q!r} -> detect_clause={detect_clause(q)!r}, identifier_query={identifier_query(q)}")')
s = s.replace('print(f"query={q!r}", file=sys.stderr)\nfor r in reqs:\n    print(f"    task_id={r.task_id}, type={r.type}, keywords={r.keywords}", file=sys.stderr)',
              'log(f"query={q!r}")\nfor r in reqs:\n    log(f"    task_id={r.task_id}, type={r.type}, keywords={r.keywords}")')
s = s.replace('planner = QueryPlanner()\n        dec = planner.plan(q)\n        log(f"query={q!r}", file=sys.stderr)\n        log(f"    intent={dec.intent}, complexity={dec.complexity}, evidence_requirements={[e.value for e in dec.evidence_requirements]}", file=sys.stderr)\n        for t in dec.tasks:\n            log(f"    task_id={t.task_id}, req={t.source_requirement_id}, objective={t.objective!r}, evidence_req={t.evidence_requirement}", file=sys.stderr)',
              'planner = QueryPlanner()\n        dec = planner.plan(q)\n        log(f"query={q!r}")\n        log(f"    intent={dec.intent}, complexity={dec.complexity}, evidence_requirements={[e.value for e in dec.evidence_requirements]}", file=sys.stderr)\n        for t in dec.tasks:\n            log(f"    task_id={t.task_id}, req={t.source_requirement_id}, objective={t.objective!r}, evidence_req={t.evidence_requirement}", file=sys.stderr)')
s = s.replace('print(f"\\nDone in {time.monotonic() - START:.2f}s", file=sys.stderr)',
              'log(f"\\nDone in {time.monotonic() - START:.2f}s")')
# remove leftover debug step prints
s = s.replace('    print(f"STEP: {label}", file=sys.stderr)\n\nstep("import chunker")\nfrom app.rag import chunker\nprint("chunker imported", file=sys.stderr)\n\nfrom app.rag.chunker import (',
              "")
open(p, "w").write(s)
print("patched OK")
