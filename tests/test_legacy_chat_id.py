"""Legacy WhatsApp chat ids remap when the contact handle is a phone."""

from __future__ import annotations

from src.core.sync.keys import stable_chat_id
from src.core.sync.legacy import legacy_chat_remap, whatsapp_jid_from_handle


def test_phone_handles_become_c_us_jids() -> None:
    assert whatsapp_jid_from_handle("15551212") == "15551212@c.us"
    assert whatsapp_jid_from_handle("+15551212") == "15551212@c.us"
    assert whatsapp_jid_from_handle("15551212@c.us") == "15551212@c.us"
    assert whatsapp_jid_from_handle("15551212@g.us") is None
    assert whatsapp_jid_from_handle("Ada") is None
    assert whatsapp_jid_from_handle("123") is None


def test_salted_shards_of_one_phone_merge_and_groups_do_not() -> None:
    plan = legacy_chat_remap(
        {
            111: {"15551212"},
            222: {"+15551212"},
            333: {"15551212", "19995550199"},
            444: {"not-a-phone"},
        }
    )
    assert plan[111] == "15551212@c.us"
    assert plan[222] == "15551212@c.us"
    assert 333 not in plan
    assert 444 not in plan
    assert stable_chat_id("whatsapp", plan[111]) == stable_chat_id(
        "whatsapp", "15551212@c.us"
    )
    assert stable_chat_id("whatsapp", plan[111]) != 111
