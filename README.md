# Navimower

Experimental Home Assistant integration for Segway Navimow robot mowers.

Navimower combines two data paths:

- the **private mobile-app cloud** for the decoded lawn map, zones, area,
  obstacles, no-mow areas, tunnels, schedules, settings and stable mower state;
- the existing **official Navimow OAuth/MQTT session** for dense live local
  `X`, `Y` and heading updates.

The project is intended for testing before any functionality is merged back
into an established integration.

> [!WARNING]
> Navimower uses an undocumented private cloud protocol. It is not affiliated
> with or supported by Segway, Ninebot, Navimow or Willand. A mower is a moving
> machine with a cutting blade; verify every command and automation safely.

## Important account requirement

Use a **dedicated second Navimow account** and share the mower to it from the
primary account.

Do not sign in to the phone app with the dedicated Home Assistant account after
Navimower has been configured. Live testing showed that a phone login can
invalidate the Home Assistant private-cloud session and make its entities
unavailable until the integration is reloaded.

Recommended arrangement:

```text
Primary account  -> official phone app
Shared account   -> Navimower private-cloud login only
```

The password is used only during setup or reauthentication. It is not stored;
Home Assistant stores the resulting access/refresh tokens and private device id.

## Implemented in the first test release

### Private-cloud map and sensors

- decoded map geometry from the mower cloud;
- real zone names, IDs and areas;
- obstacles, no-mow / vision-off areas and tunnels;
- charging-station coordinates;
- zone coverage and mowing progress;
- stable last-known values during short private-cloud failures;
- separate private-cloud connectivity diagnostics.

### MQTT live position

Navimower can select an existing `navimow` OAuth config entry and use it for:

- live local `X` and `Y` coordinates;
- live heading;
- a denser mowing trail than private-cloud polling can provide;
- MQTT connection, pose-valid and pose-age diagnostics.

