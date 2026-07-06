"""Auto-installed SpoolTap dashboard (storage mode).

v0.3.0: the "glass cockpit" layout — dark gradient background, frosted-glass cards,
one cyan accent + semantic colors (green/amber/red/purple), a level-colored hero
status banner driven by the restructured `sensor.spooltap_status` (state = level,
message in attributes), a live AMS slot strip, and a color-swatch inventory list.
Phone-first (single column); sections widen to two columns on a PC.

HA has no public API for integrations to create dashboards, so the installer uses
the same internal path the frontend's own `lovelace/dashboards/create` WS command
takes (verified against HA 2026.6.4 source, byte-identical to current dev):
core's live DashboardsCollection -> async_create_item (its CHANGE_ADDED listener
synchronously creates the LovelaceStorage and registers the sidebar panel) ->
LovelaceStorage.async_save(config). Everything is guarded — a failure here must
never fail entry setup; `spooltap.install_dashboard` (force: true) is the
recovery/refresh lever.

Frontend prerequisites (HACS Frontend, not installable from an integration):
button-card (`custom:button-card`) + card-mod. (Mushroom is no longer used.)
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

DASHBOARD_URL_PATH = "spooltap-v2"  # storage url_paths must contain a hyphen
DASHBOARD_TITLE = "SpoolTap"
DASHBOARD_ICON = "mdi:printer-3d-nozzle"

_MODES = ["Assign", "Bind", "Modify", "Spools"]
_MODE_RGB = {
    "Assign": "59,130,246",   # blue
    "Bind": "34,197,94",      # green
    "Modify": "245,158,11",   # amber
    "Spools": "167,139,250",  # purple
}
_MODE_ICONS = {
    "Assign": "mdi:tray-arrow-down",
    "Bind": "mdi:link-variant",
    "Modify": "mdi:pencil",
    "Spools": "mdi:format-list-bulleted",
}

_BG_IMAGE = "radial-gradient(circle at 25% 12%, #1b2a4a 0%, #0b1020 55%, #05060c 100%)"

# the shared frosted-glass card surface (The Bridge's card language)
_GLASS_CSS = (
    "background: linear-gradient(145deg, rgba(24,29,46,0.72), rgba(12,16,28,0.72)); "
    "backdrop-filter: blur(18px) saturate(1.6); "
    "-webkit-backdrop-filter: blur(18px) saturate(1.6); "
    "border: 1px solid rgba(255,255,255,0.10); border-radius: 18px; "
    "box-shadow: 0 8px 30px rgba(0,0,0,0.45); "
)
# force a readable palette on native cards regardless of the user's theme
_VARS_CSS = (
    "color: #fff; --primary-text-color: #fff; "
    "--secondary-text-color: rgba(255,255,255,0.60); "
    "--paper-item-icon-color: rgba(255,255,255,0.65); "
    "--state-icon-color: rgba(255,255,255,0.65); "
    "--mdc-theme-primary: #00e5ff; "
    "--divider-color: rgba(255,255,255,0.06); "
    "--mdc-text-field-fill-color: rgba(255,255,255,0.06); "
    "--mdc-text-field-ink-color: #fff; "
    "--mdc-text-field-label-ink-color: rgba(255,255,255,0.6); "
    "--mdc-select-fill-color: rgba(255,255,255,0.06); "
    "--mdc-select-ink-color: #fff; "
    "--mdc-select-label-ink-color: rgba(255,255,255,0.6); "
    "--mdc-select-dropdown-icon-color: rgba(255,255,255,0.6); "
)
# button-card style list form of the same glass surface
_GLASS_STYLES: list[dict[str, str]] = [
    {"background": "linear-gradient(145deg, rgba(24,29,46,0.72), rgba(12,16,28,0.72))"},
    {"backdrop-filter": "blur(18px) saturate(1.6)"},
    {"-webkit-backdrop-filter": "blur(18px) saturate(1.6)"},
    {"border": "1px solid rgba(255,255,255,0.10)"},
    {"border-radius": "18px"},
    {"box-shadow": "0 8px 30px rgba(0,0,0,0.45)"},
]


def _glass_mod(extra: str = "") -> dict[str, str]:
    """card-mod glass styling for NATIVE cards (entities / markdown)."""
    return {"style": f"ha-card {{ {_GLASS_CSS}{_VARS_CSS}{extra} }}"}


def _press(button_entity: str) -> dict[str, Any]:
    """tap_action that presses one of the integration's button entities."""
    return {
        "action": "call-service",
        "service": "button.press",
        "target": {"entity_id": button_entity},
    }


