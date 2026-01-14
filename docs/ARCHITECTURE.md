# RollingThunder – Architecture Reference (Authoritative)

This document defines the **authoritative architecture, constraints, and design philosophy**
for the RollingThunder mobile communications platform.

It exists to prevent architectural drift when:
- conversations reset
- context windows truncate
- new components are added
- implementation details tempt shortcuts

This document should be treated as **ground truth** and pasted into any new design discussion
to re-anchor decisions.

---

## 1. Core Purpose

RollingThunder is a **modular, vehicle-mounted mobile communications platform** designed to:

- Minimize distracted driving
- Provide reliable HF/VHF/UHF, DMR, NOAA, and situational awareness
- Support Parks on the Air (POTA), emergency monitoring, and experimentation
- Be extensible without rewriting the core framework

It is **not** a monolithic application.
It is a **system-of-systems**.

---

## 2. High-Level Architectural Principles

### 2.1 Separation of Concerns (Non-Negotiable)

Each node has a **single primary responsibility**.

- No node should become a “do everything” box
- Failures should be contained to a role, not cascade

### 2.2 Configuration Over Code

- Behavior is driven by **configuration files**, not hardcoded logic
- Pages, panels, services, lifecycles, and dependencies are declarative
- Adding new radios or services should not require modifying core logic

### 2.3 Appliance Mindset

Some nodes are treated as **appliances**, not general-purpose computers.
Stability > flexibility.

### 2.4 Deterministic Behavior

- Wired networking preferred
- Wi-Fi only for setup / recovery
- Explicit service lifecycles
- Known startup order
- No “mystery background processes”

---

## 3. Physical Node Architecture (Locked)

### 3.1 rt-controller (Raspberry Pi 4)

**Role:** System brain / orchestration / sensor fusion

Responsibilities:
- Service lifecycle manager
- Page lifecycle coordination
- Redis-based state store
- MQTT event bus client
- GPS input (authoritative position + time)
- NOAA SAME monitoring (RTL-SDR)
- Logging, truncation, and health reporting

Constraints:
- Headless
- Raspberry Pi OS Lite (64-bit, Bookworm)
- Docker installed but used deliberately
- No UI rendering

---

### 3.2 rt-display (Raspberry Pi 3)

**Role:** Display-only UI node

Responsibilities:
- HDMI output
- Kiosk-style browser rendering
- Page/panel visualization only

Constraints:
- No business logic
- No Redis writes
- No radio control
- Raspberry Pi OS Desktop (64-bit, Bookworm)
- UI behavior driven entirely by config + APIs

---

### 3.3 rt-radio (Raspberry Pi Zero 2 W)

**Role:** Dedicated HF radio appliance (Yaesu FT-891)

Responsibilities:
- CAT control
- PTT
- Audio via DigiRig DR-891
- Radio state reporting

Constraints:
- Raspberry Pi OS Lite (32-bit, Bookworm)
- **Single USB device only (DigiRig)**
- No RTL-SDR
- No GPS
- No Docker
- No multitasking
- USB host mode only
- Stability > features

---

### 3.4 rt-wpsd (Raspberry Pi 4, External)

**Role:** External DMR / WPSD system

Responsibilities:
- DMR infrastructure
- Dashboard + services provided by WPSD

Constraints:
- Treated as an **external appliance**
- Not re-architected
- Not refactored
- Integrated only via network APIs
- Hostname standardized; username retained

---

## 4. Communication Model

### 4.1 MQTT (Mosquitto)

Used as the **event bus**, not as a database.

- Button presses
- Page changes
- Alerts
- State transitions
- Commands (intent-based)

MQTT messages are:
- Small
- Stateless
- Event-oriented

---

### 4.2 Redis

Used as the **authoritative state store**.

Redis holds:
- Current page
- Current panel focus
- Radio state snapshots
- Node health
- GPS position
- Alert state

Redis is:
- Fast
- Central
- Queryable by all nodes
- The single source of truth

