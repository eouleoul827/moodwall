-- Moodwall current design schema, 2026-10-29. PostgreSQL 16+; head 0005_floating_bubbles.
-- Empty database only; review and translate to versioned migrations before deployment.
-- This file does NOT install Procrastinate's schema or create privileged DB roles.
-- Run as a migration owner. The API/worker must use a separate non-owner role.
-- No model, credentials, raw QR tokens, IP addresses or session tokens are seeded.

BEGIN;

-- Pure validation helper: JSONB CHECK expressions cannot contain subqueries.
-- Rule identifiers are bounded metadata, never matching text or offsets.
CREATE FUNCTION moodwall_rule_evidence_valid(value jsonb)
RETURNS boolean
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$
    SELECT CASE WHEN jsonb_typeof(value) IS DISTINCT FROM 'array' THEN false
    ELSE jsonb_array_length(value) <= 5 AND NOT EXISTS (
        SELECT 1 FROM jsonb_array_elements(value) AS evidence(item)
        WHERE CASE WHEN jsonb_typeof(item) IS DISTINCT FROM 'object' THEN true
        ELSE item - 'rule_id' - 'variant_id' <> '{}'::jsonb
            OR jsonb_typeof(item -> 'rule_id') IS DISTINCT FROM 'string'
            OR jsonb_typeof(item -> 'variant_id') IS DISTINCT FROM 'string'
            OR (item ->> 'rule_id') !~ '^[A-Za-z0-9_.-]{1,100}$'
            OR (item ->> 'variant_id') !~ '^[A-Za-z0-9_.-]{1,100}$'
        END
    ) END;
$$;

CREATE TABLE participants (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_submission_at timestamptz,
    CONSTRAINT participants_seen_order CHECK (last_seen_at >= created_at)
);

CREATE TABLE board_runtime (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 100),
    mode text NOT NULL DEFAULT 'MOODWALL'
        CHECK (mode IN ('MOODWALL', 'CHATBOT')),
    submissions_paused boolean NOT NULL DEFAULT false,
    display_paused boolean NOT NULL DEFAULT false,
    state_version bigint NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    last_reconciled_at timestamptz,
    last_heartbeat_at timestamptz,
    heartbeat_lease_until timestamptz,
    device_token_hash bytea,
    device_token_expires_at timestamptz,
    device_token_version bigint NOT NULL DEFAULT 0 CHECK (device_token_version >= 0),
    paired_at timestamptz,
    pending_pairing_hash bytea,
    pending_pairing_expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT board_device_hash_length CHECK (
        device_token_hash IS NULL OR octet_length(device_token_hash) = 32
    ),
    CONSTRAINT board_device_fields CHECK (
        (device_token_hash IS NULL AND device_token_expires_at IS NULL AND paired_at IS NULL)
        OR (device_token_hash IS NOT NULL AND device_token_expires_at IS NOT NULL
            AND paired_at IS NOT NULL AND device_token_expires_at > paired_at)
    ),
    CONSTRAINT board_pairing_fields CHECK (
        (pending_pairing_hash IS NULL AND pending_pairing_expires_at IS NULL)
        OR (pending_pairing_hash IS NOT NULL AND pending_pairing_expires_at IS NOT NULL
            AND octet_length(pending_pairing_hash) = 32)
    ),
    CONSTRAINT board_heartbeat_fields CHECK (
        (last_heartbeat_at IS NULL AND heartbeat_lease_until IS NULL)
        OR (last_heartbeat_at IS NOT NULL AND heartbeat_lease_until IS NOT NULL
            AND heartbeat_lease_until >= last_heartbeat_at)
    )
);

CREATE TABLE browser_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    participant_id uuid NOT NULL REFERENCES participants(id) ON DELETE RESTRICT,
    token_hash bytea NOT NULL UNIQUE CHECK (octet_length(token_hash) = 32),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    admission_board_id uuid REFERENCES board_runtime(id) ON DELETE RESTRICT,
    admission_expires_at timestamptz,
    CONSTRAINT browser_session_lifetime CHECK (expires_at > created_at),
    CONSTRAINT browser_session_admission_fields CHECK (
        (admission_board_id IS NULL AND admission_expires_at IS NULL)
        OR (admission_board_id IS NOT NULL AND admission_expires_at IS NOT NULL)
    )
);
CREATE INDEX browser_sessions_participant_idx ON browser_sessions(participant_id);
CREATE INDEX browser_sessions_admission_board_idx ON browser_sessions(admission_board_id);
CREATE INDEX browser_sessions_expiry_idx ON browser_sessions(expires_at);

