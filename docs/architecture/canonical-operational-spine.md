# Canonical Operational Spine

## Purpose

This document establishes the canonical operational architecture for HelpChain.

Its goals are to:

- define the operational source of truth
- prevent duplicate workflow expansion
- preserve migration safety while legacy compatibility remains in place
- keep the platform generic, multi-tenant, and multi-sector
- guide future roadmap decisions without requiring a platform rewrite

This document is normative for new operational features, reporting logic, SLA logic, risk logic, cockpit logic, and cross-sector expansion work.

## Canonical Operational Spine

HelpChain's canonical operational spine is:

- `Request` = canonical intake object
- `Case` = canonical coordination object
- `Assignment` = canonical operational assignment layer
- `RequestActivity` = canonical request audit trail
- `CaseEvent` = canonical coordination audit trail

These objects form the operational core of the platform.

### Canonical object roles

#### `Request`

`Request` is the canonical intake object.

It represents the entry point of an operational situation, signal, need, or demand. It is the correct foundation for:

- intake
- qualification
- queue visibility
- ownership
- SLA monitoring
- risk enrichment
- operational filtering
- exports
- reporting inputs

`Request` is the authoritative intake-level record for future operational logic.

#### `Case`

`Case` is the canonical coordination object.

It represents the structured handling layer that begins when a request requires organized follow-up, coordination, decision-making, multi-actor handling, or longer-lived operational tracking.

`Case` is the correct foundation for:

- coordinated follow-up
- structured operator workflows
- case-level risk visibility
- case-level ownership
- participant and collaborator logic
- professional orientation
- territorial command views
- institutional reporting

`Case` is not a duplicate of `Request`. It is the coordination layer above intake.

#### `Assignment`

`Assignment` is the canonical operational assignment layer.

It exists to model the assignment of operational handling capacity to a request. It must remain generic enough to support:

- intervenants
- operational staff
- care coordinators
- partner-handling contexts

It must not become a sector-specific artifact.

#### `RequestActivity`

`RequestActivity` is the canonical request audit trail.

It is the authoritative place for request-level operational traceability, including:

- status changes
- ownership changes
- assignment-related actions
- operational notes
- workflow signals used for SLA or audit interpretation

#### `CaseEvent`

`CaseEvent` is the canonical coordination audit trail.

It is the authoritative place for case-level traceability, including:

- coordination notes
- case state transitions
- internal operational events
- structured handling history

## Why `Request` + `Case` Is Structurally Stronger

The `Request` + `Case` model is structurally stronger than a single flat request model because it cleanly separates two distinct concerns:

- intake and queue management
- coordination and longitudinal handling

This separation is important because institutional operations rarely stop at intake. They often require:

- prioritization
- assignment
- follow-up
- handoff
- auditability
- territorial visibility
- executive reporting

`Request` is the correct object for the intake and queue layer.

`Case` is the correct object for the coordination and handling layer.

This structure is stronger because it:

- avoids overloading intake records with all downstream coordination semantics
- supports richer operational cockpit logic
- allows different maturity levels of handling without losing traceability
- scales better across sectors than a single workflow object
- makes future federation, referral, and case-based analytics safer

## Compatibility Layer

`SocialRequest` is now compatibility-only.

This means:

- it may remain present for legacy flows
- it may remain readable and operable while migration-safe transitions are prepared
- it may receive safety fixes and compatibility maintenance
- it must not define future operational architecture

### Compatibility rules

`SocialRequest` must not receive new operational intelligence features.

`SocialRequest` must not become the source of truth for:

- reporting
- SLA logic
- risk logic
- cockpit logic
- operational recommendations
- executive dashboards
- territorial pressure logic

`SocialRequest` must not become the reference model for future operational semantics.

It is a compatibility surface, not a strategic spine.

### Why compatibility preservation matters

Compatibility preservation matters because:

- existing flows may still depend on `SocialRequest`
- existing tests may still validate legacy behavior
- existing users may still reach legacy routes
- abrupt removal would create operational and migration risk

Preserving compatibility does not mean preserving architectural authority.

The platform should keep legacy flows stable while clearly refusing to grow them into a second operational core.

## Operational Primitives

The operational spine must support reusable primitives that remain generic across sectors.

Examples include:

- intake qualification
- ownership assignment
- operational assignment
- inactivity detection
- SLA breach detection
- stale coordination detection
- risk visibility
- workload pressure
- territorial pressure
- handoff and referral visibility
- auditability
- operational exportability

These primitives must stay abstract enough to work across institutional contexts.

They are intentionally broader than any single domain such as social action, medico-social coordination, or local public service operations.

