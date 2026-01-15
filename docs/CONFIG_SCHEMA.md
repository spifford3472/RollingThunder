# RollingThunder Configuration Schema #

## Authoritative Reference ##

This document explains the structure, intent, and invariants of the
RollingThunder configuration system as defined in `config/app.json`.

It exists to ensure that future changes remain **intentional, explainable,**
**and architecture-aligned,** even when context is lost.

This document describes **what the configuration means**, not how it is
implemented.

## 1. Purpose of the Configuration System ##

The RollingThunder configuration system is designed to:
- Describe system behavior **declaratively**
- Allow new pages, panels, and services without modifying controller code
- Ensure deterministic startup, shutdown, and failure handling
- Prevent architectural drift through explicit constraints

The configuration is the **contract** between:
- the controller
- the UI
- hardware appliances
- secondary control paths (Meshtastic)

If behavior is not explainable from configuration, it is considered a defect.

## 2. Configuration Files and Ownership ##
### 2.1 Canonical Configuration ###

**File:** `config/app.json`
**Role:** System constitution
**Scope:** Global

`app.json` defines:
  - schema versioning
  - global defaults
  - service catalog
  - lifecycle rules
  - alert normalization
  - input intent mapping

It does not define:
  - UI layout details
  - page-specific panel composition
  - rendering logic

Those live in separate files.

### 2.2 Modular Configuration (Pages & Panels) ###

Pages and panels are defined in separate directories:
```
{
config/
├── app.json
├── pages/
│   └── *.json
└── panels/
    └── *.json
}
```

This allows:
- adding a page by adding a file
- reuse of panels across pages
- clean diffs and version history
`app.json` references these definitions logically; it does not embed them.

### Include Semantics

The `pages` and `panels` sections of `app.json` may use an `include` directive.

Example:
```
{
"pages":  { "include": ["config/pages/*.json"] },
"panels": { "include": ["config/panels/*.json"] }
}
```

Rules:
- Each included file must define exactly one object with a unique id
- File name **SHOULD** match the declared id
- Duplicate IDs across files are invalid
- Include order does not imply display order

(page ordering is controlled solely by the order field)

Unknown fields inside included files must be ignored (forward compatibility)

The include mechanism is a configuration concern only.
It must not introduce execution logic or side effects.

### 3. Top-Level Structure of `app.json` ###

The configuration is divided into **catalogs**, not logic blocks.

Top-level keys:
- schema
- deploymentDefaults
- globals
- services
- inputs
- alerts
- meta

Each section has a single responsibility.

### 4. Schema Versioning (schema) ###

The `schema` block defines the **format**, not the software release.

**Responsibilities**
- Identify the schema
- Declare compatibility rules
- Enable forward compatibility

**Invariants**
- Unknown fields must be ignored
- Schema versions are semantic
- Backward compatibility is explicit

This prevents configuration breakage when features are added.

### 5. Deployment Defaults (`deploymentDefaults`) ###

Defines fleet-level defaults applied to all nodes unless overridden.

**Examples:** 
- fleet name
- environment (vehicle, bench, lab)

This allows the same config to be reused across nodes without duplication.

### 6. Global Settings (`globals`) ###

Defines system-wide behavior and defaults.

**Typical contents**
- Time authority (GPS vs system clock)
- Redis location and namespace
- MQTT broker location
- API polling defaults
- Safety and driving constraints
- Default timeouts and retry policies

**Invariants**
- Globals define policy, not features
- Globals never hardcode device-specific logic
- Globals may be referenced by services, but never modified by them

### 7. Services Catalog (`services`) ###

The services catalog defines **everything that can run** in the system.

Each service entry is a **declarative description**, not an execution script.

**Service identity**

Each service has:
- a stable `id`
- a declared `scope`
- an owning node

Service IDs are **never reused**.

### 7.1 Service Scope ###

Services are either:
- `always_on`
  Must run regardless of UI state
  Examples: Redis, MQTT, GPS, logging, NOAA, Meshtastic
- `page_scoped`
  Started and stopped based on active pages
  Examples: radio proxies, visual decoders, integrations

Scope determines lifecycle control, not importance.

### 7.2 Lifecycle Semantics ###

Lifecycle rules describe **when** a service runs, not **how**.

Defined policies include:
- `startPolicy` (on boot, on page entry, manual)
- `stopPolicy` (never, on page exit, reference counted)
- `restartPolicy` (never, on failure, with backoff)

Controllers interpret these rules consistently.

### 7.3 Health Definitions ###

Health checks are declarative assertions that a service is “alive enough.”

Health checks may be based on:
- Redis state freshness
- HTTP endpoints
- process existence
- external API reachability

Health definitions **do not** implement monitoring — they describe expectations.

### 7.4 Provides / Consumes ###

Services explicitly declare:
- what state or events they produce
- what state or services they depend on

This makes dependencies auditable and prevents hidden coupling.

### 8. Inputs and Intents (`inputs`) ###

Physical controls, UI controls, and Meshtastic commands all emit **intents**.

### Key principle ###

> There is exactly one control vocabulary.

Buttons do not “call functions.”
They emit intents such as:
- `ui.page.next`
- `alert.ack`
- `host.status`

This guarantees:
- consistent behavior across control paths
- no bypass of safety rules
- auditable control flow

### 9. Alert Normalization (`alerts`) ###

All alerts are normalized into a common structure regardless of source.

Sources include:
- NOAA SAME
- system health
- radio state
- external integrations

**Alert policy includes:**
- severity levels
- deduplication rules
- routing (UI, logs, Meshtastic)
- acknowledgment behavior
- rate limiting

Alerts are **data**, not UI events.

### 10. Meta Section (`meta`) ###

The `meta` section exists for humans.

It may include:
- notes
- TODOs
- rationale
- migration reminders

The system must ignore this section entirely.

### 11. Non-Negotiable Invariants ###

The following rules must never be violated:
1. Services do not start other services directly
2. Pages do not execute logic
3. Panels do not contain business rules
4. All control paths emit intents
5. Always-on services are minimal
6. External systems are integrated, not absorbed
7. Behavior must be explainable from JSON alone

If a change violates one of these rules, the architecture must be revised explicitly.

### 12. How This Document Is Used ###

This document should be consulted when:
- adding new services
- adding new pages or panels
- introducing new control paths
- debugging unexpected behavior

If behavior cannot be traced back to configuration, either:
- the implementation is wrong, or
- the configuration schema is incomplete

Both are fixable — silently ignoring them is not.