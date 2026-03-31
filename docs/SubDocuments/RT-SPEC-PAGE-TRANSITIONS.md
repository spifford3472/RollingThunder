# RT-SPEC-PAGE-TRANSITIONS.md
## RollingThunder v0.47 — Page Transition Execution & Lifecycle Handling
## Status: Authoritative

---

# 1. Purpose

This document defines the authoritative controller-side behavior for page transitions in RollingThunder.

It specifies how the controller executes page navigation intents and how page transitions affect:

- current page state
- focused panel
- browse state
- modal state
- page-scoped services
- allowed intents
- observability
- recovery behavior

This document strictly follows:

- `INTENTS.md`
- `RT-SPEC-CONTROLLER-INPUT.md`
- `RT-SPEC-PHYSICAL-CONTROL-PANEL.md`
- `RT-SPEC-CONTROL-MAPPING.md`
- `RT-SPEC-LED-STATE-MODEL.md`
- `RT-SPEC-PANEL-INPUT-BRIDGE.md`

This document does **not** define:

- UI rendering
- panel firmware
- electrical or enclosure design
- page visual layout
- browser behavior details beyond controller-owned state

This document defines:

- controller execution rules for `ui.page.next`, `ui.page.prev`, and `ui.page.goto`
- authoritative Redis state mutation rules during page change
- lifecycle sequencing for page-scoped services
- safe transition behavior during modal, degraded, or invalid states
- required bus publication and observability
- deterministic failure and recovery behavior

---

# 2. Core Principles

## 2.1 Page Changes Are Controller-Owned Operating Mode Transitions

A page transition is not merely a UI event.

A page transition is a controller-owned operating mode change that may alter:

- what controls mean
- what intents are allowed
- which services are active
- what the LEDs communicate
- what interaction state is valid

The controller is the sole authority for page transition execution.

## 2.2 Redis Is the Source of Truth

The controller executes the transition, but Redis stores the authoritative post-transition interaction state.

UI and panel layers are renderers and emitters only.

They MUST NOT:

- decide whether the page changed
- preserve old page interaction state independently
- continue using stale permissions after controller state changes

## 2.3 Determinism

Given:

- the same current controller state
- the same config
- the same input intent

The transition result MUST be the same.

No randomness, hidden state, or timing-dependent branching is allowed.

## 2.4 Fail Closed

If a transition cannot be safely completed, the system MUST remain on the current authoritative page and publish a clear rejection or degraded result.

The system MUST NOT silently half-switch into an ambiguous interaction state.

## 2.5 Safety Before Convenience

When modal workflows, destructive confirmation, stale state, or degraded authority exist, navigation must behave conservatively.

It is better to reject a navigation attempt than to leave the operator in an unsafe or unclear control state.

---

# 3. Scope of Page Transition Intents

The controller MUST support these canonical page navigation intents from `INTENTS.md`:

- `ui.page.next`
- `ui.page.prev`
- `ui.page.goto`

These intents are state intents.
They are executed by the controller directly.
They do not bypass validation or lifecycle control.

---

# 4. Page Model

## 4.1 Authoritative Page Definition

A page is a controller-known operating context defined by config.

A page definition MUST provide enough information for the controller to determine:

- page identifier
- page order position
- initial/default focused panel
- page-scoped services
- page-level `allowedIntents`
- whether the page is enabled for navigation

If any of these required properties are missing such that safe execution is impossible, that page MUST be treated as invalid for transition purposes.

## 4.2 Page Identity

The canonical page identity is the configured page ID string.

The page ID is the only authoritative identifier used for:

- `rt:ui:current_page`
- `ui.page.goto`
- page-scoped service association
- page-level intent gating

Display labels or human-readable names MUST NOT be used as controller page keys.

## 4.3 Page Order

Page order MUST be explicit and deterministic.

The controller MUST use a stable ordered list of navigable pages loaded from config.

Rules:

- order MUST be fixed for the loaded config revision
- disabled or invalid pages MUST be excluded from the navigable order
- order MUST NOT depend on Redis state or runtime discovery
- `ui.page.next` and `ui.page.prev` operate only on this ordered page list

## 4.4 Navigable Page Set

The controller MUST maintain an authoritative in-memory navigable page list derived from config at startup or config reload time.