---

### 4.3 APIs (HTTP / JSON)

Used for:
- Structured queries
- UI polling
- Health endpoints
- Service introspection

APIs expose **state**, not control loops.

---

## 5. UI Architecture

### 5.1 Page Model

- Pages are defined in JSON
- Each page has:
  - Page ID
  - Order
  - Top panel (fixed)
  - Bottom panel (fixed)
  - Middle layout (1–3 panels)
  - Service dependencies
  - Lifecycle rules

### 5.2 Panel Model

Panels:
- Have IDs
- Can be focusable or non-focusable
- Depend on one or more services
- Render data only (no logic)

### 5.3 Focus & Input

Physical controls (ESP32):
- Page forward / backward
- Panel focus forward / backward
- Rotary encoder (selection)
- Press-to-enter
- OK
- CANCEL

UI logic is deterministic and minimal.
No free-form typing.

---

## 6. Service Lifecycle Model

### 6.1 Always-On Services (Authoritative)

Always-on services are those that must remain active regardless of
page, panel, or UI state.

They provide system continuity, safety, and external control paths.

Always-on services include:

- Redis connectivity (authoritative state store)
- MQTT connectivity (event bus)
- GPS ingestion (authoritative position + time)
- Logging (structured, rotated, truncated)
- Node health reporting
- NOAA SAME monitoring + alerting (RTL-SDR → decode → Redis/MQTT → UI + Meshtastic)
- **Meshtastic command, status, and alert service**

### 6.1.1 Meshtastic Command & Control Service

Meshtastic is treated as a **low-bandwidth, resilient control and telemetry
side-channel**, not a primary UI or control interface.

This service is always-on to ensure:
- external command capability
- remote status visibility
- alert propagation independent of vehicle UI state

#### Responsibilities

- Listen for inbound Meshtastic messages
- Parse and validate command messages
- Enforce allow-lists and safety constraints
- Execute permitted commands via internal APIs
- Publish status and acknowledgments
- Emit alerts over Meshtastic when conditions warrant

#### Supported Command Classes

- HOST commands
  - health summary
  - node status
  - uptime
  - version info

- SERVICE commands
  - start/stop/restart (only for explicitly allowed services)
  - query running state

- PAGE commands (restricted)
  - request current page
  - request page change (optional, configurable)

- RADIO commands (read-only by default)
  - frequency
  - mode
  - TX/RX state

Write-capable commands must be:
- explicitly enabled
- allow-listed
- auditable

#### Status & Telemetry

The service may publish:
- node health summaries
- radio availability
- GPS coarse position (configurable)
- alert states (NOAA, system faults)

Status responses are:
- concise
- bounded in size
- rate-limited

#### Alerting

Meshtastic alerts are intended for:
- NOAA SAME alerts
- system fault alerts
- power or node failures

They are:
- informational or advisory
- not intended for interactive control loops
- secondary to in-vehicle UI alerts

#### Constraints (Non-Negotiable)

- Meshtastic **must not** be required for normal operation
- Loss of Meshtastic must not impair local control
- Meshtastic must not bypass UI safety rules
- Meshtastic must not become a parallel UI


### 6.2 Page-Scoped Services

Pages declare:
- Required services
- Optional/on-demand services

Controller:
- Starts services when page becomes active
- Stops services when page exits (if not shared)

This prevents:
- Unnecessary CPU usage
- Radio contention
- Background surprises

---

## 7. Logging & Safety

### 7.1 Logging

- Structured logs
- Rotation and truncation from day one
- Logs treated as data, not console spam

### 7.2 Distracted Driving Mitigation

- No free typing while driving
- Minimal interaction steps
- Alerts prioritized and interruptible
- Clear OK/CANCEL semantics

---

## 8. Extensibility Rules

Adding a new radio or capability should require:
- A new service module
- A config entry
- A page or panel definition

It should **not** require:
- Rewriting the controller
- Editing unrelated services
- Breaking existing nodes