def _label(text: str, icon: str, rgb: str) -> dict[str, Any]:
    """A small letter-spaced section label (The Bridge's 'RIDE OUT' style)."""
    return {
        "type": "custom:button-card",
        "name": text.upper(),
        "icon": icon,
        "show_state": False,
        "layout": "icon_name",
        "styles": {
            "card": [
                {"background": "none"},
                {"border": "none"},
                {"box-shadow": "none"},
                {"padding": "2px 4px 0 4px"},
            ],
            "icon": [{"width": "15px"}, {"color": f"rgb({rgb})"}],
            "name": [
                {"font-size": "12px"},
                {"font-weight": "700"},
                {"letter-spacing": "1.5px"},
                {"color": "rgba(255,255,255,0.60)"},
                {"justify-self": "start"},
                {"margin-left": "4px"},
            ],
        },
    }


def _action(
    name: str, icon: str, rgb: str, button_entity: str, confirm: str | None = None
) -> dict[str, Any]:
    """A Bridge-style compact action button (icon over an 11px label)."""
    tap: dict[str, Any] = _press(button_entity)
    if confirm:
        tap = {**tap, "confirmation": {"text": confirm}}
    return {
        "type": "custom:button-card",
        "name": name,
        "icon": icon,
        "tap_action": tap,
        "styles": {
            "card": [
                {"height": "58px"},
                {"padding": "6px 2px"},
                {"border-radius": "13px"},
                {"background": f"rgba({rgb},0.07)"},
                {"border": f"1px solid rgba({rgb},0.30)"},
                {"box-shadow": "0 4px 14px rgba(0,0,0,0.35)"},
            ],
            "icon": [{"width": "24px"}, {"color": f"rgb({rgb})"}],
            "name": [
                {"font-size": "11px"},
                {"margin-top": "3px"},
                {"color": "rgba(255,255,255,0.85)"},
            ],
        },
    }


def _js_card(js: str, triggers: Any = "all", padding: str = "12px 14px") -> dict[str, Any]:
    """One frosted card whose body is JS-rendered HTML (button-card custom field)."""
    return {
        "type": "custom:button-card",
        "show_name": False,
        "show_icon": False,
        "show_state": False,
        "triggers_update": triggers,
        "custom_fields": {"body": f"[[[ {js} ]]]"},
        "styles": {
            "card": [{"padding": padding}, *_GLASS_STYLES],
            "grid": [{"grid-template-areas": "'body'"}],
            "custom_fields": {"body": [{"width": "100%"}]},
        },
    }


def _hint(text: str) -> dict[str, Any]:
    """A short instructional line, frosted, at reduced weight."""
    return {
        "type": "markdown",
        "content": text,
        "card_mod": _glass_mod(
            "font-size: 12.5px; --primary-text-color: rgba(255,255,255,0.65); "
            "padding: 2px 6px;"
        ),
    }


def _mode_pill(mode: str) -> dict[str, Any]:
    rgb = _MODE_RGB[mode]
    active = f"entity.state === '{mode}'"
    return {
        "type": "custom:button-card",
        "entity": "select.spooltap_mode",
        "name": mode,
        "icon": _MODE_ICONS[mode],
        "show_state": False,
        "tap_action": {
            "action": "call-service",
            "service": "select.select_option",
            "target": {"entity_id": "select.spooltap_mode"},
            "data": {"option": mode},
        },
        "styles": {
            "card": [
                {"height": "62px"},
                {"padding": "6px 2px"},
                {"border-radius": "14px"},
                {"transition": "all 0.25s ease"},
                {
                    "background": f"[[[ return {active} ? 'rgba({rgb},0.18)' "
                    ": 'rgba(255,255,255,0.04)'; ]]]"
                },
                {
                    "border": f"[[[ return {active} ? '1px solid rgba({rgb},0.85)' "
                    ": '1px solid rgba(255,255,255,0.10)'; ]]]"
                },
                {
                    "box-shadow": f"[[[ return {active} ? '0 0 16px rgba({rgb},0.40)' "
                    ": '0 4px 14px rgba(0,0,0,0.35)'; ]]]"
                },
            ],
            "icon": [
                {"width": "24px"},
                {
                    "color": f"[[[ return {active} ? 'rgb({rgb})' "
                    ": 'rgba(255,255,255,0.45)'; ]]]"
                },
            ],
            "name": [
                {"font-size": "11px"},
                {"margin-top": "2px"},
                {
                    "color": f"[[[ return {active} ? '#fff' "
                    ": 'rgba(255,255,255,0.55)'; ]]]"
                },
            ],
        },
    }


