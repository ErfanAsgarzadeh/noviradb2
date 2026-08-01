# Meeting Management Test Matrix

Last updated: 2026-08-01

## Baseline

| Suite | Status | Notes |
| --- | --- | --- |
| Backend check | Passed | `DB_ENGINE=sqlite python manage.py check` |
| Backend migration dry-run | Passed | `DB_ENGINE=sqlite python manage.py makemigrations --check --dry-run` |
| Backend full pytest | Baseline failed | 467 passed, 1 failed, 12 skipped; SQLite concurrency lock failure. |
| Frontend dependency install | Passed with audit findings | 7 high severity npm audit findings. |
| Frontend lint | Baseline failed | 100 errors, 107 warnings. |
| Frontend typecheck | Baseline failed | `app/Operations/page.tsx` icon prop type. |
| Frontend build | Baseline failed | Same TypeScript failure after successful compile. |
| Playwright auth setup | Blocked/failing | Missing `NOVIRA_E2E_PASSWORD`. |

## Phase 1 Executed Tests

| Suite | Status | Notes |
| --- | --- | --- |
| Django check | Passed | `DB_ENGINE=sqlite python manage.py check` |
| Migration dry-run | Passed | `DB_ENGINE=sqlite python manage.py makemigrations --check --dry-run` |
| Meeting model tests | Passed | 9 passed. |
| Backend full pytest | Baseline failed | 476 passed, 1 failed, 12 skipped; same SQLite concurrency lock failure as baseline. |

## Phase 2 Executed Tests

| Suite | Status | Notes |
| --- | --- | --- |
| Django check | Passed | `DB_ENGINE=sqlite python manage.py check` |
| Migration dry-run | Passed | `DB_ENGINE=sqlite python manage.py makemigrations --check --dry-run` |
| Service/dependency/notification tests | Passed after fix | 10 passed after correcting invalid test factory date setup. |
| Meeting-management package | Passed | 19 passed. |
| Backend full pytest | Baseline failed | 486 passed, 1 failed, 12 skipped; same SQLite concurrency lock failure as baseline. |

## Phase 3 Executed Tests

| Suite | Status | Notes |
| --- | --- | --- |
| Django check | Passed | `DB_ENGINE=sqlite python manage.py check` |
| Migration dry-run | Passed | `DB_ENGINE=sqlite python manage.py makemigrations --check --dry-run` |
| Meeting-management API/package | Passed | 29 passed. |
| Backend full pytest | Baseline failed | 496 passed, 1 failed, 12 skipped; same SQLite concurrency lock failure as baseline. |

## Phase 4 Executed Tests

| Suite | Status | Notes |
| --- | --- | --- |
| Scoped frontend lint | Passed after fix | New meeting-management files and E2E spec passed. |
| Frontend typecheck | Passed after fix | `npx.cmd tsc --noEmit` passed. |
| Full frontend lint | Baseline failed | 99 errors, 107 warnings in pre-existing files. |
| Frontend build | Passed | `npm.cmd run build` passed and generated meeting routes. |
| Meeting Playwright spec | Blocked | Auth setup failed due missing `NOVIRA_E2E_PASSWORD`; 2 meeting tests did not run. |

## Planned Backend Tests

- `tests/meeting_management/test_models.py`
- `tests/meeting_management/test_services.py`
- `tests/meeting_management/test_state_transitions.py`
- `tests/meeting_management/test_permissions.py`
- `tests/meeting_management/test_api_meetings.py`
- `tests/meeting_management/test_api_resolutions.py`
- `tests/meeting_management/test_progress_and_completion.py`
- `tests/meeting_management/test_deadline_changes.py`
- `tests/meeting_management/test_dependencies.py`
- `tests/meeting_management/test_audit.py`
- `tests/meeting_management/test_notifications.py`
- `tests/meeting_management/test_reporting.py`

## Planned Frontend Tests

- Navigation and `allowedPages` visibility.
- Meeting creation and scheduling.
- Participant and unit invitation plus attendance.
- Minutes submission and approval.
- Resolution/action creation and RACI assignment.
- Progress report submission.
- Completion review and acceptance.
- Deadline extension flow.
- Individual/unit dashboard visibility.
- Unauthorized access denial.
- Confidential meeting isolation.
- Narrow viewport layout.

## Regression Policy

- Run narrow meeting-management tests first after changes.
- Rerun full backend suite at each backend phase gate.
- Rerun frontend lint/type/build at each frontend phase gate and classify unchanged baseline failures separately from regressions.
