"""Temp probe: inventory sections + first-chunk text across key docs for question authoring."""

from __future__ import annotations

import json
import re
import sqlite3

DB = "instance/app.db"
FSS_ACT = "60939e3b253847b9990a93fc10f5d723"
FSS_AMEND_2011 = "c71c5d4ce2da49b39452bc6af2e52849"
FSS_AMEND_2023 = "5f6a8dcaa32a4a2fb2d82f193c118fe3"
NUTRACEUTICALS = "8b2f12f1fa5f40a9b4ae80eebda35e63"
ALCOHOL = "13dbbac3cc3c4cedb7c0786acdaa65f7"
LICENSING = "d1a069819e934a688c82fd56776ed037"

c = sqlite3.connect(DB)


def load_doc(did: str) -> list[dict]:
    rows = c.execute(
        "SELECT data FROM chunk_enrichment WHERE json_extract(data, '$._document.document_id') = ?",
        (did,),
    ).fetchall()
    return [json.loads(r[0]) for r in rows]


def header_of(text: str) -> str:
    m = re.match(r"^\s*(?:section|sec\.?|§|s\.)\s*[\dA-Za-z.\-]+\s*[:.\-]?\s*(.{0,100})", text, re.I | re.S)
    return m.group(1).strip() if m else ""


def inventory(did: str, label: str, sections: list[str]) -> None:
    recs = load_doc(did)
    by_sec: dict[str, list[dict]] = {}
    for r in recs:
        sec = (r.get("legal_location") or {}).get("section") or {}
        v = sec.get("value")
        if v:
            by_sec.setdefault(str(v), []).append(r)
    for s in sections:
        rs = sorted(by_sec.get(s, []), key=lambda r: r.get("chunk_index") or 0)
        if not rs:
            continue
        first = rs[0]
        t = (first.get("original_text") or "").strip().replace("\n", " ")
        header_of(t) or t[:90]
        n = len(rs)
        (rs[1].get("original_text", "") if n > 1 else t)[:100].replace("\n", " ")


inventory(
    FSS_ACT,
    "FSS Act — remaining sections",
    [
        "5",
        "6",
        "7",
        "9",
        "10",
        "11",
        "12",
        "13",
        "14",
        "15",
        "16",
        "17",
        "18",
        "19",
        "20",
        "21",
        "24",
        "25",
        "28",
        "29",
        "33",
        "34",
        "35",
        "37",
        "39",
        "41",
        "42",
        "43",
        "44",
        "45",
        "46",
        "47",
        "48",
        "49",
        "50",
        "51",
        "52",
        "53",
        "54",
        "56",
        "57",
        "58",
        "59",
        "60",
        "61",
        "62",
        "63",
        "64",
        "65",
        "66",
        "67",
        "68",
        "69",
        "70",
        "71",
        "72",
        "73",
        "74",
        "75",
        "76",
        "77",
        "78",
        "79",
        "80",
        "81",
        "82",
        "83",
        "84",
        "85",
        "86",
        "87",
        "88",
        "89",
        "90",
        "91",
        "93",
        "94",
        "95",
        "96",
        "97",
        "98",
        "99",
        "100",
        "101",
        "102",
        "103",
        "104",
        "105",
        "106",
        "107",
        "108",
        "109",
        "110",
        "111",
        "112",
        "113",
        "114",
        "115",
        "116",
        "117",
        "118",
        "119",
        "120",
        "121",
        "122",
        "123",
        "124",
    ],
)
inventory(
    NUTRACEUTICALS,
    "Nutraceuticals Regs",
    [
        "2",
        "3",
        "4",
        "6",
        "7",
        "8",
        "9",
        "11",
        "12",
        "13",
        "14",
        "15",
        "16",
        "17",
        "18",
        "19",
        "20",
        "21",
        "22",
        "24",
        "25",
        "26",
        "27",
        "28",
        "29",
        "30",
    ],
)
inventory(
    LICENSING,
    "Licensing Regs",
    [
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
        "10",
        "12",
        "16",
        "17",
        "18",
        "19",
        "20",
        "21",
        "22",
        "24",
        "25",
        "26",
        "28",
        "30",
        "33",
        "34",
        "35",
        "37",
        "38",
        "39",
        "40",
        "41",
        "42",
        "43",
        "44",
        "45",
        "46",
        "47",
        "48",
        "49",
        "50",
    ],
)
