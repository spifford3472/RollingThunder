// nav_machine.js

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
  let _order = [];
  let _slotById = new Map();
  let _idx = -1;
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

  // NEW: allow specifying initial focus (or none)
  function setPageModel({ focusablePanelIds, slotByPanelId, initialPanelId = null }) {
    _order = uniq(focusablePanelIds || []);
    _slotById = slotByPanelId || new Map();

    if (initialPanelId && _order.includes(initialPanelId)) {
      _idx = _order.indexOf(initialPanelId);
      _setActive(initialPanelId);
      return;
    }

    // IMPORTANT: if initialPanelId is null/empty, start with NO focus
    _idx = -1;
    _setActive(null);
  }

  function panelNext() {
    if (_order.length === 0) return;
    if (_idx < 0) _idx = 0;
    else _idx = (_idx + 1) % _order.length;
    _setActive(_order[_idx]);
  }

  function panelPrev() {
    if (_order.length === 0) return;
    if (_idx < 0) _idx = _order.length - 1;
    else _idx = (_idx - 1 + _order.length) % _order.length;
    _setActive(_order[_idx]);
  }

  function clearFocus() {
    _idx = -1;
    _setActive(null);
  }

  function getState() {
    return { activePanelId: _activeId, hasFocus: _idx >= 0, focusableCount: _order.length };
  }

  return { setPageModel, panelNext, panelPrev, clearFocus, getState };
}