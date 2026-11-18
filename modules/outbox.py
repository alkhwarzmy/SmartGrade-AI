"""Deliver tenant events at least once; identity receipts make the audit effect exactly once."""

from types import SimpleNamespace
import sqlite3
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from database.models import Outbox


def flush_outbox(store, auth, limit=100):
    delivered = 0
    with store.sessions() as db:
        events = list(db.scalars(select(Outbox).order_by(Outbox.created_at, Outbox.id).limit(limit)))
    for event in events:
        payload = event.payload
        try:
            auth.audit(
                SimpleNamespace(organization_id=payload["organization_id"], user_id=payload["user_id"]),
                payload["action"],
                payload["object_id"],
                event_id=event.id,
            )
        except (sqlite3.Error, SQLAlchemyError, OSError):
            with store.sessions.begin() as db:
                row = db.get(Outbox, event.id)
                if row:
                    row.attempts += 1
            break  # Preserve per-tenant event order when a predecessor cannot be delivered.
        with store.sessions.begin() as db:
            row = db.get(Outbox, event.id)
            if row:
                db.delete(row)
        delivered += 1
    return delivered
