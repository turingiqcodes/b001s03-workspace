"""
Pipeline lab DAG for b001s03.

start -> insert_into_stage -> insert_into_public -> end

1. insert_into_stage: load new_authors.csv into stage.authors
   (the stage table is emptied first, so re-running does not duplicate rows).
2. insert_into_public: copy the rows now in stage.authors into public.authors.
   Authors that already exist (same au_id) are updated to match stage.
"""
from datetime import datetime

import psycopg2
from airflow import DAG
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import PythonOperator

RAW_DIR = "/home/training/pipeline-data/raw"
AUTHORS_CSV = f"{RAW_DIR}/new_authors.csv"

# Same connection pgAdmin uses; the password is read from ~/.pgpass by libpq.
DB_CONFIG = {
    "host": "turingiq-training-rds-pubs-b001.cx0ywsig8do8.us-east-1.rds.amazonaws.com",
    "port": 5432,
    "dbname": "pubs_b001s03",
    "user": "b001s03",
    "sslmode": "require",
}

AUTHOR_COLUMNS = "au_id, au_lname, au_fname, phone, address, city, state, zip, contract"


def insert_into_stage():
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE stage.authors")
            with open(AUTHORS_CSV) as f:
                cur.copy_expert(
                    f"COPY stage.authors ({AUTHOR_COLUMNS}) FROM STDIN WITH (FORMAT csv, HEADER true)",
                    f,
                )
            cur.execute("SELECT count(*) FROM stage.authors")
            print(f"Loaded {cur.fetchone()[0]} rows into stage.authors")
    finally:
        conn.close()


def insert_into_public():
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO public.authors ({AUTHOR_COLUMNS})
                SELECT {AUTHOR_COLUMNS} FROM stage.authors
                ON CONFLICT (au_id) DO UPDATE SET
                    au_lname = EXCLUDED.au_lname,
                    au_fname = EXCLUDED.au_fname,
                    phone    = EXCLUDED.phone,
                    address  = EXCLUDED.address,
                    city     = EXCLUDED.city,
                    state    = EXCLUDED.state,
                    zip      = EXCLUDED.zip,
                    contract = EXCLUDED.contract
                """
            )
            print(f"Inserted/updated {cur.rowcount} rows in public.authors")
    finally:
        conn.close()


with DAG(
    dag_id="b001s03_pipeline_dag",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    tags=["pipeline-lab", "b001s03"],
) as dag:
    start = EmptyOperator(task_id="start")
    stage_load = PythonOperator(task_id="insert_into_stage", python_callable=insert_into_stage)
    public_load = PythonOperator(task_id="insert_into_public", python_callable=insert_into_public)
    end = EmptyOperator(task_id="end")

    start >> stage_load >> public_load >> end