## Multi-Tenant Direction

HelpChain must remain multi-tenant by design.

The canonical operational spine must support structure-scoped operational execution without duplicating the core architecture per tenant type.

The direction is:

- `Structure` provides tenant context
- `Request` and `Case` remain structure-aware
- assignment, reporting, cockpit, and audit layers remain tenant-safe
- cross-organization or federation behavior is built above the spine, not by forking it

The platform should scale to:

- CMPP organizations
- CCAS organizations
- associations
- multi-site organizations
- federations

These are deployment contexts, not core data-model specializations.

## Sector-Pack Philosophy

HelpChain remains a generic multi-sector coordination platform.

The core must not hardcode a sector-specific worldview.

Instead, sector-specific needs should be expressed through configurable overlays, vocabulary packs, workflow packs, reporting packs, and operational presets layered on top of the canonical spine.

Examples:

- a CMPP deployment may need vocabulary around care pathways, waiting lists, and multidisciplinary coordination
- a CCAS deployment may need vocabulary around local orientation, public-service routing, and social urgency
- an association deployment may need lighter coordination and volunteer-heavy workflows
- a federation deployment may need stronger inter-organization routing and visibility

These are valid sector-pack examples, but they must not redefine the core architecture.

The core remains:

- generic
- reusable
- multi-tenant
- institution-ready

## Migration Safety Principles

Canonicalization must remain migration-safe.

This means:

- no platform rewrite is required
- compatibility layers may remain temporarily
- legacy routes may continue to exist during transition
- legacy data may remain readable
- new features must land on canonical objects, not legacy ones

### Migration safety rules

- preserve runtime stability first
- preserve legacy readability where needed
- avoid destructive or premature removals
- move strategic logic toward `Request` and `Case`
- treat `SocialRequest` as frozen in scope
- protect the transition with tests

Migration safety is not permission for continued duplication. It is a controlled path away from duplication.

## Runtime Truth Rules

The following rules define runtime truth for future development.

### Rule 1

All new operational intelligence must attach to the canonical spine.

This includes:

- SLA logic
- risk scoring
- operational recommendations
- command cockpit logic
- reporting logic
- territorial intelligence
- assignment intelligence

### Rule 2

`Request` is the intake source of truth.

If a new feature needs an intake-level operational object, it must use `Request`.

### Rule 3

`Case` is the coordination source of truth.

If a new feature needs longitudinal handling, coordinated follow-up, or structured operational progression, it must use `Case`.

### Rule 4

`SocialRequest` must not define future lifecycle semantics.

Its vocabulary, workflow shape, and legacy actor model must not be copied into new architecture.

### Rule 5

Reporting, SLA, risk, and cockpit logic must not be implemented on `SocialRequest`.

### Rule 6

New status vocabularies are forbidden unless governance is explicitly updated for the canonical spine.

### Rule 7

Duplicate operational concepts are forbidden.

Examples of forbidden duplication include:

- a second active intake source of truth
- a second coordination truth model
- a second assignment truth model
- a second reporting base for the same operational layer

## Explicit Prohibitions

The following are explicitly forbidden in future core architecture work:

- sector hardcoding in the operational core
- duplicate operational concepts
- new status vocabularies outside canonical governance
- new reporting logic on `SocialRequest`
- new SLA logic on `SocialRequest`
- new risk logic on `SocialRequest`
- new cockpit logic on `SocialRequest`
- turning sector examples into core model specializations

## Future Roadmap Alignment

Future roadmap work must deepen the canonical spine rather than fragment it.

Priority-aligned expansion should focus on:

- strengthening `Request` intake quality
- strengthening `Case` coordination depth
- improving assignment and workload visibility
- improving auditability and governance
- improving structure-safe reporting
- improving territorial and operational cockpit layers
- enabling configurable sector packs without changing the core spine

The roadmap should support institutional growth in France-first contexts while keeping the architecture exportable across sectors.

That includes credible support for:

- CMPP operations
- CCAS operations
- association operations
- federation and partner-network operations

The platform remains generic because the same canonical spine can support each of these contexts through configuration and sector packs rather than hardcoded forks.

## Summary

HelpChain's canonical operational future is:

- `Request` for intake
- `Case` for coordination
- `Assignment` for operational assignment
- `RequestActivity` for request traceability
- `CaseEvent` for coordination traceability

`SocialRequest` remains compatibility-only.

The platform must preserve compatibility where necessary, but all new operational authority must move through the canonical spine.

This preserves:

- migration safety
- architectural clarity
- multi-tenant direction
- multi-sector reuse
- institutional scalability