---

## 9. Things This Project Intentionally Avoids

- Monolithic applications
- Tight coupling between UI and radios
- “Just put it on the controller”
- Background magic
- Implicit dependencies
- Undocumented behavior

---

## 10. How to Use This Document in Conversations

When starting a new design or implementation discussion:

1. Paste this document (or a link to it)
2. State which node or subsystem you are working on
3. State whether the task is:
   - Architecture
   - Implementation
   - Refactor
   - Integration

If suggestions conflict with this document, **this document wins unless explicitly revised**.

---

## 11. Revision Philosophy

This document may evolve, but only by:
- Explicit discussion
- Intentional changes
- Versioned commits

Silent drift is considered a bug.

---

**End of authoritative architecture reference.**

---

## 12. Architecture Versioning & Change Log

This section records **intentional architectural changes** to RollingThunder.
It exists to prevent silent drift and to provide future context for
*why* decisions were made, not just *when*.

All changes to architecture **must** be recorded here.

### Versioning Philosophy

- Versions are **architectural**, not software releases
- Minor revisions clarify or extend intent
- Major revisions change assumptions, roles, or constraints
- Every entry includes a **rationale**

If behavior changes but this section is not updated, the change is considered incomplete.

---

### Version 1.0 — Initial Architecture Baseline  
**Date:** January 13, 2026  
**Status:** Authoritative Baseline

**Summary:**  
Established the foundational RollingThunder architecture after retiring the
original “mobile ham” implementation. This version defines system roles,
constraints, communication models, and safety principles.

**Key Decisions:**
- Adopted a **multi-node, role-based architecture**
- Defined `rt-controller`, `rt-display`, `rt-radio`, and `rt-wpsd`
- Formalized **configuration-over-code** philosophy
- Introduced Redis as authoritative state store
- Introduced MQTT as event bus (not a database)
- Treated WPSD as an **external appliance**
- Established deterministic service lifecycles
- Explicitly minimized distracted-driving interaction
- Documented non-goals to prevent future creep

**Rationale:**  
The original implementation suffered from tight coupling, unclear ownership,
and limited extensibility. A clean restart with explicit constraints was required
to support long-term growth and reliability.

---

### Version 1.1 — Always-On Services Clarification  
**Date:** January 13, 2026  
**Status:** Incremental Update

**Summary:**  
Clarified which services must always be running regardless of UI state.

**Changes:**
- Explicitly designated **Meshtastic Command & Control** as an Always-On service
- Explicitly designated **NOAA SAME monitoring and alerting** as an Always-On service
- Distinguished between:
  - Always-On detection/alerting
  - Page-scoped visualization and interaction

**Rationale:**  
Safety- and awareness-critical functions (NOAA alerts, remote status, resilient
control paths) must operate independently of UI state to ensure reliability and
situational awareness even when the user is not on the relevant page.

---

### Version 1.2 — Safety & Control Plane Constraints  
**Date:** January 13, 2026  
**Status:** Incremental Update

**Summary:**  
Added explicit constraints to prevent secondary control paths from undermining
system determinism or safety.

**Changes:**
- Defined Meshtastic as a **side-channel**, not a primary UI
- Required Meshtastic-originated actions to flow through the same internal APIs
  as physical controls
- Prohibited Meshtastic from bypassing UI safety rules
- Clarified alert rate-limiting, deduplication, and bounded message size

**Rationale:**  
Without explicit constraints, secondary control channels can evolve into hidden
control planes. These rules ensure all control paths remain auditable,
deterministic, and safe.

---

### How to Add a New Entry

When updating architecture:

1. Append a new version entry
2. Use the next version number
3. Include:
   - Date
   - Summary
   - Explicit changes
   - Rationale
4. Commit the change alongside any implementation work

If a future reader cannot answer *“why was this done?”* from this section,
the entry is incomplete.

---

**End of Architecture Versioning Section**


