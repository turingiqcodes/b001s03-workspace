"""
Pipeline lab DAG for b001s03.

start
  -> read_control_table
  -> insert_into_stage_1 (authors, jobs, stores, publishers)  -> qc_csv_to_stage_1
  -> insert_into_stage_2 (titles, pub_info, employee, discounts) -> qc_csv_to_stage_2
  -> insert_into_stage_3 (titleauthor, sales, roysched)        -> qc_csv_to_stage_3
  -> insert_into_public -> qc_stage_to_public
  -> update_control_table
  -> end

1. read_control_table: only tables with istrue = true in public.control_table
   are processed; every other table is skipped by the tasks below.
2. insert_into_stage_N: each new_<table>.csv is read from the S3 bucket
   S3_BUCKET and loaded into stage.<table> (the stage table is emptied first, so
   re-running does not duplicate rows). If the S3 read fails, the same bucket
   mounted at LOCAL_RAW_DIR is tried.
3. qc_*: the src_query, target_query and threshold for each check are read from
   public.qc_check (qc_id = <table>_csv_to_stage or <table>_stage_to_public),
   never written in this file. See qc_passes() for the pass rule.
4. insert_into_public: copies every stage table into public in foreign-key order
   in one transaction. Tables with a primary key are upserted; tables without one
   only get rows that are not already there.
5. update_control_table: sets ts = now() for the tables that were processed.

submit_dag.sh uploads only this one file to Airflow, so the DAG must not depend
on other files next to it.

Logging: every task writes its steps to the Airflow task log, and every task
logs a SUCCESS or FAILED summary when it finishes (see the callbacks below).
Failures are logged with the full error before the task is marked failed.
"""
import io
import logging
import os
from datetime import datetime

import psycopg2
from airflow import DAG
from airflow.operators.python import PythonOperator

try:
    from airflow.sdk import BaseHook  # Airflow 3
except ImportError:
    from airflow.hooks.base import BaseHook  # Airflow 2

log = logging.getLogger(__name__)

S3_BUCKET = "turingiq-training-pipeline-data-b001s03"
S3_PREFIX = "raw"
# The same bucket, mounted read-only on the lab VM. Used when the S3 read fails.
LOCAL_RAW_DIR = "/home/training/pipeline-data/raw"

# csv:     file name under S3_PREFIX
# columns: CSV header, loaded into stage.<table> in this order
# key:     primary key of public.<table>; None means rows are inserted only when
#          an identical row (on the CSV columns) is not already in public
# extra_public_columns: stage columns filled by a default (not in the CSV)
#          that must still be copied to public
TABLES = {
    "authors": {
        "csv": "new_authors.csv",
        "columns": ["au_id", "au_lname", "au_fname", "phone", "address", "city", "state", "zip", "contract"],
        "key": ["au_id"],
    },
    "jobs": {
        "csv": "new_jobs.csv",
        "columns": ["job_desc", "min_lvl", "max_lvl"],
        "key": None,
        "extra_public_columns": ["job_id"],
    },
    "stores": {
        "csv": "new_stores.csv",
        "columns": ["stor_id", "stor_name", "stor_address", "city", "state", "zip"],
        "key": ["stor_id"],
    },
    "publishers": {
        "csv": "new_publishers.csv",
        "columns": ["pub_id", "pub_name", "city", "state", "country"],
        "key": ["pub_id"],
    },
    "titles": {
        "csv": "new_titles.csv",
        "columns": ["title_id", "title", "type", "pub_id", "price", "advance", "royalty", "ytd_sales", "notes", "pubdate"],
        "key": ["title_id"],
    },
    "pub_info": {
        "csv": "new_pub_info.csv",
        "columns": ["pub_id", "pr_info"],
        "key": ["pub_id"],
    },
    "employee": {
        "csv": "new_employees.csv",
        "columns": ["emp_id", "fname", "minit", "lname", "job_id", "job_lvl", "pub_id", "hire_date"],
        "key": ["emp_id"],
    },
    "discounts": {
        "csv": "new_discounts.csv",
        "columns": ["discounttype", "stor_id", "lowqty", "highqty", "discount"],
        "key": None,
    },
    "titleauthor": {
        "csv": "new_titleauthor.csv",
        "columns": ["au_id", "title_id", "au_ord", "royaltyper"],
        "key": ["au_id", "title_id"],
    },
    "sales": {
        "csv": "new_sales.csv",
        "columns": ["stor_id", "ord_num", "ord_date", "qty", "payterms", "title_id"],
        "key": ["stor_id", "ord_num", "title_id"],
    },
    "roysched": {
        "csv": "new_roysched.csv",
        "columns": ["title_id", "lorange", "hirange", "royalty"],
        "key": None,
    },
}

