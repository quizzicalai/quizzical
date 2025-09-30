import uuid
import pytest


@pytest.fixture
def ids():
    """
    Minimal identity payload many agent/tool tests expect.

    Returns:
        dict: {"trace_id": "t-<hex>", "session_id": "<uuid4>"}
    """
    return {
        "trace_id": f"t-{uuid.uuid4().hex}",
        "session_id": str(uuid.uuid4()),
    }
