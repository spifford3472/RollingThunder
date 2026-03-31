# RT-SPEC-EXECUTION-ENGINE.md
## RollingThunder v0.50 — Execution Engine & Async Result Reconciliation
## Status: Authoritative

---

# 1. Purpose

This document defines the authoritative controller runtime execution model for RollingThunder.

It specifies how validated intents are admitted into execution, how execution is serialized over time, how asynchronous downstream work is tracked and reconciled, and how the controller guarantees deterministic state mutation, ordering, and responsiveness.

This document strictly follows:

- `INTENTS.md`
- `RT-SPEC-CONTROLLER-INPUT.md`
- `RT-SPEC-CONTROL-MAPPING.md`
- `RT-SPEC-LED-STATE-MODEL.md`
- `RT-SPEC-PANEL-INPUT-BRIDGE.md`
- `RT-SPEC-PAGE-TRANSITIONS.md`
- `RT-SPEC-INTERACTION-STATE.md`
- `RT-SPEC-INTENT-EXECUTION.md`

This document does **not** define:

- UI rendering
- hardware firmware or transport details
- intent vocabulary design
- page visual behavior
- downstream service business logic
- non-controller side execution semantics

This document defines:

- controller execution loop design
- runtime admission and scheduling
- bounded backpressure behavior
- async action lifecycle tracking
- correlation requirements
- result reconciliation rules
- ordering guarantees
- cancellation and preemption rules
- recovery rules after restart/failure
- execution observability

---

# 2. Core Principles

## 2.1 The Execution Engine Is the Runtime Authority

The execution engine is the controller-side runtime that turns validated intents into authoritative state transitions and observable results.

It is the sole authority for:

- execution admission
- serialization of state mutation
- pending action tracking
- result reconciliation
- timeout classification
- completion publication

No downstream service, UI, or hardware component may redefine execution truth.

## 2.2 Redis State Is Truth, Runtime Memory Is Coordination Only

Redis remains the authoritative source of durable controller state.

In-memory execution structures exist only to coordinate runtime behavior such as:

- the active execution lock
- the current in-flight execution record
- pending async execution registry
- timers and watchdog bookkeeping

If Redis and runtime memory disagree after restart, Redis-backed controller state plus deterministic recovery rules win.

## 2.3 State Mutation Must Be Serialized

All authoritative state mutation MUST be serialized through one controller execution authority.

Parallel downstream work MAY exist, but authoritative controller mutation and reconciliation MUST enter through a single serialized commit boundary.

## 2.4 Determinism

Given identical:

- validated intent stream ordering
- controller config revision
- authoritative Redis state
- downstream completion event ordering

the resulting controller state and published results MUST be identical.

## 2.5 No Hidden Queues

The system MUST NOT accumulate invisible future actions.

Any queue, buffer, coalescing behavior, drop policy, or rejection rule MUST be explicit, bounded, and observable.

## 2.6 Fail Closed

If the controller cannot safely determine:

- whether an execution should begin
- whether a result belongs to a pending execution
- whether a completion is stale
- whether state may be mutated safely

then the controller MUST reject, discard, timeout, or degrade explicitly rather than guessing.

## 2.7 Safe Mobile Operation Overrides Throughput

The runtime favors predictable, operator-safe behavior over maximum action throughput.

Immediate correctness is more important than squeezing every physical input into eventual execution.

---

# 3. Execution Engine Scope and Placement

## 3.1 Runtime Placement

The execution engine runs on `rt-controller` as part of the authoritative controller runtime.

It may be implemented as:

- a controller execution service
- an execution subsystem within the controller
- or a tightly coupled execution manager plus reconciliation worker

Implementation packaging is not normative.
Behavior is normative.

## 3.2 Upstream Inputs

The execution engine receives only **validated intents** from the v0.42/v0.44/v0.48 pipeline after semantic mapping and intent validation have succeeded.

It MUST NOT accept:

- raw panel events
- browser UI gestures directly
- transport-specific control messages
- downstream service completion events as if they were new intents

## 3.3 Downstream Outputs

The execution engine may:

- mutate controller-owned Redis state
- dispatch action requests to downstream services
- write/update pending execution state
- publish state changes
- publish result events
- publish observability events
- drive LED derivation indirectly through resulting state

---

# 4. Execution Model Overview

## 4.1 Canonical Runtime Loop

The authoritative runtime flow is:

`validated_intent_received → admission_classification → scheduling_policy → execution_lock_acquired → execution_record_created → execution_plan_built → serialized_state_mutation_if_any → commit_boundary → downstream_dispatch_if_any → pending_registration_if_any → result_publication → lock_release → async_reconciliation_until_terminal`

## 4.2 Two Execution Families

The runtime handles two top-level families exactly as v0.49 distinguishes them:

### Synchronous State Execution

Examples:

- `ui.focus.next`
- `ui.browse.delta`
- many `ui.page.*` transitions once admitted

These complete within the execution lock and reach a terminal result before lock release.

### Asynchronous Action Execution

