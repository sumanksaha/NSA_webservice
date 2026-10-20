"""Deterministic answer contract."""
from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from app.rag.retrieval.identifier import detect_act, detect_sections
from app.rag.generation.abstention import is_abstention
logger = logging.getLogger(__name__)
_AMOUNT_RE = re.compile(r"(?:₹|Rs\.?|rupees)\s*(\d+(?:,\d+)*(?:\.\d+)?)", re.IGNORECASE)
_SECTION_CITE_RE = re.compile(r"[Ss]ection\s+(\d+(?:\([^)]+\))*)")
@dataclass
class GoverningProvision:
    section: str | None = None
    act: str | None = None
    chunk_id: str | None = None
    confidence: float = 0.0
    reason: str = ""
    matched_by: str = ""
@dataclass
class AnswerContract:
    question: str
    query_type: str = ""
    named_sections: list[str] = field(default_factory=list)
    named_act: str | None = None
    governing: GoverningProvision | None = None
    should_answer: bool = True
    must_cite_sections: set[str] = field(default_factory=set)
    required_concepts: list[str] = field(default_factory=list)
    required_authorities: list[str] = field(default_factory=list)
    expects_penalty: bool = False
    expects_exact_figures: bool = False
    flags: dict[str, Any] = field(default_factory=dict)
@dataclass
class ContractViolation:
    kind: str
    severity: str
    message: str
    expected: Any = None
    found: Any = None
    fix_hint: str = ""
@dataclass
class ContractCheckResult:
    violations: list[ContractViolation] = field(default_factory=list)
    passed: bool = True
    false_abstention: bool = False
    provision_mismatch: bool = False
    citation_mismatch: bool = False
    repair_suggestions: list[str] = field(default_factory=list)
    def add_violation(self, v: ContractViolation):
        self.violations.append(v)
        self.passed = False
        if v.kind == 'false_abstention': self.false_abstention = True
        if v.kind in ('wrong_provision','missed_provision'): self.provision_mismatch = True
