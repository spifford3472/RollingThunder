# RollingThunder Parking Lot (Post-Beta) #

This document holds valuable ideas that are intentionally deferred
until the beta milestone is complete.

## Beta definition (do not expand casually) ##
Beta = config-driven rt-display runtime renders all planned pages/panels
read-only, honors refresh/bindings/layout, and kiosk remains stable.

---

## 1) UI Polish (Post-Beta)
- [ ] Tighten spacing in alerts_overlay when empty
- [ ] Consider color contrast tweak for topbar time
- [ ] Consider adding UTC time for reference in topbar
- [ ] Explore collapsing node_health_summary rows on small screens
- [ ] Add subtle animation for alert appearance (non-distracting)
- [ ] Add weather to topbar, specifically temperature in F and C (F/C)
- [ ] Top bar stretch entire width of top larger font
- [ ] Alerts at bottom of page?
- [ ] Nodes and deploy/drift next to each other in middle
- [ ] Change temp on topbar to location driven from NOAA
- [ ] Add SFI; K; and Sunspot # to weather

## 2) Hardware / Physical Controls (Beta-plus)
- [ ] Build physical control panel (buttons + rotary encoder)
- [X] RGB feedback rules (focus, severity, alert state)
- [ ] Enclosure + mounting

## 3) Thermal / Power (Beta-plus)
- [X] Add temp sensors + fan controller (Pi cooling)
- [ ] Define thermal policy (thresholds, hysteresis, fail-safe)
- [ ] Power down Raspberry Pis that overheat
- [ ] Potentially add computer controlled fans
- [ ] Can we build something to power on a Raspberry Pi when temp drops?  Does this make sense

## 4) Operational Hardening (Beta-plus)
- [ ] Auto-restart kiosk on crash
- [ ] Watchdog / health indicator LED
- [ ] “safe mode” boot page
- [ ] Add a internet "Keep-Alive function" so hotspot does not go to sleep
- [ ] Create admin screen and add recompute pota park tile data (used when a new file is loaded)
- [ ] Move LED capability hints into a projection, this makes the sender even thinner
                    rt:ui:controls
                    {
                    "primary": { "available": true },
                    "mode": { "browsable": true }
                    }

## 5) Stretch Ideas / R&D
- [ ] If rt-radio is offline then the HF Panel should not be available to select
- [ ] If rt-wpsd is offline then the future panel DMR should not be available


Rules:
- No items here block beta completion
- No work on these until all planned panels are functional
