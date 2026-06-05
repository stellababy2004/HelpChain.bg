# HelpChain Real Estate Terminology Layer

## Purpose

This document defines a presentation-only terminology layer for real estate usage.

HelpChain remains an operational coordination platform. The backend engine, models, routes, and request lifecycle stay canonical.

Real estate is a display and demo vocabulary layer, not a separate domain engine.

## Canonical Rule

- `Request` stays `Request` in backend code.
- `Structure` stays `Structure` in backend code.
- `Assignment` stays `Assignment` in backend code.
- `Referral` stays `Referral` in backend code.
- The coordination engine stays platform-first.

Use real estate wording only in:

- homepage and public positioning
- demo narratives
- pilot conversations
- optional admin/demo labels where a presentation layer is clearly safe

Do not rename:

- models
- database fields
- migrations
- routes
- request lifecycle states

## Canonical Mapping

| HelpChain canonical term | Real estate display term | When to use it | Notes |
| --- | --- | --- | --- |
| `Request` | `Demande` / `Lead operationnel` | Public pages, demo wording, pilot narratives | Prefer `Demande` by default. Use `Lead operationnel` only when the inbound opportunity context matters. |
| `Structure` | `Agence` / `Bureau` | Public vertical messaging, demo examples | Use `Agence` for network language, `Bureau` for local responsibility. |
| `Assignment` | `Attribution` / `Responsable` | UI labels, scenario wording, dashboard explanation | Prefer `Responsable` when the owner must be visible. |
| `Referral` | `Relais inter-agences` | Cross-office coordination, transfer scenarios | Keep traceability language; do not imply sales handoff. |
| `Stale follow-up` | `Relance sans activite` | Operational cockpit, alerts, pilot messaging | Use for inactivity detection, not conversion pressure. |
| `Territory` | `Secteur` / `Zone` | Coverage, routing, pressure visibility | Use `Secteur` in network framing, `Zone` in field pressure framing. |
| `Dashboard` | `Cockpit operationnel` | Public and demo positioning | Keep the emphasis on execution visibility. |
| `Owner` | `Responsable` | Admin/demo labels, cockpit wording | Best fit for accountable follow-up. |
| `Queue` | `File de traitement` | Operational overview, demo layer | Useful for inbound coordination language. |
| `Case follow-up` | `Suivi operationnel` | Product explanation, pilot narratives | Avoid legal or social-case overtones in real estate mode. |

## Preferred Wording

Use these terms when presenting HelpChain to real estate networks:

- `routage des demandes`
- `responsabilite visible`
- `suivi operationnel`
- `relance sans activite`
- `relais inter-agences`
- `pression territoriale`
- `visibilite d'execution`
- `direction reseau`
- `bureau responsable`
- `agence locale`
- `continuite de traitement`

## Forbidden CRM Terms

Do not use the following terms in real estate presentation layers:

- `deal`
- `pipeline commercial`
- `closing`
- `prevision de chiffre d'affaires`
- `compte client 360`
- `automation marketing`
- `tunnel de conversion`
- `opportunite de vente`

These terms shift HelpChain toward CRM positioning and break the coordination-first architecture.

## Demo Vocabulary Examples

Use examples like these in safe demo copy only:

- `Agence Boulogne Centre`
- `Agence Paris Ouest`
- `Demande acquereur entrante`
- `Relais inter-agences`
- `Relance sans activite 72h`
- `Secteur sous pression`
- `Bureau responsable non confirme`
- `Direction reseau`

## Public Messaging Guardrails

When presenting the real estate vertical:

- describe inbound coordination, not sales management
- describe routing and responsibility, not pipeline stages
- describe stale follow-up, not lead scoring
- describe inter-agency relay, not revenue attribution
- describe territory pressure, not commercial forecasting

## Implementation Guidance

Current safest implementation path:

1. Keep canonical backend terms in Python and data models.
2. Adapt terminology in public templates and demo copy only.
3. Introduce runtime term maps only after a dedicated audit confirms that template-level injection will not create ambiguity across sectors.

For now, this document is the canonical terminology reference for real estate demo readiness.