# ---------------------------------------------------------------- hero status card
# Renders sensor.spooltap_status (state = level) as a color-coded badge + the full
# message (from attributes) + context chips (tag in hand / armed slot / age).
_HERO_JS = """
var st = states['sensor.spooltap_status'];
var lvl = st ? st.state : 'Idle';
var msg = (st && st.attributes && st.attributes.message)
  ? st.attributes.message
  : 'Ready — tap a slot tag, or use the pickers below.';
var C = {
  Idle:    ['#94a3b8', 'STANDBY'],
  Ready:   ['#00e5ff', 'READY'],
  Armed:   ['#f59e0b', 'ARMED'],
  Working: ['#00e5ff', 'WORKING'],
  Success: ['#22c55e', 'DONE'],
  Warning: ['#f59e0b', 'CHECK'],
  Error:   ['#ef4444', 'FAILED'],
  Info:    ['#94a3b8', 'INFO']
};
var m = C[lvl] || C.Idle;
var c = m[0], badge = m[1];
var pulse = (lvl === 'Working' || lvl === 'Armed') ? 'animation:stp 1.6s infinite;' : '';
function chip(t, col) {
  return `<span style='display:inline-flex;align-items:center;background:rgba(255,255,255,0.05);border:1px solid ` + (col || `rgba(255,255,255,0.14)`) + `;color:rgba(255,255,255,0.85);padding:3px 10px;border-radius:14px;font-size:11.5px;white-space:nowrap;'>` + t + `</span>`;
}
var chips = '';
var tih = states['sensor.spooltap_tag_in_hand'];
if (tih && tih.attributes) {
  var role = tih.attributes.role, det = tih.attributes.detail;
  if (role === 'spool' || role === 'slot') chips += chip('in hand: ' + (det || role));
  else if (role === 'unbound') chips += chip('in hand: unbound tag', 'rgba(245,158,11,0.5)');
}
var ar = states['sensor.spooltap_assign_result'];
if (ar && ar.attributes && ar.attributes.pending_slot_label)
  chips += chip('armed: ' + ar.attributes.pending_slot_label, 'rgba(245,158,11,0.5)');
var ts = (st && st.attributes) ? st.attributes.updated_at : null;
if (ts) {
  var mins = Math.floor((Date.now() - new Date(ts).getTime()) / 60000);
  if (mins >= 1) chips += chip(mins < 60 ? mins + 'm ago' : Math.floor(mins / 60) + 'h ago');
}
return `<style>@keyframes stp{0%,100%{opacity:1}50%{opacity:.45}}</style>`
  + `<div style='display:flex;flex-direction:column;gap:8px;width:100%;'>`
  + `<div style='display:flex;align-items:center;gap:8px;flex-wrap:wrap;'>`
  + `<span style='background:` + c + `22;color:` + c + `;border:1px solid ` + c + `66;padding:4px 12px;border-radius:16px;font-size:12px;font-weight:700;letter-spacing:1px;box-shadow:0 0 12px ` + c + `44;` + pulse + `'>` + badge + `</span>`
  + chips
  + `</div>`
  + `<div style='font-size:15px;line-height:1.45;color:rgba(255,255,255,0.92);'>` + msg + `</div>`
  + `</div>`;
"""

_HERO_CARD = _js_card(
    _HERO_JS,
    triggers=[
        "sensor.spooltap_status",
        "sensor.spooltap_tag_in_hand",
        "sensor.spooltap_assign_result",
    ],
    padding="14px 16px",
)


