# Changelog

## 0.4.3-beta7

Independent targeted-request reserve for Parts maintenance and Mowing Reports discovery.

### Fixed

- Fix beta6 crawl-budget starvation: broad crawling can no longer consume the request budget reserved for the targeted phase.
- Give broad and targeted phases separate bounded request ceilings while retaining an overall request ceiling.
- Expose broad/targeted/source-map request counts and the targeted queue size in diagnostics so the reserve can be verified directly.
- Keep the 24-success targeted asset goal and include candidate source/theme evidence in targeted fetch diagnostics.
- De-prioritize public source-map probing after beta6 showed the sampled `.map` URLs were unavailable; targeted JS contract recovery now runs first.
- Add maintenance notification/UI evidence such as `Time to clean your mower`, `Maintenance point reached`, `review parts usage`, `start cleaning` and `reset the timer` to the search vocabulary.

### Safety

- Discovery remains Download-diagnostics-only, public, unauthenticated and GET-only.
- No live Mowing Reports request, blade timer reset, Replacement done action, Clean now action, maintenance mode, cutting-height mutation or mower command is executed.

## 0.4.3-beta6

Parts maintenance UI/source-map recovery and Mowing Reports transport proof.

### Changed

- Broaden Maintenance discovery from guessed endpoint names back to the actual Parts maintenance UI and i18n semantics.
- Target generic repair-themed lazy chunks and increase the reserved targeted asset budget from 16 to 24.
- Recover default-argument `handleH5MowerSet` wrappers such as `(e={})=>...` without spanning unrelated functions.
- Add bounded public source-map discovery for high-value maintenance/report assets.
- Capture likely Parts maintenance translation keys, UI contexts and original-source contexts.
- Keep the recovered Mowing Reports business contract while explicitly comparing H5 `body.data`/native encryption evidence with the existing private-cloud p:101 envelope shape.

### Safety

- Discovery remains Download-diagnostics-only, public, unauthenticated and GET-only.
- No report API request, maintenance counter reset, Clean now action, maintenance mode, cutting-height mutation or mower command is executed.

## 0.4.3-beta5

Targeted Maintenance + Mowing Reports H5 call-site recovery.

### Changed

- Recover minified report wrapper definitions and their call sites for the day/week/month and vehicle-main report endpoints.
- Capture bounded report wrapper arguments and nearby mowing area/time/count response-field contexts.
- Recover `handleH5MowerSet` wrapper definitions and bounded maintenance-related call sites.
- Reserve up to 16 additional successful asset fetches for high-priority chunks discovered after the 48-asset broad crawl.

### Safety

- Discovery remains Download-diagnostics-only, public, unauthenticated and GET-only.
- No report API request, maintenance counter reset, maintenance mode, cutting-height mutation or mower command is executed.

## 0.4.3-beta4

Focused Maintenance + Mowing Reports public-H5 contract recovery.

### Changed

- Fix lazy-chunk URL canonicalization so duplicate `static/js/static/js` and `assets/assets` paths do not waste the crawl budget.
- Count only successful JavaScript fetches toward the 48-asset limit while keeping a separate bounded request limit.
- Prioritize semantic hash-agnostic `native-*`, `request-*`, `service-*`, report, maintenance, blade and knife chunks.
- Capture dedicated contexts for the day/week/month report and vehicle main report endpoints, plus request/encryption/native-bridge call sites.

### Safety

- Discovery remains Download-diagnostics-only, public, unauthenticated and GET-only.
- No maintenance counter reset, maintenance mode, cutting-height mutation or mower command is executed.

## 0.4.3-beta3

Broader read-only Maintenance & Tools discovery based on the first successful beta2 diagnostics sample.

### Changed

- Crawl a bounded set of public lazy JavaScript chunks instead of discarding chunks whose import reference has no nearby maintenance keyword.
- Collect endpoint paths and native bridge calls globally from each fetched asset, with maintenance-related chunks prioritized within the fixed request budget.
- Add observed maintenance setting names such as `knifeDurationSet`, `chassisDurationSet`, `usedTime` and `setTime` to the discovery vocabulary.
- Preserve sanitized previews of small public JSON route responses, including the `/vehicle/maintenance/` route seen in beta2 diagnostics.

### Safety

