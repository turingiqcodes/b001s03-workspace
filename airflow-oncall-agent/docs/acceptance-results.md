# Acceptance Test Results — Airflow Oncall Agent v1.0

Generated 2026-11-05 from a full `pytest` run (JUnit report) and `oncall-agent simulate`.
Environment: Linux, Python 3.10.12, mock integrations and recorded API responses (no live Airflow/GitHub/Linear/Groq).

## Summary

| Item | Result |
|---|---|
| Automated tests | **577 passed, 0 failed** (577 total) |
| Test suites | e2e: 18, integration: 234, unit: 325 |
| Acceptance tests AT-001..AT-024 | **24/24 PASS** |
| Part 12.2 scenarios | **19/19 PASS** |
| Simulation scenarios (`oncall-agent simulate`) | **6/6 PASS** |
| Safety mutation checks | 16 deliberately broken safety guards (policy, clearing, limits, approvals): all detected by the final suite; 3 were initially undetected and got new tests (see below) |

AT-003 is verified against recorded Airflow API responses (mocked HTTP), not a live Airflow.

## Acceptance tests

| AT | Description | Traceability status | Result | Tests passed | Verifying tests |
|---|---|---|---|---|---|
| AT-001 | Starts with valid configuration | Verified | **PASS** | 8 | `test_valid_production_configuration_loads`, `test_check_passes_offline_in_development`, `test_run_starts_monitoring_and_stops`, `test_once_runs_a_full_cycle_and_reports_decisions` |
| AT-002 | Identifies missing required configuration | Verified | **PASS** | 9 | `test_production_with_missing_credentials_refuses_to_start`, `test_each_required_production_credential_is_enforced`, `test_check_reports_missing_production_configuration` |
| AT-003 | Connects to Airflow | Verified (mocked HTTP) | **PASS** | 6 | `test_check_connection_with_bearer_token`, `test_check_passes_offline_in_development`, `test_check_fails_when_an_integration_is_unreachable` |
| AT-004 | Detects a failed task | Verified | **PASS** | 2 | `test_detects_failed_tasks` |
| AT-005 | Detects a failed DAG run | Verified | **PASS** | 2 | `test_detects_failed_dag_run_without_failed_task` |
| AT-006 | Detects a DAG import error | Verified | **PASS** | 2 | `test_detects_import_errors` |
| AT-007 | Persists and resumes watermark | Verified | **PASS** | 2 | `test_watermarks_are_persisted_and_resumed_after_restart` |
| AT-008 | No watermark advance after failed poll | Verified | **PASS** | 2 | `test_failed_poll_does_not_advance_watermark` |
| AT-009 | Consistent fingerprints for equivalent errors | Verified | **PASS** | 6 | `test_equivalent_errors_produce_the_same_fingerprint`, `test_different_failures_produce_different_fingerprints` |
| AT-010 | Duplicates reuse existing incident | Verified | **PASS** | 4 | `test_repeated_failure_reuses_the_incident`, `test_same_failure_in_a_later_run_updates_the_existing_incident` |
| AT-011 | Context collected when available | Verified | **PASS** | 4 | `test_full_context_is_collected_when_available`, `test_agent_investigates_new_incidents_once` |
| AT-012 | Missing context handled safely | Verified | **PASS** | 6 | `test_airflow_unavailable_is_recorded_as_missing_evidence`, `test_github_unavailable_keeps_airflow_evidence`, `test_bug_in_one_step_does_not_lose_the_others` |
| AT-013 | AI returns validated structured JSON | Verified | **PASS** | 6 | `test_valid_response_is_accepted_and_commit_expanded`, `test_ai_analysis_is_stored_and_audited`, `test_every_new_incident_is_analysed_and_stored` |
| AT-014 | Invalid AI responses rejected | Verified | **PASS** | 12 | `test_invalid_responses_are_rejected`, `test_incomplete_response_is_rejected`, `test_invalid_ai_response_falls_back` |
| AT-015 | AI failure activates fallback | Verified | **PASS** | 6 | `test_provider_failure_activates_fallback`, `test_ai_outage_falls_back_for_every_incident`, `test_timeout_raises_provider_error_after_one_retry` |
| AT-016 | Fallback confidence ≤ 0.5 | Verified | **PASS** | 6 | `test_fallback_confidence_never_exceeds_cap`, `test_every_rule_respects_the_confidence_cap`, `test_fallback_never_proposes_an_executable_action_for_any_input` |
| AT-017 | Fallback cannot authorize auto-retry | Verified | **PASS** | 4 | `test_fallback_cannot_authorize_retry_even_with_high_confidence`, `test_ai_outage_means_no_automatic_action` |
| AT-018 | Eligible retriable failure passes policy | Verified | **PASS** | 4 | `test_fully_eligible_retriable_failure_passes`, `test_eligible_failure_is_cleared_with_its_upstream_failed_children` |
| AT-019 | Non-retriable fails policy | Verified | **PASS** | 23 | `test_each_condition_blocks_automatic_retry[…NON_RETRIABLE…]` |
| AT-020 | Uncertain fails policy | Verified | **PASS** | 23 | `test_each_condition_blocks_automatic_retry[…UNCERTAIN…]` |
| AT-021 | Incident retry limit enforced | Verified | **PASS** | 25 | `test_each_condition_blocks_automatic_retry[cycle_retry_count]`, `test_incident_retry_limit_is_enforced_inside_the_reservation` |
| AT-022 | Global retry limit enforced | Verified | **PASS** | 6 | `test_global_hourly_limit_is_enforced`, `test_global_retry_limit_is_enforced_inside_the_reservation`, `test_stale_policy_decision_is_still_stopped_by_the_atomic_limits` |
| AT-023 | Successful tasks never cleared | Verified | **PASS** | 8 | `test_successful_tasks_are_never_cleared`, `test_unsafe_dry_run_blocks_the_retry`, `test_approved_rerun_clears_only_failed_tasks_of_that_run_once` |
| AT-024 | Restricted ops need valid approval, run once | Verified | **PASS** | 9 | `test_label_approval_by_trusted_user_executes_once`, `test_invalid_approvals_never_execute`, `test_concurrent_execution_of_one_approval_runs_once` |

