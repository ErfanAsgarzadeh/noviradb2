# Meeting Management Implementation Plan

Last updated: 2026-08-01

## Current Phase

Phase 1 - Backend domain foundation: completed; Phase 2 starting.

## Repository Map

- Backend: `D:\KTCPLANNING\DjangoProject4`, branch `feature/enterprise-meeting-management`.
- Frontend: `D:\KTCPLANNING\FRONT\ktcproject`, branch `feature/enterprise-meeting-management`.
- Backend apps in use: `CustomUser`, `ktcPlanning`, `auditlog`, `enterprise_items`, `opc`, `management_reports`, `release_readiness`.
- Frontend App Router routes are under `app/`; shared authenticated Axios client is `lib/api.tsx`; navigation is `components/navbar.tsx`.
- Applicable guidance: `D:\KTCPLANNING\FRONT\ktcproject\AGENTS.md` requires reading local Next.js 16 docs under `node_modules/next/dist/docs/` before framework-sensitive frontend changes.

## Ordered Tasks And Acceptance Criteria

1. Phase 0: Record baseline, architecture, transition tables, permission matrix, API inventory, test plan, and risks before feature code.
2. Phase 1: Create Django app `meeting_management`; add governance, meeting, participant, agenda, and minutes foundation models, admin, migrations, factories, and model tests.
3. Phase 2: Add decisions, resolutions, actions, RACI assignments, dependencies, progress, completion, deadline changes, workflow services, audit, notifications, and management commands.
4. Phase 3: Add DRF serializers, routers under `/api/meetings/`, object permissions, filters, reporting endpoints, and API tests.
5. Phase 4: Add frontend API/types/navigation/page shell/admin pages using existing App Router and design patterns.
6. Phase 5: Complete operational workflows for minutes, resolutions, actions, progress, completion, deadline changes, dependencies, notification inbox, and history.
7. Phase 6: Add dashboards, drill-downs, exports, printable minutes, and KPI explanations.
8. Phase 7: Run full regression, migration rehearsals, permission/security review, performance/accessibility review, cleanup, docs, and final matrix.

Acceptance criteria:

- No feature code is modified before baseline and plan are recorded.
- Business state changes use explicit service functions, not direct status mutation.
- Approved minute versions are immutable; corrections create new versions.
- Backend object-level permissions prevent unauthorized detail, list, aggregate, export, and attachment inference.
- Frontend navigation uses `allowedPages` only for UI visibility; backend remains authoritative.
- Existing `CustomUser`, `OrgUnit`, `Project`, stable `Task`, `apiClient`, route conventions, and `auditlog.services.log_event` are reused.

## Architecture Decisions

- Canonical docs live in the backend repository under `docs/meeting-management/`.
- The new backend app will be `meeting_management`, mounted at `/api/meetings/`.
- Use an app-local optional-slash DRF router to preserve trailing-slash compatibility.
- Complex reads will be selectors/query helpers; commands will be service functions with `transaction.atomic()`.
- Concurrency-sensitive transitions will lock rows with `select_for_update()` where supported.
- Notifications and escalations will be deterministic service functions plus idempotent management commands, not Celery.
- Attachments will use existing Django storage settings; confidential access checks are mandatory before metadata or content is returned.
- Frontend will add small route-level pages that compose shared domain components, rather than one large page file.

## Data Migration Strategy

- Add only new migrations for `meeting_management`.
- Do not alter existing migrations.
- Initial migration creates configuration, meeting, minutes, resolution/action, evidence, workflow, notification, and history tables with constraints and indexes.
- Fresh SQLite migration rehearsal is required in each backend phase.
- PostgreSQL validation is optional only when a disposable PostgreSQL service is safely available.

## Security And Permission Model

- `company_admin`: full administrative access.
- Organizer/chairperson/secretary: meeting setup and minutes workflow rights until approval, according to status.
- Participant: view authorized non-confidential content and explicitly authorized confidential content.
- Unit manager: manage commitments owned by managed units within policy.
- Accountable/responsible users: view assigned actions, submit progress/completion where allowed.
- Reviewer/approver: review only assigned items.
- Project manager: visibility for project-linked meeting content according to existing project access rules.
- Unrelated and anonymous users: no access and no inference through 404/filtered querysets.

## Test Strategy

- Backend test package: `tests/meeting_management/`.
- Cover models, services, state transitions, permissions, API, progress/completion, deadline changes, dependencies, audit, notifications, and reporting.
- Frontend validation: lint, `tsc --noEmit`, build, targeted component tests where practical, and Playwright workflows when credentials/services are available.
- Query-count tests will be added for major list/dashboard endpoints where practical.

## Risks And Mitigations

- Dirty worktrees contain extensive unrelated changes: touch only meeting-management files and targeted integration points.
- Existing backend full suite has a SQLite concurrency failure: classify as baseline until this work affects it.
- Existing frontend full lint/type/build failures block clean frontend gates: maintain scoped validations for new files and rerun full commands to detect net-new failures.
- Authenticated Playwright credentials are unavailable: add tests but record execution blocker until `NOVIRA_E2E_PASSWORD` is provided.
- Large scope may exceed a single safe implementation slice: keep phase-scoped diffs and document deferred items explicitly.

## Deferred Items

- Production deployment, push, merge, production data access, and secret rotation are deferred by user instruction.
- PostgreSQL validation is deferred unless a disposable local service is discovered and safe to use.
