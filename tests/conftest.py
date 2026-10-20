import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest  # noqa: E402

from advhelpers import load_config, profile_from_dict, raw_profiles  # noqa: E402


@pytest.fixture(scope="session")
def cfg():
    return load_config()


@pytest.fixture
def profiles():
    return {k: profile_from_dict(v) for k, v in raw_profiles().items()}
