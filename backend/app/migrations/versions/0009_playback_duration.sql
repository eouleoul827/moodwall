-- The server chooses each attempt's full-screen time from the backlog at claim time
-- and enforces the same value on complete. Existing attempts keep the former 15 s.
ALTER TABLE playback_attempts
    ADD COLUMN duration_ms integer NOT NULL DEFAULT 15000
    CONSTRAINT playback_duration_range CHECK (duration_ms BETWEEN 5000 AND 15000);

-- 0006 pinned completion to +15 s in the database as well. The attempt now checks its
-- own chosen duration; a receipt (which has no duration) keeps the contract floor.
ALTER TABLE playback_attempts DROP CONSTRAINT playback_completion;
ALTER TABLE playback_attempts ADD CONSTRAINT playback_completion CHECK (completed_at IS NULL OR
    (first_frame_at IS NOT NULL AND completed_at>=first_frame_at+duration_ms*interval '1 millisecond'));
ALTER TABLE submission_receipts DROP CONSTRAINT receipt_completion;
ALTER TABLE submission_receipts ADD CONSTRAINT receipt_completion CHECK (completed_at IS NULL OR
    (first_start_authorized_at IS NOT NULL AND completed_at>=first_start_authorized_at+interval '5 seconds'));
