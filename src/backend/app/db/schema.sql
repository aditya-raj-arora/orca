-- ORCA database schema.
-- Owner: P4 (Geospatial & Risk Engineer), per LLD v1.0 §3.
-- This is the DDL exactly as specified in the LLD — do not diverge without
-- updating docs/ORCA_LLD_v1.0.docx §3 in the same PR (see LLD §8 sign-off note
-- and CONTRIBUTING.md §6 contract-lock discipline).
--
-- Requires the PostGIS extension (for geofence_boundary.geometry / GIST index).
-- All timestamps are stored in UTC; the client is responsible for localisation.

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE session (
    session_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    language        VARCHAR(10),
    client_metadata JSONB
);

CREATE TABLE conversation_turn (
    turn_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id        UUID NOT NULL REFERENCES session(session_id),
    role              VARCHAR(10) NOT NULL CHECK (role IN ('user','system')),
    text              TEXT NOT NULL,
    detected_language VARCHAR(10),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE agent_invocation (
    invocation_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    turn_id        UUID NOT NULL REFERENCES conversation_turn(turn_id),
    agent_name     VARCHAR(30) NOT NULL,
    input_payload  JSONB NOT NULL,
    output_payload JSONB,
    data_timestamp TIMESTAMPTZ,
    status         VARCHAR(15) NOT NULL DEFAULT 'pending',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE geofence_boundary (
    boundary_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type         VARCHAR(10) NOT NULL CHECK (type IN ('IMBL','MPA')),
    name         VARCHAR(100),
    geometry     GEOMETRY(Geometry, 4326) NOT NULL,
    source       VARCHAR(100),
    last_updated TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_turn_session ON conversation_turn(session_id);
CREATE INDEX idx_invocation_turn ON agent_invocation(turn_id);
CREATE INDEX idx_boundary_geom ON geofence_boundary USING GIST (geometry);

-- TODO(P4): add a migration tool (alembic) once the schema needs to evolve
-- past this initial baseline — for the prototype window a single schema.sql
-- applied at container init (see docker-compose.yml) is sufficient.
