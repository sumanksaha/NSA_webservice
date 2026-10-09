import json
import time
import urllib.request

U = "https://bec75c20-f1d4-4d37-aa6b-4c37f7d9c26b.australia-southeast1-0.gcp.cloud.qdrant.io".rstrip("/")
H = {
    "api-key": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2Nlc3MiOiJtIiwic3ViamVjdCI6ImFwaS1rZXk6OWY3YmNkNzAtMDQ3Yi00NTQzLTgwMzEtYWQ3OWZhNWZjNGUxIn0.o-Zc8a79kHJ9nSpkqudKZyCP3lhSj3iUoiGrbSmRcqE",
    "Content-Type": "application/json",
}
COL = "fssai_legal_768"

body = json.dumps({"filter": {}}).encode()
req = urllib.request.Request(U + "/collections/" + COL + "/points/count", data=body, headers=H)
d = json.load(urllib.request.urlopen(req, timeout=30))
print("total points:", d["result"]["count"])

for term in ["workflow", "designated officer", "Seizure", "Form VIII"]:
    body = json.dumps({"filter": {"must": [{"key": "chunk_text", "match": {"text": term}}]}}).encode()
    req = urllib.request.Request(U + "/collections/" + COL + "/points/count", data=body, headers=H)
    t = time.time()
    d = json.load(urllib.request.urlopen(req, timeout=30))
    print(f"{term!r}: {d['result']['count']} ({time.time() - t:.2f}s)")
