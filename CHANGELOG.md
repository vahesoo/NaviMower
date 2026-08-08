# Changelog

## 0.4.1-beta7 - passive discovery MQTT callback hotfix

- Import all passive-discovery helpers used by the MQTT callback, restoring normal MQTT state/location processing.
- Fix malformed `services.yaml` indentation for `mark_discovery_event`.
- Add discovery-import and YAML parsing regression coverage.

## 0.4.1-beta6 - passive discovery startup hotfix

- Fix the beta5 startup regression by importing `OPT_PASSIVE_DISCOVERY` in the MQTT bridge.
- Add a release regression test that verifies the passive-discovery option constants are actually imported where they are used.
- Preserve beta5 Passive protocol discovery behaviour unchanged; the feature remains opt-in and off by default.

## 0.4.1-beta5 - passive protocol discovery

- Add an opt-in Passive protocol discovery option for temporary vendor-protocol investigation.
- Subscribe to the current mower's `/downlink/vehicle/<device>/#` MQTT wildcard while discovery is enabled.
- Keep current-device-only topic/key inventory and up to three bounded sanitized samples per topic.
- Add `navimower.mark_discovery_event` timestamps for correlating app actions such as notifications, camera and LiDAR views.
- Record value-free private-cloud request/response schema inventory for calls made by Navimower.
- Add notification, event, push, stream, WebRTC, RTSP and LiDAR-oriented diagnostics keywords.
- Preserve the existing account-wide MQTT inventory separately; discovery never makes stale pose appear fresh and does not change mower control.

## 0.4.1-beta4 — MQTT pose-stream stability

- Stop rebuilding the complete MQTT client solely because type=1 pose is absent or stale.
- Enter `pose_degraded`, retain private-cloud position fallback and preserve useful MQTT traffic.
- Rate-limit location re-subscribe attempts to one per 120 seconds while pose remains degraded.
- Scope `last_any_message_age_s` to the current mower on shared accounts.
- Add pose re-subscribe diagnostics.

## 0.4.1-beta3 — raw-first telemetry

- Publish Task progress from the selected fresh vendor `mowingPercentage` value without monotonic rewriting.
- Publish Task mowed area from the selected fresh vendor `subtotalArea` without suppressing valid decreases.
- Publish Map mowed area from the current coverage snapshot's summed vendor `finishedArea`.
- Calculate Map coverage directly from the same current vendor coverage snapshot instead of retained per-zone cycle history.
- Keep source/raw comparison attributes so field diagnostics can distinguish vendor behaviour from integration interpretation.
- Keep session, daily-trail and cycle interpretation internal; those models no longer override the public telemetry sensors above.

## 0.3.4-beta3 — active-zone progress and map-data performance

- Recover an active zone from a stale/restored 100% session value when both the
  fresh MQTT work counter and vendor coverage confirm that the zone is still
  below the practical completion threshold.
- Heal the persisted active-session progress and clear an optimistic
  `vendor_progress` completion flag, preventing an incomplete route from being
  finalized as completed after a restart or transient 100% work counter.
- Add a second defensive check in the central zone model so stale completion can
  never override fresh active-zone telemetry while the history checkpoint heals.
- Build Map data attributes from the lightweight session index instead of deep
  copying cached sessions and thousands of route points during every state write.
- Add regressions based on the H215 Street 100%→29% and X390 Maja tagune
  100%→32% diagnostic cases.

## 0.3.4-beta2 — shared-account multi-mower session fix

- Reuse one deterministic private-cloud app/device identity for every Navimower
  entry using the same account, preventing one mower login from invalidating the
  other mower entry's private session.
- Automatically align different identities left by `0.3.4-beta1` when the
  integration is reloaded or Home Assistant restarts, without changing mower
  devices, entities, options, maps or retained history.
- Always show the mower selector before Smart Home OAuth, including when only one
  unconfigured mower remains.
- Replace the custom config-entry update listener with `OptionsFlowWithReload`,
  avoiding the deprecated listener-plus-config-flow reload combination and the
  duplicate setup/entity race seen on Home Assistant 2026.7.
- Keep OAuth token data updates reload-free while private/OAuth reconfigure flows
  still perform one explicit integration reload.
- Report idle/docked live-pose validity as unknown instead of falsely
  disconnected when MQTT is connected but a continuous position stream is not
  expected.
- Add dedicated account-session regressions and a GitHub Actions test workflow.

## 0.3.3

- Keep the existing ordered-zone command format for every mower family while the
  H1500 failure is investigated; explicit zone clicks remain the sent sequence.
