# Changelog

## 0.4.2-beta3

Third beta in the cumulative 0.4.2 development line.

### Added

- Added `navimower.resume`, a dedicated retained-task Resume action using the private-cloud `c:behavior` type `3` command.
- Added an in-memory `last_resume_command` diagnostics trace with the pre-command mower/task context, request acceptance and vendor command number when available.

### Changed

- The existing paused `lawn_mower.start_mowing` path now uses the same Resume helper as `navimower.resume`, keeping one implementation and one diagnostics format.
- Resume remains separate from `navimower.mow(reset: false)`: Resume sends no zones and does not create a new Navimower mowing cycle, while `mow(reset: false)` still sends a selected-zone `s:mower` command in continue mode.

### Field validation

- Beta3 intentionally allows the explicit Resume action to be called while the mower is docked/charging so real mowers can confirm whether a manually interrupted vendor task is retained after Dock.
- Standard Home Assistant Start behavior from docked/charging remains unchanged for now. It will not be auto-routed to Resume until field testing confirms the model/firmware behavior.
- Download diagnostics remains snapshot-only and never sends Resume while collecting the cached trace.

## 0.4.2-beta2

Second beta in the cumulative 0.4.2 development line.

### Added

- Added `navimower.mark_notification_read` for one Device notification. It uses the official app's encrypted message-detail request and then refreshes the Device feed; Home Assistant does not optimistically rewrite the cached `read` flag.
- Added `navimower.mark_all_notifications_read` using the recovered `clearBatchMessageRead` request with `searchMessageStatus: false` for the selected mower/account.
- Both notification actions force the next Device-feed poll immediately after a successful vendor call instead of waiting for the normal 60-second notification TTL.

### Changed

- Notification read state remains account-specific. The actions operate in the private-cloud Navimow account context used by the selected config entry.
- Service registration now checks each Navimower service independently, so newly added actions can be registered by an upgraded integration without relying on the older `mow` service as the only registration sentinel.

### Removed

- Removed the beta1 `notification_read_h5_discovery` source scanner and its Download-diagnostics H5 network requests after recovering the notification read request contracts.
- Download diagnostics is snapshot-only again and never marks notifications read.

### Field validation

- **Mark all as read** follows a directly recovered official-app mutation contract.
- Single-message **Mark as read** follows the official flow where Message Center marks the selected row locally and then opens `/mowerbot/user/message/getmessageDetailResp` with Device type `2`. Beta2 intentionally waits for the refreshed `vehicleMessageListField` response to prove the server-side `read: true` effect on a real unread message.

## 0.4.2-beta1

First beta in the cumulative 0.4.2 development line, built directly on stable 0.4.1.

### Added

- Added a targeted **Download diagnostics** H5 inspection for notification read-state reverse engineering. It searches public unauthenticated Navimow H5 JavaScript for `clearBatchMessageRead`, unread-count routes and surrounding request/payload structure.
- Diagnostics records bounded sanitized source context to help determine whether the official app supports both per-message **Mark as read** and **Mark all as read** behavior.
- Documented field confirmation that notification `read` state is scoped to the Navimow account used by the integration: messages become `read: true` after they are read in the app under that same account and the feed refreshes.

### Changed

- Navimower Map Card is now the only supported Navimower map UI. Existing authenticated map/history/session APIs remain unchanged.
- The normal Latest notification Device feed remains read-only in beta1. The new H5 diagnostics probe does not alter notification state and does not change normal notification polling.

### Removed

- Removed the deprecated **Legacy Map Camera** introduced by the old Home Assistant `camera` platform, including its SVG renderer, platform registration and camera entity translation.
- Removal affects only the legacy SVG map-camera entity; mower camera/VisionFence settings such as Camera positioning (EFLS) remain available where supported.

### Diagnostics safety

- H5 discovery runs only when Home Assistant **Download diagnostics** is requested.
- It performs bounded public GET requests only and sends no Navimow token, cookie, UID, device ID, mower serial or encrypted p:101 business payload.
- `clearBatchMessageRead` and all other notification mutation endpoints are **not called** in beta1.

## 0.4.1

Changes below describe the stable upgrade from **0.4.0 to 0.4.1**. The 0.4.1 beta release notes remain in `.github/release-notes/` as development history; they do not need to be installed individually.

### Added

