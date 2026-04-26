# RollingThunder Intent Vocabulary (v0.41 Aligned)

## Authoritative Reference

This document defines the **canonical intent vocabulary** used
throughout the RollingThunder platform.

An *intent* represents:

**What the user or system wants to happen**, not how it is implemented.

All control paths emit intents: - physical control panels - UI
(browser) - Meshtastic - automation - future integrations

If behavior cannot be described as an intent, it does not belong in the
system.

------------------------------------------------------------------------

## 1. Why Intents Exist

RollingThunder deliberately separates:

-   Input sources (buttons, UI, Meshtastic, scripts)
-   Intent (requested action)
-   Execution (controller + services)

This ensures: - deterministic behavior - uniform safety enforcement -
auditable control flow - no hidden bypass paths

There is exactly **one control vocabulary**.

All input sources, including:

- UI (browser)
- Physical control panel (ESP32)
- Meshtastic
- Automation

must emit intents using this vocabulary.

------------------------------------------------------------------------

## 2. Intent Structure

    {
      "intent": "ui.page.next",
      "params": {}
    }

### Required fields

-   intent --- canonical string identifier

### Optional fields

-   params --- small JSON object
-   source --- injected by runtime
-   timestamp --- injected by runtime

### Rules

-   small
-   declarative
-   transport-independent
-   no side-effects until executed

------------------------------------------------------------------------

## 3. Intent Naming Conventions

Format: `<domain>`{=html}.`<subdomain>`{=html}.`<action>`{=html}

Rules: - lowercase - verbs last - stable - no implementation details

Good: - ui.page.next - ui.browse.delta - system.shutdown

Bad: - ctrl_shift_r - shutdown_now

------------------------------------------------------------------------

## 4. Intent Domains

### 4.1 UI (ui.\*)

#### Page Navigation

-   ui.page.next
-   ui.page.prev
-   ui.page.goto

#### Focus

-   ui.focus.next
-   ui.focus.prev
-   ui.focus.set

#### Browse

-   ui.browse.delta
-   ui.browse.home
-   ui.browse.end

#### Core Actions

-   ui.ok
-   ui.cancel
-   ui.back

#### Maintenance

-   ui.reload

#### Reserved

-   ui.action.primary
-   ui.action.secondary

------------------------------------------------------------------------

### 4.2 System (system.\*)

-   system.shutdown

Rules: - requires confirmation - controller executed - auditable

------------------------------------------------------------------------

### 4.3 Alert (alert.\*)

-   alert.ack
-   alert.silence
-   alert.clear

------------------------------------------------------------------------

### 4.4 Host (host.\*)

-   host.status
-   host.uptime
-   host.version

------------------------------------------------------------------------

### 4.5 Service (service.\*)

-   service.start
-   service.stop
-   service.restart
-   service.status

Required: - serviceId

Forbidden: - system commands

------------------------------------------------------------------------

### 4.6 Radio (radio.\*)

Read-only: - radio.hf.query - radio.hf.status

Future (gated): - radio.hf.set.freq - radio.hf.set.mode - radio.hf.ptt

- radio.tune
- radio.log_qso
- radio.atas_tune 

------------------------------------------------------------------------

### 4.7 Page Lifecycle (page.\*)

-   page.enter
-   page.exit

------------------------------------------------------------------------
### 4.8 POTA (pota.*)

-   pota.select_band
-   pota.select_park

--------------------
### 4.81 Node (node.*)

-   node.reboot


----------------------------------------------------------------------------------------------------------------------------
## 5. Safety Model

### Safe

-   navigation
-   browse
-   cancel

### Context

-   ui.ok

### Maintenance

-   ui.reload

### Controlled

-   system.shutdown

Rules: - validation required - rejection logged - no bypass

------------------------------------------------------------------------

## 6. External Control

Meshtastic emits intents: - no direct execution - allow-listed only -
same validation path

------------------------------------------------------------------------

## 7. Forward Compatibility

-   add new intents freely
-   do not change meaning
-   deprecate safely
-   ignore unknown intents

------------------------------------------------------------------------

## 8. Invariants

1.  all inputs → intents
2.  intents declarative
3.  no implementation details
4.  safety enforced uniformly
5.  controller is authority

------------------------------------------------------------------------

## 9. What NOT To Do

Do NOT: - create transport-specific intents - bypass controller - embed
logic in panel - duplicate control vocabularies

------------------------------------------------------------------------

## 10. Usage

Use this document when: - adding controls - adding UI features - adding
automation - reviewing safety

------------------------------------------------------------------------

## 11. Relationship to Event System

Intents are transported via rt:ui:intents.

They are:

- the ONLY control input path
- consumed by the controller
- translated into state changes

Intents do NOT directly trigger UI updates.

UI updates occur only via:
- state changes in Redis
- ui.projection.changed events on rt:ui:bus

------------------------------------------------------------------------

## Summary

This document aligns RollingThunder with: - physical control panels -
unified intent pipeline - safe system-level actions (reload, shutdown)