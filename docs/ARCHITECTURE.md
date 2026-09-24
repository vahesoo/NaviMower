# Navimower production architecture

## Runtime naming rule

Release and beta numbers belong in `manifest.json`, changelog entries, release notes, Git tags and historical tests. They do **not** belong in production Python module names or runtime installer symbols.

A beta is cumulative. Production code is organized by responsibility so the latest beta can become stable without a second release-number cleanup pass.

## Runtime composition

`runtime.py` is the single ordered composition point for semantic extensions. `services.py` may call only the central `install_runtime_extensions()` entry point; semantic modules must not chain-install one another.

The current production responsibilities are grouped as follows.

### State, regional routing and capabilities

- `state_semantics.py` — proven state/error interpretation and error-sensor enrichment;
- `private_cloud_region.py` — persistence/diagnostics bridge for private-cloud regional routing owned by `api/regions.py` and the API client;
- `capability_extensions.py` / `capability_profile.py` / `capability_semantics.py` — model capabilities, dynamic limits, evidence-first capability inventory and model-specific presentation/write constraints;
- `setup_flow_semantics.py` — setup-flow behavior that must remain consistent with the runtime capability/account model.

### Georeference and provider frames

The georeference pipeline is deliberately layered because local mower geometry, absolute geographic registration and map-provider presentation are different concerns.

- `georeference_geodesy_semantics.py` — WGS84 ellipsoid metre conversion and persisted geodesy-state migration;
- `georeference_semantics.py` — model-independent learned local-X/Y + GPS calibration;
- `georeference_static_anchor_semantics.py` — static vendor map anchors/tie points;
- `georeference_x3_bias_semantics.py` — proven X3 RTK anchor/bias translation path;
- `georeference_pose_semantics.py` — rejection of inconsistent dock zero-pose sentinels;
- `georeference_translation_refinement_semantics.py` — tightly validated cloud-fit translation refinement for eligible static maps;
- `georeference_cartographic_semantics.py` — regional cartographic presentation translation without changing local X/Y, rotation or scale;
- `georeference_diagnostics*_semantics.py` — local-frame/reference diagnostics normalized into the active presentation frame;
- `georeference_frames_semantics.py` — provider-ready `active`, `web_wgs84` and `regional_cartographic` frames for Map/Site API consumers.

Provider-frame export must be installed after the active transform is fully composed. A map-revision change is the authoritative automatic relearn boundary; normal GPS drift must not continuously move a validated map.

### Navigation, gates, history and Map API

- `navigation_fallback.py` — MQTT-first physical navigation with freshness-aware private-cloud fallback and conservative cloud-only gate clearing;
- `navigation_intent.py` — the post-fallback owner of target freshness, same-zone command arbitration and gate-intent safety;
- `history_performance.py` — point-light metadata access without deep-copying dense retained trails unnecessarily;
- `completion_semantics.py` — monotonic, evidence-backed per-zone completion semantics;
- `map_api_performance.py` — backward-compatible phased base-map/current-cycle publication;
- `gate_area_polygon_semantics.py` — Options Flow support for exact polygon Gate areas while preserving legacy rectangles;
- `zone_entity_cleanup.py` — removes stale zone entities only after a fresh versioned map proves that the vendor zone IDs are gone.

The retained history store remains the source of truth. Performance layers may change how data is copied/rendered/published, but must not silently discard sessions or points.

### Notifications and mowing interruption status

- `notification_feed.py` — vendor notification transport/cache and merged vendor/Navimower notification presentation;
- `mowing_pause_status.py` — conservative interruption classification. `low_battery` is automation-safe only after time-matched vendor confirmation; `low_battery_pending` remains an inference.

Generic Returning/Docked states are intentionally cause-agnostic.

### Navimower Schedule

Scheduler behavior is split by responsibility so ownership and reset safety remain explicit:

- `schedule_pause_semantics.py` — reversible Schedule Off/On and explicit destructive reset action;
- `schedule_ownership_semantics.py` — retained-task ownership proof and fail-closed adoption rules;
- `schedule_round_semantics.py` — repeated rounds and round-boundary behavior;
- `schedule_queue_semantics.py` — positional Custom queue slots, including intentional duplicate zones;
- `schedule_queue_boundary_semantics.py` — stages editor changes until a real new round/window boundary.

Once a Custom queue slot has been confirmed started, ownership loss must never cause a fresh `reset=true` restart of that same slot in the active round.

### Diagnostics and privacy boundary

Normal Home Assistant **Download diagnostics** is the supported troubleshooting path. It is cached-only and sanitized. Runtime MQTT diagnostics retain bounded schema/health summaries rather than exact payload copies, and the integration does not expose development raw-export or endpoint-probe actions.

## Capability policy

Capability discovery is **positive-evidence first**. A field, endpoint or MQTT feature that has been observed may be retained as supported for the lifetime of the integration instance. One missing or empty response is not proof that a mower lacks the feature; transient cloud failures and idle-state payloads must not remove entities.

Model-family rules are allowed only for narrow constraints that are already proven. For example, first-generation H-series mowers can be restricted to selected mowing zones but do not support a user-defined custom zone sequence; the mower chooses the order. Unknown models must not be assigned capabilities merely because their model name shares a prefix with a known mower.

The capability profile is diagnostic/foundational. Future capability-driven provisioning must preserve the same confirmed-data rule before removing a registry entity.

## Map and underlay boundary

Mower-local X/Y geometry is authoritative for zones, dock, Off-limit/VF-off areas, Channels, Gate areas, Custom Areas and dense position. Geographic underlays are presentation layers only.

`map_underlay.py` owns account-scoped Google Map Tiles configuration/session handling and backend tile/viewport proxying. Google API keys and session tokens stay on the Home Assistant backend. Map/Site API metadata may expose provider availability and authenticated proxy paths, never the secrets themselves.

See [MAP_GEOREFERENCE_AND_UNDERLAYS.md](MAP_GEOREFERENCE_AND_UNDERLAYS.md).

## Regional connection boundary

The mobile-app private cloud and the official Smart Home connection are separate transports. Private-cloud account ownership is resolved across regional Passport services and the usable private mower host is persisted per config entry. Smart Home OAuth/API routing is not inferred from that private region; MQTT continues to use `mqttHost`/`mqttUrl` returned by the official API.

## Rule for future betas

Do not add `betaNN_runtime.py`, `vNN_runtime.py`, `install_betaNN_*`, or `_betaNN_*` production symbols as a cache/workaround or release mechanism.

If a future protocol experiment genuinely needs temporary isolation, give it a responsibility-based name such as `experimental_<feature>.py`. The same change must document why isolation is needed and explicitly update the architecture guard in `tests/test_runtime_architecture.py`. Removing the guard or adding a blanket beta-number exception is not an acceptable workaround.

Historical beta behavior remains recoverable from Git tags and versioned tests/release notes; production source does not need to carry obsolete intermediate release layers.