For live MQTT position, Navimower currently reuses an existing OAuth config
entry created by [vahesoo/NavimowHA](https://github.com/vahesoo/NavimowHA).
Install and configure NavimowHA first so Home Assistant has a valid Navimow OAuth
entry. During Navimower setup, select that OAuth entry as the MQTT source.

After Navimower has been configured, the older NavimowHA integration may be
**disabled** to avoid running two complete integrations at the same time. Do not
delete its config entry, because Navimower still reads the stored OAuth session
and tokens from it for MQTT access.

Navimower also works without a NavimowHA OAuth source, but live MQTT `X`, `Y` and
heading are then unavailable and position falls back to slower private-cloud
polling.

### Local channels

Rectangular local-coordinate channels are retained for gate and location
automations. A channel entity is available only while the MQTT pose is fresh;
a stale position is not silently reported as `off`.

Channels can describe more than a narrow gate, for example `Front yard`,
`Behind house` or any other rectangular area.

### Map views

Two map presentations are included:

- **Map camera entity** — app-style SVG snapshot, useful in ordinary picture
  cards and notifications;
- **`custom:navimower-map-card`** — private-cloud map geometry with MQTT mower
  position, heading, current-session trail and configured local channels.

The integration also bundles `custom:navimower-mow-card` and
`custom:navimower-scheduler-card`.

## Installation for testing

### HACS custom repository

1. In HACS, open **Integrations -> three-dot menu -> Custom repositories**.
2. Add `https://github.com/vahesoo/Navimower` as category **Integration**.
3. Install Navimower and restart Home Assistant.
4. Open **Settings -> Devices & services -> Add integration -> Navimower**.

Manual installation is also possible by copying
`custom_components/navimower` into `/config/custom_components/` and restarting.

## Setup flow

1. Enter the email and password of the dedicated shared account.
2. If the shared mower is not listed, enter its serial number. The integration
   validates it with a read-only live request.
3. Select an existing Navimow OAuth config entry for MQTT, or choose
   **Cloud map only**.
4. After setup, avoid logging the shared account into the phone app.

## Map card

Navimower includes its own dashboard cards in
`custom_components/navimower/www/`. During integration setup they are copied to
`/config/www/navimower/` and registered automatically in the Home Assistant
frontend.

The bundled **`custom:navimower-map-card`** is a new card designed for
Navimower's private-cloud geometry, MQTT live position, heading, mowing trail
and local channels. It does not depend on the separate
[vahesoo/Navimow-map-card](https://github.com/vahesoo/Navimow-map-card)
repository.

The older `Navimow-map-card` can remain installed for an existing NavimowHA
setup, but Navimower does not load or update it automatically and its entity
configuration is different. For Navimower, use the bundled card below:

```yaml
type: custom:navimower-map-card
title: Tont map
map_entity: sensor.tont_map_data
x_entity: sensor.tont_position_x
y_entity: sensor.tont_position_y
heading_entity: sensor.tont_heading
status_entity: lawn_mower.tont
battery_entity: sensor.tont_battery
zone_entity: sensor.tont_current_zone
show_channels: true
show_tunnels: true
```

The large static geometry is served by an authenticated local endpoint instead
of being stored in Home Assistant state attributes and Recorder on every poll.
The persisted mowing trail is loaded from the integration backend; fresh MQTT
points are then appended in the browser. Session resets are controlled by the
backend, so short frontend state changes do not erase the visible trail.

## Channel configuration

Open the Navimower integration's **Configure** dialog. Enter channels as a JSON
list using the same local meter coordinates as the position sensors. Saving the
options reloads Navimower and creates one binary sensor for each channel:

```json
[
  {
    "name": "Gate",
    "x_min": 5.0,
    "x_max": 8.0,
    "y_min": -72.0,
    "y_max": -60.0
  },
  {
    "name": "Behind house",
    "x_min": -20.0,
    "x_max": 5.0,
    "y_min": -40.0,
    "y_max": -10.0
  }
]
```

Changing options reloads only Navimower. OAuth token writes do not register an
integration update listener, so they do not intentionally trigger an hourly
unload/reload cycle.

## Main entities

Depending on mower firmware, Navimower creates:

- `lawn_mower` controls;
- battery, state, progress, area, coverage, current-zone and maintenance
  sensors;
- local X/Y, heading and MQTT pose-age sensors;
- private-cloud, MQTT and pose-valid binary sensors;
- one binary sensor per configured channel;
- zone selector, scheduler/calendar and supported setting entities;
- map camera and map-data sensor.

Some private settings are firmware-dependent and are created only when the
relevant value is discovered.

## Read-only diagnostics export

For protocol research, run **Developer Tools -> Actions ->
`navimower.export_diagnostics`**. The action queries every currently known
read-only private-cloud endpoint and writes two files:

```text
/config/navimower_diagnostics/navimower_diagnostics_latest.json
/config/navimower_diagnostics/navimower_diagnostics_YYYYMMDD_HHMMSS.json
```

The export contains sanitized raw decoded responses, all nested key paths,
keyword-focused indexes and a passive MQTT topic/key inventory accumulated since
the integration started. Account tokens, login details, mower serials, network
identifiers and physical GPS coordinates are removed. Local map X/Y coordinates
are retained. Large compressed or Base64 resources are represented by their size
and SHA-256 only.

The action is operationally read-only: it sends no mower commands and performs no
settings or map writes. The existing private-cloud client may automatically
reauthenticate if its stored session has expired, just as during a normal refresh.
Review the file before publishing it.

## Current limitations

- This is an **alpha test release** and has not been tested across all mower
  models or regions.
- The private endpoints are currently configured for the `fra` region.
- A dedicated shared account is effectively required for reliable coexistence
  with the phone app.
- Live MQTT in this first version reuses a pre-existing `navimow` OAuth config
  entry.
- Map editing and `clock_direction` writes are deliberately not included yet.
  Reading a map is low risk; writing the virtual safety boundary requires a
  separate backup/restore and verification phase.
- The exact swept-stripe endpoint is not used. The mowing trail is reconstructed
  from live pose samples.

## Credits and licence

Navimower is based substantially on
[ilguala/navimow_pro](https://github.com/ilguala/navimow_pro), especially its
private-cloud authentication, encrypted protocol, map decoder, coordinator,
entities, scheduler and camera implementation.

The official OAuth/MQTT bridge and local channel concept are adapted from
[vahesoo/NavimowHA](https://github.com/vahesoo/NavimowHA).

See [NOTICE.md](NOTICE.md) and [LICENSE](LICENSE). The project is distributed
under the MIT License.