# ---------------------------------------------------------------- AMS slot strip
# The Bridge's tray strip, from SpoolTap's own data: one chip per AMS slot showing
# the occupying spool's color/name; the ARMED slot glows amber; a cyan dot = the
# slot has an NFC tag bound. Dynamic (slot count comes from Bambuddy at runtime).
_STRIP_JS = """
var sl = states['sensor.spooltap_slots'];
var slots = (sl && sl.attributes && sl.attributes.slots) ? sl.attributes.slots : [];
if (!slots.length) return `<div style='color:rgba(255,255,255,0.5);font-size:12.5px;'>No AMS layout from Bambuddy yet — hit Refresh.</div>`;
var ar = states['sensor.spooltap_assign_result'];
var armed = (ar && ar.attributes) ? ar.attributes.pending_slot_label : null;
var occ = {};
Object.keys(states).forEach(function (k) {
  if (k.indexOf('sensor.spooltap_spool_') === 0) {
    var e = states[k];
    if (e.state === 'unavailable' || e.state === 'unknown') return;
    var a = e.attributes || {};
    if (a.assigned_ams_id != null && a.assigned_tray_id != null)
      occ[a.assigned_ams_id + '_' + a.assigned_tray_id] = { id: a.spool_id, name: a.color_name || e.state, rgba: a.rgba };
  }
});
var out = slots.map(function (s) {
  var o = occ[s.key];
  var col = (o && o.rgba) ? ('#' + String(o.rgba).replace('#', '').substring(0, 6)) : 'rgba(255,255,255,0.05)';
  var isArmed = armed && s.label === armed;
  var short = String(s.label).replace(' AMS', '').replace('AMS ', '').replace(' · Tray ', ' ');
  var tagdot = s.tag_uid ? `<span style='position:absolute;top:4px;right:5px;width:7px;height:7px;border-radius:50%;background:#00e5ff;box-shadow:0 0 6px #00e5ff;'></span>` : ``;
  return `<div style='position:relative;flex:1;min-width:58px;height:60px;border-radius:10px;background:` + col + `;border:1px solid rgba(255,255,255,0.14);` + (isArmed ? `box-shadow:0 0 14px rgba(245,158,11,0.9),0 0 0 2px #f59e0b;` : `box-shadow:inset 0 0 0 1.5px rgba(255,255,255,0.22),inset 0 -8px 12px rgba(0,0,0,0.35);`) + `display:flex;flex-direction:column;align-items:center;justify-content:flex-end;padding-bottom:4px;'>` + tagdot
    + (o ? `<span style='font-size:9.5px;color:#fff;text-shadow:0 1px 2px #000;max-width:94%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'>#` + o.id + ` ` + o.name + `</span>` : `<span style='font-size:9.5px;color:rgba(255,255,255,0.4);'>empty</span>`)
    + `<span style='font-size:10px;font-weight:700;color:rgba(255,255,255,0.85);text-shadow:0 1px 2px #000;'>` + short + `</span></div>`;
}).join('');
return `<div style='display:flex;gap:7px;flex-wrap:wrap;'>` + out + `</div>`;
"""


# ------------------------------------------------------------- bind slot-tag list
_SLOT_TAGS_JS = """
var sl = states['sensor.spooltap_slots'];
var slots = (sl && sl.attributes && sl.attributes.slots) ? sl.attributes.slots : [];
if (!slots.length) return `<div style='color:rgba(255,255,255,0.5);font-size:12.5px;'>No AMS layout from Bambuddy yet — hit Refresh.</div>`;
var bound = slots.filter(function (s) { return s.tag_uid; }).length;
var rows = slots.map(function (s) {
  return `<div style='display:flex;align-items:center;gap:8px;padding:5px 2px;border-bottom:1px solid rgba(255,255,255,0.06);font-size:12px;'>`
    + `<span style='width:8px;height:8px;border-radius:50%;flex-shrink:0;background:` + (s.tag_uid ? `#22c55e;box-shadow:0 0 6px #22c55e` : `rgba(255,255,255,0.15)`) + `;'></span>`
    + `<span style='flex:1;color:rgba(255,255,255,0.85);'>` + s.label + `</span>`
    + `<span style='color:rgba(255,255,255,0.4);font-family:monospace;font-size:11px;'>` + (s.tag_uid ? `…` + String(s.tag_uid).slice(-8) : `no tag`) + `</span></div>`;
}).join('');
return `<div style='font-size:11px;font-weight:700;letter-spacing:1.2px;color:rgba(255,255,255,0.55);margin-bottom:4px;'>SLOT TAGS — ` + bound + `/` + slots.length + ` BOUND</div>` + rows;
"""


