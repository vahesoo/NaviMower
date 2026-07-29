# Navimower

Experimental Home Assistant integration for Segway Navimow robot mowers.

Navimower combines the rich private mobile-app cloud with the official Smart
Home OAuth/MQTT connection in one standalone config entry:

- **Private app cloud** — map geometry, real zone names and IDs, obstacles,
  temporary doodles, tunnels, settings, schedules, commands, maintenance and
  stable cloud state.
- **Official Smart Home OAuth + MQTT** — dense local `X`, `Y`, heading and live
  mower events used for exact route history, physical-zone detection and gate
  automations.

Navimower does **not** require the older NavimowHA integration from v0.2.0
onward.

> [!WARNING]
> Navimower uses an undocumented private cloud protocol. It is not affiliated
> with or supported by Segway, Ninebot, Navimow or Willand. A mower is a moving
> machine with a cutting blade; verify commands and physical-gate automations in
> a safe environment.

## Recommended account arrangement

Use a dedicated second Navimow account for the private app-cloud session and
share the mower to it from the primary account:

```text
Primary account  -> official phone app and Smart Home OAuth
Shared account   -> Navimower private-cloud login only
```

Do not sign the dedicated shared account into the phone app after setup. Field
testing showed that a phone login can invalidate the Home Assistant private
session. The private-cloud password is used only during setup or reauthentication
and is not stored.

The Smart Home OAuth account may be different from the private-cloud account as
long as both can access the same mower. Navimower matches the mower by its stored
identity before starting MQTT.

## Main features

### Map and zones

- decoded private-cloud map geometry;
- real zone names, IDs and areas;
- boundaries and per-zone map settings;
- obstacles and vision-off/no-mow areas;
- mapped tunnels and charging station;
- temporary app doodles with their original vendor SVG, center, direction,
  scale, creation time and expiry time;
- global cutting height and each zone's configured/effective cutting height;
- zone progress, finished area, last mowing time and last completed time;
- cached map data retained through temporary private-cloud outages.

### Exact persistent mowing history

Navimower records live MQTT samples in Home Assistant, not in the browser or
Recorder. Each retained sample includes:

```text
timestamp, X, Y, heading, activity, MQTT vehicle state, MQTT action
```

A session starts when cutting begins, survives pause/transit and optionally the
return-to-dock route, and ends when the mower is docked. Only an exactly repeated
pose/context sample is discarded.

Trail retention is configurable:

- 3 days;
- 7 days (default);
- 14 days;
- 30 days;
- unlimited.

`Unlimited` preserves every completed route and can grow both Home Assistant
`.storage` usage and the current map payload substantially over time. The
dedicated session APIs allow future card versions to load older routes on
demand.

Active history is checkpointed periodically and completed sessions are stored
individually in Home Assistant `.storage`, so a restart does not intentionally
erase the route.

### Physical zones, tunnels, channels and gates

- current physical zone derived from live MQTT `X/Y` and decoded polygons;
- target zone kept separate from the physical zone;
- mapped tunnel detection;
- zone-transition sensor;
- existing rectangular local-coordinate channel sensors;
- user-friendly zone-pair gate configuration;
- bidirectional gates enabled by default;
- optional one-way `Zone A -> Zone B` operation;
- gate close delay of 0, 10, 20 or 30 seconds;
- gate-required sensor remains latched through the configured arrival delay.

The intention-based gate sensor can open a gate before the mower reaches it,
without requiring an X/Y rectangle over a normally mowed area. Rectangular
channels remain available as a precise physical fallback.

### Mower entities and controls

Depending on mower model and firmware, Navimower provides:

- `lawn_mower` controls;
- battery, state, progress, area, coverage and maintenance sensors;
- global cutting height;
- current physical zone, target zone and current tunnel sensors;
- local X/Y, heading and MQTT pose-age sensors;
- private-cloud, OAuth, MQTT and pose-valid diagnostics;
- one binary sensor per configured channel and gate;
- zone selector, calendar/scheduler and supported setting entities;
- SVG map camera and a lightweight map-data sensor.

Firmware-dependent settings are created only when the corresponding value is
reported by the mower.

## Installation

### HACS custom repository

1. Open **HACS -> Integrations -> three-dot menu -> Custom repositories**.
2. Add `https://github.com/vahesoo/Navimower` as category **Integration**.
3. Install Navimower and restart Home Assistant.
4. Open **Settings -> Devices & services -> Add integration -> Navimower**.

Manual installation is also possible by copying
`custom_components/navimower` into `/config/custom_components/` and restarting
Home Assistant.

## Setup flow

The initial setup deliberately creates both cloud branches before the config
entry is completed:

1. Enter the email and password of the dedicated private-cloud account.
2. Select the mower, or enter its serial number when a shared mower is not
   returned by the normal list endpoint.
3. Continue to the official Smart Home OAuth browser authorization.
4. Sign in with an account that can access the same mower.
5. Home Assistant stores both token sets in one Navimower config entry.

On every normal Home Assistant start, Navimower restores local map/session data
first, then starts the private-cloud refresh and OAuth validation in parallel.
MQTT starts only after the OAuth token is valid and fresh broker credentials have
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

## Navimower Map Card

The interactive map is now distributed separately:

