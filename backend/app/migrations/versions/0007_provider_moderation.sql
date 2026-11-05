-- Existing legacy decisions and strikes are preserved. Provider calls have no
-- source FK: source text is purged immediately for nonpublic outcomes.
ALTER TABLE submission_receipts ADD COLUMN required_client_features text NOT NULL DEFAULT ''
    CHECK (required_client_features IN ('','support-v1'));

ALTER TABLE operator_audit_logs DROP CONSTRAINT operator_audit_logs_action_check;
ALTER TABLE operator_audit_logs ADD CONSTRAINT operator_audit_logs_action_check CHECK (action IN (
    'HIDE_SUBMISSION','RESET_RESTRICTION','SET_BOARD_MODE','PAUSE_SUBMISSIONS','PAUSE_DISPLAY',
    'CREATE_PAIRING_CODE','REVOKE_DEVICE','DISABLE_OPERATOR','PAUSE_PROVIDER','RESUME_PROVIDER'));

CREATE TABLE moderation_provider_control (
    id text PRIMARY KEY CHECK (id='gemini'),
    mode text NOT NULL DEFAULT 'OPEN' CHECK (mode IN ('OPEN','PAUSED','OPEN_CIRCUIT','HALF_OPEN')),
    consecutive_failures integer NOT NULL DEFAULT 0 CHECK (consecutive_failures>=0),
    opened_until timestamptz,
    generation bigint NOT NULL DEFAULT 0 CHECK (generation>=0),
    probe_successes integer NOT NULL DEFAULT 0 CHECK (probe_successes>=0),
    last_probe_at timestamptz,
    closed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
INSERT INTO moderation_provider_control(id) VALUES('gemini');

CREATE TABLE moderation_provider_attempts (
    submission_id uuid PRIMARY KEY,
    release_id text NOT NULL CHECK (char_length(release_id) BETWEEN 1 AND 100),
    profile text NOT NULL CHECK (profile IN ('gemini_mock','gemini')),
    status text NOT NULL CHECK (status IN ('RESERVED','STARTED','COMPLETED','UNKNOWN','CANCELLED')),
    input_token_reservation integer NOT NULL CHECK (input_token_reservation>=0),
    output_token_reservation integer NOT NULL CHECK (output_token_reservation>=0),
    reserved_micro_usd bigint NOT NULL CHECK (reserved_micro_usd>=0),
    charged_micro_usd bigint CHECK (charged_micro_usd>=0),
    actual_input_tokens integer CHECK (actual_input_tokens>=0),
    actual_output_tokens integer CHECK (actual_output_tokens>=0),
    reserved_at timestamptz NOT NULL,
    cost_day date NOT NULL,
    rate_day date NOT NULL,
    started_at timestamptz,
    completed_at timestamptz,
    run_token uuid,
    outcome_code text CHECK (outcome_code IS NULL OR char_length(outcome_code) BETWEEN 1 AND 100),
    CHECK ((status='RESERVED' AND started_at IS NULL AND run_token IS NULL)
        OR (status='CANCELLED' AND started_at IS NULL)
        OR (status IN ('STARTED','COMPLETED','UNKNOWN') AND started_at IS NOT NULL AND run_token IS NOT NULL)),
    CHECK (completed_at IS NULL OR completed_at>=reserved_at),
    CHECK (status NOT IN ('COMPLETED','UNKNOWN','CANCELLED') OR completed_at IS NOT NULL)
);
CREATE INDEX provider_attempt_reserved_idx ON moderation_provider_attempts(reserved_at);
CREATE INDEX provider_attempt_started_idx ON moderation_provider_attempts(started_at);
CREATE INDEX provider_attempt_cost_idx ON moderation_provider_attempts(cost_day);
CREATE INDEX provider_attempt_rate_idx ON moderation_provider_attempts(rate_day);
CREATE INDEX provider_attempt_status_idx ON moderation_provider_attempts(status,reserved_at);

CREATE TABLE moderation_provider_daily (
    date date PRIMARY KEY,
    charged_micro_usd bigint NOT NULL DEFAULT 0 CHECK (charged_micro_usd>=0),
    calls integer NOT NULL DEFAULT 0 CHECK (calls>=0),
    input_tokens bigint NOT NULL DEFAULT 0 CHECK (input_tokens>=0),
    output_tokens bigint NOT NULL DEFAULT 0 CHECK (output_tokens>=0),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE content_reports (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    board_id uuid NOT NULL REFERENCES board_runtime(id) ON DELETE CASCADE,
    reporter_id uuid NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    idempotency_key uuid NOT NULL,
    request_hash bytea NOT NULL CHECK (octet_length(request_hash)=32),
    submission_id uuid,
    observed_submission_id uuid,
    category text NOT NULL CHECK (category IN ('HARASSMENT','PRIVACY','THREAT','OTHER')),
    status text NOT NULL DEFAULT 'RECEIVED' CHECK (status IN ('RECEIVED','RESOLVED')),
    received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    resolved_at timestamptz,
    resolved_by uuid REFERENCES operators(id) ON DELETE SET NULL,
    resolution text CHECK (resolution IN ('HIDDEN','BOARD_PAUSED','NO_ACTION','INSUFFICIENT_EVIDENCE')),
    UNIQUE(reporter_id,idempotency_key),
    CHECK ((status='RECEIVED' AND resolved_at IS NULL AND resolution IS NULL)
        OR (status='RESOLVED' AND resolved_at IS NOT NULL AND resolution IS NOT NULL)),
    CHECK (resolved_at IS NULL OR resolved_at>=received_at)
);
CREATE INDEX content_reports_board_idx ON content_reports(board_id,received_at DESC,id DESC);
CREATE INDEX content_reports_rate_idx ON content_reports(reporter_id,received_at);
CREATE INDEX content_reports_expiry_idx ON content_reports(received_at);

CREATE TABLE report_commands (
    operator_id uuid NOT NULL REFERENCES operators(id) ON DELETE CASCADE,
    command_key uuid NOT NULL,
    report_id uuid NOT NULL REFERENCES content_reports(id) ON DELETE CASCADE,
    request_hash bytea NOT NULL CHECK (octet_length(request_hash)=32),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(operator_id,command_key)
);