CREATE TABLE daily_usage (
    participant_id uuid NOT NULL REFERENCES participants(id) ON DELETE RESTRICT,
    business_date date NOT NULL,
    violation_count integer NOT NULL DEFAULT 0 CHECK (violation_count >= 0),
    banned_until timestamptz,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (participant_id, business_date)
);
CREATE INDEX daily_usage_date_idx ON daily_usage(business_date);

CREATE TABLE submissions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    participant_id uuid NOT NULL,
    business_date date NOT NULL,
    board_id uuid NOT NULL REFERENCES board_runtime(id) ON DELETE RESTRICT,
    idempotency_key uuid NOT NULL,
    request_hash bytea NOT NULL CHECK (octet_length(request_hash) = 32),
    content text NOT NULL CHECK (octet_length(content) BETWEEN 1 AND 2048),
    emoji_id text NOT NULL CHECK (char_length(emoji_id) BETWEEN 1 AND 50),
    accepted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    moderation_status text NOT NULL DEFAULT 'PENDING' CHECK (
        moderation_status IN ('PENDING', 'PROCESSING', 'APPROVED', 'REJECTED',
            'NEEDS_REWRITE', 'FAILED', 'EXPIRED', 'CANCELLED')
    ),
    finalized_at timestamptz,
    processing_run_token uuid,
    processing_started_at timestamptz,
    processing_lease_until timestamptz,
    attempt_count smallint NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 2),
    queue_job_id bigint UNIQUE CHECK (queue_job_id > 0),
    safety_verdict text CHECK (safety_verdict IN ('ALLOW', 'BLOCK', 'UNCERTAIN')),
    reason_codes text[] NOT NULL DEFAULT ARRAY[]::text[],
    rule_evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    strike_applied boolean NOT NULL DEFAULT false,
    error_code text CHECK (error_code IS NULL OR char_length(error_code) <= 100),
    emotion text CHECK (emotion IN ('CELEBRATION', 'JOY', 'SUPPORT', 'EXCITEMENT',
        'TIRED', 'SADNESS', 'ANGER', 'NEUTRAL')),
    emotion_source text CHECK (emotion_source IN ('MODEL', 'FALLBACK')),
    model_bundle_version text,
    inference_policy_version text,
    moderation_policy_version text NOT NULL,
    lexicon_version text NOT NULL,
    display_status text NOT NULL DEFAULT 'NONE' CHECK (
        display_status IN ('NONE', 'FLOATING', 'EXPIRED', 'HIDDEN')
    ),
    hidden_at timestamptz,
    CONSTRAINT submissions_daily_usage_fk
        FOREIGN KEY (participant_id, business_date)
        REFERENCES daily_usage(participant_id, business_date) ON DELETE RESTRICT,
    CONSTRAINT submissions_idempotency_unique UNIQUE (participant_id, idempotency_key),
    CONSTRAINT submissions_business_date CHECK (
        business_date = (accepted_at AT TIME ZONE 'Asia/Seoul')::date
    ),
    CONSTRAINT submissions_deadline CHECK (
        expires_at > accepted_at
        AND expires_at <= accepted_at + interval '120 seconds'
        AND expires_at <= ((business_date + 1)::timestamp AT TIME ZONE 'Asia/Seoul')
    ),
    CONSTRAINT submissions_finalized CHECK (
        (moderation_status IN ('PENDING', 'PROCESSING') AND finalized_at IS NULL)
        OR (moderation_status NOT IN ('PENDING', 'PROCESSING')
            AND finalized_at IS NOT NULL AND finalized_at >= accepted_at)
    ),
    CONSTRAINT submissions_processing_fields CHECK (
        (moderation_status = 'PROCESSING' AND processing_run_token IS NOT NULL
            AND processing_started_at IS NOT NULL AND processing_lease_until IS NOT NULL
            AND attempt_count BETWEEN 1 AND 2
            AND processing_started_at >= accepted_at
            AND processing_lease_until > processing_started_at
            AND processing_lease_until <= processing_started_at + interval '60 seconds'
            AND processing_lease_until <= expires_at)
        OR (moderation_status <> 'PROCESSING' AND processing_run_token IS NULL
            AND processing_started_at IS NULL AND processing_lease_until IS NULL)
    ),
    CONSTRAINT submissions_approved_emotion CHECK (
        moderation_status <> 'APPROVED'
        OR (emotion IS NOT NULL AND emotion_source IS NOT NULL
            AND safety_verdict IS NOT DISTINCT FROM 'ALLOW'
            AND attempt_count BETWEEN 1 AND 2 AND finalized_at < expires_at)
    ),
    CONSTRAINT submissions_fallback CHECK (
        emotion_source IS DISTINCT FROM 'FALLBACK' OR emotion IS NOT DISTINCT FROM 'NEUTRAL'
    ),
    CONSTRAINT submissions_strike CHECK (
        strike_applied = (moderation_status = 'REJECTED')
    ),
    CONSTRAINT submissions_display_public CHECK (
        (moderation_status = 'APPROVED' AND display_status <> 'NONE')
        OR (moderation_status <> 'APPROVED' AND display_status = 'NONE')
    ),
    CONSTRAINT submissions_hidden CHECK (
        (display_status = 'HIDDEN' AND hidden_at IS NOT NULL)
        OR (display_status <> 'HIDDEN' AND hidden_at IS NULL)
    ),
    CONSTRAINT submissions_reason_limit CHECK (cardinality(reason_codes) <= 5),
    CONSTRAINT submissions_rule_evidence_shape CHECK (
        moodwall_rule_evidence_valid(rule_evidence)
    ),
    CONSTRAINT submissions_rule_evidence_state CHECK (
        (moderation_status = 'REJECTED' AND rule_evidence <> '[]'::jsonb)
        OR (moderation_status <> 'REJECTED' AND rule_evidence = '[]'::jsonb)
    ),
    CONSTRAINT submissions_rule_rejection_no_model CHECK (
        moderation_status <> 'REJECTED'
        OR (model_bundle_version IS NULL AND inference_policy_version IS NULL
            AND safety_verdict IS NULL AND emotion IS NULL AND emotion_source IS NULL
            AND cardinality(reason_codes) = 0)
    )
);

