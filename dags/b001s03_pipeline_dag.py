"""
Pipeline lab DAG for b001s03.

start -> insert_into_stage -> insert_into_public -> end

1. insert_into_stage: load new_authors.csv into stage.authors
   (the stage table is emptied first, so re-running does not duplicate rows).
2. insert_into_public: run sql/b001s03_insert_authors_public.sql, which copies
   the rows now in stage.authors into public.authors (existing au_ids are updated).

Logging: every task writes its steps to the Airflow task log, and every task
logs a SUCCESS or FAILED summary when it finishes (see the callbacks below).
Failures are logged with the full error before the task is marked failed.
"""
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

DAG_DIR = os.path.dirname(os.path.abspath(__file__))

# The CSV is shipped with the DAG in dags/data/ so the Airflow server can read it.
AUTHORS_CSV = os.path.join(DAG_DIR, "data", "new_authors.csv")

# SQL files live next to this DAG in dags/sql/
SQL_DIR = os.path.join(DAG_DIR, "sql")
INSERT_PUBLIC_SQL = os.path.join(SQL_DIR, "b001s03_insert_authors_public.sql")

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

AUTHOR_COLUMNS = "au_id, au_lname, au_fname, phone, address, city, state, zip, contract"


def connect_db():
    password = BaseHook.get_connection(DB_CONN_ID).password
    if not password:
        raise ValueError(f"Airflow Connection '{DB_CONN_ID}' has no password set")
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


def start_pipeline(**context):
    log.info("Pipeline started | run_id=%s", context["run_id"])
    log.info("Source CSV: %s", AUTHORS_CSV)
    log.info("Target: stage.authors -> public.authors on %s/%s", DB_CONFIG["host"], DB_CONFIG["dbname"])


def insert_into_stage():
    log.info("Connecting to %s as %s", DB_CONFIG["dbname"], DB_CONFIG["user"])
    try:
        conn = connect_db()
    except Exception:
        log.exception("Could not connect to the database")
        raise

    try:
        with conn, conn.cursor() as cur:
            log.info("Truncating stage.authors")
            cur.execute("TRUNCATE TABLE stage.authors")

            log.info("Loading %s into stage.authors", AUTHORS_CSV)
            with open(AUTHORS_CSV) as f:
                cur.copy_expert(
                    f"COPY stage.authors ({AUTHOR_COLUMNS}) FROM STDIN WITH (FORMAT csv, HEADER true)",
                    f,
                )
            cur.execute("SELECT count(*) FROM stage.authors")
            log.info("Loaded %s rows into stage.authors", cur.fetchone()[0])
    except Exception:
        log.exception("Loading stage.authors failed; the transaction was rolled back")
        raise
    finally:
        conn.close()
        log.info("Database connection closed")


def insert_into_public():
    log.info("Reading SQL file %s", INSERT_PUBLIC_SQL)
    try:
        with open(INSERT_PUBLIC_SQL) as f:
            sql = f.read()
    except Exception:
        log.exception("Could not read SQL file %s", INSERT_PUBLIC_SQL)
        raise

    log.info("Connecting to %s as %s", DB_CONFIG["dbname"], DB_CONFIG["user"])
    try:
        conn = connect_db()
    except Exception:
        log.exception("Could not connect to the database")
        raise

    try:
        with conn, conn.cursor() as cur:
            log.info("Running insert from stage.authors into public.authors")
            cur.execute(sql)
            log.info("Inserted/updated %s rows in public.authors", cur.rowcount)
            cur.execute("SELECT count(*) FROM public.authors")
            log.info("public.authors now has %s rows", cur.fetchone()[0])
    except Exception:
        log.exception("Insert into public.authors failed; the transaction was rolled back")
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
    stage_load = PythonOperator(task_id="insert_into_stage", python_callable=insert_into_stage)
    public_load = PythonOperator(task_id="insert_into_public", python_callable=insert_into_public)
    end = PythonOperator(task_id="end", python_callable=end_pipeline)

    start >> stage_load >> public_load >> end
