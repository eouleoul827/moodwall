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