## Additional required scenarios (SRS Part 12.2)

| Scenario | Result | Tests passed | Verifying tests |
|---|---|---|---|
| Airflow unavailable | **PASS** | 6 | `test_failed_poll_does_not_advance_watermark`, `test_airflow_recovers_after_outage_without_losing_failures`, `test_airflow_unavailable_is_recorded_as_missing_evidence` |
| GitHub unavailable | **PASS** | 4 | `test_github_unavailable_keeps_airflow_evidence`, `test_github_unavailable_raises_retriable_error` |
| Linear unavailable | **PASS** | 4 | `test_linear_outage_does_not_stop_monitoring_and_is_retried`, `test_linear_unavailable_raises_retriable_error` |
| AI provider timeout | **PASS** | 4 | `test_timeout_raises_provider_error_after_one_retry`, `test_provider_failure_activates_fallback` |
| Malformed API responses | **PASS** | 21 | `test_malformed_responses_are_rejected`, `test_malformed_commit_lists_are_rejected`, `test_malformed_graphql_responses_are_rejected`, `test_invalid_responses_are_rejected`, `test_malformed_airflow_response_fails_the_poll_safely` |
| Repeated polling | **PASS** | 2 | `test_repeated_polling_of_the_same_window_creates_no_duplicates` |
| Agent restart | **PASS** | 6 | `test_watermarks_are_persisted_and_resumed_after_restart`, `test_restart_between_retry_and_verification`, `test_restarted_agent_analyses_incidents_left_behind` |
| Duplicate retry requests | **PASS** | 6 | `test_duplicate_retry_request_is_refused_without_escalating`, `test_same_failed_try_cannot_be_retried_twice_even_after_status_reset`, `test_concurrent_retry_requests_clear_once` |
| Concurrent state updates | **PASS** | 8 | `test_concurrent_duplicate_retry_requests_execute_once`, `test_concurrent_recording_of_one_failure_creates_one_incident`, `test_global_limit_holds_under_concurrency`, `test_concurrent_execution_of_one_approval_runs_once` |
| Approval received more than once | **PASS** | 4 | `test_label_approval_by_trusted_user_executes_once`, `test_approval_received_twice_changes_nothing_the_second_time` |
| Invalid approval | **PASS** | 13 | `test_invalid_approvals_never_execute`, `test_approval_before_the_request_does_not_count`, `test_label_added_before_the_request_does_not_count`, `test_approval_with_unknown_actor_is_rejected`, `test_empty_allowlist_rejects_everyone_outside_mock_mode` |
| Retry still running after 30 minutes | **PASS** | 4 | `test_retry_still_running_after_30_minutes_escalates`, `test_retry_still_running_after_30_minutes_is_escalated` |
| Retry fails again | **PASS** | 6 | `test_retry_that_fails_again_escalates`, `test_retry_that_fails_again_is_escalated_and_not_retried_again`, `test_failed_retry_is_escalated_in_linear` |
| Linear issue update failure | **PASS** | 4 | `test_failed_issue_update_is_retried_next_cycle`, `test_failed_mutation_is_not_repeated` |
| SQLite transaction failure | **PASS** | 8 | `test_exception_inside_transaction_rolls_everything_back`, `test_constraint_violation_rolls_back_whole_transaction`, `test_failure_mid_transaction_leaves_no_partial_state`, `test_store_failure_while_recording_holds_that_pollers_watermark` |
| MCP write operations disabled | **PASS** | 7 | `test_airflow_writes_are_disabled_by_default`, `test_linear_writes_are_disabled_by_default` |
| MCP write operations enabled but unauthorized | **PASS** | 7 | `test_enabled_writes_still_need_the_policy`, `test_mcp_can_never_approve` |
| Global retry limit set to zero | **PASS** | 6 | `test_zero_limits_disable_retries`, `test_zero_retry_limits_mean_no_airflow_writes`, `test_max_auto_retries_zero_disables_automatic_retries` |
| DAG marked as no-retry | **PASS** | 4 | `test_no_retry_dag_is_never_retried`, `test_approved_action_on_no_retry_dag_is_refused` |

