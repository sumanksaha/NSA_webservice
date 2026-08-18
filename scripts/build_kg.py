#!/usr/bin/env python3
"""Build an advanced legal knowledge graph from corpus_eval_result.json."""

import json

SECTION_CONTEXT = {
    "1": "Short title, commencement and extent",
    "2": "Definitions (clauses a-z)",
    "3": "Powers of the Authority",
    "4": "Duties and responsibilities of food business operators",
    "5": "Licence to carry on business",
    "6": "Application for licence",
    "7": "Grant of licence",
    "8": "Duration of licences",
    "9": "Renewal of licences",
    "10": "Procedures for grant / renewal",
    "11": "Appeals against orders refusing licence",
    "12": "Appeals to Appellate Authority",
    "13": "Suspension / cancellation of licence",
    "14": "Recall of food",
    "15": "Withholding of orders",
    "16": "Powers of entry, inspection and seizure",
    "17": "Seizure of unsafe food",
    "18": "Constitution and jurisdiction of authorities",
    "19": "Powers of officers",
    "22": "Food safety officers",
    "23": "Powers of food safety officers",
    "31": "Offences and penalties",
    "32": "Cognizance of offences",
    "36": "Composition of offences",
    "38": "Appeals to Appellate Authority",
    "40": "Special provisions for certain offences",
    "55": "Appellate authority",
    "92": "Power to make regulations / rules",
    "100": "Constitution of Central Advisory Committee",
    "106": "Power to amend Act",
    "107": "Power to remove difficulties",
    "110": "Power to amend regulations",
    "120": "Power to amend orders",
    "121": "Power to exempt",
    "122": "Power to modify",
    "123": "Power to suspend",
    "124": "Power to cancel",
    "175": "Transitional provisions",
    "195": "Repeal and saving",
    "199": "Amendment of Act",
    "213": "Amendment of regulations",
    "542": "Power to amend (Amendment Act 2023)",
}

AUTHORITY_CANONICAL = {
    "fssai": "FOOD SAFETY AND STANDARDS AUTHORITY OF INDIA",
    "FSSAI": "FOOD SAFETY AND STANDARDS AUTHORITY OF INDIA",
    "Food Safety and Standards Authority of India": "FOOD SAFETY AND STANDARDS AUTHORITY OF INDIA",
    "FOOD SAFETY AND STANDARDS AUTHORITY OF INDIA": "FOOD SAFETY AND STANDARDS AUTHORITY OF INDIA",
    "MINISTRY OF HEALTH AND FAMILY WELFARE": "MINISTRY OF HEALTH AND FAMILY WELFARE",
    "MINISTRY OF LAW AND JUSTICE": "MINISTRY OF LAW AND JUSTICE",
}

AUTHORITY_HIERARCHY = {
    "FOOD SAFETY AND STANDARDS AUTHORITY OF INDIA": {
        "type": "regulatory_body",
        "level": "central",
        "is_governing": True,
        "role": "Primary authority under the FSS Act, 2006 - responsible for regulation and enforcement of food safety standards across India.",
    },
    "MINISTRY OF HEALTH AND FAMILY WELFARE": {
        "type": "ministry",
        "level": "central_government",
        "is_governing": False,
        "role": "Parent ministry of FSSAI; issues notifications and regulations under the FSS Act delegation.",
    },
    "MINISTRY OF LAW AND JUSTICE": {
        "type": "ministry",
        "level": "central_government",
        "is_governing": False,
        "role": "Publishes the FSS Act itself and constitutional/statutory amendments through the Legislative Department.",
    },
}

JURISDICTION_CANONICAL = {
    "Central\nGovernment": "Central Government of India",
    "Central Government": "Central Government of India",
    "Government of India": "Central Government of India",
    "Government": "Central Government of India",
    "Republic of India": "Republic of India (Union)",
    "Republic of\nIndia": "Republic of India (Union)",
    "India": "Republic of India (Union)",
    "Government of": "Central Government of India",
}

DOC_TYPE_DESCRIPTIONS = {
    "notification": "Administrative/gazette notification - rulemaking, amendment, or policy directive under an existing Act.",
    "act": "Primary legislation - the FSS Act, 2006 or its formal amendments (Amendment Act 2008/2011/2023).",
    "regulation": "Statutory instrument - detailed rules issued by the Authority under delegated power.",
}

try:
    with open("corpus_eval_result.json", encoding="utf-8") as f:
        data = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    raise

docs = data.get("documents", [])

