import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from rea.core_runner import CoreError, find_core  # noqa: E402
from rea.facts import FactSheet  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
DEMO = ROOT / "data" / "demo"


def load_sheet(name: str) -> FactSheet:
    return FactSheet(json.loads((FIXTURES / f"facts_{name}.json").read_text()))


@pytest.fixture
def bg_sheet() -> FactSheet:
    """Forward B-phase-to-ground fault, 51G1 operated as expected, breaker normal."""
    return load_sheet("demo_bg_51g_normal")


@pytest.fixture
def ag_reverse_sheet() -> FactSheet:
    """Reverse A-phase-to-ground fault, relay correctly did not trip."""
    return load_sheet("demo_ag_reverse_restrained")


@pytest.fixture
def abc_sheet() -> FactSheet:
    """Three-phase fault, relay failed to trip."""
    return load_sheet("demo_abc_failed_to_trip")


@pytest.fixture
def cag_slow_sheet() -> FactSheet:
    """C-A-to-ground fault, relay slower than expected."""
    return load_sheet("demo_cag_slow_relay")


@pytest.fixture
def bc_slow_breaker_sheet() -> FactSheet:
    """B-C fault, relay as expected, breaker slow."""
    return load_sheet("demo_bc_slow_breaker")


@pytest.fixture(scope="session")
def core_bin() -> Path:
    try:
        return find_core()
    except CoreError:
        pytest.skip("rea-core is not built (run `make core`)")
