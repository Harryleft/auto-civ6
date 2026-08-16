"""Pluggable department implementations and the default six-module registry."""

from .base import (
    Department,
    DepartmentAssessment,
    DepartmentContext,
    DepartmentPlugin,
    ReviewDisposition,
    SupportRequest,
    Workstream,
)
from .coordinator import (
    CampaignDraft,
    DepartmentRegistry,
    NationalStrategyBrief,
    NationalStrategyCoordinator,
)
from .civics import CivicsDepartment
from .diplomacy import DiplomacyDepartment
from .economy import EconomyDepartment
from .great_people import GreatPeopleDepartment
from .military import MilitaryDepartment
from .production import ProductionDepartment
from .science import ScienceDepartment


def default_department_registry() -> DepartmentRegistry:
    """Return the seven built-in departments as a fresh, replaceable registry."""

    return DepartmentRegistry(
        (
            MilitaryDepartment(),
            ScienceDepartment(),
            CivicsDepartment(),
            ProductionDepartment(),
            EconomyDepartment(),
            DiplomacyDepartment(),
            GreatPeopleDepartment(),
        )
    )

__all__ = [
    "CampaignDraft",
    "Department",
    "DepartmentAssessment",
    "DepartmentContext",
    "DepartmentPlugin",
    "DepartmentRegistry",
    "CivicsDepartment",
    "DiplomacyDepartment",
    "EconomyDepartment",
    "GreatPeopleDepartment",
    "MilitaryDepartment",
    "NationalStrategyBrief",
    "NationalStrategyCoordinator",
    "ProductionDepartment",
    "ReviewDisposition",
    "SupportRequest",
    "ScienceDepartment",
    "Workstream",
    "default_department_registry",
]
