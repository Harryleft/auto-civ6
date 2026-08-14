from civ6_belief_engine.belief_engine import BeliefEngine as ProductBeliefEngine
from civ6_belief_engine.governance import GovernanceCouncil as ProductGovernanceCouncil
from civ_mcp.belief_engine import BeliefEngine as LegacyBeliefEngine
from civ_mcp.governance import GovernanceCouncil as LegacyGovernanceCouncil


def test_mcp_compatibility_paths_forward_to_product_domain_package():
    assert LegacyBeliefEngine is ProductBeliefEngine
    assert LegacyGovernanceCouncil is ProductGovernanceCouncil