class AnswerContractResolver:
    def __init__(self, payload_index: dict | None = None):
        self.payload_index = payload_index or {}
    def build_contract(self, question: str, context_chunks: list[Any], query_type: str = "", question_obj: Any = None) -> AnswerContract:
        c = AnswerContract(question=question, query_type=query_type)
        c.named_sections = detect_sections(question)
        c.named_act = detect_act(question)
        c.must_cite_sections = set(c.named_sections)
        # Also try to detect act from question_obj if present
        if question_obj and not c.named_act:
            try:
                for attr in ['act_name', 'act', 'statute']:
                    if hasattr(question_obj, attr):
                        val = getattr(question_obj, attr)
                        if val:
                            c.named_act = str(val)
                            break
            except Exception:
                pass
        if not c.named_act:
            ql = question.lower()
            if 'fss act' in ql or 'food safety' in ql:
                c.named_act = 'Food Safety and Standards Act, 2006'
            elif 'west bengal meat' in ql:
                c.named_act = 'West Bengal Meat Order, 1966'
            elif 'kmc act' in ql or 'kolkata municipal' in ql:
                c.named_act = 'Kolkata Municipal Corporation Act, 1980'
        if question_obj:
            try:
                if hasattr(question_obj,'gold_authorities'): c.required_authorities = list(question_obj.gold_authorities or [])
                if hasattr(question_obj,'gold_concepts'): c.required_concepts = list(question_obj.gold_concepts or [])
            except Exception: pass
        ql = question.lower()
        if any(w in ql for w in ['penalty','punish','fine','imprisonment','maximum','compounding', 'liable', 'legal consequence', 'prohibited', 'offence']):
            c.expects_penalty = True; c.expects_exact_figures = True
        c.governing = self._resolve_governing(question, context_chunks, c)
        if c.governing and c.governing.section: c.must_cite_sections.add(c.governing.section)
        return c
    def _resolve_governing(self, question: str, context_chunks: list[Any], c: AnswerContract):
        if c.named_sections:
            for ch in context_chunks:
                sec = getattr(ch,'section_number',None) or (ch.get('section_number') if isinstance(ch,dict) else None)
                cid = getattr(ch,'chunk_id',None) or (ch.get('chunk_id') if isinstance(ch,dict) else None)
                act = getattr(ch,'act_name','') or (ch.get('act_name','') if isinstance(ch,dict) else '')
                if sec and str(sec) in c.named_sections:
                    return GoverningProvision(section=str(sec), act=act or c.named_act, chunk_id=cid, confidence=0.9, reason=f'Question names Section {sec}', matched_by='named_section')
        if c.named_act:
            for ch in context_chunks:
                sec = getattr(ch,'section_number',None) or (ch.get('section_number') if isinstance(ch,dict) else None)
                cid = getattr(ch,'chunk_id',None) or (ch.get('chunk_id') if isinstance(ch,dict) else None)
                act = getattr(ch,'act_name','') or (ch.get('act_name','') if isinstance(ch,dict) else '')
                txt = getattr(ch,'text','') or (ch.get('text') or ch.get('chunk_text','') if isinstance(ch,dict) else '')
                if sec and act and c.named_act.lower() in str(act).lower() and len(str(txt))>100:
                    return GoverningProvision(section=str(sec), act=act, chunk_id=cid, confidence=0.7, reason=f'Act match with substantive section {sec}', matched_by='act_section')
        return None
    def repair_prompt(
        self,
        contract: AnswerContract,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[str, str]:
        """Return a contract-conditioned (system, user) prompt pair."""
        txt = user_prompt or ""
        tl = txt.lower()

        extra_parts: list[str] = []

        gov = contract.governing
        named = contract.named_sections

        system_add = ""

        if gov and gov.section:
            extra_parts.append(
                f"CRITICAL: The governing provision for this question is {gov.act or ''} Section {gov.section}. You MUST answer this question by citing and applying {gov.act or 'the Act'} Section {gov.section} with [n] markers. NEVER abstain or say 'not in context'/'cannot find the answer' if Section {gov.section} appears in the <legal_context>. If the provision is present, you MUST extract and apply it. Answer ALL parts of the question."
            )
            system_add = f" MANDATORY: You MUST cite {gov.act or 'the applicable Act'} Section {gov.section} with [n] markers. DO NOT give a blanket refusal. If the governing provision exists in the provided context, answer directly."
            if named:
                extra_parts.append(
                    "Address each of these named sections explicitly: "
                    + ", ".join(f"Section {s}" for s in named)
                    + "."
                )
        elif named:
            extra_parts.append(
                f"CRITICAL: The question names these sections/laws: {', '.join(f'Section {s}' for s in sorted(named))} and/or {contract.named_act or ''}. You MUST address each named element with a direct citation [n] from the provided context. Do NOT decline the entire question — answer the parts you can and explicitly state only which specific sub-part (if any) lacks support in the cited passages."
            )
            system_add = f" MANDATORY: You MUST address all sections named in the question and cite them with [n] markers from the context. Do NOT give a blanket 'not in context' refusal."
        else:
            extra_parts.append(
                "You MUST answer using ONLY the provided <legal_context>. Cite every material claim with [n] markers from the context. Do NOT guess or fabricate provisions. If only part of the question is answerable, answer that part and state the precise gap."
            )
            system_add = " MANDATORY: Answer what the context supports, cite with [n] markers. Avoid blanket refusals."

        if contract.expects_exact_figures:
            extra_parts.append(
                "Where the cited corpus supports a penalty, imprisonment, fine, or limit, state the exact figure or term as printed in the source (base fine/imprisonment, continuing daily fine if stated, escalation on repeat conviction if stated)."
            )

        if not extra_parts and not system_add:
            return system_prompt, user_prompt

        extra = "\n".join(extra_parts) if extra_parts else ""
        repair_directive = (f"\n\nCONTRACT INSTRUCTIONS (MANDATORY):\n{extra}" if extra else "")
        new_system = (system_prompt + system_add) if system_add else system_prompt
        return new_system, user_prompt + repair_directive

    def check_answer(self, answer: str, c: AnswerContract, context_chunks: list[Any] = None):
        txt = answer or ""
        tl = txt.lower()
        res = ContractCheckResult()
        if is_abstention(txt):
            # Be more aggressive for abstention_gate-style questions
            is_definite_question = any(w in tl for w in ['can ', 'is it ', 'may ', 'shall ', 'what is the legal consequence', 'penalty', 'prohibited', 'liable'])
            has_specific_law = bool(c.named_act) or bool(c.named_sections)
            if c.governing and c.governing.section:
                res.add_violation(ContractViolation(kind='false_abstention', severity='high', message=f'Abstained but governing provision Section {c.governing.section}', expected=f'Answer about Section {c.governing.section}', found='abstention/refusal'))
                return res
            elif c.must_cite_sections:
                res.add_violation(ContractViolation(kind='false_abstention', severity='high', message=f'Abstained but question names sections {sorted(c.must_cite_sections)}', expected='Address named sections', found='abstention/refusal'))
                return res
            elif has_specific_law and is_definite_question and len(context_chunks or []) > 0:
                res.add_violation(ContractViolation(kind='false_abstention', severity='high', message=f'Abstained on a definite question citing {c.named_act or "a specific law"} despite retrieved context', expected='Answer if context supports; else qualify which specific part is missing', found='abstention/refusal'))
                return res
            return res
        cited = set(_SECTION_CITE_RE.findall(txt))
        if c.must_cite_sections and c.governing and c.governing.section:
            if c.governing.section not in cited and c.governing.section not in txt:
                res.add_violation(ContractViolation(kind='wrong_provision' if cited else 'missed_provision', severity='high', message=f'Governing should be Section {c.governing.section}', expected=f'Section {c.governing.section}', found=sorted(cited) or None))
        if c.expects_exact_figures:
            am = _AMOUNT_RE.findall(txt)
            if not am and 'penalty' in tl:
                res.add_violation(ContractViolation(kind='missing_figure', severity='medium', message='Missing exact penalty figures', expected='Exact figures', found=None))
        return res
_resolver = AnswerContractResolver()
def get_answer_contract_resolver(): return _resolver