-- Global one-in-flight rule. Expired rows must be terminalized; a time-dependent
-- WHERE expires_at > now() is invalid for a partial index.
CREATE UNIQUE INDEX submissions_one_inflight_per_participant_idx
    ON submissions(participant_id)
    WHERE moderation_status IN ('PENDING', 'PROCESSING');
CREATE INDEX submissions_usage_fk_idx ON submissions(participant_id, business_date);
-- Quota is rolling 60 minutes, including previous KST date. It is not daily_usage.
-- All changes affecting this query serialize on the owning participant row.
CREATE INDEX submissions_rolling_quota_idx ON submissions(participant_id, accepted_at)
    WHERE moderation_status IN ('PENDING', 'PROCESSING', 'APPROVED');
-- Keyset pages deliver every approved bubble. Page size is not a display limit.
CREATE INDEX submissions_floating_order_idx ON submissions(board_id, business_date, finalized_at, id)
    INCLUDE(accepted_at)
    WHERE moderation_status = 'APPROVED' AND display_status = 'FLOATING';
CREATE INDEX submissions_current_display_idx ON submissions(board_id, business_date, display_status);
CREATE INDEX submissions_deadline_idx ON submissions(expires_at, id)
    WHERE moderation_status IN ('PENDING', 'PROCESSING');
CREATE INDEX submissions_processing_lease_idx ON submissions(processing_lease_until, id)
    WHERE moderation_status = 'PROCESSING';
CREATE INDEX submissions_retention_idx ON submissions(accepted_at, id);

-- One configured inference worker. Supervisors fence updates with generation.
-- This lease is independent of Procrastinate's internal liveness heartbeat.
CREATE TABLE worker_readiness (
    worker_name text PRIMARY KEY CHECK (char_length(worker_name) BETWEEN 1 AND 100),
    generation uuid NOT NULL,
    model_bundle_version text NOT NULL CHECK (char_length(model_bundle_version) BETWEEN 1 AND 100),
    ready_at timestamptz NOT NULL,
    heartbeat_at timestamptz NOT NULL,
    lease_until timestamptz NOT NULL,
    CONSTRAINT worker_readiness_time_order CHECK (
        heartbeat_at >= ready_at AND lease_until > heartbeat_at
    )
);

CREATE TABLE operators (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    login_name text NOT NULL UNIQUE CHECK (char_length(login_name) BETWEEN 3 AND 100),
    password_hash text NOT NULL CHECK (char_length(password_hash) BETWEEN 20 AND 1024),
    role text NOT NULL DEFAULT 'MODERATOR' CHECK (role IN ('ADMIN', 'MODERATOR')),
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    password_changed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE operator_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    operator_id uuid NOT NULL REFERENCES operators(id) ON DELETE RESTRICT,
    token_hash bytea NOT NULL UNIQUE CHECK (octet_length(token_hash) = 32),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    CONSTRAINT operator_session_lifetime CHECK (expires_at > created_at),
    CONSTRAINT operator_session_seen_order CHECK (last_seen_at >= created_at)
);
CREATE INDEX operator_sessions_operator_idx ON operator_sessions(operator_id);
CREATE INDEX operator_sessions_expiry_idx ON operator_sessions(expires_at);

