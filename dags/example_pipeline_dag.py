"""
Starter DAG for the Week 6/7 pipeline lab.

Before submitting, rename this file to <your_student_id>_pipeline_dag.py
(e.g. b001s01_pipeline_dag.py) - the shared Airflow instance's
submission mechanism is keyed to that exact filename.

Sample CSVs are mounted read-only at /home/training/pipeline-data/raw/.
Load them into your own pubs_<student_id> database.
"""
from datetime import datetime

from airflow import DAG
from airflow.operators.python import PythonOperator


def load_data():
    # TODO: read the CSVs from /home/training/pipeline-data/raw/ and
    # insert them into your pubs_<student_id> tables (psycopg2 or
    # Airflow's PostgresHook - your own DB credentials are the same
    # ones pgAdmin already uses).
    pass


with DAG(
    dag_id="example_pipeline_dag",  # rename to match your renamed file
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
) as dag:
    load = PythonOperator(task_id="load_data", python_callable=load_data)
