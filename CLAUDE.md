# B001S03 workspace

Training workspace for B001S03. Projects:

| Path | What it is |
|---|---|
| `dags/` | Airflow DAGs loading the `pubs` sample DB (Postgres) from S3 |
| `sql/`, `dags/sql/` | SQL scripts for the pipeline and apps |
| `airflow-terraform/` | Terraform for the Airflow EC2 instance + RDS Postgres |
| `github-mcp-server/` | Python MCP server for the GitHub API (used by `.mcp.json`) |
| `digital_library/` | FastAPI backend + vanilla JS frontend |
| `car_pooling/` | Python module with pytest tests |

## Conventions

- **Commits**: `B001S03: <imperative summary>` (e.g. `B001S03: Load all 11 pubs tables from S3`). Never "fix", "wip", "just push".
- **Branches**: `b001s03-<short-topic>`, one topic per branch and PR. Don't mix projects in one PR.
- **Never commit**: `.env`, `*.tfstate*`, `tfplan`, real `terraform.tfvars`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.ipynb_checkpoints/`, wheels/zips/binaries, test output files.

## Airflow DAGs

- `submit_dag.sh` uploads **one DAG file only**, so a DAG must be self-contained: no imports or reads of sibling files unless there is an embedded fallback.
- DB credentials come from an Airflow Connection, never from literals or env defaults.
- Loads must be safe to re-run: empty stage tables before loading; upsert (`ON CONFLICT`) or insert-if-missing into `public`, in foreign-key order, inside one transaction.
- Tables to process come from `public.control_table` (`istrue = true`); QC queries and thresholds come from `public.qc_check`. Don't hardcode either in the DAG.
- No DB/S3/file I/O at import time. Static `start_date`. A failing task must raise, not just log.
- Keep the module docstring's task flow in sync with the code.

## SQL

- Use bound parameters (`cursor.execute(sql, params)`) and `psycopg2.sql.Identifier` for identifiers; never build SQL with f-strings from inputs.

## Terraform

- Secrets go in variables marked `sensitive = true`, not in committed `.tfvars`.
- No security group rules open to `0.0.0.0/0` on 22, 5432 or 8080.

## Tests

- `github-mcp-server`: `cd github-mcp-server && .venv/bin/python -m pytest -q`
- `car_pooling`, `digital_library`: `python3 -m pytest -q` inside the project folder.

## PR review setup (`.claude/`)

- `/pr-preflight`: author-side checks before opening a PR.
- `/pr-review [PR# | branch] [--post] [--fix]`: runs the reviewer agents in `.claude/agents/` and verifies their findings. Posting to GitHub uses the `github` MCP server; the agent never approves or merges.
- Hooks in `.claude/hooks/`: reviewers are read-only, merges and pushes to `main` need confirmation, secrets are blocked from files and PR comments, and Python syntax is checked after edits.
- `.claude/PR_prompt/`: a single-agent `pr-reviewer` prompt still in progress (see its `NOTES.md`).