Each entry MUST be validated before use.

If config validation fails globally, page transitions MUST fail closed and existing current page state MUST remain authoritative until valid config is restored.

---

# 5. Intent Semantics

## 5.1 `ui.page.next`

`ui.page.next` requests transition from the current page to the next page in the authoritative ordered navigable list.

Resolution rule:

- find current page index in navigable order
- target = next index if one exists
- if current page is last page, wrap behavior MUST be explicit

### v0.47 wrap rule

For RollingThunder v0.47, page navigation by `next` and `prev` MUST wrap.

- last page + `ui.page.next` → first page
- first page + `ui.page.prev` → last page

Reason:

- physical controls are page-cycling controls
- deterministic wrap is simpler and safer than dead-end behavior in mobile use
- operator does not need to infer whether the end was reached

## 5.2 `ui.page.prev`

`ui.page.prev` requests transition from the current page to the previous page in the authoritative ordered navigable list.

Resolution rule:

- find current page index in navigable order
- target = previous index if one exists
- if current page is first page, wrap to last page

## 5.3 `ui.page.goto`

`ui.page.goto` requests transition to a specific page ID.

Required params:

```json
{
  "page": "<page_id>"
}
```

Rules:

- `page` MUST be a non-empty canonical page ID string
- target page MUST exist in validated page config
- target page MUST be navigable/enabled
- if target equals current page, the controller MUST treat the request as a no-op accepted result unless policy or diagnostics require otherwise

## 5.4 Invalid Target Handling

The controller MUST reject navigation if:

- current page is unknown and target cannot be resolved safely
- target page ID is missing
- target page ID does not exist
- target page is disabled or invalid
- page config is unavailable or internally inconsistent

Rejected navigation MUST:

- produce no semantic page change
- not stop or start services
- not clear existing page state unless explicit recovery policy applies
- emit rejection observability

---

# 6. Redis State Ownership During Page Transitions

## 6.1 Controller-Owned Keys in Scope

The controller is authoritative for the following page-transition-relevant keys:

- `rt:ui:current_page`
- `rt:ui:focused_panel`
- `rt:ui:browse:<panel>`
- `rt:ui:modal`
- `rt:input:last_accepted`
- `rt:input:last_rejected`

The controller may also read page capability and health keys needed for validation or lifecycle handling, but page transition authority remains with the controller.

## 6.2 Required Read Set

At minimum, the controller MUST read:

- `rt:ui:current_page`
- `rt:ui:focused_panel`
- `rt:ui:modal`
- relevant `rt:ui:browse:<panel>` keys for the current page
- controller-held validated page config
- controller-held service/page bindings

## 6.3 Required Write Set

During a successful page transition, the controller MUST write at minimum:

- `rt:ui:current_page`
- `rt:ui:focused_panel`
- `rt:ui:modal`
- all relevant old-page `rt:ui:browse:<panel>` keys that must be cleared
- optional new-page browse keys if explicit empty initialization is used
- `rt:input:last_accepted`
- state change bus publication reflecting the new page state

On rejection, the controller MUST write at minimum:

- `rt:input:last_rejected`
- rejection observability event

## 6.4 Key Reset / Preserve Rules

### `rt:ui:current_page`

- updated only after the transition passes validation and old-page stop policy
- MUST remain unchanged on rejected transition

### `rt:ui:focused_panel`

- MUST be reset on every successful page transition
- MUST be set to the new page’s deterministic default focused panel
- MUST NOT carry forward the old page’s focus

### `rt:ui:browse:<panel>`

- all browse state associated with the old page MUST be cleared on successful page transition
- browse state MUST NOT leak across pages
- browse state for panels not on the new page MUST not remain semantically active
- if browse state keys are materialized for the new page, they MUST start in a cleared/non-browse state

### `rt:ui:modal`

- modal state is not preserved across page transitions
- successful page transition requires modal resolution per Section 10
- after a completed successful transition, `rt:ui:modal` MUST be clear

### Other Page-Scoped Transient State

Any controller-owned transient interaction state tied to the old page MUST be cleared or invalidated before the transition becomes authoritative.

Examples:

- page-local selection caches
- page-local pending action posture
- transient browse cursor state
- page-local hint or orientation flags if stored in controller state

