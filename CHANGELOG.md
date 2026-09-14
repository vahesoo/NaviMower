# Changelog

This changelog lists stable releases. Detailed prerelease/beta history remains available in GitHub Releases and under `.github/release-notes/`.

## 0.4.4 - 2026-09-14

Stable cumulative release from the tested 0.4.4 beta line through `0.4.4-beta39`. There is no intentional runtime behavior change from beta39.

### Multi-mower, maps and underlays

- Add the integration-owned multi-mower Site API with mower-scoped maps, zones, Schedule/History identity and stable site transforms.
- Add phased Map API loading for base geometry and current-cycle rendering so the frontend can paint sooner without rebuilding completed mowing swaths in the browser.
- Add provider-aware georeference metadata and backend support for OpenStreetMap, Estonia Ortofoto/Hübriid and authenticated Google Satellite.
- Add persistent exact Gate-area polygons plus Home Assistant services used by the Map Card visual Gate editor.

### Mowing history, progress and completion

- Strengthen current-cycle trail/session handling across pause, charging, Home Assistant restart and confirmed reset/new-cycle boundaries.
- Keep confirmed completed-zone coverage at 100% when a later vendor snapshot regresses without a newer cycle/reset or geometry change.
- Stabilize coverage, task progress, mowed area and session ownership with freshness-aware source selection.
- Separate mower physical zone, task target zone, active-zone progress and whole-task progress so transit cannot transfer completion ownership.

### State and command safety

- Treat paused-returning state `0221` as resumable so Start continues the retained return instead of starting a new reset mowing task.
- Validate explicit zone IDs only against authoritative decoded map geometry; partial fallback zone lists are not used to reject a real zone.
- Allow an existing vendor schedule day to be switched off even when its stored periods are stale, overlapping or reference a removed zone.
- Reject the vendor no-fix GPS pair `0.0, 0.0` and avoid forcing Recorder updates for unchanged coordinates.
- Add mower pause-reason semantics that distinguish confirmed low-battery, pending low-battery, night, manual dock and unknown states without inventing unsupported causes.

### Diagnostics, privacy and interoperability

- Keep Home Assistant Download diagnostics cached-only, sanitized and read-only.
- Retire public research/discovery helpers that are no longer needed for production behavior while retaining functional account/cloud and MQTT support.
- Preserve dotted firmware/version values in diagnostics when they are explicitly version fields while continuing to redact real network addresses and secrets.
- Refresh public documentation around Home Assistant behavior, backend/frontend boundaries and interoperability while retaining attribution and existing branding assets.

### Compatibility

- Existing config entries, device/entity unique IDs, map/history/session storage, Schedule state and Gate/Custom Area configuration are retained.
- Upgrading from 0.4.3 or any 0.4.4 beta does not require recreating the integration.
- No 0.4.4 beta needs to be installed before this stable release.
- Navimower Map Card `0.3.6` is the matching stable card release.

## 0.4.3 - 2026-08-27

Stable cumulative release from the 0.4.3 beta line.

### Navimower Schedule

- Add the integration-owned Navimower Schedule for one-zone-at-a-time mowing with Automatic oldest-completed order or a persistent Custom queue.
- Support Time window and 24-hour modes, retained interruption state and safe charging recovery without converting an unfinished task into `reset=true`.
- Keep native Navimow Schedule and Navimower Schedule mutually exclusive.
- Add Reset schedule progress as both a Home Assistant device control and `navimower.reset_schedule` action.

### Map, zones and mowing history

- Add stable per-zone Coverage, Area, Mowed area, Last mowed and conservative Last completed entities.
- Add reset-based current-cycle history in which a confirmed new cycle replaces only the affected zone while older sessions remain in retained history.
- Add backend-prepared current-cycle rendering for the Map Card and map-version invalidation for edited maps.
- Remove stale zone registry rows only after a freshly decoded, versioned map proves the vendor zone ID no longer exists.

### Custom Areas, position and telemetry

