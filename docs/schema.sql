-- Moodwall current design schema, 2026-11-04. PostgreSQL 16+; head 0006_playback_receipts.
-- Empty database reference only; application upgrades use the versioned migrations.
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


-- V2 additive playback schema (migration 0006).
-- Additive migration: existing floating rows keep their old accepted-at TTL.
ALTER TABLE submissions DROP CONSTRAINT submissions_display_status_check;
ALTER TABLE submissions ADD CONSTRAINT submissions_display_status_check
    CHECK (display_status IN ('NONE','QUEUED','FLOATING','EXPIRED','HIDDEN'));
ALTER TABLE submissions ADD COLUMN first_start_authorized_at timestamptz,
    ADD COLUMN content_delete_at timestamptz,
    ADD COLUMN pre_display_deadline_at timestamptz;
ALTER TABLE submissions ADD CONSTRAINT submissions_playback_retention CHECK (
    (first_start_authorized_at IS NULL AND content_delete_at IS NULL)
    OR (first_start_authorized_at IS NOT NULL AND content_delete_at IS NOT NULL AND first_start_authorized_at >= accepted_at
        AND content_delete_at = first_start_authorized_at + interval '1 hour'));
ALTER TABLE submissions ADD CONSTRAINT submissions_pre_display_deadline CHECK (
    pre_display_deadline_at IS NULL OR
    (pre_display_deadline_at > accepted_at AND pre_display_deadline_at <= accepted_at + interval '10 minutes'
     AND pre_display_deadline_at <= ((business_date+1)::timestamp AT TIME ZONE 'Asia/Seoul')));

CREATE TABLE submission_receipts (
    id uuid PRIMARY KEY,
    participant_id uuid NOT NULL REFERENCES participants(id) ON DELETE RESTRICT,
    board_id uuid NOT NULL REFERENCES board_runtime(id) ON DELETE RESTRICT,
    business_date date NOT NULL,
    idempotency_key uuid NOT NULL,
    request_hash bytea NOT NULL CHECK (octet_length(request_hash)=32),
    request_version text NOT NULL DEFAULT '2' CHECK (request_version='2'),
    accepted_at timestamptz NOT NULL,
    receipt_expires_at timestamptz NOT NULL,
    processing_status text NOT NULL DEFAULT 'PENDING' CHECK (processing_status IN
        ('PENDING','PROCESSING','APPROVED','REJECTED','NEEDS_REWRITE','FAILED','EXPIRED','CANCELLED')),
    presentation_status text CHECK (presentation_status IN
        ('READY','CLAIMED','START_AUTHORIZED','PLAYING','INTERRUPTED','DONE','FAILED','EXPIRED','CANCELLED','HIDDEN')),
    content_status text NOT NULL DEFAULT 'AVAILABLE' CHECK (content_status IN ('AVAILABLE','PURGED')),
    quota_status text NOT NULL DEFAULT 'RESERVED' CHECK (quota_status IN ('RESERVED','CONSUMED','RELEASED')),
    reason_code text CHECK (reason_code IS NULL OR char_length(reason_code) BETWEEN 1 AND 100),
    pre_display_deadline_at timestamptz NOT NULL,
    first_start_authorized_at timestamptz,
    content_delete_at timestamptz,
    completed_at timestamptz,
    purged_at timestamptz,
    revision bigint NOT NULL DEFAULT 1 CHECK (revision>0),
    CONSTRAINT receipt_owner_key_unique UNIQUE (participant_id,idempotency_key),
    CONSTRAINT receipt_day CHECK (business_date=(accepted_at AT TIME ZONE 'Asia/Seoul')::date),
    CONSTRAINT receipt_lifetime CHECK (receipt_expires_at=accepted_at+interval '24 hours'),
    CONSTRAINT receipt_pre_display CHECK (pre_display_deadline_at>accepted_at
        AND pre_display_deadline_at<=accepted_at+interval '10 minutes'
        AND pre_display_deadline_at<=((business_date+1)::timestamp AT TIME ZONE 'Asia/Seoul')),
    CONSTRAINT receipt_source_lifetime CHECK (
        (first_start_authorized_at IS NULL AND content_delete_at IS NULL AND quota_status<>'CONSUMED')
        OR (first_start_authorized_at IS NOT NULL AND content_delete_at IS NOT NULL AND first_start_authorized_at>=accepted_at
            AND content_delete_at=first_start_authorized_at+interval '1 hour' AND quota_status='CONSUMED')),
    CONSTRAINT receipt_purged CHECK ((content_status='AVAILABLE' AND purged_at IS NULL)
        OR (content_status='PURGED' AND purged_at IS NOT NULL)),
    CONSTRAINT receipt_completion CHECK (completed_at IS NULL OR
        (first_start_authorized_at IS NOT NULL AND completed_at>=first_start_authorized_at+interval '15 seconds'))
);
CREATE INDEX receipt_quota_idx ON submission_receipts(participant_id,accepted_at)
    WHERE quota_status IN ('RESERVED','CONSUMED');
