# Navimower

Alpha Home Assistant integration for Segway Navimow robot mowers.

Navimower combines two cloud connections in one config entry:

- **Private app cloud** — map geometry, real zone names and IDs, Off-limit and
  VF-off areas, mapped Channels, settings, schedules, commands, maintenance and
  stable cloud state.
- **Official Smart Home OAuth + MQTT** — dense local `X`, `Y`, heading, battery
  and live mower events used for exact route history, physical-zone detection,
  stable progress and gate automations.

Navimower does **not** require the older NavimowHA integration from v0.2.0
onward.

> [!WARNING]
> Navimower uses an undocumented private cloud protocol. It is not affiliated
> with or supported by Segway, Ninebot, Navimow or Willand. A mower is a moving
> machine with a cutting blade; verify commands and physical-gate automations in
> a safe environment.

## Project origins

The private-cloud foundation used by Navimower was created by **Roberto
Gualandris** in [ilguala/navimow_pro](https://github.com/ilguala/navimow_pro).
Roberto reverse-engineered the Navimow mobile application's private-cloud
communication and implemented the authentication, encrypted protocol, map
decoding and control foundation on which this integration builds.

Navimower extends that work with the official Smart Home OAuth/MQTT connection
to obtain dense live mower events and position data without repeatedly polling
the private cloud. It also adds persistent route history, physical-zone and gate
logic, and a separately maintained
[Navimower Map Card](https://github.com/vahesoo/navimower-map-card) for the map,
Mow Now controls and schedule editing.

A sincere thank you to Roberto for the excellent reverse-engineering work and
for making the original project available to the community.

## Recommended account arrangement

Use a dedicated shared Navimow account for the private app-cloud session and
share the mower to it from the primary account:

```text
Primary account  -> official phone app and Smart Home OAuth
Shared account   -> Navimower private-cloud login only
```

Do not sign the dedicated shared account into the phone app after setup. Field
testing showed that a phone login can invalidate the Home Assistant private
session. The private-cloud password is used only during setup or
reauthentication and is not stored.

The Smart Home OAuth account may be different from the private-cloud account as
long as both can access the same mower. Navimower matches the mower by its stored
identity before starting MQTT.

> [!IMPORTANT]
> For multiple mowers, use a separate dedicated private-cloud account for each
> Navimower config entry. Reusing one private-cloud account across parallel
> entries is currently unsupported and has caused session invalidation during
> field testing.

Multiple-mower support is present: each mower can be added as its own Navimower
config entry and receives its own entities, map and history. This area is still
experimental and needs further development and broader testing, especially when
several mowers are accessed through the same private-cloud account.

## Main features

### Map and zones

- decoded private-cloud map geometry;
- real zone names, IDs and areas;
- boundaries and per-zone map settings;
- Off-limit and VF-off areas;
- mapped Channels and charging station;
- temporary app doodle metadata with original vendor SVG, center, direction,
  scale, creation time and expiry time;
- global and per-zone cutting height on models that report a trustworthy remote height;
- app-like active-zone progress, finished area, last mowing time and last completed time;
- cached map data retained through temporary private-cloud outages;
- automatic migration and refresh of older cached map schemas.

### Persistent mowing history

Navimower records live MQTT samples in Home Assistant, not in the browser or
Recorder. Each retained sample includes:

```text
timestamp, X, Y, heading, activity, MQTT vehicle state, MQTT action
```

A session starts when cutting begins, survives normal pause/transit and
optionally the return-to-dock route, and normally ends when the mower is docked.
If Navimow finishes one practical mowing cycle and immediately starts another
pass while the scheduled window is still open, a same-zone progress reset creates
a new session even though the mower never docked. This lets the active-cycle
route start cleanly while the previous route remains in history. A successful
`navimower.mow` call with `reset: true` creates that boundary immediately, even
when the previous pass stopped at 50%; `reset: false` keeps the existing vendor
progress and current cycle.

If a Stop/Start, integration reload or Home Assistant restart creates separate
history fragments, fragments with a gap of up to **five minutes** are joined
into the same logical session. Intentional cycle-reset boundaries are never
merged. A normal paused session already remains active without needing this
merge rule; separated fragments beyond five minutes stay separate.

Merged sessions retain separate route segments. Route samples that were not
received during an interruption cannot be reconstructed, and the map API does
not draw a false connecting line across that gap. The session count, start time
and end time still represent one mowing job. Navimow can legitimately finish at
97-99% when only inaccessible or obstructed remnants remain; a vendor-ended cycle
at 95% or above is therefore retained as completed instead of requiring exactly
100%.

Trail retention is configurable:

- 3 days;
- 7 days (default);
- 14 days;
- 30 days;
- unlimited.

`Unlimited` preserves every completed route and can grow both Home Assistant
`.storage` usage and the current map payload substantially over time. Active
history is checkpointed periodically and completed sessions are stored
individually in Home Assistant `.storage`.

### Physical zones, mapped Channels, Gate areas and gates

- current physical zone derived from fresh MQTT `X/Y` and decoded polygons;
- target zone kept separate from the physical zone;
- current mapped Channel detection;
- zone-transition sensor;
- optional rectangular local-coordinate **Gate areas**;
- user-friendly zone-pair gate configuration;
- bidirectional gates enabled by default;
- optional one-way `Zone A -> Zone B` operation;
- gate close delay of 0, 10, 20 or 30 seconds;
- gate-required sensor remains latched through the configured arrival delay.

The intention-based gate sensor can open a gate before the mower reaches it.
Gate areas remain available as a precise physical fallback. Gate and Gate-area
safety uses only a fresh MQTT position; stale private-cloud fallback coordinates
are never used to issue a false physical close signal.

When mowing is started from Home Assistant with an explicit ordered zone list,
Navimower latches the first requested zone immediately. This command intent has
priority over short-lived stale vendor target fields, so `gate_required` can
open the gate at departure instead of waiting for the mower to enter the
physical Gate area. An in-flight latch keeps its original direction until the
mower reaches the destination zone and the configured close delay expires.

Normal empty navigation states are exposed as readable values (`No active
target`, `Not in channel`, or a last-known/stale physical zone) rather than
generic `unknown`. Current channel keeps its last confirmed display value when
the pose stream ages instead of alternating with `Position unavailable`; a
confirmed dock is reported as `Not in channel`. The sensor exposes source,
staleness and pose age, while all Gate and Gate-area safety decisions continue
to require a fresh MQTT position. During a brief start, pause or dock
acknowledgement, the lawn-mower entity preserves the explicit command activity
instead of falling back to a false Docked state. Target and gate attributes
include the chosen source, command age, direction and pose validity for
troubleshooting.

### Mower entities and controls

Depending on mower model and firmware, Navimower provides:

- `lawn_mower` controls;
- battery, state and maintenance sensors;
- explicit Task progress, Map coverage, Task/Map mowed area and Map area
  sensors;
- one enabled Coverage sensor per mapped mowing zone;
- optional per-zone Area, Mowed area, Last mowed and Last completed sensors,
  disabled by default;
- a disabled diagnostic Route progress sensor for the vendor route counter;
- global cutting height on supported models;
- current physical zone, target zone and current Channel sensors;
- local X/Y, heading, position-source and MQTT pose-age sensors;
- private-cloud, OAuth, MQTT, pose-valid and MQTT-stream diagnostics;
- one binary sensor per configured Gate area and gate;
- zone selector, calendar/schedule and supported setting entities;
- SVG map camera and a lightweight map-data sensor;
- `navimower.mow`, `navimower.set_schedule` and diagnostics services.

The zone Coverage entity is the normal per-zone entity. Its attributes include
the zone area, mowed area, task membership, current cycle, source and retained
timestamps. Supporting area/timestamp entities can be enabled from the entity
registry when they are useful for dashboards, statistics or automations.

Firmware-dependent settings are created only when the corresponding value is
reported by the mower. Manual-height models do not expose encoded map values as
cutting-height millimetres.

## Installation

### HACS custom repository

1. Open **HACS -> Integrations -> three-dot menu -> Custom repositories**.
2. Add `https://github.com/vahesoo/NaviMower` as category **Integration**.
3. Install Navimower and restart Home Assistant.
4. Open **Settings -> Devices & services -> Add integration -> Navimower**.

Manual installation is also possible by copying
`custom_components/navimower` into `/config/custom_components/` and restarting
Home Assistant.

## Setup flow

The initial setup creates both cloud branches before the config entry is
completed:

1. Enter the email and password of the dedicated private-cloud account.
2. Select the mower, or enter its serial number when a shared mower is not
   returned by the normal list endpoint.
3. Continue to the official Smart Home OAuth browser authorization.
4. Sign in with an account that can access the same mower.
5. Home Assistant stores both token sets in one Navimower config entry.

On every normal Home Assistant start, Navimower restores local map/session data
first, then starts private-cloud refresh and OAuth validation in parallel. MQTT
starts only after the OAuth token is valid and fresh broker credentials have
been obtained.

The two branches degrade independently:

- private cloud unavailable: cached map/entities and live MQTT history can remain
  usable;
- OAuth/MQTT unavailable: private-cloud map, settings and controls can remain
  usable;
- rejected credentials: the appropriate Navimower reauthentication flow is
  started.

OAuth token refreshes do not intentionally reload the full integration. After a
token refresh or MQTT authentication disconnect, Navimower obtains fresh MQTT
credentials before reconnecting.

## Connectivity, polling and fallback

Navimower treats MQTT transport and the live position stream as separate health
signals. `MQTT connected` means the broker connection is open; `Live position
valid` means a real X/Y packet has arrived within the freshness window. A broker
can remain connected while the location subscription is silent.

While the mower is active, a five-second watchdog checks the pose stream. When
the latest pose is more than 25 seconds old, Navimower first re-subscribes to the
location topic. If no pose arrives within 10 seconds, it rebuilds only the MQTT
client with increasing backoff. The watchdog and prolonged startup-retry loop
are registered as Home Assistant background tasks, so they do not delay
integration startup. The config entry, entities, active session and map APIs
stay loaded during recovery.

| Data | Preferred source | Fallback |
| --- | --- | --- |
| X/Y, heading and exact route | official MQTT | private-cloud location |
| live activity changes | official MQTT | private-cloud `index2` |
| battery while mowing/returning | fresh official MQTT state | private-cloud SOC, then last-known |
| battery while docked/charging | private-cloud SOC | fresh MQTT state, then last-known |
| per-zone coverage and mowed area | private-cloud `partitionPercentage` / `finishedArea`, densified for the active zone by packed `mapWorkPosition.progress` and then route progress | persisted per-zone last-known |
| Task progress | overall vendor `mowingPercentage` for the selected task | integration-side area-weighted selected-zone model, then persisted last-known |
| Task mowed area | vendor `subtotalArea` for the selected task | Task area × Task progress, then area-weighted zone state |
| Map coverage / Map mowed area | integration-side area-weighted latest value for every zone | persisted zone history |
| Map area | decoded map-zone geometry | persisted map cache |
| Route progress | official MQTT/vendor route counter | unavailable; diagnostic only |
| map, zones, settings and schedule | private cloud | persisted/local cache |
| Current channel display | fresh MQTT pose | confirmed dock or last-known display value |
| physical Gate-area/gate safety | fresh MQTT only | unavailable, never stale X/Y |

Private-cloud polling is currently intentionally aggressive while field testing:

- mowing: coordinator cycle every 5 seconds;
- returning/mapping: every 8 seconds;
- docked/idle: every 15 seconds;
- individual endpoints have separate TTLs, so slow map/maintenance data is not
  downloaded on every cycle.

A failure from one private endpoint keeps its previous value and does not erase
other entity states. Battery, zone state, task totals and map totals retain
last-known-good values through short gaps and are checkpointed for restart
recovery. A confirmed new mowing cycle resets task accounting only when that
cycle enters a zone; other zones keep their latest Map coverage until a newer
cycle reaches them. Pause/resume, charging continuation, Home Assistant restart
and transient telemetry resets do not create a new zone cycle. The integration
does not interpolate synthetic battery percentages.

Reauthentication is started only for a real authentication rejection. The
diagnostic `Position source` sensor shows whether current X/Y came from `mqtt`
or `private_cloud`; `MQTT position stream` shows states such as `live`,
`resubscribing`, `rebuilding` or `backoff`. Battery, progress, area and Current
channel entities expose their selected source and freshness context as
attributes.

## Navimower Map Card

The interactive dashboard is distributed separately:

- [vahesoo/navimower-map-card](https://github.com/vahesoo/navimower-map-card)

Install it through HACS as a **Dashboard** custom repository. HACS manages the
Lovelace resource automatically; no manual `/local/...` resource is required. A
minimal card normally needs only the mower entity:

```yaml
type: custom:navimower-map-card
entity: lawn_mower.tont
```

Use **navimower-map-card v0.1.18 or later** with Navimower v0.3.0. The current
card remains compatible through the legacy payload fields; a later card release
can consume the prepared schema-v5 zone states and daily trails directly. The
standalone card includes:

- map, zones, Off-limit, VF-off, Channel and Gate-area layers;
- a Today view plus the two preceding dates for three-day mowing history;
- Mow, Pause and Dock controls;
- ordered Mow Now zone selection with restart/continue choice;
- integrated weekly schedule editor.

The integration no longer bundles or auto-registers any Lovelace JavaScript
card. The SVG camera entity remains available for picture cards, notifications
and simple dashboards.

## Authenticated map and session APIs

Large geometry and retained route data are served through authenticated local
Home Assistant endpoints rather than repeated state attributes:

```text
GET /api/navimower/map/<entry_id>
GET /api/navimower/sessions/<entry_id>
GET /api/navimower/session/<entry_id>/<session_id>
```

The map payload uses `schema_version: 5` and contains:

- map geometry, zones, Off-limit areas, VF-off areas, Channels and dock;
- doodle metadata and original vendor SVG;
- prepared `zone_states`, weighted `totals` and independent revisions;
- latest same-day `daily_trails` per zone, prepared by the integration;
- legacy coverage and zone-detail fields for current card compatibility;
- global/effective cutting heights when supported;
- active cycle trail and every session retained by the selected history policy;
- gap-aware `trail_segments` and per-session `segments`, while retaining flat
  `trail`/`points` arrays for older cards;
- local Gate areas and zone-pair gates;
- links to the complete session index/detail APIs.

`zone_states` is the same authoritative model used by Home Assistant entities.
Whole-task `mowingPercentage`, active-zone `mapWorkPosition.progress`, per-zone
`partitionPercentage` and route progress remain separate fields instead of being
used interchangeably. The card therefore does not need to calculate progress, detect
zone-cycle boundaries or classify daily route points during dashboard loading.
A new cycle replaces only the entered zone's same-day trail; other zone trails
remain available until their own next cycle or the local calendar day changes.
Prepared daily trails are cached by local date, map revision and retained-route
revision so an unchanged dashboard reopen does not reprocess route history.

The dedicated session detail endpoint returns exact timestamped points for any
retained session. The main map response includes both a flat XY path and separate
XY route segments for every retained session. New cards should prefer `segments`
so short reload, restart and operator-stop gaps are not bridged by a false line.

## Options

Open **Settings -> Devices & services -> Navimower -> Configure**.

### General

- trail retention;
- include return-to-dock route;
- standard or extended diagnostics detail.

### Gates

Use **Add gate**, **Edit gate** and **Delete gate**. Zone choices are populated
from the decoded map; users do not need to know internal IDs.

A gate has a user-defined name, Zone A, Zone B, optional one-way operation and a
close delay. Example automation:

```yaml
alias: Navimower gate
triggers:
  - trigger: state
    entity_id: binary_sensor.tont_back_yard_gate_required
actions:
  - choose:
      - conditions:
          - condition: state
            entity_id: binary_sensor.tont_back_yard_gate_required
            state: "on"
        sequence:
          - action: cover.open_cover
            target:
              entity_id: cover.back_yard_gate
    default:
      - action: cover.close_cover
        target:
          entity_id: cover.back_yard_gate
```

Physical gate controllers differ; users remain responsible for any additional
safety logic or automatic-close behaviour.

### Gate areas

Gate areas are optional local-coordinate rectangles managed with Add/Edit/Delete
steps. They can describe a gate passage or any other area that needs exact
physical presence from fresh MQTT coordinates.

## Upgrade notes

### From v0.2.9 to v0.3.0

- This is an intentional clean entity break. Old `mowing_progress`,
  `mow_route_progress`, `coverage`, `session_area`, `weekly_area` and
  `total_area` entity IDs are not migrated. Delete their unavailable registry
  entries after the restart.
- New public entities use explicit Task, Map and Route terminology. Map area is
  the static sum of mowing zones; Route progress is diagnostic and disabled by
  default.
- Every mapped zone receives one enabled Coverage entity. Per-zone Area, Mowed
  area, Last mowed and Last completed entities are created but disabled by
  default.
- The same central zone model powers entities and the schema-v5 Map API. The API
  also provides per-zone same-day trails and revisions for a lightweight card.
- Provisional zero/one-point sessions are no longer published, and existing
  completed empty stubs are removed while history loads.
- No config-entry/options migration is required. Restart Home Assistant, remove
  the old orphaned entities, then enable only the optional zone entities you
  need.

### From v0.2.8 to v0.2.9

- Active battery discharge now prefers fresh official MQTT state packets; docked
  charging remains private-cloud-first. No synthetic/interpolated percentages
  are created.
- Mowing progress, coverage and session area use freshness-aware, cycle-aware
  last-known-good filtering. A confirmed new cycle clears the old values
  immediately and waits for low new-cycle telemetry.
- Current channel no longer alternates with `Position unavailable` only because
  the idle pose exceeded its freshness window. Gate safety remains fresh-pose
  only.
- Total area and the main public telemetry values survive short source outages
  and Home Assistant restarts.
- No configuration migration or Map API change is required. Restart Home
  Assistant after updating.

### From v0.2.6 to v0.2.7

- A successful `reset: true` mow command immediately starts a new history cycle;
  a partial previous cycle remains in retained history but is not marked as
  completed.
- App-side progress resets are also detected from lower partial progress, such
  as 50% to 0-5%, without requiring the old cycle to be near 100%.
- MQTT vehicle state and action remain authoritative independently of pose age,
  preventing the Docked binary sensor from switching on while the mower is
  actually mowing or returning.
- Manual/unsupported cutting-height values such as encoded `316` are removed
  from the public map instead of being displayed as millimetres.
- A short arrival guard blocks stale reverse gate targets after a confirmed
  crossing, while a fresh Mow or Dock command still takes effect immediately.
- MQTT callbacks are detached before SDK shutdown to avoid late un-awaited
  coroutine warnings during Home Assistant restart.
- No options migration is required. Restart Home Assistant after updating.

### From v0.2.5 to v0.2.6

- The public map payload is now schema v4.
- Intentional progress resets create a fresh current-cycle session instead of
  reusing the previous route. Existing retained sessions are preserved.
- Cycles with a vendor end timestamp and at least 95% progress can populate
  `last_completed_at`; the next cycle no longer erases that timestamp.
- Install `navimower-map-card` v0.1.13 or later for the three-day History
  selector (Today plus the two preceding dates) and the corrected three-pulse
  animation.
- Restart Home Assistant after updating.

### From v0.2.4 to v0.2.5

- No configuration migration is required; existing gate pairs and Gate areas
  are reused.
- Explicit Mow Now/ordered-zone commands now pre-latch the first target zone.
- Restart Home Assistant after updating and supervise one crossing while
  checking the target source/age and gate `from_zone` / `to_zone` attributes.

### From v0.2.3 to v0.2.4

- The public map payload is now schema v3 and exposes gap-aware
  `trail_segments` and per-session `segments`.
- Flat `trail` and `points` arrays remain available for older cards, but
  `navimower-map-card` v0.1.12 or later is recommended so route gaps are not
  bridged by false connecting lines.
- Restart Home Assistant after updating.

### From v0.2.2 to v0.2.3

- Older cached map geometry is normalized automatically and refreshed once from
  the private cloud. Manual `.storage` editing is no longer required.
- Adjacent retained session fragments separated by five minutes or less are
  merged automatically at startup.
- The bundled Mow Now and Scheduler cards were removed. Install
  `navimower-map-card` v0.1.9 or later and remove any manually configured
  `/local/navimower/navimower-mow-card.js` or
  `/local/navimower/navimower-scheduler-card.js` resources.
- Restart Home Assistant after updating.

### From v0.1.x

Navimower v0.2.0 upgrades existing entries automatically:

- legacy Gate-area/gate options are normalized;
- history retention defaults are added;
- when the old `navimow` config entry still exists, its OAuth token is copied
  once into Navimower;
- runtime dependency on the old integration is removed.

Keep a Home Assistant backup before installing a major alpha upgrade.

## Read-only diagnostics export

Run **Developer Tools -> Actions -> `navimower.export_diagnostics`**:

```yaml
action: navimower.export_diagnostics
data:
  include_compressed_map: true
```

Files are written to:

```text
/config/navimower_diagnostics/navimower_diagnostics_latest.json
/config/navimower_diagnostics/navimower_diagnostics_YYYYMMDD_HHMMSS.json
```

The export includes sanitized raw responses, nested key inventory, private
endpoint age/error statistics, OAuth/MQTT recovery health, map summary, zones,
doodles, session metadata, persistent zone history and passive MQTT topic/key
inventory.

Tokens, password, email, UID, serial number, GPS coordinates, PIN, RTK anchor,
ICCID, anti-theft point and network identifiers are removed. Local map X/Y and
vendor doodle SVG remain because they are useful for geometry research. Review
the file before publishing it.

The action sends no mower commands and performs no settings or map writes.

## Current limitations

- This is an **alpha release** and has not been tested across all mower models,
  accounts, firmware versions or regions.
- Current OAuth/private endpoints target the European/FRA service.
- A dedicated private-cloud shared account is strongly recommended. Reusing
  one private-cloud account across parallel entries is currently unsupported.
- Multiple-mower support is available through separate config entries, but it
  remains experimental and needs further development and broader field testing.
- Exact state codes and some settings remain firmware-specific.
- The standalone map card does not currently render temporary doodles, although
  their raw metadata remains available in the API and diagnostics.
- The swept-stripe endpoint is not used; exact history is reconstructed from
  dense live MQTT pose samples.
- The immediate target of multi-zone tasks is decoded from vendor data. When
  Home Assistant starts an explicit ordered-zone task, the first requested zone
  is latched locally so delayed vendor target fields do not delay gate opening.
- Map writes, boundary edits, edge-mowing changes and `clock_direction` writes
  are deliberately not included.

## Credits and licence

The private-cloud foundation used by Navimower was created by **Roberto
Gualandris** in [ilguala/navimow_pro](https://github.com/ilguala/navimow_pro).
His project reverse-engineered the Navimow mobile application's private-cloud
communication and provided the authentication, encrypted protocol, map decoder,
coordinator, entities, scheduler and camera implementation from which this
project developed. Thank you, Roberto, for the excellent work and for sharing it
with the community.

Navimower adds the official Smart Home OAuth/MQTT connection so that dense live
position and mower events can be consumed without placing unnecessary polling
load on the private cloud. Persistent live route history and local
Gate-area/gate work were adapted from
[vahesoo/NavimowHA](https://github.com/vahesoo/NavimowHA) and continued in this
repository. The user interface is developed separately in
[vahesoo/navimower-map-card](https://github.com/vahesoo/navimower-map-card).

See [NOTICE.md](NOTICE.md) and [LICENSE](LICENSE). The project is distributed
under the MIT License.
