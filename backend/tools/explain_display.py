"""Measure display plans in the explicitly isolated review database only.

Run with the backend's pinned dependencies and MOODWALL_TEST_DB_URL pointing at
the dedicated moodwall_review_test database. This is an opt-in diagnostic, not
a timing assertion or a migration. It prints summaries, never submission text.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, time, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

DATABASE = "moodwall_review_test"
ROWS = 10_000
INDEX = "submissions_floating_order_idx"
KST = ZoneInfo("Asia/Seoul")

COUNT_QUERY = """SELECT count(*) AS n FROM submissions
    WHERE board_id=%s AND business_date=%s AND accepted_at>%s AND finalized_at<=%s
    AND moderation_status='APPROVED' AND display_status='FLOATING'"""
PAGE_QUERY = """SELECT id,content,emoji_id,emotion,accepted_at,finalized_at FROM submissions
    WHERE board_id=%s AND business_date=%s AND accepted_at>%s AND finalized_at<=%s
    AND moderation_status='APPROVED' AND display_status='FLOATING'
    AND (finalized_at,id)>(%s,%s) ORDER BY finalized_at,id LIMIT 101"""
FULL_QUERY = """SELECT * FROM submissions WHERE board_id=%s AND business_date=%s
    AND accepted_at>%s AND finalized_at<=%s
    AND moderation_status='APPROVED' AND display_status='FLOATING'
    ORDER BY finalized_at,id"""


def _index_identity(conn):
    return conn.execute(
        """SELECT c.oid,pg_get_indexdef(c.oid) AS definition,i.indisvalid,i.indisready
           FROM pg_class c JOIN pg_index i ON i.indexrelid=c.oid
           WHERE c.oid=to_regclass('public.submissions_floating_order_idx')"""
    ).fetchone()


def _nodes(node):
    yield node
    for child in node.get("Plans", []):
        yield from _nodes(child)


def _summary(record):
    root = record["Plan"]
    nodes = list(_nodes(root))
    return {
        "rootNode": root["Node Type"],
        "returnedRows": root["Actual Rows"] * root["Actual Loops"],
        "executionMs": record["Execution Time"],
        "planningMs": record["Planning Time"],
        # Parent buffer figures include their children: do not sum plan nodes.
        "buffers": {
            key: root.get(key, 0)
            for key in (
                "Shared Hit Blocks",
                "Shared Read Blocks",
                "Shared Dirtied Blocks",
                "Shared Written Blocks",
                "Temp Read Blocks",
                "Temp Written Blocks",
            )
        },
        "indexNames": sorted({node["Index Name"] for node in nodes if "Index Name" in node}),
        "sortNodes": [
            {
                "nodeType": node["Node Type"],
                "actualRows": node["Actual Rows"],
                "loops": node["Actual Loops"],
                "method": node.get("Sort Method"),
                "spaceType": node.get("Sort Space Type"),
                "spaceKb": node.get("Sort Space Used"),
            }
            for node in nodes
            if node["Node Type"] in ("Sort", "Incremental Sort")
        ],
        "scanNodes": [
            {
                "nodeType": node["Node Type"],
                "indexName": node.get("Index Name"),
                "relationName": node.get("Relation Name"),
                "actualRows": node["Actual Rows"],
                "loops": node["Actual Loops"],
                "heapFetches": node.get("Heap Fetches"),
                "rowsRemovedByFilter": node.get("Rows Removed by Filter", 0),
            }
            for node in nodes
            if "Scan" in node["Node Type"]
        ],
    }


def _explain(conn, query, params):
    row = conn.execute("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) " + query, params).fetchone()
    document = next(iter(row.values()))
    if isinstance(document, str):
        document = json.loads(document)
    return _summary(document[0])


def _seed(conn, board_id, observed):
    day = observed.astimezone(KST).date()
    day_start = datetime.combine(day, time(), KST).astimezone(timezone.utc)
    day_end = day_start + timedelta(days=1)
    seed_end = max(day_start, observed - timedelta(microseconds=1))
    seed_start = max(day_start, seed_end - timedelta(minutes=1))
    conn.execute(
        """CREATE TEMP TABLE moodwall_review_people (
           id uuid PRIMARY KEY,seq integer NOT NULL,accepted_at timestamptz NOT NULL
           ) ON COMMIT PRESERVE ROWS"""
    )
    with conn.transaction():
        conn.execute(
            """INSERT INTO pg_temp.moodwall_review_people(id,seq,accepted_at)
               SELECT gen_random_uuid(),g,%s::timestamptz +
                      (%s::interval * ((g-1)::double precision / %s))
               FROM generate_series(1,%s) g""",
            (seed_start, seed_end - seed_start, ROWS - 1, ROWS),
        )
        conn.execute("INSERT INTO board_runtime(id,name) VALUES(%s,'synthetic plan review')", (board_id,))
        conn.execute(
            """INSERT INTO participants(id,created_at,last_seen_at,last_submission_at)
               SELECT id,accepted_at,accepted_at,accepted_at FROM pg_temp.moodwall_review_people"""
        )
        conn.execute(
            """INSERT INTO daily_usage(participant_id,business_date)
               SELECT id,%s FROM pg_temp.moodwall_review_people""",
            (day,),
        )
        # One approved intent per synthetic participant satisfies both quota and
        # FK/uniqueness constraints. No queue-library rows or real accounts exist.
        conn.execute(
            """INSERT INTO submissions(participant_id,business_date,board_id,idempotency_key,
               request_hash,content,emoji_id,accepted_at,expires_at,moderation_status,
               finalized_at,attempt_count,safety_verdict,emotion,emotion_source,
               model_bundle_version,inference_policy_version,moderation_policy_version,
               lexicon_version,display_status)
               SELECT id,%s,%s,gen_random_uuid(),decode(repeat('00',32),'hex'),
                      'synthetic plan probe','heart',accepted_at,
                      LEAST(accepted_at+interval '120 seconds',%s),'APPROVED',
                      accepted_at,1,'ALLOW','NEUTRAL','MODEL',
                      'review-synthetic-v1','review-synthetic-v1','review-synthetic-v1',
                      'review-synthetic-v1','FLOATING'
               FROM pg_temp.moodwall_review_people""",
            (day, board_id, day_end),
        )
    return day


def _cleanup(conn, board_id):
    # These UUIDs were generated by this connection; never truncate shared tables.
    with conn.transaction():
        conn.execute("DELETE FROM submissions WHERE board_id=%s", (board_id,))
        conn.execute(
            """DELETE FROM daily_usage d USING pg_temp.moodwall_review_people p
               WHERE d.participant_id=p.id"""
        )
        conn.execute("DELETE FROM participants p USING pg_temp.moodwall_review_people t WHERE p.id=t.id")
        conn.execute("DELETE FROM board_runtime WHERE id=%s", (board_id,))
    remaining = conn.execute(
        """SELECT
           (SELECT count(*) FROM submissions WHERE board_id=%s) AS submissions,
           (SELECT count(*) FROM participants p JOIN pg_temp.moodwall_review_people t ON t.id=p.id) AS participants,
           (SELECT count(*) FROM board_runtime WHERE id=%s) AS boards""",
        (board_id, board_id),
    ).fetchone()
    if any(remaining.values()):
        raise RuntimeError("synthetic cleanup did not complete")


def run(dsn):
    # Check the parsed DSN before connecting and the server identity before any
    # mutation. Other database names containing 'test' are deliberately refused.
    if conninfo_to_dict(dsn).get("dbname") != DATABASE:
        raise ValueError("MOODWALL_TEST_DB_URL must name moodwall_review_test exactly")
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row, connect_timeout=3) as conn:
        if conn.execute("SELECT current_database() AS name").fetchone()["name"] != DATABASE:
            raise ValueError("server database name is not moodwall_review_test")
        conn.execute("SET search_path=public,pg_catalog")
        conn.execute("SET TIME ZONE 'UTC'")
        conn.execute("SET statement_timeout='60s'")
        conn.execute("SET lock_timeout='2s'")
        before = _index_identity(conn)
        if before is None or not before["indisvalid"] or not before["indisready"]:
            raise RuntimeError("the migrated floating index must be valid and ready")
        board_id = uuid4()
        observed = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
        report = {
            "database": DATABASE,
            "seedRows": ROWS,
            "syntheticParticipants": ROWS,
            "floatingIndex": INDEX,
            "serverVersion": conn.info.server_version,
            "notes": [
                "Counts scan O(n) matching entries; returned content uses 100-row keyset pages plus one lookahead.",
                "Approved bubbles have no display slots or display waiting queue.",
                "Timings depend on cache and this synthetic local dataset; no timing threshold is asserted.",
            ],
        }
        try:
            day = _seed(conn, board_id, observed)
            report["businessDate"] = day.isoformat()
            # VACUUM cannot run inside a transaction. It also makes count/index-
            # only plans visible without relying on autovacuum timing.
            conn.execute("VACUUM (ANALYZE) submissions")
            params = (board_id, day, observed - timedelta(hours=1), observed)
            actual = conn.execute(COUNT_QUERY, params).fetchone()["n"]
            if actual != ROWS:
                raise RuntimeError("synthetic floating row count is incorrect")
            report["floatingCount"] = actual
            anchors = {}
            for position in (0, 5000, 9800):
                anchors[str(position)] = conn.execute(
                    "SELECT finalized_at,id FROM submissions WHERE board_id=%s "
                    "ORDER BY finalized_at,id LIMIT 1 OFFSET %s",
                    (board_id, position),
                ).fetchone()
            report["withFloatingIndex"] = {
                "count": _explain(conn, COUNT_QUERY, params),
                "pages": {
                    label: _explain(conn, PAGE_QUERY, (*params, row["finalized_at"], row["id"]))
                    for label, row in anchors.items()
                },
                "fullSnapshot": _explain(conn, FULL_QUERY, params),
            }
            with conn.transaction(force_rollback=True):
                conn.execute("DROP INDEX public.submissions_floating_order_idx")
                report["withoutFloatingIndex"] = {
                    "pages": {
                        label: _explain(conn, PAGE_QUERY, (*params, row["finalized_at"], row["id"]))
                        for label, row in anchors.items()
                    }
                }
            for variant in ("withFloatingIndex", "withoutFloatingIndex"):
                if any(page["returnedRows"] != 101 for page in report[variant]["pages"].values()):
                    raise RuntimeError("a bounded floating page did not return 101 rows")
        finally:
            # If seeding rolled back before the temp table existed, there are no
            # persistent seed rows. Otherwise clean up only this run's UUIDs.
            temp_exists = conn.execute(
                "SELECT to_regclass('pg_temp.moodwall_review_people') IS NOT NULL AS present"
            ).fetchone()["present"]
            if temp_exists:
                _cleanup(conn, board_id)
            report["syntheticRowsRemoved"] = True
            report["indexRestored"] = _index_identity(conn) == before
            if not report["indexRestored"]:
                raise RuntimeError("floating index identity was not restored")
        return report


def main():
    dsn = os.environ.get("MOODWALL_TEST_DB_URL")
    if not dsn:
        print("Set MOODWALL_TEST_DB_URL to the isolated moodwall_review_test database.", file=sys.stderr)
        return 2
    try:
        report = run(dsn)
    except Exception as error:
        # Avoid leaking a DSN, SQL parameters or arbitrary server text on errors.
        print(json.dumps({"error": type(error).__name__, "message": "Display plan review failed."}), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