- Add a sanitized `last_mow_command` diagnostics trace for both service and native
  lawn-mower starts, including zone IDs/names, exact sent hex, `partitionSetup`,
  acknowledgement, extracted command number and before/after state snapshots.
- Query `/vehicle/set/response` read-only for the last command when diagnostics
  are downloaded, making silent H1/H1500 command rejection observable.
- Include an unsent big-endian zone-ID reference beside the actual little-endian
  payload to identify a possible generation-specific byte-order difference.
- Reduce Map Card payload size by filtering intermediate route points closer than
  0.30 m to the last published point. Exact timestamped history remains stored
  and available through the session-detail API.
- Preserve segment starts and final points so short routes and interruption
  boundaries are not lost during card-facing route simplification.
- Keep same-day per-zone mowing trails visible across dock/charge continuations,
  even when a charging stop creates a new persistent history session.
- Replace a zone's Today trail only after a confirmed completion, vendor progress
  reset, or explicit restart enters that zone; unrelated zones remain unchanged.
- Persist explicit per-zone cycle-boundary markers so Home Assistant restarts and
  the five-minute session repair cannot merge a restarted cycle into the old one.

## 0.3.2

- Redact `oauth_device_id` from native diagnostics.
- Add safe RTK metadata and quality-field extraction.
- Add structured summaries for positioning, connectivity, battery health, firmware, capabilities, maintenance, schedule, environmental settings and opaque vendor fields.

## 0.3.1 — H1 map recovery, native diagnostics and global schedule control

- Treat empty and zero location map identifiers as unavailable and fall back to `map_list`, restoring H1/H1500 maps and zone names while docked.
- Share one map identifier resolver between the coordinator and diagnostics exporter.
- Add Home Assistant native **Download diagnostics** support using the existing sanitized read-only diagnostics document.
- Add a feature-detected **Mowing schedule enabled** switch backed by `startPlan`.
- Suppress `Next mow` while the global schedule master is disabled.
- Expose `schedule_enabled` in Map API schema v5 without changing the schema version.

## 0.3.0 — zone-first progress and lightweight map data

- Replaces the ambiguous shared progress/area entities with explicit Task, Map
  and Route sensors. This is an intentional clean entity break: old entity IDs
  are not migrated and can be removed from the Home Assistant entity registry.
- Creates one enabled Coverage sensor per mowing zone. Zone area, mowed area,
  last-mowed and last-completed entities are also created but disabled by
  default.
- Uses one authoritative integration-side zone model for Home Assistant sensors
  and the Map API, including stable zone IDs, cycle IDs, weighted m²/progress,
  source details and persistent timestamps.
- Uses vendor `mowingPercentage` as whole selected Task progress and
  `subtotalArea` as Task mowed area. The integration-side area-weighted selected
  zone model remains a fallback instead of being mixed with route progress.
- Calculates Map coverage and Map mowed area from the latest retained value of
  every mapped zone, and renames Total area to the unambiguous Map area.
- Keeps packed `mapWorkPosition.progress` as active-zone/work progress and the
  vendor Route progress counter as a disabled diagnostic entity. Neither can
  replace the whole-task percentage.
- Bumps the authenticated Map API to schema v5 and adds prepared `zone_states`,
  `totals`, independent revisions, and latest same-day `daily_trails` per zone.
- Stores the physical zone ID with new route points, falls back to zone polygon
  classification for older history records, and caches unchanged daily-trail
  preparation by date/map/route revision.
- Discards provisional zero/one-point start-reset sessions and removes existing
  completed empty stubs, preventing duplicate non-clickable history rows.
- Adds regression tests for whole-task/active-zone source separation, weighted
  fallback calculations, per-zone daily trail replacement, empty-session
  filtering and the clean v0.3 sensor architecture.

## 0.2.9 — dense telemetry and stable public counters

- Uses the official MQTT state stream as the preferred battery source while the
  mower is active, producing denser discharge updates without inventing
  interpolated percentages. Private-cloud SOC remains preferred while charging.
- Adds battery-source freshness, raw MQTT/private values and anti-oscillation
  filtering so source handovers do not create false charge/discharge bounces.
- Resolves public mowing progress from fresh MQTT overall/work/route progress
  before slower private-cloud values and keeps progress monotonic within one
  confirmed mowing cycle.
- Clears progress, coverage and session area immediately after an explicit or
  detected new cycle, then rejects stale values from the previous cycle until
  low new-cycle telemetry arrives. Brief zeroes and regressions inside the same
  cycle are retained as last-known-good instead of being published.
- Retains Total area through endpoint gaps and Home Assistant restarts using the
  decoded map cache and persisted last-known telemetry.
