"""REST ingest uses provider ids and does not summarize inside the request."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import pytest

from src.api import IncomingMessage, MissingSendTimestamp, api, record_from_payload
from src.core.sync.types import IngestResult


def _payload(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "platform": "whatsapp",
        "chat_id": "15551212@c.us",
        "from_id": "15551212",
        "contact_name": "Ada",
        "text": "hello",
        "timestamp": "2024-05-01T12:00:00+00:00",
        "external_message_id": "true_15551212@c.us_ABCDEF",
        "from_me": False,
    }
    body.update(overrides)
    return body


def test_record_does_not_use_python_hash_for_identity() -> None:
    msg = IncomingMessage(**_payload())
    record = record_from_payload(msg)
    assert record.external_message_id == "true_15551212@c.us_ABCDEF"
    assert record.external_chat_id == "15551212@c.us"
    assert record.timestamp.year == 2024
    assert record.is_from_owner is False


def test_owner_flag_is_stored_for_whatsapp_from_me() -> None:
    msg = IncomingMessage(**_payload(from_me=True, text="my reply"))
    record = record_from_payload(msg)
    assert record.is_from_owner is True


def test_missing_send_timestamp_is_rejected() -> None:
    body = _payload()
    del body["timestamp"]
    with pytest.raises(MissingSendTimestamp):
        record_from_payload(IncomingMessage(**body))


def test_unparseable_send_timestamp_is_rejected() -> None:
    with pytest.raises(MissingSendTimestamp):
        record_from_payload(IncomingMessage(**_payload(timestamp="not-a-date")))


def test_batch_skips_messages_without_a_send_timestamp() -> None:
    client = TestClient(api)
    result = IngestResult(inserted=1)
    kept = _payload()
    dropped = _payload(external_message_id="no-time", text="later")
    del dropped["timestamp"]
    with (
        patch("src.api.settings") as mock_settings,
        patch(
            "src.api.persist_incoming", new_callable=AsyncMock, return_value=result
        ) as persist,
    ):
        mock_settings.api_secret_key = ""
        mock_settings.owner_telegram_id = 42
        mock_settings.owner_whatsapp_number = ""
        response = client.post(
            "/api/messages/batch",
            json={"messages": [dropped, kept], "history_complete": True},
        )
    assert response.status_code == 200
    assert response.json()["skipped"] == 1
    assert response.json()["inserted"] == 1
    records = persist.await_args.args[0]
    assert len(records) == 1
    assert records[0].external_message_id == kept["external_message_id"]
    assert persist.await_args.kwargs["gap_blocks_complete"] is True


def test_single_message_without_timestamp_is_422() -> None:
    client = TestClient(api)
    body = _payload()
    del body["timestamp"]
    with patch("src.api.settings") as mock_settings:
        mock_settings.api_secret_key = ""
        mock_settings.owner_telegram_id = 42
        mock_settings.owner_whatsapp_number = ""
        response = client.post("/api/messages", json=body)
    assert response.status_code == 422


def test_missing_provider_id_is_stable_for_the_same_payload() -> None:
    body = _payload()
    del body["external_message_id"]
    first = record_from_payload(IncomingMessage(**body))
    second = record_from_payload(IncomingMessage(**body))
    assert first.external_message_id.startswith("sha256:")
    assert first.external_message_id == second.external_message_id


def test_batch_reports_duplicates_and_does_not_catch_up() -> None:
    client = TestClient(api)
    result = IngestResult(inserted=0, duplicates=2)
    with (
        patch("src.api.settings") as mock_settings,
        patch(
            "src.api.persist_incoming", new_callable=AsyncMock, return_value=result
        ) as persist,
        patch(
            "src.core.sync.catchup.catchup_unsummarized_days",
            new_callable=AsyncMock,
        ) as catchup,
    ):
        mock_settings.api_secret_key = ""
        mock_settings.owner_telegram_id = 42
        mock_settings.owner_whatsapp_number = ""
        response = client.post(
            "/api/messages/batch",
            json={"messages": [_payload(), _payload()], "history_complete": True},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["duplicates"] == 2
    assert body["inserted"] == 0
    assert body["catchup"] == "deferred"
    persist.assert_awaited_once()
    assert persist.await_args.kwargs["history_complete"] is True
    catchup.assert_not_called()


def test_single_message_replay_is_ok() -> None:
    client = TestClient(api)
    with (
        patch("src.api.settings") as mock_settings,
        patch(
            "src.api.persist_incoming",
            new_callable=AsyncMock,
            return_value=IngestResult(duplicates=1),
        ),
    ):
        mock_settings.api_secret_key = ""
        mock_settings.owner_telegram_id = 42
        mock_settings.owner_whatsapp_number = ""
        response = client.post("/api/messages", json=_payload())
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["duplicates"] == 1
