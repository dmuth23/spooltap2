# SpoolTap

[![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=dmuth23&repository=spooltap2&category=integration)
[![GitHub release](https://img.shields.io/github/v/release/dmuth23/spooltap2)](https://github.com/dmuth23/spooltap2/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Phone-NFC filament tracking for Home Assistant, native to [Bambuddy](https://bambuddy.io) — no Spoolman.**

Tap the NFC tag on an AMS slot, then tap the tag on a spool — done. SpoolTap records which
spool is in which slot and Bambuddy does the rest: inventory, weight tracking, and pushing the
filament profile to the printer. SpoolTap is the fast, phone-driven front end over Bambuddy's
native inventory.

> **Status: Beta.** Built and live-verified against a real Bambuddy + HAOS (entities, two-tap
> assign, weight recertification, dashboard auto-install). It works, it's in daily use by its
> author, and it's still young — expect rough edges and please [open issues](https://github.com/dmuth23/spooltap2/issues).

## Features

- **Two-tap assign** — tap a slot tag → tap a spool tag → the spool is assigned to that AMS
  slot, and Bambuddy configures the tray (profile, colour, pressure advance) when the printer
  is reachable. Dropdown fallback for when you don't have the phone in hand.
- **Auto-installed dashboard** — a full SpoolTap dashboard appears in your sidebar on first
  setup. Four areas: **Assign · Bind · Modify · Spools**, with a status card that reports
  every action outcome-first (✅ / ⚠️ / ⏱).
- **Bind** — register an NFC tag to a spool or to an AMS slot, by scan or by picker. Includes
  a live slot table showing which slots have tags bound.
- **Modify / weight recertification** — load a spool (tap or picker), relabel, archive, or
  recertify its weight: type the gross scale reading and Bambuddy does the tare math, stamps
  `last_weighed_at`, and the weight is locked against coarse AMS-percent overwrites while
  per-print usage tracking continues from the certified value.
- **Spools tab** — read-only inventory table, plus an **unlinked-spools table** that flags any
  spool without a slicer filament profile link (those would push a generic profile on assign)
  with a pointer to the one-time Bambuddy fix.
- **AMS layout auto-derived from Bambuddy** — units, trays, labels, and the external feed are
  discovered automatically (works with the printer offline). No hardcoding.
- **13 services** (`spooltap.*`) — `resolve_tag`, `bind_tag`, `recycle_tag`, `create_spool`,
  `assign_slot`, `unassign_slot`, `modify_spool`, `weigh_spool`, `archive_spool`, `refresh`,
  `bind_slot_tag`, `clear_slot_tag`, `install_dashboard` — so everything the dashboard does is
  scriptable in your own automations.
- **Faceted pickers** — brand → type narrows the spool list as you pick.
- **Nothing is ever written to an NFC tag** — only the factory UID is read. Any NTAG-style
  sticker works.

## Screenshots

*(coming soon)*

## Requirements

- A running **[Bambuddy](https://bambuddy.io)** instance reachable from your Home Assistant
  host, with its **Spoolman sync OFF** (Bambuddy → Settings → Filament). SpoolTap uses
  Bambuddy's native inventory + tag path; it raises a notification if the sync is on.
- Home Assistant **2025.6+**.
- For the dashboard styling: the HACS frontend cards **Mushroom** and **card-mod**
  (both one-click installs from HACS → Frontend).
- A phone that can read NFC tags into Home Assistant (the HA companion app), plus NFC tags
  for your spools and AMS slots.

## Install

### 1. Get the integration (HACS)

Click the badge at the top of this page, **or** manually: HACS → ⋮ → **Custom repositories** →
add `https://github.com/dmuth23/spooltap2`, category **Integration**. (SpoolTap isn't in the
HACS default store yet, so the custom-repository step is required for now.)

Install **SpoolTap**, then **restart Home Assistant**.

While you're in HACS, also install **Mushroom** and **card-mod** (HACS → Frontend) — the
dashboard uses them for its styling.

### 2. Connect it to Bambuddy

**Settings → Devices & Services → Add Integration → SpoolTap** → enter your Bambuddy base URL
(e.g. `http://<bambuddy-host>:8000`, no trailing path — Bambuddy needs no auth token by
default).

Done. The **SpoolTap** dashboard appears in the sidebar on its own — the integration ships the
engine (services, sensors, the persisted slot→tag registry), the workflow (native `spooltap_*`
control entities + two-tap dispatch), and the dashboard, in one piece. Any spool tags already
bound in Bambuddy resolve immediately.

### 3. One-time setup: register your AMS slot tags

Stick an NFC tag on each AMS slot, then on the dashboard: **Bind** mode → **Bind Mode** on →
for each slot: tap its tag with your phone → pick that slot from the dropdown → **Bind Tag to
Slot**. The Bind page's table shows each slot flip to bound as you go. (No phone handy? The
paste field and pickers work too.)

### 4. First assign (acceptance check)

With the printer idle: tap a slot tag, then tap a tagged spool's tag. The status card walks you
through it, and the AMS tray configures itself with that spool's filament profile when the
printer is reachable. You're live.

### Troubleshooting

| Symptom | Fix |
|---|---|
| `cannot_connect` when adding the integration | Your HA host can't reach the Bambuddy URL — it's a networking problem, not a SpoolTap one. Test from HA's network: `curl http://<bambuddy-host>:8000/api/v1/printers/`. |
| Notification about **Spoolman sync being ON** | Turn it off in Bambuddy → Settings → Filament. That toggle is Bambuddy's own consumption sync — turning it off does not disturb a separate Spoolman install. |
| Dashboard missing / broken / deleted | Run the `spooltap.install_dashboard` service with `force: true` — it restores the shipped layout. |
| Scans do nothing | Confirm the HA companion app fires `tag_scanned` (Settings → Tags shows the scans), and check the status card — unknown tags say so and point you to Bind mode. |
| Dashboard looks unstyled | Install **Mushroom** and **card-mod** from HACS → Frontend, then refresh the browser. |

**Upgrading from 0.1.x, or running alongside SpoolTap V1?** See **[DEPLOY.md](DEPLOY.md)**.

## How it works

Bambuddy is the single source of truth. SpoolTap polls its REST API for spools and
assignments, mirrors them into HA sensors, and drives assignments through Bambuddy's own
endpoints — which means Bambuddy handles the physical tray configure and the per-print weight
deduction natively. The only state SpoolTap itself owns is the slot→tag registry (Bambuddy has
no per-slot tag concept); it ships empty and you build it once in Bind mode. Zero Bambuddy
code modifications — stock API only.

## License

MIT — see [LICENSE](LICENSE).
