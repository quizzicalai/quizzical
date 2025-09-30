# backend/tests/integration/test_result.py

import uuid
import typing as t

import pytest
from pydantic import BaseModel

from app.main import API_PREFIX
from app.models.api import ShareableResultResponse


def _strip_optional(tp):
    """Return the non-None type from Optional/Union[..., None]."""
    origin = t.get_origin(tp)
    args = t.get_args(tp)
    if origin is t.Union and args:
        non_none = [a for a in args if a is not type(None)]  # noqa: E721
        return non_none[0] if non_none else tp
    return tp


def _minimal_for_type(tp):
    """Produce a minimal value for a type annotation."""
    tp = _strip_optional(tp)

    # Base primitives
    if tp is str:
        return "test"
    if tp is int:
        return 0
    if tp is float:
        return 0.0
    if tp is bool:
        return True

    # UUID
    try:
        from uuid import UUID
        if tp is UUID:
            return uuid.uuid4()
    except Exception:
        pass

    origin = t.get_origin(tp)
    args = t.get_args(tp)

    # Collections
    if origin in (list, t.List):
        # Prefer empty list (models usually allow it)
        return []
    if origin in (dict, t.Dict):
        return {}
    if origin in (t.Tuple, tuple):
        return tuple()

    # Nested Pydantic model
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        return _minimal_instance_dict(tp)

    # Fallback to string
    return "test"


def _minimal_instance_dict(model_cls: t.Type[BaseModel]) -> dict:
    """
    Build a minimal dict satisfying required fields for a Pydantic v2 model.
    Recurses for nested models.
    """
    data = {}
    for name, field in model_cls.model_fields.items():  # type: ignore[attr-defined]
        if field.is_required():
            data[name] = _minimal_for_type(field.annotation)
    # Make results nicer if these common fields exist
    for maybe, value in (
        ("title", "Test Title"),
        ("description", "Test Description"),
        ("name", "Test Name"),
    ):
        if maybe in model_cls.model_fields and maybe not in data:
            data[maybe] = value
    return data


def _build_shareable_result(expected_id: uuid.UUID) -> ShareableResultResponse:
    """
    Create a minimal valid ShareableResultResponse, setting id/result_id
    fields to the expected path param when present.
    """
    payload = _minimal_instance_dict(ShareableResultResponse)

    # If the model includes an id field, set it to the path param
    for field_name in ("result_id", "id"):
        if field_name in ShareableResultResponse.model_fields and field_name not in payload:  # type: ignore[attr-defined]
            payload[field_name] = expected_id

    # Prefer updating if field already exists
    for field_name in ("result_id", "id"):
        if field_name in payload:
            payload[field_name] = expected_id

    return ShareableResultResponse.model_validate(payload)


@pytest.fixture()
def override_result_service_not_found(monkeypatch):
    """
    Override FastAPI DI for ResultService to return None (simulate no row).
    """
    from app.main import app as fastapi_app
    from app.services.database import ResultService

    class _FakeService:
        async def get_result_by_id(self, _rid):
            return None

    fastapi_app.dependency_overrides[ResultService] = lambda: _FakeService()
    try:
        yield
    finally:
        fastapi_app.dependency_overrides.pop(ResultService, None)


@pytest.fixture()
def override_result_service_success(monkeypatch):
    """
    Override ResultService to return a minimal valid ShareableResultResponse.
    Yields the JSON we expect on the wire (by_alias=True) so the test can assert.
    """
    from app.main import app as fastapi_app
    from app.services.database import ResultService

    expected_holder = {"json": None}  # simple box to share with the test

    class _FakeService:
        async def get_result_by_id(self, rid):
            model = _build_shareable_result(rid)
            expected_holder["json"] = model.model_dump(by_alias=True)
            return model

    fastapi_app.dependency_overrides[ResultService] = lambda: _FakeService()
    try:
        yield expected_holder
    finally:
        fastapi_app.dependency_overrides.pop(ResultService, None)


@pytest.mark.anyio
async def test_get_result_404(async_client, override_result_service_not_found):
    api = API_PREFIX.rstrip("/")
    rid = uuid.uuid4()
    r = await async_client.get(f"{api}/result/{rid}")
    assert r.status_code == 404
    assert "not found" in r.text.lower()


@pytest.mark.anyio
async def test_get_result_200_returns_shareable_payload(async_client, override_result_service_success):
    api = API_PREFIX.rstrip("/")
    rid = uuid.uuid4()
    r = await async_client.get(f"{api}/result/{rid}")
    assert r.status_code == 200

    body = r.json()
    expected = override_result_service_success["json"]
    # Sanity: we should have built an expected payload
    assert isinstance(expected, dict) and expected, "Expected JSON not prepared by fake service"

    # Response should match the model's by_alias dump exactly
    assert body == expected
