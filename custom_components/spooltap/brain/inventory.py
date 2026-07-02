"""Inventory model + mapper: BB spool JSON -> SpoolModel, joined with slot assignments.

Pure module — no HA, no aiohttp imports — so it unit-tests fast. Adapted from thegrove's
proven `brain/inventory.py` (own copy). `remaining_grams` is DERIVED (BB has no such field).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NON_HEX = re.compile(r"[^0-9a-fA-F]")


@dataclass(frozen=True, slots=True)
class SpoolModel:
    """An HA-friendly projection of a Bambuddy native spool + its slot assignment."""

    spool_id: int
    material: str
    color_name: str | None
    rgba: str | None
    brand: str | None
    category: str | None
    label_weight: float
    weight_used: float
    core_weight: float
    remaining_grams: float
    tag_uid: str | None
    tag_type: str | None
    data_origin: str | None
    slicer_filament: str | None
    nozzle_temp_min: int | None
    nozzle_temp_max: int | None
    archived_at: str | None
    assigned_slot: str | None = None
    assigned_printer_id: int | None = None
    assigned_ams_id: int | None = None
    assigned_tray_id: int | None = None

    @property
    def display_name(self) -> str:
        parts = [p for p in (self.brand, self.color_name, self.material) if p]
        return " ".join(parts) or f"Spool {self.spool_id}"


def _f(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _i(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _s(data: dict, key: str) -> str | None:
    value = data.get(key)
    return str(value) if value not in (None, "") else None


def map_spool(data: dict, assignment: dict | None) -> SpoolModel:
    label = _f(data.get("label_weight"), 0.0)
    used = _f(data.get("weight_used"), 0.0)
    slot = printer_id = ams_id = tray_id = None
    if assignment:
        printer_id = assignment.get("printer_id")
        ams_id = assignment.get("ams_id")
        tray_id = assignment.get("tray_id")
        if None not in (printer_id, ams_id, tray_id):
            slot = f"P{printer_id}/AMS{ams_id}/Tray{tray_id}"
    return SpoolModel(
        spool_id=int(data["id"]),
        material=str(data.get("material") or "?"),
        color_name=_s(data, "color_name"),
        rgba=_s(data, "rgba"),
        brand=_s(data, "brand"),
        category=_s(data, "category"),
        label_weight=label,
        weight_used=used,
        core_weight=_f(data.get("core_weight"), 250.0),
        remaining_grams=round(label - used, 2),
        tag_uid=_s(data, "tag_uid"),
        tag_type=_s(data, "tag_type"),
        data_origin=_s(data, "data_origin"),
        slicer_filament=_s(data, "slicer_filament"),
        nozzle_temp_min=_i(data.get("nozzle_temp_min")),
        nozzle_temp_max=_i(data.get("nozzle_temp_max")),
        archived_at=_s(data, "archived_at"),
        assigned_slot=slot,
        assigned_printer_id=_i(printer_id) if printer_id is not None else None,
        assigned_ams_id=_i(ams_id) if ams_id is not None else None,
        assigned_tray_id=_i(tray_id) if tray_id is not None else None,
    )


def _assignments_by_spool(assignments) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for row in assignments if isinstance(assignments, list) else []:
        if isinstance(row, dict) and row.get("spool_id") is not None:
            out[int(row["spool_id"])] = row
    return out


def map_inventory(spools, assignments) -> dict[int, SpoolModel]:
    by_spool = _assignments_by_spool(assignments)
    out: dict[int, SpoolModel] = {}
    for row in spools if isinstance(spools, list) else []:
        if isinstance(row, dict) and row.get("id") is not None:
            model = map_spool(row, by_spool.get(int(row["id"])))
            out[model.spool_id] = model
    return out


# --------------------------------------------------------------- tag resolution
def canonical_tag(tag_uid: str | None) -> str:
    """The form BB stores/matches, mirroring BB's normalize_tag_uid: strip every
    non-hex char FIRST, then keep the last 16, uppercased. The strip matters for
    separator-bearing HA tag ids (companion-app UUIDs, dashed ESPHome ids) — BB
    strips server-side on link, so we must strip too or those tags never resolve."""
    return _NON_HEX.sub("", tag_uid or "")[-16:].upper()


def resolve_tag(inventory: dict[int, SpoolModel], tag_uid: str) -> int | None:
    """Client-side resolve: match a scanned tag against the polled inventory.

    Avoids POST /spoolbuddy/nfc/tag-scanned, which (a) flips to Spoolman when that
    integration is on and (b) broadcasts a WS event that refetches inventory on every
    open Bambuddy session + pops "New Tag Detected" on any SpoolBuddy kiosk. BB stores
    tag_uid canonicalized, so an exact canonical match is authoritative (no fuzzy path).
    """
    want = canonical_tag(tag_uid)
    if not want:
        return None
    for spool_id, model in inventory.items():
        if model.archived_at is None and canonical_tag(model.tag_uid) == want:
            return spool_id
    return None


# ------------------------------------------------------------------- AMS layout
@dataclass(frozen=True, slots=True)
class TrayState:
    """Live per-tray view from the printer status (for slot picker + guards)."""

    ams_id: int
    tray_id: int
    material: str | None
    tray_type: str | None
    active: bool  # this tray is feeding the current print


def parse_ams(status: dict | None) -> dict[tuple[int, int], TrayState]:
    """Parse BB `GET /printers/{id}/status` -> {(ams_id, tray_id): TrayState}.

    Defensive: BB/firmware shapes vary. Recognizes `ams` (list of units, each with
    `id`/`trays`) + `vt_tray`/`external` for ams 255. Returns {} when offline/empty.
    """
    out: dict[tuple[int, int], TrayState] = {}
    if not isinstance(status, dict):
        return out
    active_ext = status.get("active_extruder")
    tray_now = status.get("tray_now")

    def _add(ams_id: int, tray: dict) -> None:
        tid = _i(tray.get("id") if tray.get("id") is not None else tray.get("tray_id"))
        if tid is None:
            return
        gid = ams_id if ams_id >= 128 else ams_id * 4 + tid
        active = str(tray_now) == str(gid) if tray_now is not None else False
        out[(ams_id, tid)] = TrayState(
            ams_id=ams_id,
            tray_id=tid,
            material=_s(tray, "tray_type") or _s(tray, "material"),
            tray_type=_s(tray, "tray_type"),
            active=active,
        )

    for unit in status.get("ams", []) if isinstance(status.get("ams"), list) else []:
        if not isinstance(unit, dict):
            continue
        ams_id = _i(unit.get("id"))
        if ams_id is None:
            continue
        for tray in unit.get("tray", []) or unit.get("trays", []) or []:
            if isinstance(tray, dict):
                _add(ams_id, tray)
    for vt in status.get("vt_tray", []) if isinstance(status.get("vt_tray"), list) else []:
        if isinstance(vt, dict):
            _add(255, vt)
    _ = active_ext  # reserved for dual-nozzle active-tray refinement
    return out


def derive_slots(status: dict | None, labels: dict | None) -> list[dict]:
    """Auto-derive the AMS slot layout from BB (labels included) — no hardcoding.

    Returns [{key:'<ams>_<tray>', ams_id, tray_id, label}] for every tray BB reports,
    plus the external feed (ams 255). BB caches the layout, so this works offline.
    """
    slots: list[dict] = []
    if not isinstance(status, dict):
        return slots
    labels = labels or {}
    for unit in status.get("ams", []) if isinstance(status.get("ams"), list) else []:
        if not isinstance(unit, dict):
            continue
        ams_id = _i(unit.get("id"))
        if ams_id is None:
            continue
        label = labels.get(str(ams_id)) or f"AMS {ams_id}"
        for tray in unit.get("tray", []) or unit.get("trays", []) or []:
            tid = _i(tray.get("id") if isinstance(tray, dict) else None)
            if tid is None:
                continue
            slots.append({"key": f"{ams_id}_{tid}", "ams_id": ams_id, "tray_id": tid,
                          "label": f"{label} · Tray {tid + 1}"})
    if status.get("vt_tray"):
        slots.append({"key": "255_0", "ams_id": 255, "tray_id": 0, "label": "External"})
    return slots