## Simulation

| Scenario | Result |
|---|---|
| Retriable task failure | **PASS** |
| Non-retriable task failure | **PASS** |
| Uncertain failure | **PASS** |
| Duplicate incident | **PASS** |
| AI provider unavailable | **PASS** |
| Restricted operation requiring human approval | **PASS** |

<details><summary>Full simulation output</summary>

```
Simulation — mock Airflow, GitHub, Linear and AI; temporary databases. No real system is contacted.

[1/6] Retriable task failure  ...  PASS
  Failure       : sales_etl.extract_orders — requests.exceptions.ReadTimeout: HTTPSConnectionPool(host='api.shop.example', port=443): Read timed out. (read timeout=30)
  Investigation : collected task_log, dag_source, run_history, commits; missing none; 9 of the last 10 runs succeeded; task extract_orders failed in 1 of them
  Analysis      : retriable (confidence 0.85, ai) → proposes retry_task
  Root cause    : Transient timeout calling an upstream API; previous runs succeeded and no related code changed.
  Policy        : auto_retry — all automatic retry conditions are satisfied
  Expected      : automatic retry of the failed task and its upstream_failed children
  Outcome       : mock Airflow: cleared ['extract_orders', 'load_orders', 'transform_orders'] in run scheduled__2026-11-05T07:22:33+00:00
  Outcome       : next cycle: retry succeeded; incident resolved
  Final state   : incident #1 resolved; Linear ONC-1

[2/6] Non-retriable task failure  ...  PASS
  Failure       : customer_sync.transform_customers — KeyError: 'customer_tier'
  Investigation : collected task_log, dag_source, run_history, commits; missing none; 9 of the last 10 runs succeeded; task transform_customers failed in 1 of them
  Analysis      : non_retriable (confidence 0.90, ai) → proposes manual_investigation
  Root cause    : The task code references a field that the input data does not contain after a recent code change.
  Policy        : escalate — proposed action is manual_investigation; a human must investigate
  Expected      : escalate to a human via Linear; no Airflow change
  Outcome       : suspected commit a1b2c3d; assigned to alice-gh
  Final state   : incident #1 escalated; Linear ONC-1

[3/6] Uncertain failure  ...  PASS
  Failure       : ml_feature_build.build_features — Task exited with return code -9
  Investigation : collected task_log, dag_source, run_history, commits; missing patch:9988776:dags/ml_feature_build.py: GitHub did not provide a patch (binary or too large); 9 of the last 10 runs succeeded; task build_features failed in 1 of them
  Analysis      : uncertain (confidence 0.40, ai) → proposes manual_investigation
  Root cause    : The process was killed, possibly out of memory; the logs do not show why.
  Policy        : escalate — proposed action is manual_investigation; a human must investigate
  Expected      : escalate to a human via Linear; no Airflow change
  Final state   : incident #1 escalated; Linear ONC-1

[4/6] Duplicate incident  ...  PASS
  Failure       : customer_sync.transform_customers — KeyError: 'customer_tier'
  Investigation : collected task_log, dag_source, run_history, commits; missing none; 9 of the last 10 runs succeeded; task transform_customers failed in 1 of them
  Analysis      : non_retriable (confidence 0.90, ai) → proposes manual_investigation
  Root cause    : The task code references a field that the input data does not contain after a recent code change.
  Expected      : escalate to a human via Linear; no Airflow change
  Outcome       : same incident #1: 2 occurrences, 1 Linear issue (ONC-1) updated with a comment
  Final state   : incident #1 escalated; Linear ONC-1

[5/6] AI provider unavailable  ...  PASS
  Failure       : sales_etl.extract_orders — requests.exceptions.ReadTimeout: HTTPSConnectionPool(host='api.shop.example', port=443): Read timed out. (read timeout=30)
  Investigation : collected task_log, dag_source, run_history, commits; missing none; 9 of the last 10 runs succeeded; task extract_orders failed in 1 of them
  Analysis      : retriable (confidence 0.50, fallback) → proposes manual_investigation
  Root cause    : A network call to an external service failed or timed out.
  Policy        : escalate — proposed action is manual_investigation; a human must investigate
  Expected      : escalate to a human via Linear; no Airflow change
  Outcome       : fallback reason: AI provider unavailable: mock AI provider unavailable
  Final state   : incident #1 escalated; Linear ONC-1

[6/6] Restricted operation requiring human approval  ...  PASS
  Failure       : customer_sync.transform_customers — KeyError: 'customer_tier'
  Investigation : collected task_log, dag_source, run_history, commits; missing none; 9 of the last 10 runs succeeded; task transform_customers failed in 1 of them
  Analysis      : uncertain (confidence 0.60, ai) → proposes rerun_dag
  Root cause    : The transform failed on a field the upstream export may have renamed; re-running the whole run may be needed once the export is confirmed.
  Policy        : request_approval — rerun_dag is a restricted operation and requires human approval
  Expected      : request human approval in Linear; run once after a trusted approval
  Outcome       : '/approve' from mallory@example.com (not trusted): rejected, nothing executed
  Outcome       : label 'oncall:approved' from alice@example.com (trusted): approval executed; mock Airflow cleared ['publish_customers', 'transform_customers']
  Final state   : incident #1 resolved; Linear ONC-1

6/6 scenarios behaved as expected.
```