- Keeps Current channel at the last confirmed value when the MQTT pose becomes
  stale, and reports `Not in channel` while docking is confirmed. Gate and Gate
  area safety still require a fresh MQTT position and never act on the retained
  display value.
- Tracks per-message MQTT freshness separately for pose, progress, area and
  battery so cached fields in a later location packet do not look newly updated.
- Extends sensor attributes and exported diagnostics with telemetry source, age,
  raw candidate, stale-channel and cycle-reset context.
- Adds dependency-free regression tests for battery source selection, cycle
  reset holds, transient regressions, MQTT field freshness and channel stability.

## 0.2.7 — state consistency and cycle reset reliability

- Makes a successful `navimower.mow` `reset: true` command an immediate history
  boundary, so a partially completed route is retained but the next active route
  starts cleanly without waiting for a later vendor progress update.
- Detects app-side partial resets such as 50% to 0-5%, while keeping partial
  cycles separate from `last_completed_at` and preserving the 95% practical
  completion rule.
- Tracks MQTT vehicle-state and action freshness independently of pose age,
  merges partial MQTT snapshots, and forces Docked off whenever the normalized
  mower activity is mowing, paused or returning.
- Filters encoded/manual cutting-height values and removes unsupported raw
  `height_set` data from the public map, preventing values such as `316 mm` on
  i105-class manual-height mowers.
- Adds a short gate-arrival guard that ignores a stale reverse cloud target after
  the mower reaches the destination, without delaying a fresh Mow or Dock command.
- Restores SDK MQTT callbacks before disconnect so late paho-thread messages do
  not create un-awaited coroutine warnings during Home Assistant shutdown.
- Expands diagnostics with dock-source, MQTT state/action age, cycle reset,
  cutting-height capability and gate-arrival context.
- Adds regression coverage for explicit/partial cycle resets, Docked consistency,
  cutting-height filtering, gate-arrival protection and MQTT hook cleanup.

## 0.2.6 — cycle-aware history and app-like progress

- Detects an intentional new mowing cycle when the same zone progress resets,
  including the brief non-cutting handover before Navimow starts another pass
  without docking.
- Splits the new cycle into a fresh active route so the Current map clears while
  the completed route remains available in retained history.
- Prevents the normal five-minute session repair rule from merging across an
  intentional cycle-reset boundary.
- Treats vendor-ended cycles at 95% or higher as practically completed, covering
  inaccessible remnants and temporary obstacles that prevent a literal 100%.
- Preserves `last_completed_at` and the previous final percentage when the next
  cycle has already started, and records practical completion again on a
  confirmed completed dock return.
- Prefers official MQTT route progress, then packed work progress, for the active
  zone while retaining the private-cloud coverage percentage for diagnostics.
- Bumps the public map API to schema v4 and exposes cycle/history metadata for
  navimower-map-card v0.1.13.
- Expands dependency-free history regression tests with cycle-reset and practical
  completion coverage.

## 0.2.5 — reliable gate intent and command-state handling

- Latches an explicit HA Mow Now/ordered-zone command immediately, so the
  gate-required sensor can pre-open a gate before the mower reaches the local
  Gate area.
- Gives fresh HA command intent priority over a stale packed work target and
  ignores the common transient where the reported immediate target still equals
  the physical origin while the selected task zone is elsewhere.
- Protects an in-flight gate latch from being overwritten by a short-lived
  reversed/stale target; the original from-zone/to-zone direction remains active
  until arrival and the configured close delay.
- Requires confirmed docking before clearing gate latches while a fresh pose is
  elsewhere on the map.
- Adds optimistic start, pause and dock activity handling so unknown transition
  codes no longer create false `Docked` events in the Home Assistant logbook.
- Replaces normal empty text states with `No active target`, `Not in channel` and
  an explicit stale/last-known physical-zone state instead of generic `unknown`.
- Adds target source, age, confirmation and pose-valid context to target/gate
  attributes for automation traces and diagnostics.
- Adds dependency-free navigation-intent regression tests.

## 0.2.4 — gap-aware session routes and map API v3

- Bumped the public map API to schema v3.
- Added gap-aware `trail_segments` for the active route and per-session
  `segments` for retained history, so reload, restart and short operator-stop
  gaps are not bridged by a false connecting line.
- Kept the existing flat `trail` and `points` arrays for compatibility with
  older map-card releases.
- Updated the documented standalone-card requirement to
  navimower-map-card v0.1.10 or later.
- Expanded session regression tests to cover merged route segments and active
  trail segmentation.
- Expanded the public README credits and clarified that multiple-mower support
  is available but remains experimental.

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
