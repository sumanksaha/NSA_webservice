import json, sys
sys.path.insert(0, r"C:/github/NSA_webservice")
from evaluation.experiment_f_calibration_eval import load_gate_lists

gates = load_gate_lists()
for key, val in gates.items():
    print(key, len(val), val[:5] if val else [])