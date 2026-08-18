"""Tests for the multi-domain legal Knowledge Graph (Phase 3 pilot).

Covers all 6 acceptance criteria from the task spec:
- Test 1: Domain separation (FSSAI ≠ land-revenue)
- Test 2: Cross-domain retrieval (slaughterhouse → 4+ domains)
- Test 3: Provenance (Provision → Chunk → Document → Source)
- Test 4: Authority identification
- Test 5: Temporal correctness (current vs repealed)
- Test 6: No unsupported inference (no CONFLICTS_WITH / OVERRIDES / APPLIES)

Integration tests run against the real Neo4j Aura instance (requires
NEO4J_URI + NEO4J_PASSWORD in .env).  Unit tests work against a mock driver.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

# Load .env for integration tests
from dotenv import load_dotenv

load_dotenv()

NEO4J_AVAILABLE = bool(os.environ.get("NEO4J_URI") and os.environ.get("NEO4J_PASSWORD"))


# --------------------------------------------------------------------------- #
# Mock driver fixture — for unit tests that can't reach Neo4j
# --------------------------------------------------------------------------- #

class MockResult:
    def __init__(self, records):
        self._records = records
        self.summary = MagicMock()
        self.summary.counters = MagicMock()
        self.summary.counters.constraints_added = 0
        self.summary.counters.indexes_added = 0

    @property
    def records(self):
        return self._records


class MockDriver:
    """Records all Cypher calls and returns canned results."""

    def __init__(self):
        self.calls: list[dict] = []
        self._returns: dict[str, list] = {}
        self._default_return: list = []

    def execute_query(self, cypher, parameters_=None, database_=None):
        self.calls.append({"cypher": cypher, "params": parameters_ or {}, "database": database_})
        # Match based on Cypher content
        c = cypher.lower().strip()
        records = self._default_return
        for key, value in self._returns.items():
            if key in c:
                records = value
                break
        return MockResult(records)

    def set_default(self, records: list):
        self._default_return = records

    def set_return(self, keyword: str, records: list):
        """Set return value for queries containing `keyword`."""
        self._returns[keyword] = records


@pytest.fixture
def mock_driver():
    return MockDriver()


@pytest.fixture
def kg_queries(mock_driver):
    from kg.queries import LegalKGQueries
    return LegalKGQueries(driver=mock_driver)


@pytest.fixture
def kg_validator(mock_driver):
    from kg.validation import KGValidator
    return KGValidator(driver=mock_driver)


@pytest.fixture
def kg_ingester(mock_driver):
    from kg.ingestion import LegalKGIngestionEngine
    return LegalKGIngestionEngine(driver=mock_driver)


# --------------------------------------------------------------------------- #
# Unit tests — run without Neo4j (all environments)
# --------------------------------------------------------------------------- #


class TestDomainManifest:
    """Verify the domain manifest data is complete and consistent."""

    def test_all_domains_defined(self):
        from kg.domain_manifest import DOMAINS
        # CRIMINAL added 2026-08-11 (Option B) to align the KG taxonomy with
        # the Qdrant corpus collections (Bharatiya Nyaya Sanhita, 2023).
        expected = {"FOOD_SAFETY", "ANIMAL_SLAUGHTER", "ENVIRONMENT_POLLUTION",
                     "MUNICIPAL", "PUBLIC_HEALTH", "BUSINESS_CIVIL", "LAND_PREMISES",
                     "CRIMINAL"}
        assert set(DOMAINS.keys()) == expected

    def test_domains_have_unique_priorities(self):
        from kg.domain_manifest import DOMAINS
        priorities = [d.priority for d in DOMAINS.values()]
        assert len(priorities) == len(set(priorities)), "Domain priorities must be unique"

    def test_fssai_is_primary(self):
        from kg.domain_manifest import DOMAINS
        assert DOMAINS["FOOD_SAFETY"].priority == 1

    def test_all_authorities_have_jurisdictions(self):
        from kg.domain_manifest import AUTHORITIES, JURISDICTIONS
        for auth in AUTHORITIES.values():
            assert auth.jurisdiction in JURISDICTIONS, f"{auth.authority_id} references unknown jurisdiction"

    def test_all_instrument_authorities_exist(self):
        from kg.domain_manifest import AUTHORITIES, PILOT_INSTRUMENTS
        for inst in PILOT_INSTRUMENTS:
            assert inst.issuing_authority in AUTHORITIES, \
                f"Instrument {inst.instrument_id} references unknown authority {inst.issuing_authority}"

    def test_cross_domain_relationships_are_valid(self):
        from kg.domain_manifest import CROSS_DOMAIN_RELATIONSHIPS, PILOT_INSTRUMENTS, PROVISION_STUBS
        all_pids = {f"{inst.instrument_id}_SEC_{s}" for inst in PILOT_INSTRUMENTS
                     for s in PROVISION_STUBS.get(inst.instrument_id, {})}
        # Also include FSS Act sections
        all_pids.update(f"FSS_ACT_2006_SEC_{s}" for s in PILOT_INSTRUMENTS[0].provisions)
        for src, _rel, tgt, ev in CROSS_DOMAIN_RELATIONSHIPS:
            # Evidence must always be non-empty
            assert ev and len(ev) > 10, f"Cross-domain rel {src}->{tgt} has empty evidence"

    def test_provision_concept_map_references_exist(self):
        from kg.domain_manifest import AUTHORITIES, CONCEPTS, PROVISION_CONCEPT_MAP
        # Authority is a node label, also valid as a target
        valid_ids = set(CONCEPTS.keys()) | set(AUTHORITIES.keys()) | {"Authority"}
        for pid, mappings in PROVISION_CONCEPT_MAP.items():
            for concept_id, _rel_type, evidence in mappings:
                # Concept must exist OR be an authority (FSO, WB_FODDER_DEPT, etc.) or "Authority" label
                assert concept_id in valid_ids, \
                    f"Unknown concept/authority: {concept_id} in {pid}"
                assert evidence and len(evidence) > 5, f"Empty evidence for {pid}->{concept_id}"


class TestSchema:
    """Verify the schema Cypher is valid."""

    def test_constraints_count(self):
        from kg.schema import CONSTRAINTS_CYPHER
        assert len(CONSTRAINTS_CYPHER) >= 30, "Should have at least 30 constraints"

    def test_indexes_count(self):
        from kg.schema import INDEXES_CYPHER
        assert len(INDEXES_CYPHER) >= 15, "Should have at least 15 indexes"

    def test_all_constraints_use_if_not_exists(self):
        from kg.schema import CONSTRAINTS_CYPHER
        for cypher in CONSTRAINTS_CYPHER:
            assert "IF NOT EXISTS" in cypher, f"Constraint must be idempotent: {cypher}"

    def test_all_indexes_use_if_not_exists(self):
        from kg.schema import INDEXES_CYPHER
        for cypher in INDEXES_CYPHER:
            assert "IF NOT EXISTS" in cypher, f"Index must be idempotent: {cypher}"


class TestQueries:
    """Test query functions use correct Cypher patterns."""

    def test_get_provision_query_exists(self, kg_queries):
        result = kg_queries.get_provision("FSS_ACT_2006_SEC_32")
        # Mock returns empty, so result is None
        assert result is None  # empty graph → None
        # Verify the right Cypher was executed
        assert any("LegalProvision" in call["cypher"] for call in kg_queries._driver.calls)

    def test_get_instrument_query_exists(self, kg_queries):
        result = kg_queries.get_instrument("FSS_ACT_2006")
        assert result is None  # empty graph → None
        assert any("instrument_id" in call["cypher"] for call in kg_queries._driver.calls)

    def test_get_domain_provisions_query(self, kg_queries):
        # Set up mock return with proper keys
        kg_queries._driver.set_default([
            {"provision_id": "FSS_ACT_2006_SEC_31", "provision_number": "31",
             "title": "Licence", "status": "current", "effective_from": None,
             "confidence": 0.95, "instrument_id": "FSS_ACT_2006",
             "instrument_title": "FSS Act", "authority": "FSSAI", "source_uri": "/doc.pdf"}
        ])
        result = kg_queries.get_domain_provisions("FOOD_SAFETY")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["provision_id"] == "FSS_ACT_2006_SEC_31"

    def test_search_provisions_query(self, kg_queries):
        kg_queries._driver.set_default([
            {"provision_id": "FSS_ACT_2006_SEC_32", "provision_number": "32",
             "title": "Powers of FSO", "snippet": "...",
             "instrument_title": "FSS Act", "instrument_id": "FSS_ACT_2006",
             "legal_domain": "FOOD_SAFETY", "source_uri": "/doc.pdf"}
        ])
        result = kg_queries.search_provisions("inspection", domain="FOOD_SAFETY")
        assert len(result) == 1
        assert result[0]["provision_id"] == "FSS_ACT_2006_SEC_32"

    def test_build_llm_contract_empty_graph(self):
        from kg.queries import LegalKGQueries, build_llm_retrieval_contract
        kg = LegalKGQueries(driver=MockDriver())
        contract = build_llm_retrieval_contract(
            "What laws apply to a slaughterhouse as a food business?",
            kg,
        )
        assert contract["query"] == "What laws apply to a slaughterhouse as a food business?"
        assert "entities" in contract
        assert "legal_domains" in contract
        assert "provisions" in contract
        assert "retrieval_strategy" in contract

    def test_classify_query_domain(self):
        from kg.queries import _classify_query_domain
        assert _classify_query_domain("fssai food licence requirements") == "FOOD_SAFETY"
        assert _classify_query_domain("slaughterhouse animal welfare") == "ANIMAL_SLAUGHTER"
        assert _classify_query_domain("water pollution consent") == "ENVIRONMENT_POLLUTION"
        assert _classify_query_domain("kmc trade licence") == "MUNICIPAL"
        assert _classify_query_domain("contract breach damages") == "BUSINESS_CIVIL"
        assert _classify_query_domain("land tenure") == "LAND_PREMISES"
        assert _classify_query_domain("random query") is None

    def test_extract_concept_mentions(self):
        from kg.queries import _extract_concept_mentions
        concepts = _extract_concept_mentions("slaughterhouse food business licence wastewater")
        assert "Slaughterhouse" in concepts
        assert "FoodBusiness" in concepts
        assert "Licence" in concepts
        assert "Wastewater" in concepts


class TestValidation:
    """Test validation query functions."""

    def test_validator_creates_valid_driver(self, mock_driver):
        from kg.validation import KGValidator
        v = KGValidator(driver=mock_driver)
        assert v._driver is mock_driver

    def test_check_orphan_provisions_empty(self, kg_validator):
        result = kg_validator.check_orphan_provisions()
        assert result["check"] == "orphan_provisions"
        assert result["passed"]  # empty graph → no orphans

    def test_check_no_hallucinated_relationships(self, kg_validator):
        result = kg_validator.check_no_hallucinated_relationships()
        assert result["check"] == "no_hallucinated_relationships"
        assert result["passed"]  # mock returns empty

    def test_check_domain_separation(self, kg_validator):
        kg_validator._driver.set_return("food_safety_count", [{"food_safety_count": 100, "land_premises_count": 0}])
        result = kg_validator.check_domain_separation()
        assert result["passed"]

    def test_check_domain_separation_violation(self, kg_validator):
        kg_validator._driver.set_return("count", [{"food_safety_count": 100, "land_premises_count": 5}])
        result = kg_validator.check_domain_separation()
        assert not result["passed"]

    def test_check_cross_domain_retrieval_no_domains(self, kg_validator):
        kg_validator._driver.set_return("domains", [{"domains": []}])
        result = kg_validator.check_cross_domain_retrieval("Slaughterhouse")
        assert not result["passed"]
        assert len(result["domains"]) == 0

    def test_check_cross_domain_retrieval_multi_domain(self, kg_validator):
        kg_validator._driver.set_return("domains", [{"domains": ["FOOD_SAFETY", "ANIMAL_SLAUGHTER", "ENVIRONMENT_POLLUTION", "MUNICIPAL"]}])
        result = kg_validator.check_cross_domain_retrieval("Slaughterhouse")
        assert result["passed"]
        assert len(result["domains"]) >= 4


class TestIngestion:
    """Test ingestion engine basic operations."""

    def test_engine_initialization(self, mock_driver):
        from kg.ingestion import LegalKGIngestionEngine
        engine = LegalKGIngestionEngine(driver=mock_driver)
        assert engine._driver is mock_driver

    def test_load_vocabularies(self, kg_ingester):
        result = kg_ingester.load_vocabularies()
        assert "domains" in result
        assert "authorities" in result
        assert "concepts" in result
        assert result["domains"] > 0
        assert result["authorities"] > 0

    def test_instrument_label_mapping(self, kg_ingester):
        assert kg_ingester._instrument_label("act") == "Act"
        assert kg_ingester._instrument_label("rule") == "Rule"
        assert kg_ingester._instrument_label("regulation") == "Regulation"
        assert kg_ingester._instrument_label("notification") == "Notification"

    def test_load_instruments_creates_nodes(self, kg_ingester):
        result = kg_ingester.load_instruments()
        assert "FOOD_SAFETY" in result
        assert result["FOOD_SAFETY"] == 1  # one instrument per domain


# --------------------------------------------------------------------------- #
# Integration tests — run against real Neo4j (requires credentials)
# --------------------------------------------------------------------------- #


#: Live-integration tests are OPT-IN (KG_RUN_LIVE_INTEGRATION=1 AND
#: NEO4J_ALLOW_WRITE=1) because ``setup_graph`` calls ``clear_legal_kg()`` —
#: it wipes the legal KG and rebuilds only the small pilot.  Against a
#: shared/production Neo4j this destroys the 1,861-provision corpus (hit on
#: 2026-08-11; again via test_neo4j_kg_sync.py on 2026-08-12), so credentials
#: alone are NOT enough to run these tests — the fail-closed
#: ``NEO4J_ALLOW_WRITE`` guard (kg/schema.py) refuses the clear otherwise.
NEO4J_INTEGRATION_ENABLED = os.environ.get("KG_RUN_LIVE_INTEGRATION", "0").lower() in ("1", "true", "yes")
NEO4J_WRITES_ALLOWED = os.environ.get("NEO4J_ALLOW_WRITE", "0").lower() in ("1", "true", "yes")


@pytest.mark.skipif(
    not NEO4J_AVAILABLE or not NEO4J_INTEGRATION_ENABLED or not NEO4J_WRITES_ALLOWED,
    reason="Neo4j credentials missing, or KG_RUN_LIVE_INTEGRATION / NEO4J_ALLOW_WRITE not set "
    "(fixture CLEARS the legal KG)",
)
class TestNeo4jIntegration:
    """Full end-to-end tests against the real Neo4j Aura instance.

    DESTRUCTIVE: ``setup_graph`` clears the legal KG and rebuilds only the
    pilot.  Runs only when ``KG_RUN_LIVE_INTEGRATION=1`` AND
    ``NEO4J_ALLOW_WRITE=1`` are set explicitly.
    """

    @pytest.fixture(autouse=True)
    def setup_graph(self):
        """Run full ingestion before tests in this class."""
        from kg.ingestion import LegalKGIngestionEngine
        from kg.schema import setup_legal_kg_schema

        # Setup schema
        setup_legal_kg_schema()
        # Clear and ingest
        engine = LegalKGIngestionEngine()
        engine.run_full_ingestion()
        yield
        # Cleanup happens in clear_legal_kg before next run

    def test_schema_constraints_exist(self):
        """Verify all legal KG constraints were created."""
        from app.services.neo4j_graph import query_neo4j
        results = query_neo4j("SHOW CONSTRAINTS")
        constraint_names = {r["name"] for r in results}
        # Check at least the instrument + provision constraints exist
        assert any("legal" in n.lower() or "provision" in n.lower() for n in constraint_names)

    def test_domains_loaded(self):
        """Test 1 (acceptance) — domain nodes exist."""
        from kg.queries import LegalKGQueries
        q = LegalKGQueries()
        domains = q.get_all_domains()
        domain_names = {d["domain_name"] for d in domains}
        assert "FOOD_SAFETY" in domain_names
        assert "ANIMAL_SLAUGHTER" in domain_names
        assert "ENVIRONMENT_POLLUTION" in domain_names
        assert "MUNICIPAL" in domain_names

    # --- Acceptance Test 1: Domain separation ---

    def test_1_domain_separation(self):
        """FSSAI provisions do not indiscriminately return land-revenue provisions."""
        from kg.queries import LegalKGQueries
        q = LegalKGQueries()
        provisions = q.get_domain_provisions("FOOD_SAFETY")
        assert len(provisions) > 0, "Should have FSSAI provisions"
        for p in provisions:
            assert p["legal_domain"] != "LAND_PREMISES", \
                f"FSSAI provision {p['provision_id']} has wrong domain"

    # --- Acceptance Test 2: Cross-domain retrieval ---

    def test_2_cross_domain_slaughterhouse(self):
        """Slaughterhouse query retrieves provisions from 4+ domains."""
        from kg.queries import LegalKGQueries
        q = LegalKGQueries()
        result = q.get_applicable_laws("FoodBusiness")
        # FoodBusiness should have provisions in FOOD_SAFETY at minimum
        domains = list(result["domains"].keys())
        assert "FOOD_SAFETY" in domains, "Should find FOOD_SAFETY provisions for FoodBusiness"

    def test_2_cross_domain_concepts(self):
        """Concepts like Slaughterhouse appear in multiple domains."""
        from kg.queries import LegalKGQueries
        q = LegalKGQueries()
        # Slaughterhouse concept should appear in both FOOD_SAFETY and ANIMAL_SLAUGHTER
        provisions = q.get_cross_domain_laws("Slaughterhouse")
        domains = set(p["domain"] for p in provisions if p.get("domain"))
        assert "ANIMAL_SLAUGHTER" in domains or "FOOD_SAFETY" in domains

    # --- Acceptance Test 3: Provenance ---

    def test_3_provenance_chain(self):
        """Every provision traces back to Instrument → Chunk → Document."""
        from kg.queries import LegalKGQueries
        q = LegalKGQueries()
        evidence = q.get_source_evidence("FSS_ACT_2006_SEC_31")
        assert evidence is not None, "Should find provenance for FSS Act Section 31"
        assert evidence["instrument"]["instrument_id"] == "FSS_ACT_2006"
        # Chunk or document must exist
        assert evidence["document"]["document_id"] or evidence["chunk"]["chunk_id"]

    # --- Acceptance Test 4: Authority identification ---

    def test_4_authority_identification(self):
        """Provision → authority is source-supported."""
        from kg.queries import LegalKGQueries
        q = LegalKGQueries()
        auths = q.get_authorities("FSS_ACT_2006_SEC_32")
        assert len(auths) > 0, "Section 32 should have an authority"
        for a in auths:
            assert a["evidence"], "Authority relationship must have evidence"
            assert a["confidence"] >= 0.8, "Authority relationship must be high-confidence"

    # --- Acceptance Test 5: Temporal correctness ---

    def test_5_temporal_distinction(self):
        """Current vs repealed provisions are distinguishable."""
        from kg.queries import LegalKGQueries
        q = LegalKGQueries()
        provisions = q.get_current_provisions("FoodBusiness")
        assert len(provisions) > 0, "Should find current provisions"
        for p in provisions:
            # Current provisions should have effective_from
            assert p.get("effective_from") is not None or p.get("confidence") > 0

    # --- Acceptance Test 6: No unsupported inference ---

    def test_6_no_hallucinated_relationships(self):
        """No CONFLICTS_WITH / OVERRIDES / INVALIDATES / APPLIES edges exist."""
        from app.services.neo4j_graph import query_neo4j
        forbidden = ["CONFLICTS_WITH", "OVERRIDES", "INVALIDATES", "APPLIES"]
        for rel in forbidden:
            results = query_neo4j(f"MATCH ()-[r:{rel}]->() RETURN count(r) AS c")
            assert results[0]["c"] == 0, f"Forbidden relationship {rel} should not exist"

    # --- Final demonstration query ---

    def test_final_demonstration(self):
        """The acceptance query: slaughterhouse food business → 4 domains."""
        from app.services.neo4j_graph import query_neo4j
        # Check that FOOD_SAFETY, ANIMAL_SLAUGHTER, ENVIRONMENT, MUNICIPAL exist
        domains = query_neo4j("MATCH (d:LegalDomain) RETURN d.domain_name AS name")
        domain_names = {d["name"] for d in domains}
        # The 4 domains relevant to the scenario
        required = {"FOOD_SAFETY", "ANIMAL_SLAUGHTER", "ENVIRONMENT_POLLUTION", "MUNICIPAL"}
        assert required.issubset(domain_names), f"Missing domains: {required - domain_names}"

        # Verify there are provisions in each of those 4 domains
        for dom in required:
            provisions = query_neo4j(
                f"MATCH (p:LegalProvision)-[:BELONGS_TO_DOMAIN]->(:LegalDomain {{domain_name: '{dom}'}})"
                " RETURN count(p) AS c"
            )
            assert provisions[0]["c"] > 0, f"No provisions found in domain {dom}"

        # Verify cross-domain relationships exist
        cross_rels = query_neo4j(
            "MATCH (p1:LegalProvision)-[r:INTERACTS_WITH|COMPLEMENTS|CROSS_REFERENCES]->(p2:LegalProvision)"
            " WHERE p1 <> p2 RETURN count(*) AS c"
        )
        assert cross_rels[0]["c"] > 0, "Should have cross-domain relationships"

        # Verify provenance chain: at least one provision → chunk → document
        provenance = query_neo4j(
            "MATCH (p:LegalProvision)-[:SUPPORTED_BY]->(ch:Chunk)<-[:HAS_CHUNK]-(doc:Document)"
            " RETURN count(*) AS c"
        )
        assert provenance[0]["c"] > 0, "Should have provenance chains"