- Added **Latest notification**, backed by the Navimow app's read-only Notification -> Device feed. The sensor keeps the newest title as state and exposes bounded message details plus up to five recent notifications.
- Preserved vendor notification codes as strings, including alphanumeric codes such as `150A`, and preserved the vendor read flag as a boolean. Native app jump URLs are not retained or exposed.
- Added production handling for the observed **Idle** (`0103`), **Lifted** (`0302`) and active numeric-fault (`0301`) states.
- Added detailed numeric fault reporting from live `index2.error_data`. When the mower supplies a fault object, the Error sensor exposes the vendor code, title and content; field captures include `6108` (Mower got stuck) and `6106` (Motion planning error).
- Added freshness-aware private-cloud position fallback for Current physical zone and Current channel when the official MQTT pose stream is temporarily unavailable.
- Added guarded private-cloud fallback for Gate and Gate-area presence. A cloud-based close/clear or OFF transition requires two distinct fresh vendor position reports.
- Added initial **i2 AWD** capability support, including the observed i208 AWD settings and global cutting-height capability. This support is **experimental and not yet field-tested on a live i2 AWD mower through Navimower**.

### Changed

- Private-cloud polling can no longer be starved by dense MQTT position pushes. A guarded poll task keeps normal cloud state, settings, schedule and coverage refreshes running during active mowing.
- MQTT pose degradation no longer forces a complete MQTT client rebuild while other useful MQTT traffic is healthy. Position recovery is handled independently and re-subscribe attempts are rate-limited.
- Task progress and Task mowed area now publish the selected fresh vendor task values directly instead of being rewritten by retained monotonic history.
- Map coverage and Map mowed area now come from the current vendor per-zone coverage snapshot. Physical mower position is kept separate from the work-target/progress-owner zone.
- Multi-zone mowing remains one logical history session across normal zone transitions. A confirmed per-zone cycle reset affects only that zone's daily trail and does not clear unrelated completed zones.
- Repeated deliveries of one vendor pose are deduplicated from route history, and persisted duplicate samples from the beta investigation are compacted on load where safe.
- Completed-session SVG footprints use the mower's reported `mowingPathWidth` when available instead of assuming a universal 0.25 m swath.
- Cutting-height compatibility is more defensive: unknown encoded values are not converted into invented millimetre values and no longer disable otherwise valid mower-level height support.
- **Night mowing**, **Rain** and **Rain sensor** now use a robot-first plus legacy-cloud-persist write path. All three were field-tested bidirectionally on H215 and remain persistent after the write.
- Multiple mower entries may share one dedicated private-cloud account while retaining separate devices, maps and histories.
- The built-in SVG camera is now named **Legacy Map Camera**. It remains available in 0.4.1 for compatibility, but Navimower Map Card is the supported map UI. Legacy Map Camera is scheduled for removal in **0.4.2**, beginning with the 0.4.2-beta1 development line.
- Home Assistant **Download diagnostics** is the supported diagnostics interface and remains sanitized/read-only. It keeps general mower, connectivity, positioning, telemetry, map/history, Problem/Error and latest-notification context.

### Removed

- Removed the 0.4.1 beta-only **Passive protocol discovery** and **Diagnostics detail** options from the production options flow. Existing beta option values are discarded when stable 0.4.1 starts or options are saved.
- Removed the development-only `navimower.export_diagnostics` and `navimower.mark_discovery_event` actions from the production service interface.
- Removed state-transition capture, passive MQTT discovery inventories, private request-schema inventories and beta probe output from Home Assistant Download diagnostics.
- Removed the notification native-app `url` field from retained notification data and Home Assistant attributes.

### Compatibility

- No 0.4.1 beta needs to be installed before the stable release; 0.4.1 is cumulative from 0.4.0.
- Existing mower config entries, map/session storage and entity unique IDs are retained.
- `Latest notification` keeps the existing internal `notification` key/unique ID used by the beta sensor.
- Legacy Map Camera remains present for the whole 0.4.1 stable line; migrate dashboards to Navimower Map Card before 0.4.2.

## 0.4.0

- Added persistent completed-session SVG render archives while keeping the exact timestamped route as authoritative history.
- Added lightweight Map Card query flags so initial dashboard loads can skip completed-session and daily-trail geometry when not needed.
- Kept first-generation H-series zone selection compatible while allowing those mowers to choose their own selected-zone mowing order.
- Preserved existing entities and map schema compatibility without a config-entry migration.

## Earlier releases

Detailed historical changes remain available in the repository's GitHub releases and the versioned files under `.github/release-notes/`.