- Discovery remains Download-diagnostics-only, public, unauthenticated and GET-only.
- No maintenance counter reset, maintenance mode, cutting-height mutation or mower command is executed.

## 0.4.3-beta2

Focused hotfix for beta1 Maintenance & Tools H5 discovery.

### Fixed

- Fixed the malformed import-time `BRIDGE_RE` expression that prevented Home Assistant from loading Navimower diagnostics and hid Download diagnostics.
- Removed beta1's extra escaping layer from all Maintenance H5 regular expressions and JavaScript whitespace normalization.

### Validation

- Added regression coverage that compiles every discovery `re.compile(...)` expression and verifies intended JavaScript regex syntax.
- Maintenance discovery remains read-only and Download-diagnostics-only; no maintenance mutation is added in beta2.

## 0.4.3-beta1

First beta in the cumulative 0.4.3 line, based directly on stable 0.4.2.

### Added
- Bounded public H5 Maintenance & Tools discovery in Download diagnostics only.
- Focused parsed/raw maintenance diagnostics for blade/chassis runtime correlation.

### Investigation targets
- Blade/knife runtime reset after blade replacement.
- Mower maintenance mode and the service flow that lowers the cutting deck.
- Nearby maintenance endpoints, HTTP/encryption metadata, payload keys and native bridge methods.

### Safety and architecture
- No maintenance mutation or mower/account identity is sent by beta1 discovery.
- No user-facing maintenance controls are added until contracts are proven.
- Temporary discovery is removed once useful contracts are recovered; later betas stay cumulative.

## 0.4.2

Stable cumulative release from the 0.4.2 beta line. No beta release needs to be installed first.

### Added

- Added persistent Navimower-local mower activity notifications and merged them with the vendor Device notification feed.
- Added `navimower.mark_notification_read`, `navimower.mark_all_notifications_read` and the dedicated retained-task `navimower.resume` action.
- Added private-cloud account region discovery/host persistence across the observed Europe, Asia-Pacific, Americas and mainland China routes while keeping official Smart Home OAuth/MQTT routing independent.
- Added an evidence-first capability profile to parsed mower snapshots and Home Assistant Download diagnostics for future model-aware entity provisioning.

### Fixed

- Fixed zone-restricted schedule `partitionPlan` writes to encode selected zone ids as little-endian uint16, preventing shifted payloads and phantom `00:15-00:15` periods.
- Preserved already-working schedule master On/Off behavior while correcting only the selected-zone wire format.

### Changed

- Removed Legacy Map Camera; Navimower Map Card is the supported map UI.
- Replaced accumulated beta-numbered runtime layers with responsibility-based semantic modules and one explicit runtime composition point.
- Download diagnostics remains sanitized/read-only but intentionally information-rich: config/model state, connectivity, regional routing, capability evidence, settings, telemetry, positioning, map/history/problem context, notifications, polling/MQTT health and the sanitized raw private-cloud snapshot are retained for support.

### Validation and compatibility

- The 0.4.2 beta line was exercised on H2- and X3-series mowers for the functionality available on those test devices, including local notifications and local Mark as read.
- Non-European regional routing follows upstream field evidence but was not locally hardware-tested before stable; future diagnostics can refine routing or capability mapping when real users report differences.
- Existing config entries, entities, histories and notification storage are retained.

## 0.4.2-beta7

Seventh beta in the cumulative 0.4.2 development line.

### Added

- Added regional private-cloud account discovery across Europe (`fra`/`eu`), Asia-Pacific (`sg`/`sea`), Americas (`us`/`ore`) and mainland China (`bj`) using the signed passport `/v3/region` lookup before password login.
- Added per-client mower-cloud host probing and persistence so the private mobile-app cloud is no longer hardwired to FRA at runtime.
- Added an evidence-first capability profile to mower snapshots and Download diagnostics, including endpoint presence, reported setting key paths, positive capability evidence and narrow proven model constraints.

### Changed

- Kept official Smart Home OAuth/MQTT routing independent from the private-cloud region; MQTT continues to use the `mqttHost` / `mqttUrl` returned by the official API.
- Updated the semantic runtime architecture and permanent architecture guard for responsibility-based private-region and capability-profile modules.

### Safeguards

