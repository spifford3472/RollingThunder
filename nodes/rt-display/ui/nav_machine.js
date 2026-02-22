// nav_machine.js
// Minimal v1: roving focus only (PANEL_NEXT/PREV).
// No page switching, no panel-mode, no modal.

function uniq(arr) {
  const out = [];
  const seen = new Set();
  for (const x of arr) {
    const s = String(x || "").trim();
    if (!s || seen.has(s)) continue;
    seen.add(s);
    out.push(s);
  }
  return out;
}

export function createNavMachine() {
  let _order = [];          // ordered focusable panelIds
  let _slotById = new Map(); // panelId -> slotEl
  let _idx = -1;            // -1 means "no focus"
  let _activeId = null;

  function _clearActiveClass() {
    if (_activeId) {
      const el = _slotById.get(_activeId);
      if (el) el.classList.remove("rt-active");
    }
  }

  function _setActive(panelId) {
    _clearActiveClass();
    _activeId = panelId || null;
    if (_activeId) {
      const el = _slotById.get(_activeId);
      if (el) el.classList.add("rt-active");
    }
  }

  function setPageModel({ focusablePanelIds, slotByPanelId }) {
    _order = uniq(focusablePanelIds || []);
    _slotById = slotByPanelId || new Map();
    _idx = _order.length ? 0 : -1;
    _setActive(_idx >= 0 ? _order[_idx] : null);
  }

  function panelNext() {
    if (_order.length === 0) return;
    _idx = (_idx + 1) % _order.length;
    _setActive(_order[_idx]);
  }

  function panelPrev() {
    if (_order.length === 0) return;
    _idx = (_idx - 1 + _order.length) % _order.length;
    _setActive(_order[_idx]);
  }

  function getState() {
    return { activePanelId: _activeId, hasFocus: _idx >= 0, focusableCount: _order.length };
  }

  return { setPageModel, panelNext, panelPrev, getState };
}