Page-local transient state MUST NOT be reinterpreted on the new page.

---

# 7. Canonical Transition Execution Order

## 7.1 High-Level Rule

Page transitions MUST execute in a strict, safe sequence.

The controller MUST NOT expose the new page as authoritative until the transition has either:

- completed successfully, or
- failed and remained on the old page

## 7.2 Authoritative Transition Sequence

For a valid page navigation request, the controller MUST execute in this order:

1. receive validated page intent
2. snapshot current authoritative transition-relevant state
3. resolve target page deterministically
4. validate target page exists and is navigable
5. validate that navigation is allowed in the current interaction/safety context
6. determine old-page and new-page service sets
7. reject or resolve active modal per modal rules
8. stop old page-scoped services that must not remain active
9. verify stop result and classify failures
10. clear old page transient interaction state
11. write new `rt:ui:current_page`
12. write new `rt:ui:focused_panel`
13. clear `rt:ui:modal`
14. clear old page browse state keys
15. initialize new page interaction state as needed
16. start new page-scoped services
17. verify start result and classify failures
18. publish authoritative state change event(s)
19. publish page transition result event
20. run LED/state derivation from resulting authoritative state
21. finalize observability logs

## 7.3 Why State Writes Occur Before New Service Start Completion

The new page’s allowed intents and interaction posture must become authoritative immediately after the controller commits to the new page.

Therefore:

- stop of old page-scoped services occurs before page state mutation
- new page state mutation occurs before new service start completion reporting
- start failure on the new page does **not** roll the page back automatically

This avoids stale permissions or stale interaction context lingering while services are spinning up.

## 7.4 Partial Completion Policy

A page transition may complete with lifecycle degradation, but not with semantic ambiguity.

Allowed outcome:

- page changes successfully
- old page interaction state is cleared
- new page gating becomes authoritative
- one or more new page services fail to start
- degraded state is published clearly

Forbidden outcome:

- page appears partly old and partly new
- old page permissions remain active after current page changed
- browse/focus/modal state from old page remains live on new page

---

# 8. Detailed Execution Rules

## 8.1 Snapshot Before Mutation

Before mutating transition state, the controller MUST snapshot enough data to support deterministic logging and recovery classification.

Recommended snapshot contents:

- current page ID
- current focused panel
- active modal summary
- active browse keys for old page
- old page service set
- requested navigation intent and params
- controller config revision or page registry revision

This snapshot is for observability and recovery only.
It is not a second source of truth.

## 8.2 Target Resolution

Target page resolution MUST use validated config, not UI hints or panel state.

Resolution steps:

1. obtain current authoritative page ID
2. find current page in navigable order
3. apply intent semantics (`next`, `prev`, or `goto`)
4. resolve target page ID
5. verify target page definition is valid

If the current page is missing from navigable order but exists in Redis, the controller MUST treat this as degraded state and follow failure handling in Section 13.

## 8.3 Same-Page Requests

If target page equals current page:

- no service stop/start is required
- no focus/browse/modal reset is required
- controller SHOULD publish an accepted no-op result for observability
- controller SHOULD NOT emit a misleading full page transition state change

This keeps behavior deterministic without unnecessary churn.

## 8.4 Old Page Service Stop Before State Cutover

All old page-scoped services that are not shared with the new page MUST be stopped before the controller writes the new current page.

Reason:

- the old page should not continue active page-scoped execution once the controller is preparing to leave it
- page exit boundary must be clean

Shared services that are scoped to both old and new pages need not be stopped if their configured identity and operating mode remain valid.

## 8.5 Clear Old Interaction State Before New Focus/Browse Becomes Live

The controller MUST clear or invalidate old page interaction state before the new page is exposed as fully active to downstream readers.

Minimum required reset domain:

- old modal
- old browse state
- old focused panel
- old page-local transient selection state

## 8.6 New Page State Commit

A successful semantic cutover occurs when the controller writes:

- `rt:ui:current_page = <new_page>`
- `rt:ui:focused_panel = <new_default_panel>`
- `rt:ui:modal = clear`
- old browse state cleared

At that moment:

- new page intent gating becomes authoritative
- old page permissions are no longer valid
- UI must render the new page state as truth
- LED derivation must follow the new page state

## 8.7 New Service Start After State Commit