CREATE TABLE operator_audit_logs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    operator_id uuid NOT NULL REFERENCES operators(id) ON DELETE RESTRICT,
    action text NOT NULL CHECK (action IN ('HIDE_SUBMISSION', 'RESET_RESTRICTION',
        'SET_BOARD_MODE', 'PAUSE_SUBMISSIONS', 'PAUSE_DISPLAY',
        'CREATE_PAIRING_CODE', 'REVOKE_DEVICE', 'DISABLE_OPERATOR')),
    board_id uuid REFERENCES board_runtime(id) ON DELETE RESTRICT,
    target_id uuid,
    reason_code text NOT NULL CHECK (char_length(reason_code) BETWEEN 1 AND 100),
    before_violation_count integer CHECK (before_violation_count >= 0),
    after_violation_count integer CHECK (after_violation_count >= 0),
    before_banned_until timestamptz,
    after_banned_until timestamptz,
    command_key uuid,
    request_hash bytea,
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT operator_audit_command_fields CHECK (
        (action = 'RESET_RESTRICTION' AND command_key IS NOT NULL
            AND request_hash IS NOT NULL AND octet_length(request_hash) = 32)
        OR (action <> 'RESET_RESTRICTION' AND command_key IS NULL AND request_hash IS NULL)
    )
    -- target_id deliberately has no FK: the audit event survives content deletion.
    -- No raw content, passwords, bearer tokens or arbitrary request JSON here.
);
CREATE INDEX operator_audit_operator_idx ON operator_audit_logs(operator_id, occurred_at);
CREATE INDEX operator_audit_board_idx ON operator_audit_logs(board_id, occurred_at);
CREATE INDEX operator_audit_hide_target_idx ON operator_audit_logs(board_id, target_id, occurred_at)
    WHERE action = 'HIDE_SUBMISSION' AND target_id IS NOT NULL;
CREATE INDEX operator_audit_retention_idx ON operator_audit_logs(occurred_at);
CREATE UNIQUE INDEX operator_audit_command_unique_idx
    ON operator_audit_logs(operator_id, action, command_key)
    WHERE command_key IS NOT NULL;

-- Optional shared atomic request limiter, replacing per-process in-memory counters.
-- subject_hash = keyed digest(scope || subject); never a raw IP address.
-- Per-participant 10-second business cooldown belongs in participants, not here.
CREATE TABLE rate_limit_buckets (
    scope text NOT NULL CHECK (char_length(scope) BETWEEN 1 AND 60),
    subject_hash bytea NOT NULL CHECK (octet_length(subject_hash) = 32),
    window_start timestamptz NOT NULL,
    hits integer NOT NULL CHECK (hits > 0),
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (scope, subject_hash, window_start),
    CONSTRAINT rate_limit_bucket_expiry CHECK (expires_at > window_start)
);
CREATE INDEX rate_limit_buckets_expiry_idx ON rate_limit_buckets(expires_at);

COMMENT ON COLUMN submissions.content IS
    'Private until APPROVED; 1..30 extended grapheme clusters enforced by API, UTF-8 <=2048 bytes by DB.';
COMMENT ON COLUMN submissions.request_hash IS
    'SHA-256 of canonical validated content+emoji+contract-version; no policy/model versions; not for identity or authentication.';
COMMENT ON COLUMN submissions.queue_job_id IS
    'Unique Procrastinate job handle only; no FK to the library-owned schema, so finished-job cleanup can precede submission retention.';
COMMENT ON COLUMN submissions.processing_lease_until IS
    'Immutable per-run deadline <= processing_started_at+60s and expires_at. Finalizer must check DB clock before this lease; expired results cannot approve or apply a strike.';
COMMENT ON COLUMN submissions.rule_evidence IS
    'At most 5 rule_id/variant_id pairs, REJECTED only; versions resolve through lexicon_version. No matched text, search views, offsets or AI reason codes.';
COMMENT ON COLUMN operator_audit_logs.command_key IS
    'RESET_RESTRICTION idempotency UUID; same operator/action/key replays the audit, never resets later strikes again. Retained 90 days with the audit.';
COMMENT ON COLUMN participants.last_submission_at IS
    'Last accepted new intent; under participant row lock enforces 10-second cooldown across KST midnight.';
COMMENT ON TABLE daily_usage IS
    'KST-day moderation strikes and suspension only. Rolling 60-minute/3-request quota derives from submissions under participant row lock.';
COMMENT ON TABLE worker_readiness IS
    'Supervisor publishes verified model readiness every 5s with 15s lease; no row or expired lease blocks new submissions.';
COMMENT ON COLUMN board_runtime.device_token_hash IS
    'SHA-256 of random 256-bit credential, never a build-time frontend secret.';

COMMIT;
