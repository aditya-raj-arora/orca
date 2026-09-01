"""
SQLAlchemy ORM models mirroring app/db/schema.sql exactly.

Owner: P4 (Geospatial & Risk Engineer).
Reference: LLD v1.0 §3.

Keep these in lockstep with schema.sql — if you add/change a column in one,
change it in the other in the same commit.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Session(Base):
    __tablename__ = "session"

    session_id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    language: Mapped[str | None] = mapped_column(String(10))
    client_metadata: Mapped[dict | None] = mapped_column(JSONB)

    # TODO(P4): add relationship() to ConversationTurn once repos need it.


class ConversationTurn(Base):
    __tablename__ = "conversation_turn"

    turn_id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("session.session_id"))
    role: Mapped[str] = mapped_column(String(10))
    text: Mapped[str]
    detected_language: Mapped[str | None] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))

    __table_args__ = (CheckConstraint("role IN ('user','system')"),)


class AgentInvocation(Base):
    __tablename__ = "agent_invocation"

    invocation_id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    turn_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversation_turn.turn_id"))
    agent_name: Mapped[str] = mapped_column(String(30))
    input_payload: Mapped[dict] = mapped_column(JSONB)
    output_payload: Mapped[dict | None] = mapped_column(JSONB)
    data_timestamp: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    status: Mapped[str] = mapped_column(String(15), default="pending")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))


class GeofenceBoundary(Base):
    __tablename__ = "geofence_boundary"

    boundary_id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    type: Mapped[str] = mapped_column(String(10))
    name: Mapped[str | None] = mapped_column(String(100))
    # TODO(P4): geometry column needs GeoAlchemy2's Geometry type, not a plain
    # SQLAlchemy type — add `geoalchemy2` to requirements.txt when you implement
    # this (see LLD §3 note on PostGIS GEOMETRY + GIST index).
    source: Mapped[str | None] = mapped_column(String(100))
    last_updated: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))

    __table_args__ = (CheckConstraint("type IN ('IMBL','MPA')"),)
