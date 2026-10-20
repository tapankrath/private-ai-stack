import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest  # noqa: E402

from gwhelpers import make  # noqa: E402


@pytest.fixture
def gw():
    return make()