# ------------------------------------------------------------ modify loaded spool
_LOADED_JS = """
var ml = states['sensor.spooltap_mod_loaded'];
var sid = ml ? parseInt(ml.state) : 0;
if (!sid || sid < 1 || isNaN(sid)) return `<div style='color:rgba(255,255,255,0.5);font-size:13px;'>No spool loaded — pick one above, or scan a spool tag with no slot pending.</div>`;
var e = states['sensor.spooltap_spool_' + sid + '_info'];
var a = (e && e.attributes) ? e.attributes : ((ml.attributes) || {});
var col = a.rgba ? ('#' + String(a.rgba).replace('#', '').substring(0, 6)) : '#475569';
var left = Math.round(a.remaining_grams || 0);
var total = Math.max(1, Math.round(a.label_weight || 1000));
var pct = Math.max(0, Math.min(100, 100 * left / total));
var barc = pct < 15 ? '#ef4444' : (pct < 35 ? '#f59e0b' : '#22c55e');
var lbl = {};
var slsen = states['sensor.spooltap_slots'];
(((slsen || {}).attributes || {}).slots || []).forEach(function (s) { lbl[s.key] = s.label; });
var slotTxt = (a.assigned_ams_id != null && a.assigned_tray_id != null)
  ? (lbl[a.assigned_ams_id + '_' + a.assigned_tray_id] || a.assigned_slot)
  : a.assigned_slot;
return `<div style='display:flex;align-items:center;gap:12px;'>`
  + `<span style='width:34px;height:34px;border-radius:10px;background:` + col + `;border:1px solid rgba(255,255,255,0.25);box-shadow:0 0 10px ` + col + `66;flex-shrink:0;'></span>`
  + `<div style='flex:1;min-width:0;'>`
  + `<div style='font-size:15px;font-weight:600;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>#` + sid + ` — ` + (a.color_name || a.material || `?`) + `</div>`
  + `<div style='height:5px;border-radius:3px;background:rgba(255,255,255,0.08);margin:6px 0 4px 0;'><div style='height:5px;border-radius:3px;width:` + pct + `%;background:` + barc + `;'></div></div>`
  + `<div style='display:flex;gap:10px;font-size:11.5px;color:rgba(255,255,255,0.55);'><span>` + (a.material || `?`) + `</span><span>` + left + `g left</span>`
  + (a.tag_uid ? `<span style='color:#00e5ff;'>NFC</span>` : ``)
  + (slotTxt ? `<span style='color:#a78bfa;'>` + slotTxt + `</span>` : ``)
  + `</div></div></div>`;
"""


# ------------------------------------------------------------------ inventory list
_INVENTORY_JS = """
var rows = [];
Object.keys(states).forEach(function (k) {
  if (k.indexOf('sensor.spooltap_spool_') === 0) {
    var e = states[k];
    if (e.state === 'unavailable' || e.state === 'unknown') return;
    rows.push(e.attributes || {});
  }
});
rows.sort(function (x, y) { return (x.spool_id || 0) - (y.spool_id || 0); });
if (!rows.length) return `<div style='color:rgba(255,255,255,0.5);font-size:13px;'>No spools in Bambuddy yet.</div>`;
var noprof = rows.filter(function (a) { return !a.slicer_filament; });
var warn = noprof.length
  ? `<div style='background:rgba(245,158,11,0.08);border:1px solid rgba(245,158,11,0.35);border-radius:12px;padding:8px 12px;margin-bottom:10px;font-size:12px;color:rgba(255,255,255,0.85);'><b style='color:#f59e0b;'>` + noprof.length + ` spool` + (noprof.length > 1 ? `s` : ``) + ` without a filament profile</b> — an assign pushes a generic profile. Fix in Bambuddy: edit the spool, set Filament Profile. (` + noprof.map(function (a) { return `#` + a.spool_id; }).join(` `) + `)</div>`
  : ``;
var head = `<div style='font-size:11px;font-weight:700;letter-spacing:1.2px;color:rgba(255,255,255,0.55);margin-bottom:2px;'>INVENTORY — ` + rows.length + ` SPOOLS</div>`;
var lbl = {};
var slsen = states['sensor.spooltap_slots'];
(((slsen || {}).attributes || {}).slots || []).forEach(function (s) { lbl[s.key] = s.label; });
var list = rows.map(function (a) {
  var col = a.rgba ? ('#' + String(a.rgba).replace('#', '').substring(0, 6)) : '#475569';
  var left = Math.max(0, Math.round(a.remaining_grams || 0));
  var total = Math.max(1, Math.round(a.label_weight || 1000));
  var pct = Math.max(0, Math.min(100, 100 * left / total));
  var barc = pct < 15 ? '#ef4444' : (pct < 35 ? '#f59e0b' : '#22c55e');
  return `<div style='display:flex;align-items:center;gap:10px;padding:7px 2px;border-bottom:1px solid rgba(255,255,255,0.06);'>`
    + `<span style='width:16px;height:16px;border-radius:5px;background:` + col + `;border:1px solid rgba(255,255,255,0.25);flex-shrink:0;'></span>`
    + `<div style='flex:1;min-width:0;'>`
    + `<div style='display:flex;justify-content:space-between;font-size:12.5px;color:#fff;'><span style='overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'>#` + a.spool_id + ` ` + (a.color_name || a.material || ``) + `</span><span style='color:rgba(255,255,255,0.6);flex-shrink:0;margin-left:8px;'>` + left + `g</span></div>`
    + `<div style='height:4px;border-radius:2px;background:rgba(255,255,255,0.08);margin-top:4px;'><div style='height:4px;border-radius:2px;width:` + pct + `%;background:` + barc + `;'></div></div>`
    + `<div style='display:flex;gap:8px;font-size:10.5px;color:rgba(255,255,255,0.45);margin-top:3px;'><span>` + (a.material || `?`) + `</span>`
    + (a.brand ? `<span>` + a.brand + `</span>` : ``)
    + (a.tag_uid ? `<span style='color:#00e5ff;'>NFC</span>` : ``)
    + ((function () { var t = (a.assigned_ams_id != null && a.assigned_tray_id != null) ? (lbl[a.assigned_ams_id + '_' + a.assigned_tray_id] || a.assigned_slot) : a.assigned_slot; return t ? `<span style='color:#a78bfa;'>` + t + `</span>` : ``; })())
    + `</div></div></div>`;
}).join('');
return warn + head + `<div>` + list + `</div>`;
"""


