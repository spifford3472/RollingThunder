# RollingThunder Parking Lot (Post-Beta) #

This document holds valuable ideas that are intentionally deferred
until the beta milestone is complete.

## Beta definition (do not expand casually) ##
Beta = config-driven rt-display runtime renders all planned pages/panels
read-only, honors refresh/bindings/layout, and kiosk remains stable.

---

## 1) UI Polish (Post-Beta)
- [X] Tighten spacing in alerts_overlay when empty
- [X] Consider color contrast tweak for topbar time
- [X] Consider adding UTC time for reference in topbar
- [X] Explore collapsing node_health_summary rows on small screens
- [X] Add subtle animation for alert appearance (non-distracting)
- [X] Add weather to topbar, specifically temperature in F and C (F/C)
- [X] Top bar stretch entire width of top larger font
- [X] Alerts at bottom of page?
- [X] Nodes and deploy/drift next to each other in middle
- [X] Change temp on topbar to location driven from NOAA
- [ ] Add SFI; K; and Sunspot # to weather

## 2) Hardware / Physical Controls (Beta-plus)
- [X] Build physical control panel (buttons + rotary encoder)
- [X] RGB feedback rules (focus, severity, alert state)
- [X] Enclosure + mounting

## 3) Thermal / Power (Beta-plus)
- [X] Add temp sensors + fan controller (Pi cooling)
- [X] Define thermal policy (thresholds, hysteresis, fail-safe)
- [ ] Power down Raspberry Pis that overheat
- [X] Potentially add computer controlled fans
- [ ] Can we build something to power on a Raspberry Pi when temp drops?  Does this make sense

## 4) Operational Hardening (Beta-plus)
- [X] Auto-restart kiosk on crash
- [X] Watchdog / health indicator LED
- [?] “safe mode” boot page
- [ ] Add a internet "Keep-Alive function" so hotspot does not go to sleep
- [ ] Create admin screen and add recompute pota park tile data (used when a new file is loaded)
- [X] Move LED capability hints into a projection, this makes the sender even thinner
                    rt:ui:controls
                    {
                    "primary": { "available": true },
                    "mode": { "browsable": true }
                    }

## 5) Stretch Ideas / R&D
- [ ] If rt-radio is offline then the HF Panel should not be available to select
- [ ] If rt-wpsd is offline then the future panel DMR should not be available
## 6) Performance
- [ ] Reduce UI refresh scope so ui.browse.delta only updates the active panel instead of triggering full panel/page re-renders
- [ ] Eliminate flicker in controller_services_summary by replacing any mountCurrentPage() calls with targeted refreshPanelBindings(panelId)
- [ ] Validate changed_keys → panel mapping to ensure browse updates do not cause unrelated panel refreshes
- [ ] Investigate occasional 1–2 second UI pauses during extreme encoder scrolling; likely remaining source is renderer-specific DOM cost or residual projection/data refresh coalescing, but normal-use behavior is now acceptable.

Rules:
- No items here block beta completion
- No work on these until all planned panels are functional