CREATE INDEX receipt_owner_idx ON submission_receipts(participant_id,id);
CREATE INDEX receipt_expiry_idx ON submission_receipts(receipt_expires_at,id);
CREATE INDEX receipt_unstarted_due_idx ON submission_receipts(pre_display_deadline_at,id)
    WHERE first_start_authorized_at IS NULL AND quota_status='RESERVED';
CREATE INDEX receipt_source_due_idx ON submission_receipts(content_delete_at,id)
    WHERE content_status='AVAILABLE';

CREATE TABLE presentation_items (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    submission_id uuid NOT NULL UNIQUE REFERENCES submission_receipts(id) ON DELETE CASCADE,
    board_id uuid NOT NULL REFERENCES board_runtime(id) ON DELETE RESTRICT,
    ready_sequence bigint NOT NULL CHECK (ready_sequence>0),
    ready_at timestamptz NOT NULL,
    status text NOT NULL DEFAULT 'READY' CHECK (status IN
        ('READY','CLAIMED','START_AUTHORIZED','PLAYING','INTERRUPTED','DONE','FAILED','EXPIRED','CANCELLED','HIDDEN')),
    scene_id text NOT NULL CHECK (scene_id IN
        ('celebration','joy','support','excitement','tired','sadness','anger','neutral')),
    scene_version text NOT NULL DEFAULT '1' CHECK (scene_version='1'),
    scene_seed text NOT NULL CHECK (scene_seed ~ '^[0-9a-f]{16}$'),
    attempt_count smallint NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 2),
    CONSTRAINT presentation_ready_unique UNIQUE(board_id,ready_sequence)
);
CREATE UNIQUE INDEX presentation_one_active_idx ON presentation_items(board_id)
    WHERE status IN ('CLAIMED','START_AUTHORIZED','PLAYING');
CREATE INDEX presentation_ready_order_idx ON presentation_items(board_id,ready_sequence)
    WHERE status='READY';
CREATE INDEX presentation_status_idx ON presentation_items(board_id,status);

CREATE TABLE playback_attempts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id uuid NOT NULL REFERENCES presentation_items(id) ON DELETE CASCADE,
    board_id uuid NOT NULL REFERENCES board_runtime(id) ON DELETE RESTRICT,
    client_instance_id uuid NOT NULL,
    device_generation bigint NOT NULL CHECK (device_generation>=0),
    playback_epoch bigint NOT NULL CHECK (playback_epoch>0),
    attempt_number smallint NOT NULL CHECK (attempt_number BETWEEN 1 AND 2),
    status text NOT NULL DEFAULT 'CLAIMED' CHECK (status IN
        ('CLAIMED','START_AUTHORIZED','PLAYING','DONE','INTERRUPTED')),
    claimed_at timestamptz NOT NULL,
    lease_until timestamptz NOT NULL,
    start_authorized_at timestamptz,
    start_not_after timestamptz,
    first_frame_at timestamptz,
    completed_at timestamptz,
    interrupted_at timestamptz,
    interrupt_reason text CHECK (interrupt_reason IS NULL OR interrupt_reason IN
        ('PAGE_HIDDEN','HIDDEN','RELOAD','FRAME_GAP','ASSET_FAILURE','NETWORK','CLIENT_ERROR',
         'LEASE_EXPIRED','MODE_CHANGED','DAY_ENDED','DEVICE_CHANGED','CONTENT_EXPIRED')),
    CONSTRAINT playback_item_number UNIQUE(item_id,attempt_number),
    CONSTRAINT playback_epoch_unique UNIQUE(board_id,playback_epoch),
    CONSTRAINT playback_start_fields CHECK (
        (start_authorized_at IS NULL AND start_not_after IS NULL AND first_frame_at IS NULL AND completed_at IS NULL)
        OR (start_authorized_at IS NOT NULL AND start_not_after IS NOT NULL
            AND start_authorized_at>=claimed_at AND start_not_after=start_authorized_at+interval '5 seconds')),
    CONSTRAINT playback_frame_order CHECK (first_frame_at IS NULL OR
        (start_authorized_at IS NOT NULL AND start_not_after IS NOT NULL
            AND first_frame_at>=start_authorized_at AND first_frame_at<start_not_after)),
    CONSTRAINT playback_completion CHECK (completed_at IS NULL OR
        (first_frame_at IS NOT NULL AND completed_at>=first_frame_at+interval '15 seconds')),
    CONSTRAINT playback_done CHECK ((status='DONE')=(completed_at IS NOT NULL)),
    CONSTRAINT playback_interrupt CHECK ((status='INTERRUPTED')=(interrupted_at IS NOT NULL)),
    CONSTRAINT playback_status_fields CHECK (
        (status='CLAIMED' AND start_authorized_at IS NULL AND first_frame_at IS NULL)
        OR (status='START_AUTHORIZED' AND start_authorized_at IS NOT NULL AND first_frame_at IS NULL)
        OR (status IN ('PLAYING','DONE') AND first_frame_at IS NOT NULL)
        OR (status='INTERRUPTED' AND interrupt_reason IS NOT NULL)),
    CONSTRAINT playback_lease CHECK (lease_until>claimed_at)
);
CREATE INDEX playback_lease_idx ON playback_attempts(board_id,lease_until)
    WHERE status IN ('CLAIMED','START_AUTHORIZED','PLAYING');
