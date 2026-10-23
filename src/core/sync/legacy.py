"""Rewrite pre-sync WhatsApp rows onto the stable chat id.

Python's ``hash()`` cannot be reversed: it is salted per process, and the
old ``messages`` rows never stored the WhatsApp JID. What they did store
is the contact handle. For a 1:1 chat that handle is the other person's
phone number, and the bridge's chat id is ``{number}@c.us``. Shards of the
same chat (one per process salt) share that handle, so they can be merged.
A chat whose rows name more than one contact is a group; the group JID was
never stored, and those rows are left for ingest to adopt when history is
replayed.
"""

from __future__ import annotations

_JID_SUFFIX = "@c.us"


def whatsapp_jid_from_handle(handle: str) -> str | None:
    """Return the 1:1 JID for *handle*, or None when it is not a phone.

    Accepts ``15551212`` and ``15551212@c.us``. Anything else (a group JID,
    a LID, a display name) is not guessed.
    """
    raw = handle.strip()
    if raw.endswith(_JID_SUFFIX):
        local = raw[: -len(_JID_SUFFIX)]
        if local.isdigit() and 7 <= len(local) <= 15:
            return f"{local}{_JID_SUFFIX}"
        return None
    digits = raw.removeprefix("+")
    if digits.isdigit() and 7 <= len(digits) <= 15:
        return f"{digits}{_JID_SUFFIX}"
    return None


def legacy_chat_remap(chat_handles: dict[int, set[str]]) -> dict[int, str]:
    """Map an old salted ``chat_id`` to the reconstructed WhatsApp JID.

    *chat_handles* is ``chat_id -> {contact handles on that id}``. A chat
    is remapped only when every handle is the same phone number. Mixed
    handles stay put.
    """
    plan: dict[int, str] = {}
    for chat_id, handles in chat_handles.items():
        if len(handles) != 1:
            continue
        jid = whatsapp_jid_from_handle(next(iter(handles)))
        if jid is not None:
            plan[int(chat_id)] = jid
    return plan
