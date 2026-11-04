from app.answer_contract import parse_answer, verify_citations, extract_citations


def test_parse_answer_contract():
    text = "Claim: X\nCitations: 1, 2\nVerdict: yes"
    a = parse_answer(text)
    assert a.claim == "X"
    assert a.citations == ["1", "2"]
    assert a.verdict == "yes"
    assert a.is_valid()


def test_extract_citations_fallback():
    text = "Answer [3] and [7]"
    assert extract_citations(text) == ["3", "7"]


def test_verify_citations_valid():
    ans = "Claim: X\nCitations: 1,2\nVerdict: yes"
    res = verify_citations(ans, ["[1] foo", "[2] bar"])
    assert res["valid"] is True
    assert res["missing"] == []


def test_verify_citations_missing():
    ans = "Claim: X\nCitations: 1,9\nVerdict: yes"
    res = verify_citations(ans, ["[1] foo"])
    assert res["valid"] is False
    assert res["missing"] == ["9"]