kg = {
    "metadata": {
        "corpus_dir": data.get("corpus_dir", ""),
        "total_documents": len(docs),
        "generated_at": data.get("generated_at", "2026-08-09"),
        "vector_space": "fssai_legal_768 (768-dim cosine)",
        "embedding_model": "sentence-transformers/all-mpnet-base-v2",
    },
    "schema": {
        "entity_types": {
            "document": "Legal document (Act, Amendment, Notification, Regulation, Rule)",
            "section": "Legal section/subsection/clause within an Act or regulation",
            "authority": "Governing body or ministry (FSSAI, Ministries)",
            "jurisdiction": "Geographic/law-tier scope (Central Government, Republic of India)",
            "reference": "Citation extracted from chunk text (cross-reference to another legal source)",
        },
        "relationship_types": {
            "document_contains_section": "document -> section",
            "document_issued_by": "document -> authority",
            "document_applies_to": "document -> jurisdiction",
            "section_cites": "section -> section (intra-document)",
            "section_references": "section -> external section/document",
            "authority_oversees": "authority -> document type",
            "document_amends": "Amendment Act -> Act section",
        },
    },
    "entities": {
        "documents": [],
        "sections": [],
        "authorities": [],
        "jurisdictions": [],
        "references": [],
    },
    "relationships": {
        "document_contains_section": [],
        "document_issued_by": [],
        "document_applies_to": [],
        "section_cooccurrence": [],
        "canonical_authorities": [],
    },
    "canonical_lookup": {
        "authorities": AUTHORITY_CANONICAL,
        "jurisdictions": JURISDICTION_CANONICAL,
        "document_types": DOC_TYPE_DESCRIPTIONS,
        "sections": SECTION_CONTEXT,
    },
    "statistics": {},
}

all_sections = set()
all_authorities = {}
all_jurisdictions = set()
section_docs = {}
section_cooccur = {}
grade_totals = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0, "F": 0}
all_issues = {}
total_chunks = 0
total_citations = 0
total_refs = 0

for doc in docs:
    fname = doc.get("file", "?")
    chunks = doc.get("chunks", {})
    sections = chunks.get("sections", [])
    metadata = doc.get("metadata", {})
    classification = doc.get("classification", {})
    quality = doc.get("quality", {})

    raw_authority = metadata.get("authority") or classification.get("authority") or ""
    canonical_authority = AUTHORITY_CANONICAL.get(raw_authority, raw_authority)

    raw_juris = metadata.get("jurisdiction") or ""
    juris_list = [j.strip() for j in str(raw_juris).split("\n") if j.strip()]
    canonical_juris = set()
    for j in juris_list:
        canonical_juris.add(JURISDICTION_CANONICAL.get(j, j))

    doc_type = metadata.get("document_type") or classification.get("document_type") or "unknown"

    grades = quality.get("grades", {})
    grade = max(grades.items(), key=lambda x: x[1])[0] if grades else "A"

    doc_entity = {
        "id": doc.get("extraction", {}).get("document_id", ""),
        "file": fname,
        "document_type": doc_type,
        "document_type_description": DOC_TYPE_DESCRIPTIONS.get(doc_type, "Unknown document type"),
        "raw_authority": raw_authority,
        "canonical_authority": canonical_authority,
        "status": doc.get("status", "?"),
        "chunk_count": chunks.get("count", 0),
        "sections": sections,
        "quality_grade": grade,
        "quality_grades": grades,
        "quality_issues": quality.get("issues", []),
        "citations_count": chunks.get("citations", 0),
        "references_count": chunks.get("references", 0),
        "jurisdiction_raw": raw_juris,
        "jurisdictions_canonical": sorted(canonical_juris),
        "state": metadata.get("state"),
        "effective_date": metadata.get("effective_date"),
        "is_current": metadata.get("is_current"),
        "pages": doc.get("extraction", {}).get("pages", 0),
        "file_size_bytes": doc.get("extraction", {}).get("file_size_bytes", 0),
        "elapsed_s": doc.get("elapsed_s", 0),
    }
    kg["entities"]["documents"].append(doc_entity)

    total_chunks += doc_entity["chunk_count"]
    total_citations += doc_entity["citations_count"]
    total_refs += doc_entity["references_count"]

    for g, count in grades.items():
        if g in grade_totals:
            grade_totals[g] += count
    for issue in quality.get("issues", []):
        all_issues[issue] = all_issues.get(issue, 0) + 1

    if canonical_authority:
        if canonical_authority not in all_authorities:
            all_authorities[canonical_authority] = {"raw_variants": [], "doc_count": 0}
        all_authorities[canonical_authority]["doc_count"] += 1
        all_authorities[canonical_authority]["raw_variants"].append(raw_authority)

    for j in canonical_juris:
        all_jurisdictions.add(j)

    for s in sections:
        all_sections.add(s)
        if s not in section_docs:
            section_docs[s] = []
        section_docs[s].append(fname)
        kg["relationships"]["document_contains_section"].append({
            "document": fname,
            "section": s,
            "section_description": SECTION_CONTEXT.get(s, ""),
        })

    kg["relationships"]["document_issued_by"].append({
        "document": fname,
        "raw_authority": raw_authority,
        "canonical_authority": canonical_authority,
    })

    for j in canonical_juris:
        kg["relationships"]["document_applies_to"].append({
            "document": fname,
            "jurisdiction": j,
        })

