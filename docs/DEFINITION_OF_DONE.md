# RollingThunder — Definition of Done (Beta v1) #

This document defines the criteria required to declare RollingThunder
"Beta v1 complete".

It exists to prevent scope creep, protect architectural integrity,
and ensure the system reaches a usable, stable state before polish
or expansion.

If a task or idea is not required by this document, it must not block
Beta v1 completion and should be placed in PARKING_LOT.md.
---

## 1) Core Architectural Guarantees (MANDATORY) ##
All must be true.
- [ ] rt-display runs in config-driven runtime mode
- [ ] No page layout is hardcoded in HTML
- [ ] All panels are instantiated from config/pages/*.json and config/panels/*.json
- [ ] Renderer lookup is strictly by panel.type
- [ ] Unknown panel types render a bounded error panel, not a crash
- [ ] Legacy UI exists only as a fallback, never primary
- [ ] A single runtime reload does not require rebooting the node
---
## 2) Required Pages & Panels (READ-ONLY) ##
Each must exist, render, and refresh without errors.

***Home Page***
- ✅ topbar_core
- ✅ alerts_overlay
- ✅ node_health_summary
- ✅ deploy_drift_summary
- ✅ Home page renders node health from live bindings
- ✅ No legacy fallback
- ✅ No console errors

***Other Planned Pages (as already designed)***
- [ ] All planned pages load via config
- [ ] Empty data states render clearly (no blank panels)
- [ ] Missing data does not throw JS errors

No control actions required in beta.
---
## 3) Data Binding & Refresh Semantics ##
- [ ] Every panel declares its own refresh policy
- [ ] Polling intervals respect config (no hardcoded timers)
- [ ] Panels tolerate:
        - missing fields
        - empty arrays
        - partial payloads
- [ ] A failing API endpoint does not break other panels
---
## 4) Kiosk Safety & Stability ##
- [ ] Runtime JS errors trigger legacy fallback
- [ ] UI never renders a blank screen
- [ ] Browser reload restores UI without manual steps
- [ ] No keyboard or mouse interaction required to recover
---
## 5) Controller Interaction (Minimal) ##
- [ ] Controller APIs are read-only for beta
- [ ] Expanded config bundle endpoint optional (can be post-beta)
- [ ] Drift and node health endpoints work as-is
---
## 6) Explicit Non-Goals for Beta v1 (OUT OF SCOPE) ##
These must not block beta completion:
⛔ Physical control panel (buttons, encoders, RGB)
⛔ Fan / thermal control automation
⛔ UI polish, spacing tweaks, animations
⛔ Mobile/phone layouts
⛔ Alert acknowledgement, silencing, or control
⛔ Power-loss recovery beyond browser reload
⛔ Performance optimization
⛔ Feature completeness on external integrations

If it’s not required above, it goes in the Parking Lot.
---
### 7) Beta Exit Criteria (THE LINE) ##
Beta v1 is complete when:
```
The system can boot in the vehicle, display all planned pages and panels from config, refresh continuously, survive transient failures, and be trusted not to go blank.
```
Nothing else qualifies. Nothing else is required.
---
END OF PARKING_LOT
---