- [vahesoo/navimower-map-card](https://github.com/vahesoo/navimower-map-card)

Install it through HACS as a **Dashboard** custom repository. A minimal card can
normally be configured from only the mower entity because the card auto-detects
Navimower's related sensors:

```yaml
type: custom:navimower-map-card
entity: lawn_mower.tont
```

The integration no longer bundles or registers `navimower-map-card.js`.

Navimower still includes:

- `custom:navimower-mow-card`;
- `custom:navimower-scheduler-card`.

The SVG camera entity also remains available for picture cards, notifications
and simple dashboards.

## Authenticated map and session APIs

Large geometry and retained route data are served through authenticated local
Home Assistant endpoints rather than repeated state attributes:

```text
GET /api/navimower/map/<entry_id>
GET /api/navimower/sessions/<entry_id>
GET /api/navimower/session/<entry_id>/<session_id>
```

The map payload uses `schema_version: 2` and contains:

- map geometry, zones, obstacles, no-mow areas, tunnels and dock;
- full doodle metadata and original SVG;
- current coverage and zone details;
- global/effective cutting heights;
- active trail and every session retained by the selected history policy;
- channels and gates;
- links to the complete session index/detail APIs.

The dedicated session detail endpoint returns the exact timestamped points for
any retained session. For compatibility with the current standalone card, the
main map response also includes the XY path for every retained session; future
card versions can use the lighter list/detail endpoints for on-demand loading.

## Options

Open **Settings -> Devices & services -> Navimower -> Configure**.

### General

- trail retention;
- include return-to-dock route;
- standard or extended diagnostics detail.

### Gates

Use **Add gate**, **Edit gate** and **Delete gate**. Zone choices are populated
from the decoded map; users do not need to know internal IDs.

A gate has:

- user-defined name;
- Zone A and Zone B;
- bidirectional switch (on by default);
- close delay: immediately, 10, 20 or 30 seconds.

For a mower named `Tont`, a configured `Back yard gate` normally creates an
entity similar to:

```text
binary_sensor.tont_back_yard_gate_required
```

Example automation:

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

The integration-level close delay keeps the binary sensor on after target-zone
arrival. Physical gate controllers differ; users remain responsible for any
additional safety logic or automatic-close behaviour.

### Channels

Channels are local-coordinate rectangles managed with Add/Edit/Delete steps.
They can describe a gate passage, front yard, area behind the house or any other
useful rectangle.

## Migration from v0.1.x

Navimower v0.2.0 upgrades existing entries automatically:

- legacy channel/gate JSON is normalized to structured options;
- history retention defaults are added;
- when the old `navimow` config entry still exists, its OAuth token is copied
  once into Navimower;
- runtime dependency on the old integration is removed.

If the old OAuth entry is unavailable or its token cannot be used, Navimower
starts its own Smart Home OAuth reauthentication flow. Keep a Home Assistant
backup before installing a major alpha upgrade.

Because a GitHub browser upload does not delete files that are absent from the
new ZIP, repository maintainers upgrading from v0.1.x must manually delete:

```text
custom_components/navimower/www/navimower-map-card.js
```

Dashboard users should keep only the standalone HACS resource
`/hacsfiles/navimower-map-card/navimower-map-card.js` and remove any old
`/local/navimower/navimower-map-card.js` resource. A full Home Assistant restart
clears the old runtime registration.

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

The v2 export includes:

- sanitized raw responses from known read-only private endpoints;
- nested key inventory and focused map/camera/LiDAR/terrain indexes;
- private/OAuth/MQTT health;
- map API summary, zones and doodles;
- session metadata and persistent zone history;
- passive MQTT topic/key inventory.

Tokens, password, email, UID, serial number, GPS coordinates, PIN, RTK anchor,
ICCID, anti-theft point and network identifiers are removed. Local map X/Y and
vendor doodle SVG remain because they are needed for geometry research. Review
the file before publishing it.

The action sends no mower commands and performs no settings or map writes. The
normal private client may refresh its session if the stored token has expired.

## Current limitations

- This is an **alpha major release** and has not been tested across all mower
  models, accounts or regions.
- Current OAuth/private endpoints target the European/FRA service.
- A dedicated private-cloud shared account is strongly recommended.
- Exact state codes and some settings remain firmware-specific.
- The swept-stripe endpoint is not used; exact history is reconstructed from
  dense live MQTT pose samples.
- The immediate target of multi-zone tasks is decoded from the packed
  `map_work_position` value when the mower publishes it. Firmware that omits or
  delays that value can still open an intention-based gate later than desired.
- Map writes, boundary edits, edge-mowing changes and `clock_direction` writes
  are deliberately not included.

## Credits and licence

Navimower is based substantially on
[ilguala/navimow_pro](https://github.com/ilguala/navimow_pro), especially its
private-cloud authentication, encrypted protocol, map decoder, coordinator,
entities, scheduler and camera implementation.

The standalone official OAuth/MQTT bridge, persistent live route history and
local channel/gate work are adapted from
[vahesoo/NavimowHA](https://github.com/vahesoo/NavimowHA) and continued in this
repository.

See [NOTICE.md](NOTICE.md) and [LICENSE](LICENSE). The project is distributed
under the MIT License.
