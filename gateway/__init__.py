"""Gateway: the only path to the model. Owns policy, ACLs, audit, retrieval and memory."""

__version__ = "0.3.0"

SENSITIVITY = {"public": 0, "personal": 1, "confidential": 2, "restricted": 3}
SENSITIVITY_NAMES = {v: k.capitalize() for k, v in SENSITIVITY.items()}


def sens_level(value) -> int:
    """Accept 0-3 or a name (case-insensitive)."""
    if isinstance(value, int):
        if value not in SENSITIVITY_NAMES:
            raise ValueError(f"sensitivity must be 0-3, got {value}")
        return value
    return SENSITIVITY[str(value).lower()]