- Region discovery fails closed when a regional directory cannot be checked instead of sending the account password to a guessed server.
- One empty or missing endpoint response is not treated as proof that a mower lacks a capability; positive observations remain sticky for the loaded coordinator.
- General sensors are not pruned in beta7. Existing field-driven switch/number/select provisioning remains unchanged while the capability profile gathers safer cross-model evidence.

## 0.4.2-beta6

Sixth beta in the cumulative 0.4.2 development line.

### Changed

- Replaced production `beta16_runtime.py`, `beta17_runtime.py`, `beta18_runtime.py` and `beta26_runtime.py` layers with responsibility-based semantic modules.
- Added one explicit `runtime.py` composition point while preserving the historically proven install order and runtime behavior.
- Renamed version-stamped internal notification/navigation/history runtime state to responsibility-based names.

### Guardrails

- Added a permanent architecture test that rejects beta/version-numbered production runtime files and beta-numbered runtime installer/state symbols.
- Added `docs/ARCHITECTURE.md` documenting the cumulative-beta rule and the explicit exception process for genuinely isolated experiments.

## 0.4.2-beta5

Fifth beta in the cumulative 0.4.2 development line.

### Fixed

- Fixed zone-restricted schedule writes to encode every selected zone id as little-endian uint16 instead of one byte in the robot `partitionPlan` payload.
- Prevented selected zones from shifting later schedule bytes, which could make the mower drop zones, misread later periods or create a phantom `00:15-00:15` period that synchronized back to the Navimow app.
- Kept multi-period framing unchanged; app captures confirmed that multi-period all-zones schedules were already encoded correctly.

### Validation

- Added byte-level regression tests for disabled days, one/multiple all-zones periods, one/multiple selected zones and multiple zone-restricted periods.
- Schedule encoding now reuses the same `encode_partition_ids()` little-endian uint16 helper as immediate zone mowing.

### Upstream confirmation

- Synced the schedule zone-id wire format with the official-app-captured fix published by `ilguala/navimow_pro` v0.2.9 for issue #5. The separate schedule-master-switch state report remains outside this fix.

## 0.4.2-beta4

Fourth beta in the cumulative 0.4.2 development line.

### Added

- Added a persistent Navimower notification center that retains up to 20 locally generated mower-activity notifications per config entry and merges them with the newest 10 vendor Device notifications.
- Added mowing timeline attribution for confirmed Home Assistant Mow/Resume/Dock commands, conservative schedule starts/ends, external starts, night/sunrise interruption and continuation, charge interruption/continuation, and unambiguous 100% completion.
- Added local notification `origin`, `kind` and `confidence` metadata plus separate combined/vendor/local counts on Latest notification.
- Added notification-center diagnostics including the retained task context, interruption reason, last mowing progress/battery and observed MQTT `mowStartType` / `taskDelay` values without guessing those numeric semantics.

### Changed

- Latest notification now keeps two independent budgets: up to 10 vendor rows plus up to 20 Navimower-local rows, merged newest-first into a maximum 30-row `recent` list.
- `navimower.mark_notification_read` dispatches `navimower:` IDs to persistent local read state while vendor IDs keep the encrypted Navimow detail-open flow.
- `navimower.mark_all_notifications_read` now marks both retained local rows and the vendor Device feed read.
- Local start/stop notifications wait for a confirmed private-cloud or official-MQTT mower state and are not emitted from Home Assistant's short optimistic command activity.
- Scheduled mowing notifications include the configured window end and, when Night mowing is off, can include Home Assistant location-based sunset context without claiming sunset control is performed by the integration.
- External mowing starts use observed target zones where available but are deliberately not labelled as mobile-app starts unless a future protocol signal proves the source.

### Attribution safeguards

- Night pause, sunrise resume and charging pause/resume messages are explicitly inference-based and require matching known context instead of treating every dock/return transition as the same reason.
- Local completion requires 100% task progress in beta4; the historical practical history threshold is not reused to generate a user-facing completion claim.
- Ordered zone names are shown only for Home Assistant commands whose command trace confirms ordered mowing support; first-generation H-series selected-zone tasks remain mower-ordered.

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

Detailed historical changes remain available in the repository's GitHub releases and the versioned files under `.github/release-notes/` as development history.