Examples:

- `radio.tune`
- `system.shutdown`
- any controller-dispatched action requiring downstream completion

These may:

- mutate state first
- dispatch downstream work
- register pending state
- release the execution lock before terminal completion
- finish later through serialized reconciliation

## 4.3 One Serialized Authority, Many Possible Pending Actions

At most one state mutation/reconciliation commit may execute at a time.

However, multiple async actions MAY remain pending concurrently if and only if:

- they are explicitly allowed by controller policy
- each has a unique execution record
- each can be reconciled independently
- stale completion rules can prevent cross-contamination

The controller MUST NOT assume only one pending action globally unless later policy restricts it.

---

# 5. Execution Admission and Intake Model

## 5.1 Intake Source

Validated intents enter execution from the controller input pipeline after the sequence defined in v0.42:

`raw_event_received → … → intent_validation → execution`.

The execution engine is the first stage after validation, consistent with v0.42 and v0.49.  fileciteturn0file2L14-L18 fileciteturn0file1L23-L31

## 5.2 Explicit Intake Structure

The controller MUST maintain exactly one explicit intake structure for not-yet-started validated intents:

- `execution_intake_slot` for immediate admission attempt
- and optionally one bounded `execution_wait_slot` for a currently admissible coalesced navigation update

There is **no general-purpose FIFO backlog** in v0.50.

This means:

- no hidden multievent queue
- no indefinite stacking of future operator actions
- no replay of accumulated navigation after long downstream delay

## 5.3 Default Admission Policy

Default policy for validated intents arriving while no serialized execution is active:

- admit immediately
- create execution record
- acquire execution lock
- begin execution

Default policy for validated intents arriving while serialized execution is active:

- apply explicit overload policy by intent class
- either reject immediately, coalesce deterministically, or admit later through the single wait slot if allowed

## 5.4 Admission Is Deterministic and Observable

Every validated intent MUST receive one of the following admission outcomes:

- `admitted_immediate`
- `admitted_after_wait_slot`
- `coalesced_replacement`
- `rejected_busy`
- `rejected_preempted`
- `rejected_policy_blocked`

No validated intent may disappear silently.

---

# 6. Execution Lock Model

## 6.1 Single Serialized Execution Lock

The controller MUST maintain one authoritative execution lock covering:

- execution plan construction
- all authoritative state mutation
- state commit boundary
- terminal result publication for synchronous execution
- reconciliation commit for async completions

This lock MAY be implemented with:

- in-process mutex
- single-threaded event loop discipline
- actor mailbox with serialized handler
- equivalent mechanism

Implementation detail is not normative.
Single-authority serialization is normative.

## 6.2 Lock Coverage

The execution lock MUST protect against concurrent overlap in:

- state snapshot versus state mutation
- page transition cutover
- browse/focus mutation
- modal mutation
- pending registry mutation
- timeout terminalization
- late completion reconciliation

## 6.3 Lock Duration Rule

The lock MUST be held only long enough to complete serialized controller work.

The lock MUST NOT remain held while waiting on downstream services to finish.

For async action intents, the required pattern is:

1. acquire lock
2. mutate state and/or register pending state
3. commit
4. dispatch downstream work
5. record pending execution state
6. publish pending result
7. release lock
8. wait asynchronously for completion outside the lock

This preserves responsiveness while maintaining serialized truth creation.

## 6.4 Lock Fairness Rule

The execution engine MUST avoid starvation.

Because there is no unbounded queue, starvation prevention in v0.50 means:

- lock hold times must be bounded
- coalescing must collapse bursty redundant navigation instead of monopolizing execution
- repeated arrivals of one class must not indefinitely suppress emergency-safe intents such as cancel

---

# 7. Execution Record Model

## 7.1 Every Execution Gets a Unique Execution ID

Every admitted execution instance MUST receive a unique `execution_id` before any state mutation or downstream dispatch occurs.

Required properties:

- globally unique for practical controller runtime purposes
- non-reused across restart boundaries
- stable for the lifetime of the execution
- included in all pending state and completion publications

UUIDv4 or equivalent unique opaque IDs are acceptable.

## 7.2 Canonical Execution Record

Each admitted execution MUST have an execution record with at least:

```json
{
  "execution_id": "<uuid>",
  "intent": "radio.tune",
  "params": {},
  "source": "panel.serial|ui.browser|system",
  "admitted_at": "<iso8601>",
  "state_class": "state|action|hybrid",
  "lifecycle_state": "scheduled|running|pending|completed|failed|timed_out|discarded",
  "page_at_admission": "<page_id|null>",
  "focused_panel_at_admission": "<panel_id|null>",
  "interaction_layer_at_admission": "modal|transient|browse|default|null",
  "config_revision": "<string|integer>",
  "timeout_ms": <integer|null>,
  "supersedes_execution_id": "<uuid|null>",
  "cancellation_policy": "not_cancellable|discard_on_completion|best_effort_cancel",
  "reconciliation_policy": "strict_match_required"
}
```