STAGE_GROUPS = [
    ["authors", "jobs", "stores", "publishers"],
    ["titles", "pub_info", "employee", "discounts"],
    ["titleauthor", "sales", "roysched"],
]

# Parents before children, so the foreign keys in public are satisfied.
PUBLIC_LOAD_ORDER = [
    "publishers", "jobs", "stores", "authors", "titles", "pub_info",
    "employee", "discounts", "titleauthor", "sales", "roysched",
]

# The password is not stored here. It is read from the Airflow Connection
# DB_CONN_ID (Admin -> Connections in the Airflow UI) when a task connects.
DB_CONN_ID = "pubs_b001s03"
DB_CONFIG = {
    "host": "turingiq-training-rds-pubs-b001.cx0ywsig8do8.us-east-1.rds.amazonaws.com",
    "port": 5432,
    "dbname": "pubs_b001s03",
    "user": "b001s03",
    "sslmode": "require",
}


def connect_db():
    password = BaseHook.get_connection(DB_CONN_ID).password
    if not password:
        raise ValueError(f"Airflow Connection '{DB_CONN_ID}' has no password set")
    log.info("Connecting to %s as %s", DB_CONFIG["dbname"], DB_CONFIG["user"])
    return psycopg2.connect(**DB_CONFIG, password=password)


def log_task_success(context):
    ti = context["task_instance"]
    log.info(
        "SUCCESS | dag=%s task=%s run_id=%s try=%s duration=%ss",
        ti.dag_id, ti.task_id, context["run_id"], ti.try_number, ti.duration,
    )


def log_task_failure(context):
    ti = context["task_instance"]
    log.error(
        "FAILED | dag=%s task=%s run_id=%s try=%s error=%r",
        ti.dag_id, ti.task_id, context["run_id"], ti.try_number, context.get("exception"),
    )


def enabled_tables(context, tables):
    """Return the tables in `tables` that read_control_table marked as enabled."""
    enabled = context["ti"].xcom_pull(task_ids="read_control_table") or []
    for table in tables:
        if table not in enabled:
            log.info("Skipping %s: not enabled in public.control_table", table)
    return [t for t in tables if t in enabled]


def read_csv(file_name):
    """Return the text of file_name from the S3 bucket (or its local mount)."""
    key = f"{S3_PREFIX}/{file_name}"
    try:
        import boto3

        log.info("Reading s3://%s/%s", S3_BUCKET, key)
        obj = boto3.client("s3").get_object(Bucket=S3_BUCKET, Key=key)
        return obj["Body"].read().decode("utf-8")
    except Exception:
        log.warning("Could not read s3://%s/%s; trying %s", S3_BUCKET, key, LOCAL_RAW_DIR, exc_info=True)

    local_path = os.path.join(LOCAL_RAW_DIR, file_name)
    if not os.path.exists(local_path):
        raise FileNotFoundError(f"{file_name} not found in s3://{S3_BUCKET}/{S3_PREFIX} or {LOCAL_RAW_DIR}")
    log.info("Reading %s", local_path)
    with open(local_path) as f:
        return f.read()