# ------------------------------------------------------------ bind-mode toggle card
_BIND_TOGGLE = {
    "type": "custom:button-card",
    "entity": "switch.spooltap_bind_mode",
    "name": "Bind Mode",
    "icon": "mdi:link-variant",
    "show_state": True,
    "tap_action": {"action": "toggle"},
    "styles": {
        "card": [
            {"height": "58px"},
            {"border-radius": "13px"},
            {"transition": "all 0.25s ease"},
            {
                "background": "[[[ return entity.state==='on' ? 'rgba(34,197,94,0.14)' "
                ": 'rgba(255,255,255,0.04)'; ]]]"
            },
            {
                "border": "[[[ return entity.state==='on' ? '1px solid rgba(34,197,94,0.8)' "
                ": '1px solid rgba(255,255,255,0.10)'; ]]]"
            },
            {
                "box-shadow": "[[[ return entity.state==='on' ? '0 0 16px rgba(34,197,94,0.45)' "
                ": '0 4px 14px rgba(0,0,0,0.35)'; ]]]"
            },
        ],
        "icon": [
            {"width": "24px"},
            {
                "color": "[[[ return entity.state==='on' ? '#22c55e' "
                ": 'rgba(255,255,255,0.45)'; ]]]"
            },
        ],
        "name": [{"font-size": "12px"}, {"color": "#fff"}],
        "state": [
            {"font-size": "10px"},
            {"text-transform": "uppercase"},
            {"letter-spacing": "1px"},
            {
                "color": "[[[ return entity.state==='on' ? '#22c55e' "
                ": 'rgba(255,255,255,0.5)'; ]]]"
            },
        ],
    },
}


def _mode_section(mode: str, cards: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "grid",
        "column_span": 1,
        "visibility": [
            {"condition": "state", "entity": "select.spooltap_mode", "state": mode}
        ],
        "cards": [_label(mode, _MODE_ICONS[mode], _MODE_RGB[mode]), *cards],
    }


_ASSIGN_CARDS: list[dict[str, Any]] = [
    _hint(
        "Tap a **slot tag**, then a **spool tag** — or use the pickers. Assigning "
        "writes Bambuddy; the tray auto-configures when the printer is online."
    ),
    _js_card(_STRIP_JS),
    {
        "type": "horizontal-stack",
        "cards": [
            _action("Cancel", "mdi:cancel", "239,68,68", "button.spooltap_cancel"),
            _action("Refresh", "mdi:refresh", "59,130,246", "button.spooltap_refresh"),
        ],
    },
    {
        "type": "entities",
        "entities": [
            {"entity": "select.spooltap_assign_slot", "name": "Slot"},
            {"entity": "select.spooltap_assign_brand", "name": "Brand"},
            {"entity": "select.spooltap_assign_type", "name": "Type"},
            {"entity": "select.spooltap_assign_spool", "name": "Spool (unassigned)"},
        ],
        "title": "Manual assign (no scan)",
        "card_mod": _glass_mod(),
    },
    _action(
        "Assign spool to slot",
        "mdi:tray-arrow-down",
        "59,130,246",
        "button.spooltap_assign",
    ),
]