## 7.3 Execution Record Use

The execution record is the canonical runtime bookkeeping object for:

- observability
- timeout handling
- completion matching
- stale completion rejection
- cancellation/discard behavior
- diagnostics and postmortem reconstruction

## 7.4 In-Memory and Redis Projection

The full execution record MAY live in runtime memory.

A controller-owned Redis projection SHOULD exist for pending async executions so restart recovery is deterministic.

Recommended key family:

- `rt:exec:pending:<execution_id>`
- `rt:exec:index:pending` (set or sorted set)

The Redis projection MUST contain enough information to:

- identify the intent
- identify timeout/deadline
- determine discard policy
- reconcile or expire after restart

---

# 8. Scheduling and Backpressure Model

## 8.1 No General Hidden Queue

v0.50 explicitly forbids a hidden general backlog of future intents.

The controller MUST use one of these explicit outcomes when input arrives faster than execution capacity:

- immediate execution
- deterministic coalescing into one replacement wait slot
- deterministic rejection

## 8.2 Intent Classes for Scheduling

The execution engine MUST classify validated intents for scheduling into at least these classes:

1. `cancel_priority`
2. `navigation_delta`
3. `page_navigation`
4. `modal_response`
5. `ordinary_state`
6. `async_action`
7. `maintenance_or_destructive`

This classification is runtime-local scheduling metadata.
It does not change intent vocabulary.

## 8.3 Overload Rule by Class

### cancel_priority

Examples:

- `ui.cancel`

Rule:

- MUST bypass navigation coalescing backlog
- MUST be admitted at the first safe serialized boundary
- MUST NOT be trapped behind a long chain of redundant navigation
- if current execution is already in non-interruptible commit section, cancel begins immediately after that boundary

### navigation_delta

Examples:

- repeated encoder-derived browse/focus deltas

Rule:

- MAY be coalesced deterministically
- MUST use at most one explicit wait slot
- newest admitted coalescible navigation intent replaces older coalescible navigation intent in the wait slot if both target the same semantic family and same context class

### page_navigation

Examples:

- `ui.page.next`
- `ui.page.prev`
- `ui.page.goto`

Rule:

- MUST NOT queue multiple future page transitions
- while a page transition is active, additional page navigation MUST be rejected with `transition_in_progress` per v0.47  fileciteturn0file6L600-L612

### modal_response

Examples:

- `ui.ok` or `ui.cancel` while modal active

Rule:

- MUST not be coalesced with non-modal work
- MUST preserve exact ordering
- MAY replace an older unstarted identical modal-response wait-slot entry only if identical intent and identical modal instance correlation are both true

### ordinary_state

Examples:

- focus changes not from rapid knob bursts

Rule:

- reject when engine busy unless policy explicitly allows wait-slot replacement

### async_action

Examples:

- `radio.tune`

Rule:

- reject new identical conflicting action if one already pending and policy forbids parallelism
- or accept as a new pending execution if action family explicitly supports concurrency

### maintenance_or_destructive

Examples:

- `system.shutdown`

Rule:

- MUST never be coalesced
- MUST preserve strict exact ordering
- MUST reject while conflicting execution context is active unless explicit policy allows it

## 8.4 Encoder Rapid Rotation Policy

Rapid encoder rotation is expected in mobile use.

The controller MUST treat repeated navigation deltas as the canonical coalescing case.

Required v0.50 rule:

- only one unstarted navigation wait-slot entry may exist
- if a new navigation-delta intent arrives while one navigation-delta wait-slot entry already exists and both belong to the same semantic family, the controller MUST replace the old wait-slot entry with the new one or merge them into one equivalent deterministic delta if the target semantics permit exact arithmetic merge

### Permitted merge cases

- repeated `ui.focus.next` / `ui.focus.prev` may merge into net direction only if resulting semantics remain exactly reproducible
- repeated `ui.browse.delta {delta:n}` may merge by integer summation if the same panel, same browse context, and same page remain authoritative at merge time

### Forbidden merge cases

- across page boundaries
- across different focused panels
- across different interaction layers
- across modal instance changes
- across destructive or maintenance workflows

If safe merge cannot be proven, replacement rather than merge MUST be used.

## 8.5 Repeated Button Press Policy

Repeated button presses MUST be handled deterministically by semantic meaning, not raw button identity.

Rules:

- repeated `ui.ok` while busy: reject unless exact same pending modal-response replacement rule applies
- repeated `ui.cancel`: latest cancel may replace earlier unstarted cancel in wait slot if no semantic difference exists; otherwise both are not queued and only current safe cancel path remains authoritative
- repeated destructive confirmation presses: reject while busy; never queue hidden confirmations

## 8.6 Starvation Prevention

The engine MUST prevent a continuous burst of navigation input from starving higher-safety intents.

Required rule:

- the single navigation wait slot is lower priority than a newly arriving `cancel_priority` intent
- after each completed execution, the scheduler MUST check for a pending cancel-priority wait slot before any coalesced navigation wait slot

