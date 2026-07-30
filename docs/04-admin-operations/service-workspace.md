# Service Workspace

## Purpose

Each `StructureService` has an operational workspace under:

- `GET /admin/structures/<structure_id>/services/<service_id>`
- `POST /admin/structures/<structure_id>/services/<service_id>/workspace`

This workspace extends the existing structure workspace pattern to service-level operations without changing the core admin architecture.

## Editable Operational Data

The service workspace supports editing:

- description
- category
- priority
- risk level
- capacity
- available capacity override
- SLA
- operating hours
- waiting time
- target audience
- eligibility rules
- required documents
- supported languages
- assigned contact
- assigned professionals
- routing rules
- emergency support
- territory

## Security And Scope

The save flow must remain:

- authenticated
- CSRF protected
- restricted to `superadmin` routes
- scoped by both `structure_id` and `service_id`
- tenant-safe for structure-bound admins

Unknown structures or services must not mutate data and must return `404` or `403` according to the existing admin scope rules.

## Operational Refresh

After a successful save, the service page redirects back to the same workspace and the refreshed data must be visible in:

- the service detail page
- structure readiness
- operational intelligence
- capacity metrics
- AI routing preparation inputs

Only stored data may be used. The workspace must not fabricate capacity, readiness, routing signals, or SLA coverage.