_BIND_CARDS: list[dict[str, Any]] = [
    _hint(
        "Turn **Bind Mode ON**, give a tag (scan · registry pick · paste a UID), "
        "pick a spool **or** a slot, then Bind. Auto-clears when you leave this tab."
    ),
    {
        "type": "horizontal-stack",
        "cards": [
            _BIND_TOGGLE,
            _action("Refresh", "mdi:refresh", "34,197,94", "button.spooltap_refresh"),
        ],
    },
    {
        "type": "entities",
        "entities": [
            {"entity": "select.spooltap_bind_tag", "name": "Tag from registry"},
            {"entity": "text.spooltap_tag_input", "name": "…or paste a Tag UID"},
        ],
        "title": "Tag source",
        "card_mod": _glass_mod(),
    },
    {
        "type": "entities",
        "entities": [
            {"entity": "select.spooltap_bind_pool", "name": "Pool"},
            {"entity": "select.spooltap_bind_brand", "name": "Brand"},
            {"entity": "select.spooltap_bind_type", "name": "Type"},
            {"entity": "select.spooltap_bind_spool", "name": "Spool"},
        ],
        "title": "Bind tag → spool",
        "card_mod": _glass_mod(),
    },
    _action(
        "Bind Tag to Spool",
        "mdi:link-variant-plus",
        "34,197,94",
        "button.spooltap_bind_spool",
    ),
    {
        "type": "entities",
        "entities": [{"entity": "select.spooltap_bind_slot", "name": "Slot"}],
        "title": "Bind tag → slot",
        "card_mod": _glass_mod(),
    },
    _action(
        "Bind Tag to Slot", "mdi:tray-plus", "34,197,94", "button.spooltap_bind_slot"
    ),
    _js_card(_SLOT_TAGS_JS),
]

_MODIFY_CARDS: list[dict[str, Any]] = [
    _hint(
        "Load a spool (pick below, or scan its tag with no slot pending), edit, then "
        "**Save**. Deep edits (colour, cost, vendor) live in Bambuddy."
    ),
    {
        "type": "entities",
        "entities": [
            {"entity": "select.spooltap_mod_open_tag", "name": "Open by Tag"},
            {"entity": "select.spooltap_mod_open_spool", "name": "Open by Spool"},
        ],
        "title": "Open a spool",
        "card_mod": _glass_mod(),
    },
    _js_card(_LOADED_JS),
    {
        "type": "entities",
        "entities": [
            {"entity": "text.spooltap_mod_name", "name": "Name (→ colour name)"},
            {"entity": "select.spooltap_mod_material", "name": "Material"},
            {"entity": "number.spooltap_mod_core", "name": "Empty-spool / tare (g)"},
            {"entity": "number.spooltap_mod_gross", "name": "Weigh-in: gross on scale (g)"},
            {"entity": "number.spooltap_mod_net", "name": "Remaining (g)"},
        ],
        "title": "Edit loaded spool",
        "card_mod": _glass_mod(),
    },
    _hint(
        "**Recertify** — the fields load the spool's *current* values, so first check "
        "**Remaining** against your scale; if it matches, there's nothing to fix. "
        "Otherwise: spool on the scale, type the **gross** reading, **Save** — Bambuddy "
        "does the tare math and locks the weight against the coarse AMS remain% sync; "
        "per-print tracking continues from it. *(Typing Remaining directly also works.)*"
    ),
    {
        "type": "horizontal-stack",
        "cards": [
            _action("Save", "mdi:content-save", "34,197,94", "button.spooltap_save"),
            _action(
                "Archive",
                "mdi:archive-arrow-down",
                "239,68,68",
                "button.spooltap_archive",
                confirm="Archive this spool? Its NFC tag becomes free to recycle.",
            ),
            _action(
                "Close", "mdi:close-circle-outline", "148,163,184", "button.spooltap_close"
            ),
            _action("Refresh", "mdi:refresh", "245,158,11", "button.spooltap_refresh"),
        ],
    },
]

_SPOOLS_CARDS: list[dict[str, Any]] = [
    _js_card(_INVENTORY_JS),
    _action("Refresh", "mdi:refresh", "167,139,250", "button.spooltap_refresh"),
]