Recommended admission precedence after lock release:

1. modal cancel/confirm if waiting
2. cancel_priority
3. maintenance/destructive exact responses
4. page_navigation
5. ordinary_state
6. navigation_delta wait-slot

---

# 9. Intake → Execution → Completion Lifecycle

## 9.1 Lifecycle States

Every execution instance MUST progress through explicit lifecycle states.

Canonical states:

- `scheduled`
- `running`
- `committed_sync`
- `pending`
- `completed`
- `completed_degraded`
- `noop`
- `rejected_post_validation`
- `failed`
- `timed_out`
- `discarded_stale`
- `discarded_duplicate`
- `cancelled_before_dispatch`
- `cancel_requested_pending`

## 9.2 Synchronous Lifecycle

For synchronous state intents:

`scheduled → running → committed_sync → completed|noop|failed`

## 9.3 Asynchronous Lifecycle

For async actions:

`scheduled → running → state_committed_if_any → dispatched → pending → reconciled_terminal`

Terminal async outcomes:

- `completed`
- `completed_degraded`
- `failed`
- `timed_out`
- `discarded_stale`
- `discarded_duplicate`

## 9.4 Hybrid Lifecycle

For hybrid intents:

`scheduled → running → state_mutated_and_committed → dispatched → pending → reconciliation → terminal`

This preserves v0.49’s state-first rule that hybrid execution is `state → commit → dispatch`.  fileciteturn0file1L99-L103

---

# 10. Async Action Lifecycle Model

## 10.1 Async Admission Rule

An action intent becomes async only when controller policy requires downstream completion beyond the immediate serialized commit.

An async execution MUST NOT be declared terminally successful merely because dispatch succeeded.

Dispatch success yields `accepted_pending`, not final completion, consistent with v0.49.  fileciteturn0file1L58-L70

## 10.2 Canonical Async Lifecycle

The authoritative lifecycle for async action intents is:

1. intent admitted
2. execution record created
3. execution lock acquired
4. preconditions revalidated against authoritative state
5. controller computes execution plan
6. controller mutates state for pending posture if required
7. controller commits state
8. controller dispatches downstream request with correlation metadata
9. controller registers pending execution durably
10. controller publishes `accepted_pending`
11. controller releases execution lock
12. downstream processes request
13. completion, failure, or timeout signal arrives
14. controller serializes reconciliation under execution lock
15. controller validates correlation and staleness
16. controller commits terminal result state if still applicable
17. controller publishes terminal result and state change

## 10.3 Pending State Tracking

Each async execution MUST be tracked at least by:

- `execution_id`
- intent name
- dispatch timestamp
- deadline/timeout timestamp
- dispatch target/service id if applicable
- pending classification
- discard/cancel policy
- context snapshot needed for stale-result detection

## 10.4 Multiple Pending Actions

Multiple pending actions MAY coexist only when explicit policy allows it.

Required v0.50 policy model:

- pending actions are grouped into `action families`
- each family declares concurrency mode:
  - `exclusive_global`
  - `exclusive_per_target`
  - `parallel_allowed`

### Default policy

If no family policy exists, async action families default to `exclusive_per_target`.

This means:

- one pending action per target resource
- a second conflicting action for the same target is rejected with `conflicting_pending_execution`

### Example

For `radio.tune` against the active radio target, default policy is effectively one pending tune at a time for that target.

## 10.5 Pending Visibility

Pending controller truth MUST be externally visible through controller-published result/state, not inferred.

At minimum, the controller SHOULD publish:

- pending result event on bus
- Redis pending projection for recovery
- optional page/service capability state reflecting “action in progress” if LEDs/UI need it

---

# 11. Correlation Model

## 11.1 Correlation Is Mandatory

Every async action MUST carry a correlation identifier from controller dispatch through downstream completion.

That identifier MUST be the controller-generated `execution_id` or a controller-generated derivative that maps bijectively back to `execution_id`.

## 11.2 Dispatch Envelope Requirement

Downstream-dispatched async requests MUST include at minimum:

```json
{
  "execution_id": "<uuid>",
  "intent": "radio.tune",
  "target": "<service-or-node>",
  "params": {}
}
```

## 11.3 Completion Envelope Requirement

Every downstream async completion MUST include at minimum:

```json
{
  "execution_id": "<uuid>",
  "intent": "radio.tune",
  "status": "success|failure|timeout|cancelled",
  "completed_at": "<iso8601>",
  "target": "<service-or-node>",
  "payload": {}
}
```

## 11.4 Strict Match Rule

A completion event is eligible for reconciliation only if all of the following hold:

- `execution_id` matches a currently pending execution
- intent family matches expected execution record
- target, if applicable, matches expected target
- execution is still pending and not already terminalized

If any check fails, the completion MUST be rejected or discarded explicitly.

## 11.5 Unmatched Response Rule

Unmatched responses MUST be rejected.

They MUST NOT mutate state.

They MUST be logged and published as observability events such as:

- `execution.reconciliation.unmatched`
- `execution.reconciliation.discarded`

