# Changelog
## 0.2.3 — public preview, unified sessions and frontend cleanup
- Treats mowing fragments separated by no more than five minutes as one logical
  session. This covers short manual stops, zone reselection, integration reloads
  and Home Assistant restarts without inflating the session count.
- Repairs matching adjacent fragments already present in retained history and
  records segment start timestamps for future gap-aware map rendering.
- Protects a resumed session from a stale finalize write that was already in
  flight when cutting restarted.
- Registers the long-lived MQTT watchdog and startup-retry loop as Home Assistant
  background tasks so they do not hold integration startup open.
- Added map-geometry cache schema v3. Older `obstacles`, `vision_off` and
  `tunnels` keys are normalized immediately and a one-time cloud refresh is
  forced after upgrade.
- Removed the bundled Mow Now and Scheduler frontend cards and the frontend
  dependency. Their controls are now part of navimower-map-card v0.1.9 or later;
  the `navimower.mow` and `navimower.set_schedule` services remain available.
- Updated validation and public documentation for the standalone-card workflow,
  account limitations, current map terminology and release migration.

## 0.2.2 — stability and data freshness

- Added an MQTT pose-stream watchdog that distinguishes an open broker
  connection from a healthy live `realtimeDate/location` subscription.
- Re-subscribes when an active mower has no fresh pose for 25 seconds and
  rebuilds only the MQTT client if the stream does not recover within 10
  seconds; entities, map APIs and active history remain loaded.
- Added recovery generation guards, task cancellation, timeout-bounded
  disconnect and a quiesce-before-unload lifecycle so callbacks from an old
  MQTT client cannot write after a reload.
- Added increasing MQTT recovery backoff while private-cloud fallback remains
  available.
- Increased private-cloud polling to 5 seconds while mowing, 8 seconds while
  returning/mapping and 15 seconds while idle for the initial field-test
  profile.
- Split private polling into per-endpoint TTLs and last-good caches. One timeout
  no longer blanks unrelated sensors or marks all data unavailable.
- Added immediate throttled private refreshes on MQTT activity changes and when
  the live pose stream returns.
- Added `Position source`, `MQTT position stream` and optional private-poll-age
  diagnostics, plus endpoint and recovery details in manual exports.
- Clarified private-cloud versus Smart Home OAuth reauthentication and improved
  trail, channel and gate option descriptions.
- Documented the source priority, fallback behaviour, polling profile and MQTT
  automatic recovery model.

## 0.2.0 — standalone OAuth, exact history and map API v2

- Added Navimower-owned Smart Home OAuth and official MQTT setup; the old
  `navimow` integration is no longer required at runtime.
- Added a dual-connection setup flow: private app-cloud login and mower
  discovery first, followed by official browser OAuth for live MQTT data.
- Restored cached map/session state before starting the private-cloud and
  OAuth/MQTT branches in parallel, so either branch can remain useful during a
  temporary outage of the other.
- Refreshes OAuth first and then obtains new MQTT credentials after an MQTT
  authentication disconnect, without reloading the entire config entry on
  routine token refresh.
- Added persistent Home Assistant-owned mowing sessions with timestamp, X/Y,
  heading, activity, MQTT vehicle state and action for every received sample.
- Added configurable trail retention: 3, 7, 14, 30 days or unlimited, with an
  option to include the return-to-dock path.
- Added authenticated session index/detail endpoints and map API
  `schema_version: 2`.
- Added global cutting height and effective per-zone cutting heights; vendor
  `height_set=256` is decoded as inheriting the global value.
- Added persistent per-zone mowing history from `get-path-info-time`, including
  progress, finished area, last started/mowed/completed timestamps.
- Decoded the packed `map_work_position` immediate target, action, sub-action
  and progress so multi-zone gate intent is not confused with the full selected
  zone list.
- Added complete temporary doodle metadata, original vendor SVG and transform
  data to the decoded map and map API, plus doodle rendering in the SVG camera.
- Replaced gate/channel JSON editing with user-friendly Add/Edit/Delete option
  flows. Gates use mapped-zone dropdowns, bidirectional mode by default and a
  configurable 0/10/20/30 second close delay.
- Added migration of v0.1.x gate/channel options and one-time copying of the old
  NavimowHA OAuth token when that source entry is still present.
- Removed the bundled `navimower-map-card.js`; the interactive map is now
  installed separately from `vahesoo/navimower-map-card`.
- Kept the bundled mow-now and scheduler cards, SVG camera, services and
  sanitized raw diagnostics exporter.
- Expanded diagnostics with private/OAuth/MQTT health, map API v2, doodles, zone
  details and retained-session metadata.
- Fixed Home Assistant options-flow menu translations by placing each menu
  label map under its corresponding step's `menu_options` key.

Map writes, boundary edits, edge-mowing changes and `clock_direction` writes are
still intentionally excluded.

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
