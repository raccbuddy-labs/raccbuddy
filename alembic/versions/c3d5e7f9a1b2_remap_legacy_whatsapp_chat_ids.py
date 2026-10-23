"""remap legacy WhatsApp chat ids

Rows ingested before history sync stored ``chat_id`` as Python's salted
``hash()`` of the WhatsApp JID. That integer cannot be recomputed. Where
every message in a chat belongs to one phone-number contact, rewrite
``chat_id`` to the SHA-256 id used for ``{number}@c.us`` and record that
JID on ``external_chat_id``. Group threads (more than one contact) are
not guessed; a later backfill adopts those rows by text and send time.

Downgrade cannot restore the salted ids.

Revision ID: c3d5e7f9a1b2
Revises: b2c4d6e8f0a1
Create Date: 2026-10-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3d5e7f9a1b2"
down_revision: str | Sequence[str] | None = "b2c4d6e8f0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Point 1:1 WhatsApp history at the stable chat id."""
    from src.core.sync.keys import stable_chat_id
    from src.core.sync.legacy import legacy_chat_remap

    bind = op.get_bind()
    rows = bind.execute(sa.text("""
            SELECT m.chat_id, c.contact_handle
            FROM messages AS m
            JOIN contacts AS c ON c.id = m.from_contact_id
            WHERE m.platform = 'whatsapp'
              AND m.external_message_id IS NULL
            """)).fetchall()
    grouped: dict[int, set[str]] = {}
    for chat_id, handle in rows:
        if handle is None:
            continue
        grouped.setdefault(int(chat_id), set()).add(str(handle))

    for old_id, jid in legacy_chat_remap(grouped).items():
        bind.execute(
            sa.text("""
                UPDATE messages
                SET chat_id = :new_id,
                    external_chat_id = :jid
                WHERE platform = 'whatsapp'
                  AND chat_id = :old_id
                  AND external_message_id IS NULL
                """),
            {
                "new_id": stable_chat_id("whatsapp", jid),
                "jid": jid,
                "old_id": old_id,
            },
        )


def downgrade() -> None:
    """Salted hash() ids cannot be reconstructed. Leave the stable ids."""
    return
