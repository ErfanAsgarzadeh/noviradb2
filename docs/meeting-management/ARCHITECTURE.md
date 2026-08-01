# Meeting Management Architecture

Last updated: 2026-08-01

## Backend Module

New Django app: `meeting_management`.

Primary packages:

- `models.py`: durable domain records and constraints.
- `services.py`: state transitions and workflow commands.
- `selectors.py`: query helpers and reporting aggregates.
- `permissions.py`: queryset and object-level authorization.
- `serializers.py`: DRF request/response validation.
- `views.py`: viewsets and explicit action endpoints.
- `notifications.py`: idempotent notification/escalation generation.
- `admin.py`: initial operational admin registration.
- `management/commands/generate_meeting_notifications.py`: deterministic notification run.

Phase 1 implemented the governance, meeting, participation, agenda, and minutes-version model layer. Phase 2 added the resolution/action execution chain, workflow services, audit calls, notifications, and deterministic notification command. Phase 3 exposed the DRF API and reporting selectors. Phases 4 through 6 added the frontend application shell, operational panels, reporting dashboards, export, and printable minutes.

## ERD-Level Model Groups

Governance:

- `MeetingType`
- `Committee`
- `CommitteeMembership`
- `MeetingSeries`
- `ApprovalWorkflow`
- `ApprovalWorkflowStep`
- `ReminderEscalationPolicy`

Meeting lifecycle:

- `Meeting`
- `MeetingUnitInvitation`
- `MeetingParticipant`
- `MeetingAgendaItem`
- `MeetingMinutesVersion`

Resolution chain:

- `MeetingDecision`
- `Resolution`
- `ResolutionAction`
- `ResolutionActionRoleAssignment`
- `ActionDependency`
- `ActionAssignmentHistory`
- `ActionDeadlineChangeRequest`

Execution and review:

- `ActionProgressReport`
- `ActionEvidenceAttachment`
- `ActionCompletionSubmission`
- `ApprovalWorkflowInstance`
- `ApprovalWorkflowTask`

Notifications and audit support:

- `MeetingNotification`
- Existing `AuditEvent` through `auditlog.services.log_event`.

External links:

- User/person: `CustomUser.CustomUser`
- Organizational accountability: `CustomUser.OrgUnit`
- Project: `ktcPlanning.Project`
- Stable task link: `ktcPlanning.Task`

## Meeting State Transitions

| From | Action | To |
| --- | --- | --- |
| `draft` | schedule | `scheduled` |
| `scheduled` | mark held | `held` |
| `held` | start minutes | `minutes_drafting` |
| `minutes_drafting` | submit minutes | `in_review` |
| `in_review` | approve minutes | `approved` |
| `in_review` | request revision | `minutes_drafting` |
| `approved` | archive | `archived` |
| `draft`, `scheduled`, `held`, `minutes_drafting`, `in_review` | cancel | `cancelled` |

## Action State Transitions

| From | Action | To |
| --- | --- | --- |
| `draft` | activate | `not_started` |
| `not_started` | submit progress | `in_progress` |
| `in_progress` | submit progress 100 | `ready_for_review` |
| `not_started`, `in_progress` | submit completion | `submitted_for_review` |
| `submitted_for_review` | accept | `accepted` |
| `submitted_for_review` | request revision | `revision_requested` |
| `revision_requested` | resubmit completion | `submitted_for_review` |
| `accepted` | close | `closed` |
| active states | suspend | `suspended` |
| active states | cancel | `cancelled` |

Responsible users cannot directly move actions to `closed`; an accepted completion submission is required.

## Permission Matrix

| Actor | Meeting | Minutes | Actions | Reports |
| --- | --- | --- | --- | --- |
| Company admin | Full | Full | Full | Full |
| Organizer/chair/secretary | Manage before approval | Draft/submit depending role | Create resolution/action from meeting | Meeting scope |
| Participant | View authorized | View approved/authorized | View linked authorized actions | Own visible rows |
| Confidential participant | Explicit view only | Explicit view only | Explicit view only | Explicit visible rows |
| Unit manager | Unit-visible | Unit-visible | Manage owned-unit commitments | Unit dashboard |
| Accountable user | View/update accountability fields allowed by service | View linked authorized | Update allowed fields; request deadline change | Own/assigned |
| Responsible user | View | View linked authorized | Submit progress/completion | Own |
| Reviewer/approver | Review assigned | Review assigned | Review assigned completion/deadline | Review queues |
| Project manager | Project-visible | Project-visible | Project-linked visible | Project reports |
| Unrelated/anonymous | None | None | None | None |

## Performance Notes

- Index meeting date/status/type/project/unit/confidentiality.
- Index action status, priority, due dates, accountable unit/user, responsible user, reviewer, and project/task links.
- Dashboard selectors must aggregate in the database and avoid row-by-row permission or KPI queries.
- API list endpoints use filtered querysets with `select_related()` for common meeting/action relations and object visibility is applied before detail responses and aggregate reports.