---

# 12. Result Reconciliation Rules

## 12.1 Reconciliation Is Serialized

All completion reconciliation MUST acquire the same serialized execution lock used for execution-time state mutation.

No completion may mutate controller state concurrently with:

- another completion
- a page transition
- a synchronous intent execution
- timeout terminalization

## 12.2 Success Reconciliation

When a matching completion indicates success:

1. acquire execution lock
2. confirm execution still pending
3. perform stale-result checks
4. apply terminal state mutation if still valid
5. clear pending registry entry
6. publish `accepted_completed` or `accepted_completed_degraded`
7. publish `state.changed` if state changed
8. release lock

## 12.3 Failure Reconciliation

When a matching completion indicates failure:

1. acquire lock
2. confirm execution still pending
3. clear pending registry entry
4. commit failure state if controller tracks one
5. publish `execution_failed` or explicit failure terminal classification
6. release lock

## 12.4 Timeout Reconciliation

When deadline expires before a terminal completion:

1. timeout watcher acquires lock
2. confirm execution still pending
3. mark execution terminal as `timed_out`
4. clear pending registry entry
5. commit timeout-related state if any
6. publish timeout result
7. release lock

After timeout terminalization, any later completion becomes a late response and must be discarded.

## 12.5 Duplicate Response Rule

If a second terminal completion arrives for an execution already terminalized:

- it MUST NOT mutate state
- it MUST be classified `discarded_duplicate`
- it MUST be observable

## 12.6 Late Response Rule

If a completion arrives after:

- timeout terminalization
- explicit discard policy activation
- page/context invalidation for discard-on-completion actions
- newer conflicting execution superseded the old one

then the completion MUST be classified `discarded_stale` or `discarded_late` and MUST NOT mutate state.

## 12.7 Stale Completion Detection

A completion is stale if any configured stale check fails.

Required stale checks:

1. execution no longer pending
2. execution already terminal
3. execution explicitly superseded by newer execution in same exclusivity family
4. execution discard policy triggered by page/context change

Optional additional stale checks by action family:

- target resource generation changed
- page-at-admission no longer matches required reconciliation scope
- focused object identity changed where action semantics require exact scope continuity

If the family requires these checks, they MUST be explicit and deterministic.

---

# 13. Ordering Guarantees

## 13.1 Canonical Ordering Requirements

The execution engine MUST guarantee the following order:

1. input ordering preserved up to admission decision
2. admitted execution obtains unique execution record before mutation
3. authoritative state mutation occurs only under serialized lock
4. state commit occurs before success publication
5. hybrid dispatch occurs only after commit, per v0.49  fileciteturn0file1L44-L56
6. pending result publication occurs only after pending registration is durable enough for recovery
7. completion reconciliation occurs only after correlation validation
8. terminal result publication occurs only after terminal state commit

## 13.2 No Out-of-Order State Mutation

No later completion may override newer authoritative state if stale/discard rules say it no longer applies.

This is mandatory for avoiding:

- old tune completion overriding a newer tune intent
- old page-scoped action affecting a new page after cutover
- stale modal response mutating a replaced modal

## 13.3 Page Transition Ordering Interaction

Page transition ordering from v0.47 remains authoritative.

The execution engine MUST enforce that async reconciliation cannot violate the page transition cutover guarantees in v0.47, including immediate new-page authority at commit and no stale permissions after cutover.  fileciteturn0file6L579-L597

## 13.4 Interaction State Ordering Interaction

The effective interaction layer resolution from v0.48 remains authoritative.

Execution and reconciliation MUST use authoritative interaction state at the moment of serialized validation/reconciliation, not cached UI assumptions.  fileciteturn0file4L68-L83

---

# 14. Timing and Responsiveness Constraints

## 14.1 General Rule

The execution engine MUST remain perceptibly responsive for field/mobile operation and must never block the operator behind long hidden work.

## 14.2 Maximum Acceptable Latency Targets

These are controller-runtime targets, not transport guarantees.

### Input → execution start

- normal state intent target: ≤ 50 ms
- cancel-priority intent target: ≤ 25 ms once current non-interruptible commit section ends
- encoder navigation under burst/coalescing target: ≤ 75 ms for latest effective navigation step

### Execution start → state commit

- simple state intent target: ≤ 50 ms
- page transition state cutover target after stop success: ≤ 150 ms excluding downstream service start latency
- modal confirm/cancel target: ≤ 50 ms

### Async pending publication

- async action admitted → pending result published: ≤ 100 ms

These are target envelopes for implementation and testing.
They are not permission to violate deterministic ordering.

## 14.3 Responsiveness Guarantees

### Encoder Navigation

Encoder navigation MUST prefer latest effective state over replaying every microstep if replay would create lag.

That is why coalescing exists.

The operator should experience responsive current navigation, not historical backlog playback.

### Cancel Actions

`ui.cancel` MUST be treated as immediate-priority work.