- Add persistent Custom Areas imported from a temporary Navimow Off-limit polygon for virtual presence uses such as gate passages and driveways.
- Add a native Home Assistant Location `device_tracker` when valid latitude/longitude is available.
- Separate MQTT connection health from live-position validity and expose position source/age explicitly.
- Improve state arbitration, problem/error handling, battery/task-progress stabilization and physical Dock representation.

### Setup and model-aware controls

- Support multiple mower config entries sharing one dedicated account identity while retaining separate devices, maps and histories.
- Improve automatic mower selection and regional account routing across the observed Europe, Asia-Pacific, Americas and mainland China families.
- Expand capability-driven settings for modern mower families without inventing unsupported controls.

### Notifications and diagnostics

- Keep a merged Latest notification timeline with vendor and Navimower-local origins and origin-aware read actions.
- Use neutral wording for observed mowing starts when Home Assistant cannot prove the command source.
- Keep Home Assistant Download diagnostics sanitized, read-only and suitable for support.

### Compatibility

- Existing config entries, entity/device unique IDs, map/history/session storage and notification storage are retained.
- No 0.4.3 beta needs to be installed before this stable release.

## 0.4.2

### Added

- Added persistent Navimower-local mower activity notifications and merged them with the vendor Device notification feed.
- Added `navimower.mark_notification_read`, `navimower.mark_all_notifications_read` and the dedicated retained-task `navimower.resume` action.
- Added account-region discovery/host persistence across observed Europe, Asia-Pacific, Americas and mainland China routes while keeping official Smart Home OAuth/MQTT routing independent.
- Added an evidence-first capability profile to parsed mower snapshots and Home Assistant Download diagnostics for model-aware provisioning.

### Fixed

- Fixed zone-restricted schedule `partitionPlan` writes to encode selected zone IDs as little-endian uint16, preventing shifted payloads and phantom `00:15-00:15` periods.
- Preserved already-working schedule master On/Off behavior while correcting only the selected-zone wire format.

### Changed

- Removed Legacy Map Camera; Navimower Map Card is the supported map UI.
- Replaced accumulated beta-numbered runtime layers with responsibility-based semantic modules and one explicit runtime composition point.
- Kept Download diagnostics sanitized/read-only while retaining useful support context for connectivity, routing, capabilities, settings, telemetry, positioning, maps/history, problems, notifications and MQTT health.

### Compatibility

- Existing config entries, entities, histories and notification storage are retained.
- No 0.4.2 beta needs to be installed before this stable release.

## 0.4.1

### Added

- Added Latest notification backed by the Navimow Device feed, including bounded recent message details and vendor read state.
- Added production handling for observed Idle (`0103`), Lifted (`0302`) and active numeric-fault (`0301`) states.
- Added detailed numeric fault reporting from live `index2.error_data`.
- Added freshness-aware cloud position fallback for Current physical zone/channel and guarded Gate/Gate-area presence.
- Added initial i2 AWD capability support.

### Changed

- Prevented dense MQTT pose traffic from starving normal cloud polling.
- Kept MQTT pose degradation separate from broader MQTT connection health.
- Stabilized task progress, task mowed area, per-zone coverage and route/history handling.
- Improved cutting-height compatibility and weather-adaptive setting writes.
- Allowed multiple mower entries to share one dedicated account while retaining separate devices, maps and histories.

### Removed

- Removed beta-only protocol-discovery controls and development-only diagnostics actions from the production interface.
- Removed native-app jump URLs and development probe output from retained diagnostics/notification data.

### Compatibility

- Existing mower config entries, map/session storage and entity unique IDs are retained.
- Legacy Map Camera remained present for the 0.4.1 stable line and was removed in 0.4.2.

## 0.4.0

- Added persistent completed-session SVG render archives while keeping the exact timestamped route as authoritative history.
- Added lightweight Map Card query flags so initial dashboard loads can skip completed-session and daily-trail geometry when not needed.
- Kept first-generation H-series zone selection compatible while allowing those mowers to choose their own selected-zone mowing order.
- Preserved existing entities and map schema compatibility without a config-entry migration.

## Earlier releases

Detailed historical changes remain available in the repository's GitHub Releases and the versioned files under `.github/release-notes/`.
