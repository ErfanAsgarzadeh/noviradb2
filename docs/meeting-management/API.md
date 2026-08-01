# Meeting Management API

Last updated: 2026-08-01

Base namespace: `/api/meetings/`

Routing will preserve optional trailing slash compatibility.

## Resource Inventory

- `meeting-types/`
- `committees/`
- `committee-memberships/`
- `meeting-series/`
- `meetings/`
- `unit-invitations/`
- `participants/`
- `agenda-items/`
- `minute-versions/`
- `decisions/`
- `resolutions/`
- `actions/`
- `role-assignments/`
- `dependencies/`
- `progress-reports/`
- `completion-submissions/`
- `deadline-change-requests/`
- `approval-workflows/`
- `workflow-instances/`
- `notifications/`
- `reports/individual/`
- `reports/unit/`
- `reports/executive/`
- `reports/secretariat/`

## Command Endpoints

- `POST /api/meetings/meetings/{id}/schedule/`
- `POST /api/meetings/meetings/{id}/mark-held/`
- `POST /api/meetings/meetings/{id}/start-minutes/`
- `POST /api/meetings/meetings/{id}/submit-minutes/`
- `POST /api/meetings/meetings/{id}/cancel/`
- `POST /api/meetings/meetings/{id}/archive/`
- `POST /api/meetings/minute-versions/{id}/approve/`
- `POST /api/meetings/minute-versions/{id}/request-revision/`
- `POST /api/meetings/actions/{id}/submit-progress/`
- `POST /api/meetings/actions/{id}/submit-completion/`
- `POST /api/meetings/actions/{id}/convert-to-project-task/`
- `POST /api/meetings/completion-submissions/{id}/accept/`
- `POST /api/meetings/completion-submissions/{id}/request-revision/`
- `POST /api/meetings/completion-submissions/{id}/reject/`
- `POST /api/meetings/deadline-change-requests/{id}/approve/`
- `POST /api/meetings/deadline-change-requests/{id}/reject/`

Generic status mutation is not allowed.

## Common Filters

- `search`
- `status`
- `meeting_type`
- `committee`
- `project`
- `owning_unit`
- `user`
- `priority`
- `confidentiality`
- `date_from`
- `date_to`
- `due_from`
- `due_to`
- `overdue`
- `blocked`
- `missing_report`

## Error Contract

- Validation errors use DRF field-level errors where possible.
- Invalid workflow transitions return HTTP 400 with a stable `detail`.
- Unauthorized object access returns 404 when returning 403 would reveal object existence.
- Authenticated but forbidden global operations return 403.