CREATE INDEX playback_item_idx ON playback_attempts(item_id);

ALTER TABLE board_runtime ADD COLUMN next_ready_sequence bigint NOT NULL DEFAULT 0 CHECK(next_ready_sequence>=0),
    ADD COLUMN playback_epoch bigint NOT NULL DEFAULT 0 CHECK(playback_epoch>=0),
    ADD COLUMN playback_owner_instance_id uuid,
    ADD COLUMN playback_owner_generation bigint,
    ADD COLUMN playback_owner_lease_until timestamptz,
    ADD COLUMN playback_current_item_id uuid REFERENCES presentation_items(id) ON DELETE SET NULL;
ALTER TABLE board_runtime ADD CONSTRAINT board_playback_owner_fields CHECK (
    (playback_owner_instance_id IS NULL AND playback_owner_generation IS NULL AND playback_owner_lease_until IS NULL)
    OR (playback_owner_instance_id IS NOT NULL AND playback_owner_generation IS NOT NULL
        AND playback_owner_lease_until IS NOT NULL));

CREATE TABLE playback_commands (
    board_id uuid NOT NULL REFERENCES board_runtime(id) ON DELETE RESTRICT,
    command_key uuid NOT NULL,
    request_hash bytea NOT NULL CHECK(octet_length(request_hash)=32),
    action text NOT NULL CHECK(action IN ('claim','start','frame','complete','interrupt')),
    attempt_id uuid NOT NULL REFERENCES playback_attempts(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL,
    PRIMARY KEY(board_id,command_key)
);
CREATE INDEX playback_commands_attempt_idx ON playback_commands(attempt_id);

CREATE TABLE board_admission_counters (
    board_id uuid PRIMARY KEY REFERENCES board_runtime(id) ON DELETE RESTRICT,
    reserved_count integer NOT NULL DEFAULT 0 CHECK(reserved_count BETWEEN 0 AND 32)
);
INSERT INTO board_admission_counters(board_id) SELECT id FROM board_runtime;
CREATE TABLE admission_reservations (
    submission_id uuid PRIMARY KEY REFERENCES submission_receipts(id) ON DELETE CASCADE,
    board_id uuid NOT NULL REFERENCES board_runtime(id) ON DELETE RESTRICT,
    reserved_at timestamptz NOT NULL,
    released_at timestamptz,
    CHECK(released_at IS NULL OR released_at>=reserved_at)
);
CREATE INDEX admission_unreleased_idx ON admission_reservations(board_id,submission_id)
    WHERE released_at IS NULL;
CREATE INDEX submissions_content_delete_due_idx ON submissions(content_delete_at,id)
    WHERE content_delete_at IS NOT NULL;
CREATE INDEX submissions_pre_display_due_idx ON submissions(pre_display_deadline_at,id)
    WHERE pre_display_deadline_at IS NOT NULL AND first_start_authorized_at IS NULL;

COMMENT ON TABLE submission_receipts IS '24h owner-scoped v2 outcomes and HMAC idempotency evidence; never raw text or emoji.';
COMMENT ON TABLE playback_commands IS 'Successful command idempotency, no raw text or response body; transient rejection is never cached.';
COMMENT ON COLUMN submissions.content_delete_at IS 'V2 first start authorization +1h, immutable across retries. NULL on legacy rows.';
COMMENT ON COLUMN submissions.request_hash IS 'Legacy v1 SHA-256 of content+emoji+version; v2 HMAC-SHA256 of canonical content+version. Never authentication or raw content.';
UPDATE board_runtime SET state_version=state_version+1;

COMMIT;

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
