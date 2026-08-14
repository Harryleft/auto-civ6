from civ_mcp.lua.models import GovernmentStatus, PolicyInfo, PolicySlot
from civ_mcp import narrate


def test_empty_wildcard_slot_is_explicit_action_required():
    status = GovernmentStatus(
        government_name="Classical Republic",
        government_type="GOVERNMENT_CLASSICAL_REPUBLIC",
        slots=[
            PolicySlot(0, "SLOT_ECONOMIC", "POLICY_URBAN_PLANNING", "Urban Planning"),
            PolicySlot(1, "SLOT_WILDCARD", None, None),
        ],
        available_policies=[
            PolicyInfo(
                policy_type="POLICY_INSPIRATION",
                name="Inspiration",
                description="+2 Great Person points per turn.",
                slot_type="SLOT_WILDCARD",
            )
        ],
    )

    text = narrate.narrate_policies(status)

    assert "ACTION REQUIRED" in text
    assert "1 (Wildcard)" in text
    assert "set_policies(assignments='...')" in text


def test_policy_report_does_not_require_empty_slot_without_available_card():
    status = GovernmentStatus(
        government_name="Chiefdom",
        government_type="GOVERNMENT_CHIEFDOM",
        slots=[PolicySlot(0, "SLOT_WILDCARD", None, None)],
    )

    assert "ACTION REQUIRED" not in narrate.narrate_policies(status)
