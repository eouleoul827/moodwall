-- Completion order is separate from READY order: an interrupted item may finish later.
ALTER TABLE presentation_items ADD COLUMN completed_sequence bigint
    CONSTRAINT presentation_completed_positive CHECK (completed_sequence > 0);
WITH numbered AS (
    SELECT p.id,row_number() OVER (PARTITION BY p.board_id ORDER BY r.completed_at,r.id) AS sequence
    FROM presentation_items p JOIN submission_receipts r ON r.id=p.submission_id
    WHERE r.completed_at IS NOT NULL
)
UPDATE presentation_items p SET completed_sequence=n.sequence FROM numbered n WHERE p.id=n.id;
CREATE UNIQUE INDEX presentation_completed_order_idx
    ON presentation_items(board_id,completed_sequence) WHERE completed_sequence IS NOT NULL;

ALTER TABLE board_runtime
    ADD COLUMN next_completed_sequence bigint NOT NULL DEFAULT 0,
    ADD COLUMN retired_completed_sequence bigint NOT NULL DEFAULT 0,
    ADD CONSTRAINT board_completed_sequence_bounds CHECK (
        retired_completed_sequence>=0 AND next_completed_sequence>=retired_completed_sequence);
UPDATE board_runtime b SET next_completed_sequence=COALESCE((
    SELECT max(p.completed_sequence) FROM presentation_items p WHERE p.board_id=b.id),0);
-- Apply the approved cap to already completed text during upgrade. Waiting and active
-- presentations are excluded. Receipts retain SHOWN and quota evidence after body purge.
UPDATE board_runtime b SET retired_completed_sequence=COALESCE((
    SELECT p.completed_sequence FROM presentation_items p
    JOIN submission_receipts r ON r.id=p.submission_id
    WHERE p.board_id=b.id AND p.status='DONE' AND r.content_status='AVAILABLE'
      AND r.content_delete_at>clock_timestamp()
    ORDER BY p.completed_sequence DESC OFFSET 30 LIMIT 1),0);
UPDATE submission_receipts r SET content_status='PURGED',purged_at=clock_timestamp(),revision=revision+1
FROM presentation_items p,board_runtime b
WHERE p.submission_id=r.id AND b.id=p.board_id AND p.completed_sequence<=b.retired_completed_sequence
  AND r.content_status='AVAILABLE';
DELETE FROM submissions s USING submission_receipts r,presentation_items p,board_runtime b
WHERE s.id=r.id AND p.submission_id=r.id AND b.id=p.board_id
  AND p.completed_sequence<=b.retired_completed_sequence;
COMMENT ON COLUMN presentation_items.completed_sequence IS 'Board-local immutable completion order, assigned only on successful complete; never READY order.';
COMMENT ON COLUMN board_runtime.retired_completed_sequence IS 'Persistent no-resurrection boundary for completed cloud bodies evicted by the latest-30 limit.';
UPDATE board_runtime SET state_version=state_version+1;
