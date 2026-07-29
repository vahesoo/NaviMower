# Changelog

## 0.1.5 — zone-intent gates and trail reliability

- Added bidirectional zone-pair gate configuration in the options flow.
- Added one `gate required` binary sensor per configured zone pair.
- Added current physical zone, target zone and current tunnel sensors.
- Added a general zone-transition binary sensor.
- Derived physical zone from live MQTT X/Y and decoded polygons, with mapped-tunnel detection and a small boundary-mowing tolerance.
- Added return-to-dock target inference from the dock's mapped zone when available.
- Kept existing rectangular channel sensors unchanged.
- Fixed missing mowing trails by accepting live MQTT `vehicleState=4` and observed mowing actions as active-mowing sources when the private-cloud state is delayed or unknown.
- Updated the map card to use backend `trail_active` metadata instead of depending only on the configured lawn-mower entity state.
- Kept the read-only raw diagnostics export and added derived gate/zone/trail context to it.
- Added ICCID and anti-theft-point redaction to diagnostics privacy filtering.
- Bumped the integration and bundled map-card cache version to 0.1.5.

## 0.1.4 — diagnostics test release

- Republished the extended read-only diagnostics build for field testing.
- The repository release tag was updated, while the integration manifest still reported 0.1.3.

## 0.1.3 — read-only raw diagnostics export

- Added `navimower.export_diagnostics` for sanitized raw exports from every known read-only private-cloud endpoint.
- Added a recursive inventory of all nested key paths and focused indexes for map, edge, direction, height, camera, LiDAR, terrain, image and resource fields.
- Added a passive MQTT topic/key inventory without storing full MQTT payload values.
- Large compressed/base64 resources are recorded by length and SHA-256 instead of being copied into the export.
- The diagnostics action sends no mower commands and performs no settings or map writes; the normal client may reauthenticate if its session has expired.

## 0.1.2 — validation and translation fixes

- Added the Home Assistant config-entry-only `CONFIG_SCHEMA` required by hassfest.
- Fixed invalid translation placeholders caused by an inline JSON example in the options description.
- Moved the channel JSON example to the README reference text.
- Bumped the integration version to 0.1.2.

## 0.1.1 — map trail and channel options fixes

- Fixed the Configure/options dialog returning a 500 error on current Home Assistant versions.
- Kept the custom map card trail tied to the coordinator's persisted mowing session instead of clearing it on transient frontend mower-state changes.
- Added a backend trail-session marker so the card resets only for a genuine new mowing session.
- Improved channel JSON guidance and validation feedback.
- Bumped the bundled frontend card cache version.

## 0.1.0 — initial test release

- Added private-cloud authentication and read-only map decoding.
- Added real zone names, IDs, areas, boundaries, obstacles, no-mow areas,
  tunnels, dock coordinates and zone coverage.
- Added optional reuse of an existing Navimow OAuth config entry for live MQTT
  X/Y/heading and a denser mowing trail.
- Added private-cloud, MQTT and live-pose diagnostics while preserving the last
  valid private-cloud state during short failures.
- Preserved local channel/corridor entities for gate and area automations.
- Added an authenticated map-data endpoint, `custom:navimower-map-card`, and an
  optional SVG camera entity.
- Added scheduler, mow-now card and supported configuration entities inherited
  from the private-cloud implementation.

Map writes, boundary edits and `clock_direction` changes are intentionally not
included in this release.