def qc_passes(src_value, target_value, threshold):
    # No threshold (the csv_to_stage checks): the target must have at least as
    # many rows as the source; with src_query "select 1" the stage table must
    # not be empty.
    if threshold is None:
        return target_value >= src_value
    # With a threshold (the stage_to_public checks): threshold is the row count
    # public had before the new data, so public must now hold exactly
    # threshold + the stage rows.
    return target_value == src_value + threshold


def run_qc_checks(qc_ids):
    if not qc_ids:
        log.info("No QC checks to run")
        return

    conn = connect_db()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT qc_id, src_query, target_query, threshold FROM public.qc_check WHERE qc_id = ANY(%s)",
                (qc_ids,),
            )
            checks = {row[0]: row[1:] for row in cur.fetchall()}
            missing = [q for q in qc_ids if q not in checks]
            if missing:
                raise ValueError(f"qc_id not found in public.qc_check: {missing}")

            failed = []
            for qc_id in qc_ids:
                src_query, target_query, threshold = checks[qc_id]
                cur.execute(src_query)
                src_value = cur.fetchone()[0]
                cur.execute(target_query)
                target_value = cur.fetchone()[0]
                passed = qc_passes(src_value, target_value, threshold)
                log.info(
                    "QC %s | %s | src=%s (%s) target=%s (%s) threshold=%s",
                    "PASSED" if passed else "FAILED", qc_id,
                    src_value, src_query, target_value, target_query, threshold,
                )
                if not passed:
                    failed.append(qc_id)
    except Exception:
        log.exception("QC checks could not be run")
        raise
    finally:
        conn.close()
        log.info("Database connection closed")

    if failed:
        raise ValueError(f"QC checks failed: {failed}")
    log.info("All %s QC checks passed", len(qc_ids))


def start_pipeline(**context):
    log.info("Pipeline started | run_id=%s", context["run_id"])
    log.info("Source: s3://%s/%s (fallback %s)", S3_BUCKET, S3_PREFIX, LOCAL_RAW_DIR)
    log.info("Target: stage -> public on %s/%s", DB_CONFIG["host"], DB_CONFIG["dbname"])


def read_control_table():
    conn = connect_db()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT table_name FROM public.control_table WHERE istrue")
            names = [row[0] for row in cur.fetchall()]
    except Exception:
        log.exception("Reading public.control_table failed")
        raise
    finally:
        conn.close()
        log.info("Database connection closed")

    unknown = [n for n in names if n not in TABLES]
    if unknown:
        log.warning("Ignoring tables in public.control_table this DAG does not load: %s", unknown)
    enabled = [n for n in PUBLIC_LOAD_ORDER if n in names]
    log.info("Enabled tables: %s", enabled)
    # The return value is pushed to XCom for the tasks below.
    return enabled


def insert_into_stage(tables, **context):
    tables = enabled_tables(context, tables)
    if not tables:
        log.info("No enabled tables in this group")
        return

    conn = connect_db()
    try:
        with conn, conn.cursor() as cur:
            for table in tables:
                cfg = TABLES[table]
                data = read_csv(cfg["csv"])
                log.info("Truncating stage.%s", table)
                cur.execute(f"TRUNCATE TABLE stage.{table}")
                log.info("Loading %s into stage.%s", cfg["csv"], table)
                cur.copy_expert(
                    f"COPY stage.{table} ({', '.join(cfg['columns'])}) FROM STDIN WITH (FORMAT csv, HEADER true)",
                    io.StringIO(data),
                )
                cur.execute(f"SELECT count(*) FROM stage.{table}")
                log.info("Loaded %s rows into stage.%s", cur.fetchone()[0], table)
    except Exception:
        log.exception("Loading stage tables %s failed; the transaction was rolled back", tables)
        raise
    finally:
        conn.close()
        log.info("Database connection closed")


