from types import SimpleNamespace

import pytest

from app.agents.schemas import RequirementProfile
from app.services.matching import check_constraints


@pytest.mark.parametrize("bound", [">=8", "≥8", "不低于 8 级", "至少8级"])
def test_model_trl_lower_bound_drives_deterministic_check(bound):
    profile = RequirementProfile(
        title="割草设备",
        normalized_description="TRL 至少8级",
        scenario="堤坡",
        problem="除草",
        completeness_score=1,
        hard_constraints={"TRL": bound},
    )
    assert profile.hard_constraints == {"min_maturity_level": 8}
    achievement = SimpleNamespace(maturity_level=7)
    chunks = [{"field": "maturity_level", "id": "trl-7", "text": "TRL 7"}]
    assert check_constraints(profile.hard_constraints, achievement, chunks)[0].status == "violated"
    achievement.maturity_level = None
    assert check_constraints(profile.hard_constraints, achievement, [])[0].status == "unknown"


@pytest.mark.parametrize("value", ["成熟度高", "<=8", "8", ">8", True])
def test_ambiguous_or_other_trl_bounds_are_not_reinterpreted(value):
    assert RequirementProfile.normalize_trl_minimum({"TRL": value}) == {"TRL": value}


def test_multiple_explicit_lower_bounds_keep_stricter_requirement():
    assert RequirementProfile.normalize_trl_minimum(
        {"min_maturity_level": None, "TRL": ">=8"}
    ) == {"min_maturity_level": 8}
    assert RequirementProfile.normalize_trl_minimum(
        {
            "min_maturity_level": 9,
            "TRL": ">=8",
            "施工": "不停水",
        }
    ) == {"min_maturity_level": 9, "施工": "不停水"}