After the new page state becomes authoritative, the controller starts new page-scoped services.

If service start fails:

- page state remains on the new page
- transition result is classified as degraded success
- health/lifecycle failure is published
- allowed intents remain those of the new page, not the old page

---

# 9. Focus and Browse Reset Rules

## 9.1 Focus Reset Is Mandatory

Focused panel MUST NOT persist across page transitions.

On successful page transition:

- controller MUST set focus to the new page’s configured default focused panel
- if the new page lacks a valid focus target, transition MUST fail closed

## 9.2 Focus Target Requirements

The new default focused panel MUST:

- exist on the target page
- be interactable according to config
- be stable and deterministic

The controller MUST NOT choose a focus target heuristically at runtime.

## 9.3 Browse Reset Is Mandatory

Browse is page-local interaction state.

On successful page transition:

- all old-page browse state MUST be cleared
- no old browse cursor/index/position may survive onto the new page
- new page begins not in browse mode unless the page spec explicitly defines a controller-owned initial browse state

Default rule for v0.47:

- new page starts with browse inactive

## 9.4 Browse Position Reset

Any browse position or highlighted index stored in controller state for the old page MUST be invalidated.

The controller MUST NOT attempt to map old indices to new page panels or datasets.

## 9.5 Modal Reset

Modal state MUST NOT survive a successful page transition.

A successful transition implies that:

- the prior modal is resolved or force-cancelled according to modal policy
- `rt:ui:modal` is clear after transition

## 9.6 Transient Page Interaction Reset

Controller-owned transient interaction state associated with the old page MUST be cleared.

Examples include:

- temporary confirmation posture tied to old page
- item highlight pending confirmation
- staged page-local action choice
- page-local orientation cues that no longer apply

---

# 10. Modal and Safety Rules

## 10.1 Modal Priority

Modal state has higher interaction priority than page navigation.

If `rt:ui:modal` is active, the controller MUST evaluate the modal before allowing page transition.

## 10.2 Default Rule: Navigation Is Blocked While Modal Is Active

For v0.47, page transition intents MUST be rejected while a modal is active.

This includes:

- confirmation modals
- destructive confirmation workflows
- transient decision dialogs
- controller-owned error acknowledgment modals

Reason:

- modal semantics already override normal page context
- allowing page transitions around a pending modal creates ambiguity and safety risk
- destructive confirmation must never be bypassed silently

## 10.3 Destructive Confirmation Pending

If a destructive confirmation is pending, page navigation MUST be rejected.

Required behavior:

- current page remains authoritative
- modal remains authoritative
- rejection reason explicitly indicates modal/destructive block
- red/green safety posture remains unchanged

## 10.4 Restricted Contexts

Navigation MUST be rejected in any context where the controller marks page change as unsafe.

Examples:

- shutdown workflow armed
- controller-owned restricted maintenance state
- explicit in-transition lock already active
- unrecoverable config inconsistency

## 10.5 Degraded State Handling

Degraded state does not automatically forbid page navigation.

Rules:

- if controller authority remains intact and safe validation is still possible, page transitions may proceed
- if degraded state undermines authoritative validation or service lifecycle safety, page transitions MUST be rejected

The rejection threshold is not “any error exists.”
The threshold is “controller can no longer guarantee safe deterministic transition.”

## 10.6 Safe Failure Signaling

When navigation is rejected for modal or safety reasons:

- no state mutation occurs
- rejection must be observable
- LEDs should remain truthful to the active modal/safety state
- the system must not imply that page change partly occurred

---

# 11. Service Lifecycle Integration

## 11.1 Page-Scoped Service Model

A page-scoped service is a controller-managed service whose active lifecycle is bound to one or more pages.

Examples may include:

- page-local pollers
- page-local adapters
- page-local workflow helpers

Shared always-on services are not page-scoped and are not started/stopped by page transitions.

## 11.2 Service Set Resolution

For each transition, the controller MUST derive:

- `old_page_services`
- `new_page_services`
- `services_to_stop = old - shared`
- `services_to_start = new - shared`
- `shared_services = old ∩ new`

This resolution MUST be deterministic and config-driven.

## 11.3 Stop Rules

Before page state cutover, the controller MUST stop services in `services_to_stop`.

Stop rules:

- use controller-owned lifecycle manager only
- bounded timeout required
- each stop result must be classified: `stopped`, `already_stopped`, `timeout`, `error`
- repeated stop requests during an in-flight identical transition MUST NOT create duplicate side effects

## 11.4 Start Rules

After new page state cutover, the controller MUST start services in `services_to_start`.

Start rules:

- use controller-owned lifecycle manager only
- bounded timeout required
- each start result must be classified: `started`, `already_running`, `timeout`, `error`
- start order SHOULD be deterministic and stable for the same config

## 11.5 Ordering Requirements

Required ordering:

1. resolve lifecycle delta
2. stop old-only services
3. commit new page interaction state
4. start new-only services
5. publish final result/degraded result

This ordering is authoritative for v0.47.

## 11.6 Shared Services

Services shared across old and new pages SHOULD remain running if no mode change is required.

If a shared service requires page-specific reconfiguration, that reconfiguration MUST be treated explicitly by the service manager and MUST be observable.

The controller MUST NOT silently assume that a shared service reconfigured itself.

## 11.7 Stop Failure Handling

### Default v0.47 rule

Stop failure prevents page transition completion.

If a required old-page service cannot be stopped safely:

- controller MUST reject the page transition
- `rt:ui:current_page` remains old page
- old focus/browse/modal state remains authoritative unless explicit safe cleanup was already completed and is reversible
- lifecycle failure must be published and logged

Reason:

- old page-scoped execution still active means semantic departure from old page is not clean
- fail closed is safer here than forcing cutover

## 11.8 Start Failure Handling

Start failure after new page state commit does **not** automatically revert the page transition.

Required behavior:

- new page remains authoritative
- transition result classified as `completed_degraded`
- failed service start details published
- relevant health/degraded keys updated
- operator awareness should reflect degraded state

Reason:

- old page has already been exited cleanly
- reverting may be more dangerous and less deterministic than remaining on the new page in degraded mode

## 11.9 No Silent Partial Completion

Partial completion is permitted only when explicitly classified as degraded success.

It is never acceptable for the controller to:

- change the page silently while hiding service failures
- fail to stop/start services without observability
- leave service state ambiguous

---

# 12. allowedIntents Gating Across Page Changes

## 12.1 New Page Gating Becomes Authoritative Immediately at State Commit

Once the controller commits the new page state, the new page’s `allowedIntents` MUST become authoritative immediately.

There must be no grace period.

## 12.2 No Stale Page Permissions

After `rt:ui:current_page` is written to the new page:

- old page `allowedIntents` are no longer valid
- validation MUST use new page permissions for subsequent inputs
- cached UI/client assumptions are irrelevant

## 12.3 In-Flight Input Handling

Inputs arriving during transition execution MUST be deterministic.

Required rule for v0.47:

- the controller MUST serialize page transitions and intent validation through a transition lock or equivalent single-authority sequencing
- while a page transition is in progress, subsequent navigation intents MAY be coalesced or rejected, but behavior MUST be explicit and deterministic
- non-navigation intents received during the critical cutover window MUST validate against the authoritative state visible at the moment of validation, not against stale pre-transition assumptions

## 12.4 Recommended v0.47 Policy for Repeated Page Navigation Input

While a page transition is in progress:

- additional `ui.page.next`, `ui.page.prev`, and `ui.page.goto` intents SHOULD be rejected with reason `transition_in_progress`
- they MUST NOT queue indefinitely by default

Reason:

- repeated knob/button navigation during mobile use must not create hidden backlog
- rejecting extra page changes is safer and easier to reason about than buffering multiple future operating mode changes

## 12.5 Post-Transition Validation Boundary

After the state commit boundary, any subsequent intent validation MUST use:

- new `rt:ui:current_page`
- new `rt:ui:focused_panel`
- cleared modal state
- cleared browse state
- new page `allowedIntents`

This boundary is the authoritative permission cutover.

---

# 13. Failure Handling

## 13.1 Failure Categories

Page transition failures MUST be classified clearly.

Required categories:

- `page_target_missing`
- `page_target_invalid`
- `page_current_unknown`
- `page_config_invalid`
- `modal_blocked`
- `safety_blocked`
- `transition_in_progress`
- `service_stop_timeout`
- `service_stop_error`
- `service_start_timeout`
- `service_start_error`
- `state_missing`
- `state_stale`
- `recovery_applied`
- `controller_restart_recovery`

