Lifecycle stages
1. Intake
A request enters the system through a public form, admin creation, import, demo seed, or integration source.
Typical data captured:


requester information


title and description


category


city or territory


urgency / priority


risk indicators


source channel


structure scope


2. Qualification
The request is reviewed by an admin or operator.
The goal is to confirm:


whether the request is valid


which structure/service owns it


what level of urgency applies


whether a case should be created


whether a responsible operator must be assigned


3. Ownership
A request should have a clear operational owner when it requires follow-up.
Ownership can be represented by:


owner_id


owned_at


assigned operator/admin


linked service or structure


Unowned requests are considered operational risk when they remain open for too long.
4. Prioritization
Requests may be prioritized using:


explicit priority


risk score


risk level


age


lack of owner


lack of recent activity


failed notification signals


category sensitivity


Priority is used to drive dashboards, queues, and operational attention.
5. Case conversion
A request may become a case when it requires structured follow-up.
A case is used for:


longer operational tracking


multi-step follow-up


intervention coordination


cross-service collaboration


auditability


The request remains the original intake object. The case becomes the structured operational container.
6. Assignment and intervention
Depending on the situation, a request/case can be assigned to:


admin user


operator


internal service


volunteer


professional/intervenant


structure-level responsible person


Assignments should remain auditable.
7. Activity tracking
Meaningful activity should be recorded when:


ownership changes


status changes


assignment changes


notification is sent


intervention is attempted


note/comment is added


escalation occurs


closure is completed


Activity timestamps are used for SLA, stale detection, and operational dashboards.
8. Closure
A request can be completed or closed when the operational need has been handled or no further action is required.
Closure should include enough context to understand:


what happened


who handled it


when it was resolved


whether follow-up remains necessary


9. Archival
Archived requests are removed from active queues while remaining available for history, reporting, and audit.
Status model
StatusMeaningnewRequest received but not yet fully qualifiedpendingWaiting for review, validation, or missing informationapprovedValidated and accepted for operational handlingassignedAssigned to a responsible actorin_progressActive follow-up is underwaycompletedOperational need has been resolvedclosedNo further active action is requiredrejectedRequest was not accepted for handlingarchivedRemoved from active operational queues
Operational risk signals
A request may require attention when:


no owner is assigned


no recent activity exists


priority is high or critical


risk score is above threshold


notification delivery failed


volunteer/intervenant has not seen or acted


SLA threshold is exceeded


Dashboard implications
Request lifecycle data feeds:


admin dashboard


operator workspace


request queue


case queue


risk panel


SLA indicators


notification monitoring


reporting and impact metrics


## Product principle
The lifecycle must stay simple enough for daily operators, but structured enough for institutional audit, tenant isolation, and operational governance.


