from __future__ import annotations

import pytest

from app.database.models import Nation
from app.services.ai_domain import require_ai_nation, require_human_nation


def test_ai_and_human_domain_guards_are_explicit():
    human = Nation(
        group_id=-100945001,
        name="Human Guard",
        currency_code="HGD",
        invite_code="OPX-AI-945001",
        is_ai=False,
    )
    ai = Nation(
        group_id=None,
        name="AI Guard",
        currency_code="AIG",
        invite_code="OPX-AI-945002",
        is_ai=True,
    )

    assert require_human_nation(human) == -100945001
    require_ai_nation(ai)

    with pytest.raises(ValueError):
        require_human_nation(ai)

    with pytest.raises(ValueError):
        require_ai_nation(human)