## 13.2 Invalid Page Target

Behavior:

- reject transition
- remain on current page
- emit rejection event
- write `rt:input:last_rejected`
- no lifecycle actions

## 13.3 Page Missing From Config

If target page or current page cannot be found in validated config:

- treat as config/state inconsistency
- do not guess a target
- reject transition
- publish degraded/config error observability

## 13.4 Missing or Stale Redis State

If transition-relevant Redis state is missing:

- controller SHOULD recover from config defaults only when recovery rule is explicit and deterministic
- otherwise reject safely

### Required recovery rule for missing `rt:ui:current_page`

If `rt:ui:current_page` is missing but config is valid and controller is authoritative, the controller MAY restore current page to the configured default startup page and publish `recovery_applied`.

However, this recovery is allowed only when:

- no modal is active
- no in-progress transition marker exists
- controller restart or state-loss recovery path is clearly recognized

## 13.5 Repeated Page Navigation Input

Repeated navigation input during an active transition MUST NOT produce multiple overlapping transitions.

Behavior:

- reject additional page navigation intents with `transition_in_progress`
- log rate-limited diagnostics if needed
- do not create hidden queue by default

## 13.6 Controller Restart During Transition

The system MUST remain recoverable if the controller restarts mid-transition.

Required policy:

- page transition execution MUST be restart-safe
- on controller startup, the controller MUST reconstruct authoritative page state from Redis if valid
- if Redis state is incomplete or inconsistent, controller MUST reconcile to a deterministic safe baseline

### Required reconciliation order after restart

1. validate page config
2. read `rt:ui:current_page`
3. verify page exists and is navigable
4. clear any stale modal that cannot be safely resumed
5. clear page-local browse state if integrity is uncertain
6. set `rt:ui:focused_panel` to valid default if missing or invalid
7. reconcile page-scoped services to match authoritative current page
8. publish recovery state change and diagnostics

The controller MUST prefer a clean safe baseline over attempting to resume an uncertain partial transition.

## 13.7 Service Stop Failure

Behavior:

- reject transition
- old page remains authoritative
- publish lifecycle failure
- do not write new current page

## 13.8 Service Start Failure

Behavior:

- keep new page authoritative
- publish degraded transition completion
- mark relevant health/lifecycle state degraded
- do not revert automatically

## 13.9 Stale or Missing Focus State

If current page is valid but `rt:ui:focused_panel` is missing or invalid:

- controller MAY repair it by writing the page default focused panel
- this is a safe deterministic repair
- publish recovery diagnostics if repair occurred

## 13.10 Stale or Missing Browse State

Missing browse state is not fatal.

Behavior:

- treat as browse inactive
- on page transition, explicitly clear any stale browse keys for the old page if known

## 13.11 Stale or Invalid Modal State

If modal state is malformed or cannot be interpreted safely:

- controller MUST treat it as blocking for navigation until it is cleared or repaired by explicit recovery logic
- silent reinterpretation is forbidden

---

# 14. Observability and Bus Publication

## 14.1 Required Event Coverage

The controller MUST emit observability for:

- accepted page change requests
- rejected page change requests
- page transition completion
- degraded page transition completion
- lifecycle stop failures
- lifecycle start failures
- recovery actions
- state change publication

## 14.2 Recommended Event Topics

Recommended controller bus events include:

- `ui.page.transition.requested`
- `ui.page.transition.rejected`
- `ui.page.transition.completed`
- `ui.page.transition.degraded`
- `ui.page.transition.recovered`
- `state.changed`

These names are recommendations; exact event topic naming may align with existing bus conventions, but semantic coverage is required.

## 14.3 Required Rejection Payload Elements

Rejection observability SHOULD include at minimum:

```json
{
  "ok": false,
  "intent": "ui.page.next",
  "reason": "modal_blocked",
  "current_page": "<page_id>",
  "requested_target": "<page_id|null>",
  "timestamp": "<iso8601>"
}
```

## 14.4 Required Completion Payload Elements

Successful or degraded completion observability SHOULD include at minimum:

