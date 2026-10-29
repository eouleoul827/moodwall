-- Design regression checks for an isolated empty database AFTER schema.sql.
-- psql -v ON_ERROR_STOP=1 -f docs/checks/schema-invariants.sql
-- No application, worker, HTTP concurrency or moderation-quality claim.
-- Every fixture and temporary helper is rolled back.
\set ON_ERROR_STOP on
BEGIN;

CREATE FUNCTION pg_temp.expect_constraint(command text, expected_name text)
RETURNS void LANGUAGE plpgsql AS $function$
DECLARE actual_name text;
BEGIN
    BEGIN
        EXECUTE command;
    EXCEPTION WHEN integrity_constraint_violation THEN
        GET STACKED DIAGNOSTICS actual_name = CONSTRAINT_NAME;
        IF actual_name <> expected_name THEN
            RAISE EXCEPTION 'expected constraint %, received %', expected_name, actual_name;
        END IF;
        RETURN;
    END;
    RAISE EXCEPTION 'constraint % did not reject the invalid fixture', expected_name;
END;
$function$;

DO $test$
DECLARE
    participant uuid;
    board uuid;
    first_id uuid;
    t timestamptz := '2026-10-29 01:00:00+00';
    n integer;
    copy_insert text;
    valid_approval text;
BEGIN
    INSERT INTO participants DEFAULT VALUES RETURNING id INTO participant;
    INSERT INTO board_runtime(name) VALUES ('isolated standards check') RETURNING id INTO board;
    INSERT INTO daily_usage(participant_id, business_date) VALUES (participant, '2026-10-29');
    INSERT INTO submissions(participant_id, business_date, board_id, idempotency_key,
        request_hash, content, emoji_id, accepted_at, expires_at, moderation_policy_version, lexicon_version)
    VALUES(participant, '2026-10-29', board, gen_random_uuid(), decode(repeat('00', 32), 'hex'),
        'synthetic fixture', 'party', t, t + interval '120 seconds', 'policy1', 'lexicon1')
    RETURNING id INTO first_id;

    copy_insert := format($sql$
        INSERT INTO submissions(participant_id, business_date, board_id, idempotency_key,
            request_hash, content, emoji_id, accepted_at, expires_at, moderation_policy_version, lexicon_version)
        SELECT participant_id, business_date, board_id, gen_random_uuid(), request_hash,
            content, emoji_id, accepted_at, expires_at, moderation_policy_version, lexicon_version
        FROM submissions WHERE id = %L
    $sql$, first_id);
    PERFORM pg_temp.expect_constraint(copy_insert, 'submissions_one_inflight_per_participant_idx');

    PERFORM pg_temp.expect_constraint(format(
        'UPDATE submissions SET moderation_status=''APPROVED'', attempt_count=1, '
        'finalized_at=accepted_at+interval ''1 second'', display_status=''FLOATING'' WHERE id=%L', first_id),
        'submissions_approved_emotion');
    PERFORM pg_temp.expect_constraint(format(
        'UPDATE submissions SET moderation_status=''REJECTED'', '
        'finalized_at=accepted_at+interval ''1 second'', '
        'rule_evidence=''[{"rule_id":"ko-explicit-001","variant_id":"exact"}]''::jsonb '
        'WHERE id=%L', first_id), 'submissions_strike');
    PERFORM pg_temp.expect_constraint(format(
        'UPDATE submissions SET expires_at=accepted_at+interval ''121 seconds'' WHERE id=%L', first_id),
        'submissions_deadline');
    PERFORM pg_temp.expect_constraint(format(
        'UPDATE submissions SET content=repeat(''x'',2049) WHERE id=%L', first_id), 'submissions_content_check');

    PERFORM pg_temp.expect_constraint(format(
        'UPDATE submissions SET attempt_count=3 WHERE id=%L', first_id), 'submissions_attempt_count_check');
    PERFORM pg_temp.expect_constraint(format(
        'UPDATE submissions SET moderation_status=''PROCESSING'', attempt_count=0, '
        'processing_run_token=gen_random_uuid(), processing_started_at=accepted_at+interval ''1 second'', '
        'processing_lease_until=accepted_at+interval ''31 seconds'' WHERE id=%L', first_id),
        'submissions_processing_fields');
    PERFORM pg_temp.expect_constraint(format(
        'UPDATE submissions SET moderation_status=''PROCESSING'', attempt_count=1, '
        'processing_run_token=gen_random_uuid(), processing_started_at=accepted_at-interval ''1 second'', '
        'processing_lease_until=accepted_at+interval ''30 seconds'' WHERE id=%L', first_id),
        'submissions_processing_fields');
    PERFORM pg_temp.expect_constraint(format(
        'UPDATE submissions SET moderation_status=''PROCESSING'', attempt_count=1, '
        'processing_run_token=gen_random_uuid(), processing_started_at=accepted_at+interval ''1 second'', '
        'processing_lease_until=accepted_at+interval ''62 seconds'' WHERE id=%L', first_id),
        'submissions_processing_fields');
    PERFORM pg_temp.expect_constraint(format(
        'UPDATE submissions SET moderation_status=''APPROVED'', attempt_count=1, '
        'finalized_at=expires_at, safety_verdict=''ALLOW'', emotion=''JOY'', '
        'emotion_source=''MODEL'', display_status=''FLOATING'' WHERE id=%L', first_id),
        'submissions_approved_emotion');
    PERFORM pg_temp.expect_constraint(format(
        'UPDATE submissions SET moderation_status=''FAILED'', '
        'finalized_at=accepted_at-interval ''1 second'' WHERE id=%L', first_id), 'submissions_finalized');
    PERFORM pg_temp.expect_constraint(format(
        'UPDATE submissions SET queue_job_id=0 WHERE id=%L', first_id), 'submissions_queue_job_id_check');

    valid_approval := format(
        'UPDATE submissions SET moderation_status=''APPROVED'', attempt_count=1, '
        'finalized_at=accepted_at+interval ''1 second'', safety_verdict=''ALLOW'', '
        'emotion=''JOY'', emotion_source=''MODEL'', display_status=''FLOATING'', queue_job_id=42 WHERE id=%L', first_id);
    EXECUTE valid_approval;

    PERFORM pg_temp.expect_constraint(replace(copy_insert, 'gen_random_uuid(), request_hash', 'idempotency_key, request_hash'),
        'submissions_idempotency_unique');
    PERFORM pg_temp.expect_constraint(format($sql$
        INSERT INTO submissions(participant_id, business_date, board_id, idempotency_key,
            request_hash, content, emoji_id, accepted_at, expires_at, moderation_policy_version, lexicon_version, queue_job_id)
        SELECT participant_id, business_date, board_id, gen_random_uuid(), request_hash,
            content, emoji_id, accepted_at, expires_at, moderation_policy_version, lexicon_version, queue_job_id
        FROM submissions WHERE id=%L
    $sql$, first_id), 'submissions_queue_job_id_key');

    INSERT INTO submissions(participant_id, business_date, board_id, idempotency_key,
        request_hash, content, emoji_id, accepted_at, expires_at, moderation_status, finalized_at,
        attempt_count, safety_verdict, emotion, emotion_source, display_status, moderation_policy_version, lexicon_version)
    SELECT participant, '2026-10-29', board, gen_random_uuid(), decode(repeat('00', 32), 'hex'),
        'synthetic approved', 'party', t+make_interval(mins=>i), t+make_interval(mins=>i)+interval '120 seconds',
        'APPROVED', t+make_interval(mins=>i)+interval '1 second', 1,
        'ALLOW', 'JOY', 'MODEL', 'FLOATING', 'policy1', 'lexicon1' FROM (VALUES(10),(20)) AS x(i);

    SELECT count(*) INTO n FROM submissions
    WHERE participant_id=participant AND accepted_at>t+interval '59 minutes'-interval '60 minutes'
      AND accepted_at<=t+interval '59 minutes' AND moderation_status IN ('PENDING','PROCESSING','APPROVED');
    IF n <> 3 THEN RAISE EXCEPTION 'rolling count expected 3, received %', n; END IF;
    SELECT count(*) INTO n FROM submissions
    WHERE participant_id=participant AND accepted_at>t+interval '60 minutes'-interval '60 minutes'
      AND accepted_at<=t+interval '60 minutes' AND moderation_status IN ('PENDING','PROCESSING','APPROVED');
    IF n <> 2 THEN RAISE EXCEPTION 'rolling boundary expected 2, received %', n; END IF;
    UPDATE submissions SET display_status='HIDDEN', hidden_at=t+interval '30 minutes' WHERE id=first_id;
    SELECT count(*) INTO n FROM submissions WHERE participant_id=participant AND moderation_status='APPROVED';
    IF n <> 3 THEN RAISE EXCEPTION 'hiding changed approved consumption'; END IF;

    RAISE NOTICE 'PASS: 14 named constraint rejections and 3 rolling/hiding assertions';
