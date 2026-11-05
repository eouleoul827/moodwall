-- Diagnostics are advisory operational metadata, never student text or tokens.
CREATE TABLE display_diagnostic_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    board_id uuid NOT NULL REFERENCES board_runtime(id) ON DELETE CASCADE,
    device_generation bigint NOT NULL CHECK (device_generation >= 0),
    component text NOT NULL CHECK (component IN ('SESSION','QR','PLAYBACK','CACHE','RENDER')),
    code text NOT NULL CHECK (code IN ('CONNECTION_FAILED','AUTH_REQUIRED','QR_UNAVAILABLE',
        'PLAYBACK_OWNED','PLAYBACK_INTERRUPTED','PLAYBACK_UNKNOWN','ASSET_FAILURE',
        'STORAGE_UNAVAILABLE','RENDER_FAILURE')),
    state text NOT NULL CHECK (state IN ('FAILED','RECOVERED')),
    version text NOT NULL CHECK (version='1'),
    count integer NOT NULL CHECK (count BETWEEN 1 AND 1000),
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL
);
CREATE INDEX display_diagnostic_board_recent_idx
    ON display_diagnostic_events(board_id,received_at DESC,id DESC);
CREATE INDEX display_diagnostic_expiry_idx ON display_diagnostic_events(received_at,id);

CREATE TABLE display_diagnostic_latest (
    board_id uuid NOT NULL REFERENCES board_runtime(id) ON DELETE CASCADE,
    device_generation bigint NOT NULL CHECK (device_generation >= 0),
    component text NOT NULL CHECK (component IN ('SESSION','QR','PLAYBACK','CACHE','RENDER')),
    code text NOT NULL CHECK (code IN ('CONNECTION_FAILED','AUTH_REQUIRED','QR_UNAVAILABLE',
        'PLAYBACK_OWNED','PLAYBACK_INTERRUPTED','PLAYBACK_UNKNOWN','ASSET_FAILURE',
        'STORAGE_UNAVAILABLE','RENDER_FAILURE')),
    state text NOT NULL CHECK (state IN ('FAILED','RECOVERED')),
    version text NOT NULL CHECK (version='1'),
    count integer NOT NULL CHECK (count BETWEEN 1 AND 1000),
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL,
    PRIMARY KEY(board_id,device_generation,component,code)
);
CREATE INDEX display_diagnostic_latest_expiry_idx ON display_diagnostic_latest(received_at);
COMMENT ON TABLE display_diagnostic_events IS 'Allowlisted advisory reports only, 7 days and at most 1000 events per board.';
COMMENT ON TABLE display_diagnostic_latest IS 'Latest component/code report per device generation; not evidence of physical panel health.';
