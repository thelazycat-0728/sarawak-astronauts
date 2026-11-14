"""Live IRSA checks. Deselected by default; run with: python -m pytest -q -m live"""

from __future__ import annotations

import pytest

from app.irsa.epochs import EPOCH_ID_RE
from app.irsa.sia import find_epochs

pytestmark = pytest.mark.live


def test_barnard_d2_has_exposures():
    result = find_epochs(269.4464, 4.7668, "D2")
    assert len(result.epochs) >= 1
    assert all(e.exposure.band == "D2" for e in result.epochs)
    assert all(EPOCH_ID_RE.match(e.id) for e in result.epochs)
    assert result.visit_count >= 1
    assert set(result.releases) <= {"DR1", "QR3"}