```json
{
  "ok": true,
  "result": "completed|completed_degraded",
  "from_page": "<old_page>",
  "to_page": "<new_page>",
  "focused_panel": "<new_default_panel>",
  "services_stopped": ["..."],
  "services_started": ["..."],
  "service_failures": ["..."],
  "timestamp": "<iso8601>"
}
```

## 14.5 State Change Publication

On successful transition, the controller MUST publish state change notification after the new authoritative page state is written.

That publication MUST reflect the committed truth, not the requested target.

## 14.6 Logging Requirements

Diagnostic logging MUST record at minimum:

- requested intent
- current page
- resolved target page
- modal/safety block decision
- service stop/start decisions
- cutover point reached or not reached
- final result classification
- recovery actions if any

Logging MUST be sufficient to reconstruct why a page change did or did not occur.

---

# 15. Recovery Behavior

## 15.1 Transition Lock

The controller SHOULD maintain an internal transition lock or equivalent execution guard.

This lock is implementation detail, but the behavior is authoritative:

- only one page transition may execute at a time
- overlapping transitions are forbidden
- lock acquisition failure causes deterministic rejection of additional navigation requests

## 15.2 Restart Reconciliation

On controller restart, the controller MUST reconcile page-scoped services and interaction state to a single authoritative page.

The controller MUST NOT assume old page services are correct merely because they are running.

It MUST reconcile runtime to state, not state to runtime guesswork.

## 15.3 Safe Baseline Preference

When state is inconsistent after restart or failure, the controller SHOULD prefer:

- valid current page
- valid default focus
- browse cleared
- modal cleared unless explicitly resumable and safe
- page-scoped services matched to current page

This is the safest deterministic recovery posture for mobile operation.

---

# 16. Completion Criteria

A page transition is considered complete and authoritative only when all of the following are true:

1. target page was resolved deterministically
2. target page was validated as existing and navigable
3. modal/safety policy permitted the transition
4. all required old-page services were stopped successfully
5. old page transient interaction state was cleared
6. `rt:ui:current_page` reflects the new page
7. `rt:ui:focused_panel` reflects the new page default focus
8. `rt:ui:modal` is clear
9. old page browse state is cleared
10. new page `allowedIntents` gating is authoritative
11. required state change publication has been emitted
12. completion or degraded-completion observability has been emitted
13. LED derivation can truthfully reflect the new authoritative state

If new-page service start fails after state cutover, the transition may still be considered complete **only** as `completed_degraded`.

A transition is **not** complete if the controller has not crossed the semantic cutover boundary or if old-page service stop failed.

---

# 17. Implementation Guidance Summary

## 17.1 Authoritative Rules to Implement Exactly

- page order is config-defined and deterministic
- `next` and `prev` wrap
- modal blocks navigation
- destructive confirmation blocks navigation
- required old-page service stop failure rejects transition
- new-page service start failure produces degraded completion, not rollback
- focus resets to target page default panel
- browse resets on every successful page change
- modal clears on every successful page change
- new page `allowedIntents` become authoritative immediately at state commit
- repeated page navigation during transition is rejected, not silently queued

## 17.2 High-Level Pseudocode Order

```text
validate intent
acquire transition lock
read current state
resolve target
validate target
if modal/safety blocked: reject
resolve service delta
stop old-only services
if stop failure: reject
clear old transient state
write new current_page
write new focused_panel
clear modal
clear browse state
initialize new page defaults
start new-only services
publish state.changed
publish completed or completed_degraded
release transition lock
```

This pseudocode is illustrative only.
The normative behavior is the full specification above.

---

# 18. Non-Negotiable Rules

1. Page transitions are controller-owned operating mode changes.
2. Redis-backed controller state is authoritative.
3. No page transition may bypass the controller pipeline.
4. Modal/destructive contexts block navigation.
5. Focus, browse, and modal state do not carry across pages.
6. Old page services must stop before semantic cutover.
7. New page permissions become authoritative immediately after state commit.
8. Service failures must be observable.
9. The system must fail closed on unsafe ambiguity.
10. Recovery must prefer a clean deterministic baseline.

---

# Final Rule

A RollingThunder page transition is complete only when the controller has safely exited the old operating context, committed the new page as authoritative state, reset page-local interaction posture, reconciled page-scoped services, and published the result observably.

Anything less is not a valid page transition.