It MUST begin execution at the earliest safe serialized boundary.
It MUST NOT wait behind redundant queued navigation because such a queue does not exist in v0.50.

## 14.4 Timeout Thresholds

Every async family MUST declare a bounded timeout.

If no family-specific timeout is defined, a default timeout MUST be applied.

Recommended defaults:

- local controller-side async helper: 2 seconds
- radio control action such as `radio.tune`: 3 seconds
- service lifecycle start/stop action: use page/service lifecycle spec default, commonly 3 to 5 seconds
- shutdown-stage acknowledgements: explicit per phase, not infinite

No async pending execution may remain pending indefinitely.

## 14.5 Slow-Service Handling

When downstream service is slow but within timeout:

- pending state remains authoritative
- controller remains responsive to other admissible intents
- completions still reconcile serially

When service exceeds timeout:

- controller terminalizes the execution as timeout
- late completion becomes stale/discarded

---

# 15. Concurrency Model

## 15.1 What Must Be Serialized

These MUST be serialized through the execution lock:

- all authoritative state mutation
- pending registry mutation
- timeout terminalization
- completion reconciliation
- modal replacement/clear
- page transition cutover
- focus/browse mutation
- state.changed and result publication ordering relative to commit

## 15.2 What May Run Concurrently

These MAY run concurrently outside the serialized lock:

- waiting for downstream completion
- multiple downstream service operations in different allowed async families
- heartbeat/health polling
- UI rendering
- panel transport handling
- observability shipping that does not change authoritative state

## 15.3 Concurrency Safety Rule

Parallel downstream activity is allowed only if all authoritative consequences re-enter through serialized reconciliation.

## 15.4 Family Concurrency Declarations

Every async action family SHOULD declare one of:

- `exclusive_global`
- `exclusive_per_target`
- `parallel_allowed`

Examples:

- `system.shutdown`: `exclusive_global`
- `radio.tune` for active radio target: `exclusive_per_target`
- unrelated page-local async info fetch, if ever exposed and harmless: possibly `parallel_allowed`

---

# 16. Cancellation and Preemption Rules

## 16.1 Cancellation Is Policy-Driven, Not Assumed

The controller MUST not assume that downstream services can actually cancel an in-flight action.

Therefore each async family MUST declare a cancellation policy:

- `not_cancellable`
- `discard_on_completion`
- `best_effort_cancel`

## 16.2 Cancel During Pending Action

If user presses cancel while an async action is pending, behavior depends on family policy.

### not_cancellable

- controller may exit any related modal/transient UI posture if safe
- pending execution remains active
- completion will still reconcile normally unless later discarded by policy

### discard_on_completion

- controller marks execution as `discard_if_late_or_irrelevant`
- downstream work is allowed to continue
- if completion arrives after discard trigger, completion is ignored/discarded

### best_effort_cancel

- controller emits cancel request downstream with same correlation family
- pending record enters `cancel_requested_pending`
- if downstream confirms cancellation, execution terminalizes as cancelled/failure per family rule
- if downstream later succeeds anyway, stale/discard policy determines acceptance

## 16.3 Page Change During Pending Action

When page changes while an action is pending, the family must define whether reconciliation remains page-independent or page-scoped.

### Page-independent actions

Example:

- a radio tune whose effect is globally meaningful regardless of page

Rule:

- completion MAY still be accepted after page change if correlation still matches and no superseding execution invalidated it

### Page-scoped actions

Example:

- a page-local selection helper whose result only matters on originating page

Rule:

- page change triggers discard-on-completion
- late completion MUST be discarded as stale

## 16.4 Newer Execution Superseding Older Pending Action

If a family allows a newer execution to supersede an older pending action:

- older execution record MUST be marked superseded by newer `execution_id`
- older completion MUST be discarded stale if it arrives later
- this MUST be explicit family policy, never implicit guesswork

## 16.5 Preemption Boundaries

Serialized commit sections are non-interruptible.

Preemption or priority handling only applies:

- before execution starts
- after lock release
- at safe scheduling boundaries

---

# 17. Failure and Recovery in Runtime Loop

## 17.1 Controller Crash During Pending Action

If controller crashes while async actions are pending:

On restart the controller MUST:

1. restore or validate config/runtime prerequisites
2. inspect Redis pending execution projections
3. determine whether each pending execution can still be resumed, timed out, or discarded safely
4. clear any execution whose deadline has already expired
5. re-arm timeout watchers for still-valid pending executions
6. reconcile family exclusivity state
7. publish recovery diagnostics

## 17.2 Restart Recovery Rule

The controller MUST prefer deterministic safe terminalization over uncertain resumption.

Therefore:

- if a pending execution’s downstream state cannot be trusted or correlated after restart, the controller SHOULD terminalize it as timed out or recovered_discarded rather than guessing it succeeded
- if a pending execution is still within deadline and downstream completion correlation channel is trustworthy, the controller MAY continue waiting

## 17.3 Redis Disconnect

If Redis becomes unavailable during serialized execution before commit:

- execution MUST fail closed
- no success publication may occur
- lock released after failure classification
- downstream dispatch for hybrid/action intents MUST NOT proceed if required commit did not occur

If Redis becomes unavailable after commit but before pending/observability publication:

- controller MUST treat persistence/publication failure as degraded runtime condition
- it MUST attempt to restore/publish once connectivity returns only if doing so does not invent state
- no duplicate terminal state may be created

## 17.4 Service Unavailable

If downstream target is unavailable before dispatch:

- async execution may terminalize immediately as failed or completed_degraded depending on family policy
- if no dispatch occurred, execution MUST NOT remain pending

## 17.5 Execution Loop Stall

The controller SHOULD maintain a watchdog for execution loop health.

A stall condition exists if:

- lock held longer than configured maximum serialized section budget
- timeout sweep not running
- pending registry not being serviced

On detected stall:

- publish degraded observability
- fail closed for new non-critical admissions if authoritative execution cannot be guaranteed
- preserve current Redis truth

## 17.6 Timeout Sweep Recovery

Timeout sweeps MUST be restart-safe and idempotent.

Re-running the timeout check after restart MUST not produce conflicting terminal states.

---

# 18. Observability Requirements

## 18.1 Required Event Coverage

The runtime MUST emit observability for all important execution phases.

Required semantic coverage:

- execution scheduled
- execution started
- execution committed
- execution pending
- execution completed
- execution completed degraded
- execution failed
- execution timeout
- reconciliation success
- reconciliation failure
- stale result discarded
- duplicate result discarded
- unmatched result rejected
- pending recovery after restart
- busy rejection / coalescing decision

## 18.2 Recommended Event Topics

Recommended bus topics or log event names:

- `execution.scheduled`
- `execution.started`
- `execution.committed`
- `execution.pending`
- `execution.completed`
- `execution.completed_degraded`
- `execution.failed`
- `execution.timeout`
- `execution.reconciliation.applied`
- `execution.reconciliation.discarded_stale`
- `execution.reconciliation.discarded_duplicate`
- `execution.reconciliation.unmatched`
- `execution.recovered`
- `execution.rejected_busy`
- `execution.coalesced`

Exact topic names may align with existing conventions, but semantic coverage is mandatory.

## 18.3 Required Payload Elements

Each execution observability event SHOULD include at minimum:

```json
{
  "execution_id": "<uuid>",
  "intent": "<intent>",
  "lifecycle_state": "<state>",
  "result": "<result-classification|null>",
  "reason": "<reason|null>",
  "source": "<source>",
  "page": "<page_id|null>",
  "focused_panel": "<panel_id|null>",
  "timestamp": "<iso8601>"
}
```

## 18.4 Logging Requirements

Diagnostics MUST be sufficient to reconstruct:

- what was admitted
- what was coalesced or rejected
- what state was committed
- what downstream dispatch occurred
- what became pending
- what result matched which execution
- why a stale result was discarded
- whether timeout or restart recovery occurred

---

# 19. Integration with v0.49 Intent Execution

## 19.1 v0.49 Remains the Semantic Execution Contract

v0.50 does not replace v0.49.
It operationalizes it as a continuous controller runtime.

v0.49 defines the normative execution semantics:

- execution pipeline
- state-first rule
- atomic mutation
- result classes
- dispatch after commit
- single execution lock requirement  fileciteturn0file1L9-L17 fileciteturn0file1L23-L35 fileciteturn0file1L71-L90 fileciteturn0file1L104-L110

v0.50 defines how that semantic contract behaves over time under bursts, pending work, and completion races.

## 19.2 Invocation Rule

For each admitted intent, the v0.50 engine MUST invoke the v0.49 execution model inside the serialized execution section.

Conceptually:

1. v0.50 admits/schedules the intent
2. v0.50 acquires execution lock
3. v0.50 constructs execution record
4. v0.50 invokes v0.49 execution semantics for mutation/dispatch classification
5. v0.50 persists pending tracking if needed
6. v0.50 publishes immediate result
7. v0.50 later reconciles async terminal result

## 19.3 Result Class Compatibility

v0.50 MUST preserve v0.49 result classes and extend them only operationally.

Immediate execution classifications remain:

- `accepted_completed`
- `accepted_completed_degraded`
- `accepted_noop`
- `accepted_pending`
- `rejected_post_validation`
- `execution_failed`
- `execution_timeout`

v0.50 may add observability-side subreason fields, but MUST NOT redefine v0.49 result meaning.

---

# 20. Integration with Interaction State, LEDs, and UI

## 20.1 State Feeds UI and LED Model Through Existing Truth Path

The execution engine does not update LEDs or UI directly by optimism.

It affects them only through authoritative state/result publication, consistent with v0.45 and v0.49.  fileciteturn0file5L24-L41 fileciteturn0file1L44-L56

## 20.2 Pending State and LEDs

If the controller exposes pending posture in authoritative state, the LED model MAY reflect that pending posture truthfully.

Examples:

- green off while action no longer immediately confirmable
- white pulse or slow blink for info/pending awareness if page semantics justify it
- red caution if cancellation remains relevant or degraded trust exists

LEDs MUST NOT imply downstream success until reconciliation commits terminal truth, consistent with v0.45.  fileciteturn0file5L441-L449

## 20.3 UI Result Publication

The execution engine MUST publish immediate and terminal results onto controller-observable channels so UI remains renderer-only.

The UI may render:

- pending state
- completion state
- timeout/failure state

But the UI may not invent them.

## 20.4 Interaction State Invalidations

Execution and reconciliation MUST honor v0.48 interaction state rules.

If completion arrives for a context that no longer has one valid effective interaction state, completion MUST not invent a new one; it must either reconcile against current valid state or be discarded under family stale rules.  fileciteturn0file4L357-L369

---

# 21. Recommended Redis Keys and Runtime Structures

## 21.1 Recommended Redis Pending Projection

Recommended per-pending key:

`rt:exec:pending:<execution_id>`

Recommended fields:

- `execution_id`
- `intent`
- `state_class`
- `status` (`pending|cancel_requested`)
- `source`
- `page_at_admission`
- `focused_panel_at_admission`
- `interaction_layer_at_admission`
- `target`
- `dispatched_at_ms`
- `deadline_ms`
- `cancellation_policy`
- `superseded_by`
- `discard_on_page_change` (`true|false`)
- `config_revision`

## 21.2 Recommended Pending Index

Recommended index key:

`rt:exec:index:pending`

as a set or sorted set by deadline.

## 21.3 Recommended Last Result Projection

An optional derived projection may exist:

- `rt:input:last_result`

if the controller wants a consolidated recent result state for LEDs/UI, consistent with v0.45’s derived-state model.  fileciteturn0file5L133-L147

---

# 22. Deterministic Test Scenarios

The implementation is not complete unless these scenarios behave deterministically.

## 22.1 Rapid Encoder Burst

Given a rapid burst of same-context browse deltas:

- engine coalesces deterministically
- latest effective navigation state is reached
- no hidden backlog remains after burst subsides

## 22.2 Cancel During Navigation Burst

Given rapid navigation input followed by cancel:

- cancel is admitted at next safe boundary ahead of remaining coalesced navigation work
- no starvation occurs

## 22.3 Tune Superseded by New Tune

Given pending `radio.tune` execution A and a policy-allowed superseding tune B:

- B becomes authoritative pending execution
- late success from A cannot override B
- A completion is discarded stale

## 22.4 Page Change During Page-Scoped Pending Action

Given a page-scoped pending action and successful page transition:

- transition remains authoritative
- pending action completion is discarded if family requires originating-page continuity

## 22.5 Timeout Then Late Success

Given async execution times out and later emits success:

- timeout remains authoritative terminal result
- late success is discarded and observable

## 22.6 Restart With Pending Action

Given controller restart while execution pending:

- pending projection is read
- execution either continues waiting within deadline or is terminalized safely
- no duplicate success occurs

## 22.7 Redis Failure Before Commit

Given Redis unavailable before commit:

- no success is published
- no downstream dispatch occurs for hybrid action requiring committed state first

---

# 23. Non-Negotiable Rules

1. All authoritative state mutation is serialized.
2. No async result may mutate state without strict correlation match.
3. Every async action MUST have a unique execution ID.
4. No hidden general backlog is allowed.
5. Overload behavior must be explicit: admit, coalesce, or reject.
6. Cancel-priority behavior must not be starved by redundant navigation bursts.
7. Hybrid intents follow state → commit → dispatch.
8. Terminal result publication occurs only after authoritative terminal state decision.
9. Late, duplicate, or unmatched completions must never override newer truth.
10. Restart recovery must prefer deterministic safe terminalization over guessing.
11. UI and panel remain non-authoritative.
12. Same input sequence and completion sequence must produce the same outcome.

---

# 24. Completion Criteria

v0.50 is complete only when the controller can demonstrate all of the following:

1. validated intents are admitted through an explicit deterministic scheduling model
2. all authoritative mutation is serialized under one execution authority
3. burst input handling is explicit, bounded, and observable
4. async actions receive unique execution IDs and durable pending tracking
5. downstream completions reconcile only by strict correlation
6. stale, duplicate, unmatched, and late results are safely discarded
7. timeouts produce terminal authoritative outcomes
8. page transitions and interaction state remain protected from stale async overwrite
9. cancel behavior remains responsive under burst load
10. restart recovery handles pending executions deterministically
11. result publication feeds UI and LED truth only through controller-owned state/result flow
12. real hardware stress tests cannot create hidden race-dependent outcomes

---

# Final Rule

The RollingThunder execution engine is correct only if one authoritative serialized controller truth exists at every commit boundary, every pending action is explicitly tracked, every completion is strictly correlated and reconciled deterministically, and no stale or hidden work can override the operator’s current safe state.

If two identical sequences of admitted inputs and completion events can produce different state:

**the execution engine is wrong.**
