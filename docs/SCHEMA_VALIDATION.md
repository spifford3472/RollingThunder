# RollingThunder Schema Validation  
**Authoritative Rules & Guarantees**

This document defines the **validation rules and invariants** for the
RollingThunder configuration schema.

It exists to ensure that:
- configuration errors are caught early
- architectural intent remains enforceable
- future changes do not silently introduce drift

This document describes **what must be true**, not how validation is implemented.

If configuration violates these rules, the configuration is invalid —
even if the system appears to run.

---

## 1. Scope of Validation

Validation applies to the following configuration artifacts:

- `config/app.json`
- `config/pages/*.json`
- `config/panels/*.json`

Validation is **structural and semantic**, not stylistic.

---

## 2. Global Validation Principles

1. **Fail fast**  
   Invalid configuration must be detected before runtime behavior diverges.

2. **Explicit over implicit**  
   Missing references are errors, not defaults.

3. **Forward compatibility**  
   Unknown fields are allowed, but unknown *references* are not.

4. **Determinism**  
   The same configuration must always result in the same system behavior.

---

## 3. `app.json` Validation Rules

### 3.1 Schema Block

- `schema.id` **must exist** and be non-empty
- `schema.version` **must exist** and be semantic (`MAJOR.MINOR.PATCH`)
- `schema.compat.allowUnknownFields` **must be true**

If schema compatibility fails, the configuration must not load.

---

### 3.2 Globals

- Required top-level blocks:
  - `globals.time`
  - `globals.state`
  - `globals.bus`
  - `globals.api`
- `globals.state.namespace` must be a non-empty string
- Global values must be primitives or objects (no arrays at top level)

Globals define defaults only; no globals may encode device-specific behavior.

---

### 3.3 Services Catalog

For each service entry:

#### Identity
- `id` must exist and match the map key
- Service IDs must be unique
- Service IDs must be lowercase and underscore-separated

#### Scope
- `scope` must be one of:
  - `always_on`
  - `page_scoped`

#### Ownership
- `ownerNode` must be one of:
  - `rt-controller`
  - `rt-radio`
  - `rt-display`
  - `external`

#### Lifecycle
- `lifecycle.startPolicy` must exist
- `lifecycle.stopPolicy` must exist
- `restartPolicy.mode` must be defined if restartPolicy exists

#### Dependencies
- All `dependsOn` entries must reference valid service IDs
- Circular dependencies are invalid

#### Health
- `health.type` must be defined
- `health.target` must exist
- `staleAfterMs` must be a positive integer if present

---

## 4. Include Semantics Validation

For include directives such as:

```json
"pages":  { "include": ["config/pages/*.json"] },
"panels": { "include": ["config/panels/*.json"] }
```
Rules:
1. Each included file must define exactly one object
2. That object must contain an id
3.IDs must be globally unique within their domain
4. File name should match the id
5. Include order does not imply display or execution order
6. Duplicate IDs are a hard error.

## 5. Panel Validation (`config/panels/*.json`) ##

For each panel file:

**Required fields**
- `id`
- `type`
- `focusable`
- `bindings`

**Bindings**
- Each binding must define a `source`
- `source` must be one of:
   - `state`
   - `api`
   - `bus`
- State bindings must define `key`
- API bindings must define `url`
- Bus bindings must define `topic`

**Actions**
- `actions.intent` values must exist in `docs/INTENTS.md`
- `params` must be a JSON object if present

**Panel invariants**
- Panels must not reference services
- Panels must not define navigation behavior
- Panels must not contain executable logic

## 6. Page Validation (`config/pages/*.json`) ##

For each page file:

**Required fields**
- `id`
- `order`
- `title`
- `layout`
- `requires`
- `optional`
- `controls.allowedIntents`
- `focusPolicy`

**Layout rules**
- `layout.top` must be an array
- `layout.middle` must be an array of arrays
- `layout.bottom` must be an array
- `layout.middle must` contain 1–3 columns
- Each column must reference valid panel IDs

**Services**
- All `requires` service IDs must exist in `app.json`
- All `optional` service IDs must exist in `app.json`
- A service may not appear in both `requires` and `optional`

**Controls**
- All intents listed in `controls.allowedIntents`
must exist in `docs/INTENTS.md`

**Focus policy**
- `defaultPanel` must exist in the page layout
- All entries in `rotation` must exist in the page layout

## 7. Cross-File Validation Rules ##

All of the following must be true:
1. Every referenced panel ID exists
2. Every referenced service ID exists
3. Every referenced intent exists
4. Page `order` values are unique integers
5. No service dependency cycles exist
6. No panel ID appears twice in the same layout column
---
## 8. Warning-Level Conditions (Non-Fatal) ##

The following should generate warnings but not block startup:
- Panels that are never referenced by any page
- Services that are never required or optional on any page
- Pages with empty middle layouts
- Panels with no actions and no bindings beyond static display
Warnings should be logged but not prevent operation.

## 9. Evolution Rules ##

- Validation rules may be extended in future schema versions
- Existing rules must not be weakened silently
- Any relaxation of rules requires:
    - architecture discussion
    - versioned documentation update

## 10. Non-Negotiable Invariants ##

1. Configuration must be self-consistent
2. References must be explicit and resolvable
3. IDs are stable and never reused
4. Unknown fields are allowed
5. Unknown references are not

If a configuration passes validation, it must be safe to load.
---

### 11. What Validation Guarantees ###

If configuration passes all validation rules:
- The controller can determine service lifecycles deterministically
- The UI can render pages without guesswork
- Safety constraints are enforceable
- Control paths are auditable
- Architectural drift is detectable