</details>

## Mutation checks

To confirm the safety tests can fail, each guard below was disabled in turn and the relevant tests re-run (sources restored afterwards):

| Guard | Detected |
|---|---|
| Clearing successful tasks (FR-058) | yes |
| Clearing other runs (FR-059) | yes |
| Clearing unrelated failed tasks (FR-057) | yes |
| Approval claim-once (FR-070) | yes |
| Confidence threshold 0.7 (FR-050) | yes |
| Fallback cannot retry (FR-045) | yes |
| No-retry DAGs | yes |
| Atomic global hourly limit | yes, after adding `test_stale_policy_decision_is_still_stopped_by_the_atomic_limits` (initially undetected) |
| Atomic per-incident limit | yes |
| Duplicate retry request must not escalate | yes |
| Approval by the agent's own user | yes |
| Untrusted approver | yes |
| Comment approval before the request / loose matching | yes |
| Label approval before the request | yes, after adding `test_label_added_before_the_request_does_not_count` (initially undetected) |
| Empty allowlist outside mock mode | yes |
| Approval with unknown identity | yes, after adding `test_approval_with_unknown_actor_is_rejected` (initially undetected) |
| Missing configuration directory (fail closed) | found during deployment verification; fixed and covered by `test_missing_config_directory_fails_closed` |