def qc_csv_to_stage(tables, **context):
    run_qc_checks([f"{t}_csv_to_stage" for t in enabled_tables(context, tables)])


def public_insert_sql(table):
    cfg = TABLES[table]
    columns = cfg.get("extra_public_columns", []) + cfg["columns"]
    column_list = ", ".join(columns)
    sql = f"INSERT INTO public.{table} ({column_list})\nSELECT {column_list}\nFROM stage.{table} s"
    if cfg["key"]:
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c not in cfg["key"])
        return sql + f"\nON CONFLICT ({', '.join(cfg['key'])}) DO UPDATE SET {updates}"
    # No primary key: skip rows that are already in public (NULLs compare equal).
    match = " AND ".join(f"p.{c} IS NOT DISTINCT FROM s.{c}" for c in cfg["columns"])
    return sql + f"\nWHERE NOT EXISTS (SELECT 1 FROM public.{table} p WHERE {match})"


def insert_into_public(**context):
    tables = enabled_tables(context, PUBLIC_LOAD_ORDER)
    if not tables:
        log.info("No enabled tables to copy to public")
        return

    conn = connect_db()
    try:
        with conn, conn.cursor() as cur:
            for table in tables:
                log.info("Copying stage.%s into public.%s", table, table)
                cur.execute(public_insert_sql(table))
                log.info("Inserted/updated %s rows in public.%s", cur.rowcount, table)
                cur.execute(f"SELECT count(*) FROM public.{table}")
                log.info("public.%s now has %s rows", table, cur.fetchone()[0])
    except Exception:
        log.exception("Insert into public failed; the transaction was rolled back")
        raise
    finally:
        conn.close()
        log.info("Database connection closed")


def qc_stage_to_public(**context):
    run_qc_checks([f"{t}_stage_to_public" for t in enabled_tables(context, PUBLIC_LOAD_ORDER)])


def update_control_table(**context):
    tables = enabled_tables(context, PUBLIC_LOAD_ORDER)
    if not tables:
        log.info("No tables to update in public.control_table")
        return

    conn = connect_db()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE public.control_table SET ts = now() WHERE istrue AND table_name = ANY(%s)",
                (tables,),
            )
            log.info("Set ts = now() for %s rows in public.control_table: %s", cur.rowcount, tables)
    except Exception:
        log.exception("Updating public.control_table failed")
        raise
    finally:
        conn.close()
        log.info("Database connection closed")


def end_pipeline(**context):
    log.info("Pipeline finished | run_id=%s", context["run_id"])


default_args = {
    "on_success_callback": log_task_success,
    "on_failure_callback": log_task_failure,
}

with DAG(
    dag_id="b001s03_pipeline_dag",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    default_args=default_args,
    tags=["pipeline-lab", "b001s03"],
) as dag:
    start = PythonOperator(task_id="start", python_callable=start_pipeline)
    control = PythonOperator(task_id="read_control_table", python_callable=read_control_table)

    previous = control
    for n, group in enumerate(STAGE_GROUPS, start=1):
        stage_load = PythonOperator(
            task_id=f"insert_into_stage_{n}",
            python_callable=insert_into_stage,
            op_kwargs={"tables": group},
        )
        stage_qc = PythonOperator(
            task_id=f"qc_csv_to_stage_{n}",
            python_callable=qc_csv_to_stage,
            op_kwargs={"tables": group},
        )
        previous >> stage_load >> stage_qc
        previous = stage_qc

    public_load = PythonOperator(task_id="insert_into_public", python_callable=insert_into_public)
    public_qc = PythonOperator(task_id="qc_stage_to_public", python_callable=qc_stage_to_public)
    control_update = PythonOperator(task_id="update_control_table", python_callable=update_control_table)
    end = PythonOperator(task_id="end", python_callable=end_pipeline)

    start >> control
    previous >> public_load >> public_qc >> control_update >> end