END;
$test$;

-- Metadata-shape and audit-key checks only. These do not execute a filter,
-- increment daily_usage, or prove HTTP replay / cross-transaction rollback.
DO $metadata_test$
DECLARE
    participant uuid;
    another_participant uuid;
    board uuid;
    rejected_id uuid;
    operator_one uuid;
    operator_two uuid;
    restore_command_key uuid := gen_random_uuid();
    command_hash bytea := decode(repeat('11', 32), 'hex');
    audit_one bigint;
    audit_two bigint;
    n integer;
    t timestamptz := '2026-10-29 02:00:00+00';
    evidence jsonb := '[{"rule_id":"ko-explicit-001","variant_id":"exact"}]'::jsonb;
    bad_evidence jsonb;
    field_sql text;
BEGIN
    INSERT INTO participants DEFAULT VALUES RETURNING id INTO participant;
    INSERT INTO participants DEFAULT VALUES RETURNING id INTO another_participant;
    INSERT INTO board_runtime(name) VALUES ('isolated metadata check') RETURNING id INTO board;
    INSERT INTO daily_usage(participant_id, business_date) VALUES (participant, '2026-10-29');

    -- One valid rule rejection: its strike flag is evidence of this row's
    -- terminal classification, not a test that the application increments once.
    INSERT INTO submissions(participant_id, business_date, board_id, idempotency_key,
        request_hash, content, emoji_id, accepted_at, expires_at, moderation_status,
        finalized_at, attempt_count, strike_applied, rule_evidence,
        moderation_policy_version, lexicon_version)
    VALUES(participant, '2026-10-29', board, gen_random_uuid(), decode(repeat('00', 32), 'hex'),
        'synthetic rule fixture', 'party', t, t + interval '120 seconds', 'REJECTED',
        t + interval '1 second', 1, true, evidence, 'policy1', 'lexicon1')
    RETURNING id INTO rejected_id;

    SELECT count(*) INTO n FROM submissions WHERE id=rejected_id
        AND moderation_status='REJECTED' AND strike_applied AND rule_evidence=evidence
        AND model_bundle_version IS NULL AND inference_policy_version IS NULL
        AND safety_verdict IS NULL AND emotion IS NULL AND emotion_source IS NULL
        AND cardinality(reason_codes)=0;
    IF n <> 1 THEN RAISE EXCEPTION 'valid rule rejection metadata did not persist'; END IF;

    -- Every bad value is nonempty, so only the shape check is violated on
    -- an otherwise valid REJECTED row (not the state/strike checks).
    FOR bad_evidence IN SELECT item FROM (VALUES
        ('{}'::jsonb),
        ('[{"rule_id":"r"}]'::jsonb),
        ('[{"rule_id":"r","variant_id":"v","text":"synthetic"}]'::jsonb),
        ('[{"rule_id":"bad id","variant_id":"v"}]'::jsonb),
        (jsonb_build_array(jsonb_build_object('rule_id', repeat('r',101), 'variant_id','v'))),
        ('[{"rule_id":"r1","variant_id":"v"},{"rule_id":"r2","variant_id":"v"},
           {"rule_id":"r3","variant_id":"v"},{"rule_id":"r4","variant_id":"v"},
           {"rule_id":"r5","variant_id":"v"},{"rule_id":"r6","variant_id":"v"}]'::jsonb)
    ) AS fixtures(item)
    LOOP
        PERFORM pg_temp.expect_constraint(format(
            'UPDATE submissions SET rule_evidence=%L::jsonb WHERE id=%L', bad_evidence, rejected_id),
            'submissions_rule_evidence_shape');
    END LOOP;

    PERFORM pg_temp.expect_constraint(format(
        'UPDATE submissions SET rule_evidence=''[]''::jsonb WHERE id=%L', rejected_id),
        'submissions_rule_evidence_state');
    PERFORM pg_temp.expect_constraint(format(
        'UPDATE submissions SET moderation_status=''FAILED'', strike_applied=false WHERE id=%L', rejected_id),
        'submissions_rule_evidence_state');

    -- Protect each forbidden fabricated-model field independently.
    FOREACH field_sql IN ARRAY ARRAY[
        'model_bundle_version=''fabricated-model''',
        'inference_policy_version=''fabricated-inference''',
        'safety_verdict=''BLOCK''',
        'emotion=''JOY''',
        'emotion_source=''MODEL''',
        'reason_codes=ARRAY[''HARASSMENT'']::text[]'
    ]
    LOOP
        PERFORM pg_temp.expect_constraint(format(
            'UPDATE submissions SET %s WHERE id=%L', field_sql, rejected_id),
            'submissions_rule_rejection_no_model');
    END LOOP;

    INSERT INTO operators(login_name, password_hash, role)
    VALUES ('isolated-admin-one', 'synthetic-password-hash-not-a-credential', 'ADMIN')
    RETURNING id INTO operator_one;
    INSERT INTO operators(login_name, password_hash, role)
    VALUES ('isolated-admin-two', 'synthetic-password-hash-not-a-credential', 'ADMIN')
    RETURNING id INTO operator_two;

    PERFORM pg_temp.expect_constraint(format(
        'INSERT INTO operator_audit_logs(operator_id, action, target_id, reason_code) '
        'VALUES(%L,''RESET_RESTRICTION'',%L,''FALSE_POSITIVE'')', operator_one, participant),
        'operator_audit_command_fields');
    PERFORM pg_temp.expect_constraint(format(
        'INSERT INTO operator_audit_logs(operator_id, action, target_id, reason_code, command_key) '
        'VALUES(%L,''RESET_RESTRICTION'',%L,''FALSE_POSITIVE'',%L)', operator_one, participant, restore_command_key),
        'operator_audit_command_fields');
    PERFORM pg_temp.expect_constraint(format(
        'INSERT INTO operator_audit_logs(operator_id, action, target_id, reason_code, request_hash) '
        'VALUES(%L,''RESET_RESTRICTION'',%L,''FALSE_POSITIVE'',%L::bytea)', operator_one, participant, command_hash),
        'operator_audit_command_fields');
    PERFORM pg_temp.expect_constraint(format(
        'INSERT INTO operator_audit_logs(operator_id, action, target_id, reason_code, command_key, request_hash) '
        'VALUES(%L,''RESET_RESTRICTION'',%L,''FALSE_POSITIVE'',%L,decode(repeat(''11'',31),''hex''))',
        operator_one, participant, restore_command_key), 'operator_audit_command_fields');
    PERFORM pg_temp.expect_constraint(format(
        'INSERT INTO operator_audit_logs(operator_id, action, target_id, reason_code, command_key, request_hash) '
        'VALUES(%L,''HIDE_SUBMISSION'',%L,''OTHER'',%L,%L::bytea)',
        operator_one, rejected_id, restore_command_key, command_hash), 'operator_audit_command_fields');

    INSERT INTO operator_audit_logs(operator_id, action, target_id, reason_code,
        command_key, request_hash, before_violation_count, after_violation_count)
    VALUES(operator_one, 'RESET_RESTRICTION', participant, 'FALSE_POSITIVE',
        restore_command_key, command_hash, 5, 0) RETURNING id INTO audit_one;
    SELECT count(*) INTO n FROM operator_audit_logs WHERE id=audit_one
        AND operator_id=operator_one AND action='RESET_RESTRICTION'
        AND command_key=restore_command_key AND request_hash=command_hash
        AND target_id=participant AND before_violation_count=5 AND after_violation_count=0;
    IF n <> 1 THEN RAISE EXCEPTION 'valid reset audit metadata did not persist'; END IF;

    -- Target is deliberately NOT part of the unique command identity.
    PERFORM pg_temp.expect_constraint(format(
        'INSERT INTO operator_audit_logs(operator_id, action, target_id, reason_code, command_key, request_hash) '
        'VALUES(%L,''RESET_RESTRICTION'',%L,''FALSE_POSITIVE'',%L,%L::bytea)',
        operator_one, another_participant, restore_command_key, command_hash),
        'operator_audit_command_unique_idx');

    INSERT INTO operator_audit_logs(operator_id, action, target_id, reason_code, command_key, request_hash)
    VALUES(operator_two, 'RESET_RESTRICTION', another_participant, 'FALSE_POSITIVE', restore_command_key, command_hash)
    RETURNING id INTO audit_two;
    IF audit_one=audit_two THEN RAISE EXCEPTION 'different operators reused the same audit row'; END IF;
    SELECT count(*) INTO n FROM operator_audit_logs WHERE action='RESET_RESTRICTION'
        AND operator_id IN (operator_one,operator_two) AND command_key=restore_command_key;
    IF n <> 2 THEN RAISE EXCEPTION 'operator-scoped command key should allow two audit rows'; END IF;

    INSERT INTO operator_audit_logs(operator_id, action, target_id, reason_code)
    VALUES(operator_one, 'HIDE_SUBMISSION', rejected_id, 'OTHER');
    SELECT count(*) INTO n FROM operator_audit_logs WHERE operator_id=operator_one
        AND action='HIDE_SUBMISSION' AND target_id=rejected_id
        AND command_key IS NULL AND request_hash IS NULL;
    IF n <> 1 THEN RAISE EXCEPTION 'normal audit should permit absent command fields'; END IF;

    RAISE NOTICE 'PASS: 20 additional named constraint rejections and 5 rule/audit assertions';
END;
$metadata_test$;
ROLLBACK;
