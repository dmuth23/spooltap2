"""Async client for the stock Bambuddy HTTP API.

SpoolTap V2 owns its own copy of this client (it reuses thegrove's proven `_request`
pattern but is NOT thegrove — no import, no edits). Every call shape here was proven
6/6 green live by the despoolman-alpha harness; do not re-derive them.

Contract (BB `/api/v1`):
  - list_spools      GET  /inventory/spools?include_archived=
  - get_spool        GET  /inventory/spools/{id}
  - list_assignments GET  /inventory/assignments
  - create_spool     POST /inventory/spools                 body {"material": ...}
  - link_tag         PATCH /inventory/spools/{id}/link-tag  body {tag_uid,tag_type,data_origin}
  - clear_tag        PATCH /inventory/spools/{id}           body {"tag_uid": null} (plain, skips checks)
  - resolve_tag      POST /spoolbuddy/nfc/tag-scanned       body {device_id,tag_uid} -> {matched,spool_id}
  - assign_slot      POST /inventory/assignments            body {spool_id,printer_id,ams_id,tray_id}
  - archive_spool    POST /inventory/spools/{id}/archive    (no body)

Canonicalization: pass the FULL HA tag_id to BOTH link_tag and resolve_tag; BB
normalizes on write and scan by stripping non-hex chars, then keeping the last 16,
uppercased — `canonical_tag` below mirrors that exactly.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import re
from typing import Any

from aiohttp import ClientSession, ClientTimeout

_LOGGER = logging.getLogger(__name__)
_TIMEOUT = ClientTimeout(total=15)
API = "/api/v1"


class BambuddyApiError(RuntimeError):
    """Non-2xx response from Bambuddy (except link-tag 409, which is handled inline)."""

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"Bambuddy {status}: {detail}")


def _detail(text: str) -> str:
    try:
        data = _json.loads(text)
    except Exception:  # noqa: BLE001
        return text[:300]
    if isinstance(data, dict) and "detail" in data:
        return str(data["detail"])
    return text[:300]


def _maybe_json(text: str) -> Any:
    if not text:
        return None
    try:
        return _json.loads(text)
    except Exception:  # noqa: BLE001
        return text


def _norm_params(params: dict[str, Any] | None) -> dict[str, str] | None:
    """FastAPI query-param contract: bools -> 'true'/'false', drop None."""
    if not params:
        return None
    out: dict[str, str] = {}
    for key, value in params.items():
        if value is None:
            continue
        if value is True:
            out[key] = "true"
        elif value is False:
            out[key] = "false"
        else:
            out[key] = str(value)
    return out


_NON_HEX = re.compile(r"[^0-9a-fA-F]")


def canonical_tag(tag_uid: str) -> str:
    """The form BB stores/matches, mirroring BB's normalize_tag_uid: strip every
    non-hex char FIRST, then keep the last 16, uppercased (BB strips server-side
    on link, so separator-bearing HA tag ids must be stripped here too)."""
    return _NON_HEX.sub("", tag_uid or "")[-16:].upper()


class BambuddyRestClient:
    """Thin async wrapper over the stock Bambuddy HTTP API."""

    def __init__(
        self, session: ClientSession, base_url: str, *, api_key: str | None = None
    ) -> None:
        self._session = session
        self._base = base_url.rstrip("/")
        self._api_key = api_key
        self._recycle_lock = asyncio.Lock()  # serialize recycle (single-flight per client)

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self._api_key} if self._api_key else {}

    async def _request(
        self, method: str, path: str, *, params=None, json=None
    ) -> Any:
        url = f"{self._base}{API}{path}"
        async with self._session.request(
            method,
            url,
            headers=self._headers(),
            params=_norm_params(params),
            json=json,
            timeout=_TIMEOUT,
        ) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise BambuddyApiError(resp.status, _detail(text))
            return _maybe_json(text)

    # ----------------------------------------------------------------- reads
    async def health(self) -> Any:
        """Raising probe for the config flow (a bad host surfaces here)."""
        return await self._request("GET", "/printers/")

    async def list_spools(self, include_archived: bool = False) -> list[dict]:
        data = await self._request(
            "GET", "/inventory/spools", params={"include_archived": include_archived}
        )
        return data if isinstance(data, list) else []

    async def get_spool(self, spool_id: int) -> dict | None:
        data = await self._request("GET", f"/inventory/spools/{spool_id}")
        return data if isinstance(data, dict) else None

    async def list_assignments(self) -> list[dict]:
        data = await self._request("GET", "/inventory/assignments")
        return data if isinstance(data, list) else []

    async def get_printers(self) -> list[dict]:
        data = await self._request("GET", "/printers/")
        return data if isinstance(data, list) else []

    async def get_printer_status(self, printer_id: int) -> dict | None:
        """AMS/tray state. BB caches it, so `ams` is present even when the printer is
        offline — enough to auto-derive the slot layout. None only on error."""
        try:
            data = await self._request("GET", f"/printers/{printer_id}/status")
        except BambuddyApiError:
            return None
        return data if isinstance(data, dict) else None

    async def get_ams_labels(self, printer_id: int) -> dict:
        """{ams_id(str): friendly label} e.g. {'0':'Left AMS','1':'Right AMS'}."""
        try:
            data = await self._request("GET", f"/printers/{printer_id}/ams-labels")
        except BambuddyApiError:
            return {}
        return data if isinstance(data, dict) else {}

    async def spoolman_enabled(self) -> bool:
        """True if BB<->Spoolman is ON (the native tag path requires it OFF)."""
        try:
            settings = await self._request("GET", "/settings/spoolman")
        except BambuddyApiError:
            return False
        return str((settings or {}).get("spoolman_enabled")).lower() == "true"

    # ---------------------------------------------------------------- writes
    async def create_spool(self, material: str, **fields: Any) -> dict:
        body = {"material": material}
        body.update({k: v for k, v in fields.items() if v is not None})
        data = await self._request("POST", "/inventory/spools", json=body)
        return data if isinstance(data, dict) else {}

    async def link_tag(self, spool_id: int, tag_uid: str) -> tuple[int, Any]:
        """Bind a tag. Returns (status, body). 409 => tag on another ACTIVE spool."""
        body = {"tag_uid": tag_uid, "tag_type": "generic", "data_origin": "nfc_link"}
        url = f"{self._base}{API}/inventory/spools/{spool_id}/link-tag"
        async with self._session.request(
            "PATCH", url, headers=self._headers(), json=body, timeout=_TIMEOUT
        ) as resp:
            text = await resp.text()
            if resp.status >= 400 and resp.status != 409:
                raise BambuddyApiError(resp.status, _detail(text))
            return resp.status, _maybe_json(text)

    async def clear_tag(self, spool_id: int) -> Any:
        """Null a spool's tag via the plain PATCH (skips link-tag safety checks).
        Sends null, not "" — BB's untagged auto-link pool requires tag_uid IS NULL,
        so an empty string would diverge from a BB-native clear."""
        return await self._request(
            "PATCH", f"/inventory/spools/{spool_id}", json={"tag_uid": None}
        )

    async def resolve_tag(self, tag_uid: str, device_id: str = "spooltap") -> int | None:
        body = {"device_id": device_id, "tag_uid": tag_uid}
        res = await self._request("POST", "/spoolbuddy/nfc/tag-scanned", json=body)
        if isinstance(res, dict) and res.get("matched"):
            return res.get("spool_id")
        return None

    async def recycle_tag(
        self, tag_uid: str, target_spool_id: int, source_spool_id: int | None = None
    ) -> int:
        """Frictionless recycle, single-flight + rollback-safe.

        link -> on 409 unbind the source (caller passes source_spool_id from the
        client-side resolve; we do NOT fall back to the broadcasting tag-scanned) then
        re-link. If the re-link fails, restore the tag to the source so it is NEVER
        orphaned. Serialized so an interleaved recycle can't strand the tag.
        """
        async with self._recycle_lock:
            status, _ = await self.link_tag(target_spool_id, tag_uid)
            if status != 409:
                return status
            if source_spool_id is None or source_spool_id == target_spool_id:
                return status  # can't safely move without a distinct source
            # Guard against a stale caller-side resolve: only clear the source if it
            # STILL holds this tag right now, else clear_tag would wipe an unrelated
            # binding on a spool the tag has since moved away from.
            try:
                source = await self.get_spool(source_spool_id)
            except BambuddyApiError:
                return status  # source gone — unresolved conflict, caller reports it
            if canonical_tag((source or {}).get("tag_uid") or "") != canonical_tag(tag_uid):
                return status  # tag moved on — unresolved conflict, caller reports it
            await self.clear_tag(source_spool_id)
            try:
                status, _ = await self.link_tag(target_spool_id, tag_uid)
                if status >= 400:
                    raise BambuddyApiError(status, "re-link after unbind failed")
                return status
            except Exception:
                try:  # rollback — never leave the tag orphaned
                    rb_status, _ = await self.link_tag(source_spool_id, tag_uid)
                    if rb_status < 400:
                        _LOGGER.warning(
                            "recycle re-link failed; restored tag to spool %s",
                            source_spool_id)
                    else:
                        _LOGGER.error(
                            "RECYCLE ROLLBACK FAILED (%s): tag %s orphaned (was spool %s)",
                            rb_status, tag_uid, source_spool_id)
                except Exception:  # noqa: BLE001
                    _LOGGER.error("RECYCLE ROLLBACK FAILED: tag %s orphaned (was spool %s)",
                                  tag_uid, source_spool_id)
                raise

    async def assign_slot(
        self, spool_id: int, printer_id: int, ams_id: int, tray_id: int
    ) -> dict:
        body = {
            "spool_id": spool_id,
            "printer_id": printer_id,
            "ams_id": ams_id,
            "tray_id": tray_id,
        }
        data = await self._request("POST", "/inventory/assignments", json=body)
        return data if isinstance(data, dict) else {}

    async def unassign(self, printer_id: int, ams_id: int, tray_id: int) -> Any:
        """Delete a slot's assignment (used to relocate a spool off its prior slot)."""
        return await self._request(
            "DELETE", f"/inventory/assignments/{printer_id}/{ams_id}/{tray_id}"
        )

    async def archive_spool(self, spool_id: int) -> Any:
        return await self._request("POST", f"/inventory/spools/{spool_id}/archive")

    async def modify_spool(self, spool_id: int, **fields: Any) -> dict:
        """PATCH a single spool (name->color_name, material, weight_used, weight_locked).
        BB auto-sets weight_locked=true when weight_used is sent without an explicit
        weight_locked (an explicit false in the same body wins)."""
        body = {k: v for k, v in fields.items() if v is not None}
        data = await self._request("PATCH", f"/inventory/spools/{spool_id}", json=body)
        return data if isinstance(data, dict) else {}

    async def weigh_spool(self, spool_id: int, gross_grams: float) -> dict:
        """BB-native weigh-in: POST the GROSS scale reading; BB does the tare math
        (net = gross - core_weight; weight_used = label_weight - net), corrects in
        both directions, and stamps last_scale_weight/last_weighed_at."""
        body = {"spool_id": spool_id, "weight_grams": gross_grams}
        data = await self._request(
            "POST", "/spoolbuddy/scale/update-spool-weight", json=body
        )
        return data if isinstance(data, dict) else {}