DASHBOARD_CONFIG: dict[str, Any] = {
    "views": [
        {
            "title": "SpoolTap",
            "path": "main",
            "icon": DASHBOARD_ICON,
            "type": "sections",
            "max_columns": 2,
            "background": {"image": _BG_IMAGE},
            "sections": [
                {
                    "type": "grid",
                    "cards": [
                        {
                            "type": "horizontal-stack",
                            "cards": [_mode_pill(m) for m in _MODES],
                        },
                        _HERO_CARD,
                    ],
                },
                _mode_section("Assign", _ASSIGN_CARDS),
                _mode_section("Bind", _BIND_CARDS),
                _mode_section("Modify", _MODIFY_CARDS),
                _mode_section("Spools", _SPOOLS_CARDS),
            ],
        }
    ]
}


def _get_core_dashboards_collection(hass: HomeAssistant):
    """Core's LIVE DashboardsCollection, via the registered WS create handler.

    LovelaceData holds no collection reference (it is a local in lovelace's
    async_setup), so the only handle is the bound WS handler — the same object
    the frontend's own dashboards UI mutates. Returns None when unreachable.
    """
    from homeassistant.components.lovelace.dashboard import DashboardsCollection

    try:
        handler, _ = hass.data["websocket_api"]["lovelace/dashboards/create"]
        ws = handler.__wrapped__.__wrapped__.__self__  # require_admin -> async_response
        coll = ws.storage_collection
    except (KeyError, AttributeError):
        return None
    return coll if isinstance(coll, DashboardsCollection) else None


async def async_ensure_dashboard(hass: HomeAssistant, *, force: bool = False) -> bool:
    """Create the SpoolTap dashboard if missing; overwrite its config when force.

    Returns True when the dashboard exists (created or already present) and, if
    created/forced, its config was saved. Never raises to the caller's setup path
    — errors are logged and False is returned (the install_dashboard service is
    the retry lever).
    """
    try:
        from homeassistant.components import frontend
        from homeassistant.components.lovelace import _register_panel
        from homeassistant.components.lovelace.const import LOVELACE_DATA, MODE_STORAGE
        from homeassistant.components.lovelace.dashboard import (
            DashboardsCollection,
            LovelaceStorage,
        )

        lovelace = hass.data.get(LOVELACE_DATA)
        if lovelace is None:
            _LOGGER.warning("dashboard install skipped: lovelace not set up")
            return False

        existed = DASHBOARD_URL_PATH in lovelace.dashboards
        if not existed:
            coll = _get_core_dashboards_collection(hass)
            own_instance = coll is None
            if own_instance:
                # Fallback: our own collection instance persists the registry entry
                # but core's CHANGE_ADDED listener won't fire — register the storage
                # + panel by hand. (Core's instance could clobber the registry file
                # on a later save of its own; install_dashboard recovers.)
                _LOGGER.warning(
                    "dashboard install: core dashboards collection unreachable, "
                    "using fallback instance"
                )
                coll = DashboardsCollection(hass)
                await coll.async_load()
            item = next(
                (
                    i
                    for i in coll.async_items()
                    if i.get("url_path") == DASHBOARD_URL_PATH
                ),
                None,
            )
            if item is None:
                item = await coll.async_create_item(
                    {
                        "url_path": DASHBOARD_URL_PATH,
                        "title": DASHBOARD_TITLE,
                        "icon": DASHBOARD_ICON,
                        "show_in_sidebar": True,
                        "require_admin": False,
                    }
                )
            if DASHBOARD_URL_PATH not in lovelace.dashboards:
                # only reachable on the fallback path (no core listener ran)
                lovelace.dashboards[DASHBOARD_URL_PATH] = LovelaceStorage(hass, item)
                if not frontend.async_panel_exists(hass, DASHBOARD_URL_PATH):
                    _register_panel(hass, DASHBOARD_URL_PATH, MODE_STORAGE, item, False)

        if existed and not force:
            return True  # never overwrite a dashboard the user may have customized
        await lovelace.dashboards[DASHBOARD_URL_PATH].async_save(DASHBOARD_CONFIG)
        _LOGGER.info(
            "SpoolTap dashboard %s at /%s",
            "refreshed" if existed else "created",
            DASHBOARD_URL_PATH,
        )
        return True
    except Exception:  # noqa: BLE001 — internal lovelace APIs; never break setup
        _LOGGER.exception(
            "SpoolTap dashboard auto-install failed; run spooltap.install_dashboard "
            "to retry, or add the dashboard manually (see DEPLOY.md)"
        )
        return False