# Build section entities
for s in sorted(all_sections, key=lambda x: (len(str(x)), x)):
    docs_with = section_docs.get(s, [])
    kg["entities"]["sections"].append({
        "section_number": s,
        "semantic_description": SECTION_CONTEXT.get(s, "Unknown - no semantic mapping available"),
        "appears_in_documents": len(docs_with),
        "documents": docs_with,
        "hierarchy_hint": f"Section {s} of FSS Act 2006 / relevant regulation"
        if s in SECTION_CONTEXT
        else f"Section {s} (context unknown)",
    })

# Build authority entities
for auth, info in all_authorities.items():
    hierarchy = AUTHORITY_HIERARCHY.get(auth, {})
    kg["entities"]["authorities"].append({
        "name": auth,
        "type": hierarchy.get("type", "unknown"),
        "level": hierarchy.get("level", "unknown"),
        "is_governing": hierarchy.get("is_governing", False),
        "doc_count": info["doc_count"],
        "raw_variants_normalized": sorted(set(info["raw_variants"])),
    })

# Jurisdictions
for j in sorted(all_jurisdictions):
    docs_with_j = [d["file"] for d in kg["entities"]["documents"] if j in d["jurisdictions_canonical"]]
    kg["entities"]["jurisdictions"].append({
        "name": j,
        "doc_count": len(docs_with_j),
        "documents": docs_with_j,
    })

# Section co-occurrence
for d in kg["entities"]["documents"]:
    for s1 in d["sections"]:
        for s2 in d["sections"]:
            if s1 != s2:
                key = tuple(sorted([s1, s2]))
                section_cooccur[key] = section_cooccur.get(key, 0) + 1
top_cooccur = sorted(section_cooccur.items(), key=lambda x: x[1], reverse=True)[:15]
kg["relationships"]["section_cooccurrence"] = [
    {
        "sections": list(k),
        "cooccurrence_count": v,
        "section_descriptions": {
            k[0]: SECTION_CONTEXT.get(k[0], "?"),
            k[1]: SECTION_CONTEXT.get(k[1], "?"),
        },
    }
    for k, v in top_cooccur
]

# Canonical authorities with hierarchy
canonical_auth_entities = []
for auth, info in all_authorities.items():
    hierarchy = AUTHORITY_HIERARCHY.get(auth, {})
    canonical_auth_entities.append({
        "canonical_name": auth,
        "type": hierarchy.get("type", "unknown"),
        "level": hierarchy.get("level", "unknown"),
        "is_governing": hierarchy.get("is_governing", False),
        "role": hierarchy.get("role", "Unknown"),
        "doc_count": info["doc_count"],
        "normalized_from": sorted(set(info["raw_variants"])),
    })
kg["relationships"]["canonical_authorities"] = sorted(canonical_auth_entities, key=lambda x: -x["doc_count"])

# Statistics
type_dist = {}
for d in kg["entities"]["documents"]:
    t = d["document_type"] or "unknown"
    type_dist[t] = type_dist.get(t, 0) + 1

kg["statistics"] = {
    "total_documents": len(docs),
    "total_chunks": total_chunks,
    "total_citations": total_citations,
    "total_references": total_refs,
    "avg_chunks_per_doc": round(total_chunks / len(docs), 1) if docs else 0,
    "avg_citations_per_doc": round(total_citations / len(docs), 1) if docs else 0,
    "document_types": type_dist,
    "quality_grades": grade_totals,
    "quality_issues": all_issues,
    "unique_sections": len(all_sections),
    "unique_authorities": len(all_authorities),
    "unique_jurisdictions": len(all_jurisdictions),
    "empty_documents": sum(1 for d in docs if d["chunks"].get("count", 0) == 0),
    "largest_document": max(docs, key=lambda d: d.get("chunks", {}).get("count", 0))["file"] if docs else "",
    "largest_chunk_count": max(docs, key=lambda d: d.get("chunks", {}).get("count", 0))
    .get("chunks", {})
    .get("count", 0)
    if docs
    else 0,
    "most_cited_document": max(docs, key=lambda d: d.get("chunks", {}).get("citations", 0))["file"] if docs else "",
    "most_citations_count": max(docs, key=lambda d: d.get("chunks", {}).get("citations", 0))
    .get("chunks", {})
    .get("citations", 0)
    if docs
    else 0,
}

output_path = "knowledge_graph.json"
try:
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(kg, f, indent=2, ensure_ascii=False, default=str)
except OSError:
    raise
