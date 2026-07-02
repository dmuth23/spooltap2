# Deploying SpoolTap V2

A portable install guide. SpoolTap is a Home Assistant custom integration that talks to a
**Bambuddy** instance over REST — Bambuddy is the single source of truth for all inventory,
tag, assignment, and weight data. **Since v0.2.0 the HACS install is the whole product**: the
integration ships the engine (sensors + `spooltap.*` services), the workflow (the two-tap
dispatch and all the `select./number./text./switch./button.spooltap_*` control entities), and
the dashboard (auto-created in your sidebar). No `packages/` file, no `configuration.yaml`
edits.

The integration stores **no** HA-instance-specific state except one thing you build yourself:
the **slot→tag registry** (which NFC tag is on which AMS slot). It ships **empty** — you
populate it once, in Bind mode. Nothing is ever written *to* an NFC tag; only its factory UID
is read.

---

## Prerequisites

- A running **Bambuddy** instance, reachable from your HA host, with the **Bambuddy↔Spoolman**
  sync **OFF** (Bambuddy → Settings → Filament). The integration raises a notification if it's
  on. This toggle is Bambuddy's own consumption sync — it is *not* the Spoolman service, so
  turning it off does not disturb any separate Spoolman you may run.
- Home Assistant **2025.6.0+**.
- For the dashboard's styling: **Mushroom** + **card-mod** (HACS → Frontend). These are the
  only pieces HACS can't bundle with the integration.

## Install

1. HACS → ⋮ → **Custom repositories** → `https://github.com/dmuth23/spooltap2`, category
   **Integration** → install **SpoolTap** → **restart Home Assistant**.
2. Settings → Devices & Services → **Add Integration** → **SpoolTap** → enter your Bambuddy
   URL. `cannot_connect` means your HA box can't reach Bambuddy — that's a networking fix
   before anything else (Bambuddy needs no auth token by default).
3. Done. The **SpoolTap** dashboard appears in the sidebar (auto-created; if you ever delete
   or break it, run the `spooltap.install_dashboard` service — `force: true` restores the
   shipped layout). Spool tags already bound in Bambuddy resolve immediately.

## Register the AMS slot tags (one time)

Bambuddy has no per-slot tag concept, so HA owns the slot→tag map — and it ships **empty**.
On the dashboard: **Bind** mode → **Bind Mode** on → for each AMS slot, tap the slot's NFC
tag → pick that slot → **Bind Tag to Slot**. The Bind page's table shows each slot as
bound/unbound. (No tag reader handy? Use the paste field or the tag-registry picker instead
of scanning.)

## Acceptance test (first assign on an IDLE printer)

Two-tap a slot then a tagged spool and confirm in Bambuddy that the spool's `tag_uid` is
still intact after the AMS echoes the assignment, and that the tray configures when the
printer is reachable. (The AMS echo *cannot* clobber an assigned spool's tag — verified in
the Bambuddy source — so this is confirmation, not discovery.)

## Weight recertification (the Modify workflow)

The Modify form loads the spool's current values — check **Remaining** against your scale
first; if it matches, there's nothing to fix. To recertify: type the **gross** scale reading
and **Save**. Bambuddy does the tare math (gross − core), records `last_weighed_at`, and the
weight is locked against Bambuddy's coarse AMS remain% sync (which is increase-only and would
clobber downward corrections); Bambuddy's precise per-print tracker keeps deducting regardless,
so tracking continues from the certified value. Never run Bambuddy's *"sync AMS weights"*
recovery tool after a recertify — it force-overwrites from remain% in both directions.

---

## Upgrading from 0.1.x (the three-piece install)

v0.1.x needed a `packages/spooltap_v2.yaml` file and a `lovelace:` YAML-mode dashboard next
to the integration. 0.2.0 replaces both with native `spooltap_*` entities and an auto-created
storage dashboard. To upgrade:

1. **Remove the old pieces from your config**: delete `packages/spooltap_v2.yaml`, the
   dashboard YAML file, and the `lovelace: dashboards: spooltap-v2:` block in
   `configuration.yaml`. (Leaving the package in place double-fires every scan; leaving the
   YAML dashboard registered squats the `spooltap-v2` url and blocks the auto-install.)
2. **HACS → update SpoolTap to 0.2.0**, then **restart once**.
3. The integration recreates the dashboard itself. Your slot→tag registry is preserved (and
   migrated to canonical tag form automatically).
4. Cosmetic cleanup: the old `stv2_*` helpers become `unavailable` registry ghosts — delete
   them in Settings → Devices & Services → **Entities** (filter `stv2`, select all, delete).

---

## Running alongside an existing SpoolTap V1 (parallel cutover)

V1 and V2 coexist cleanly: different domains and namespaces, separate dashboards, different
data stores (V1 reads Spoolman; V2 reads Bambuddy). The one rule for a live parallel run:
**only one system may actively WRITE to the AMS.** Disable these 8 V1 automations
(Settings → Automations):

| Automation (alias) | id |
|---|---|
| NFC: Scan dispatch (Slot/Spool routing) | `nfc_phase_a_dispatch` |
| NFC: Queue → fire deferred Assign on AMS load | `nfc_empty_slot_queue_watcher` |
| NFC: Commit desktop Assign (no tag) | `nfc_assign_commit_desktop` |
| BB Reconciler: 5-min poll | `nfc_bb_reconciler_poll` |
| BB Reconciler: slot-change kicker | `nfc_bb_reconciler_event` |
| BB Reconciler: startup sync | `nfc_bb_reconciler_startup` |
| BB Reconciler: manual run-now | `nfc_bb_reconciler_manual` |
| NFC: Capture scanned tag (V1's `tag_scanned` listener) | `nfc_capture_scanned_tag` |

The 8th cannot write to the AMS by itself, but left on it consumes every V2 scan into V1's
helpers and can fire V1 notifications — disable it too so V1 is fully deaf. Also set
`input_boolean.bb_reconciler_enabled` **off** (the manual automation above bypasses it, which
is why it's disabled directly).

**Verify before the first assign** — in Developer Tools → Template, confirm all 8 read `off`:

```jinja
{{ ['automation.nfc_phase_a_dispatch','automation.nfc_empty_slot_queue_watcher',
    'automation.nfc_assign_commit_desktop','automation.nfc_bb_reconciler_poll',
    'automation.nfc_bb_reconciler_event','automation.nfc_bb_reconciler_startup',
    'automation.nfc_bb_reconciler_manual','automation.nfc_capture_scanned_tag']
   | map('states') | list }}
```

**Leave running**: Profile-Sync (`nfc_profile_sync_daily`, only PATCHes Spoolman), AMS drying
(`packages/ams_dry.yaml` — a separate subsystem V2 does not replace), and the Spoolman
service/integration itself (V1 still reads it; the Bambuddy↔Spoolman *toggle* being off does
not touch it).

### If you also run a V2 on another HA box (e.g. a dev instance)
Two V2 instances pointed at the same Bambuddy both poll harmlessly, but an assign from either
box hits the same physical trays. After cutover, quiesce the non-primary V2 (disable its
integration, or simply don't drive its dashboard) so there is a single writer.
