# Prepared map artifacts

This is an additive presentation API. ZoneLedger and VendorTrailStore still own cycle identity and exact vendor geometry; History owns individual sessions. Map artifacts do not change cloud polling, navigation, mowing commands, gate behavior or reset rules.

## Compatibility

The existing combined Map API and `current_cycle_only=1` JSON response keep their schemas. They now share a coordinator-owned prewarmed cache. A cold/dirty legacy request waits for one valid publication, not for an indefinitely stable moving geometry revision. Multiple cards share the build. A failed cold build returns HTTP 503 with Retry-After instead of an incorrect successful empty cycle.

Navimower Map Card 0.3.7 progressively consumes the prepared backend resources while retaining the legacy composite path as a compatibility fallback. This document describes the integration-owned artifact protocol; presentation remains frontend-owned.

## Discovery and manifest

Normal Map API responses advertise `map_artifacts.manifest_url`. It points to the same authenticated map endpoint with `?artifacts_only=1`.

The manifest is ready-only: it may queue preparation but never waits for SVG construction. It is served with `Cache-Control: no-store` and contains:

- `schema_version: 1`, `scope: current_cycle_artifacts`, `entry_id`, `coordinate_space: map_xy_m`;
- stable `cycle_identity` pairs, plus a separate `publication_revision` and `building` flag;
- one `zones[]` descriptor per vendor-owned zone, with `zone_id`, `cycle_id`, desired `geometry_revision`, `pending`, and an optional prepared `artifact`;
- `fallback_zone_ids` for zones that still use the existing History fallback. A new frontend must preserve that fallback instead of assuming a missing vendor artifact is an empty completed zone. Live MQTT movement remains a separate presentation layer.

A prepared artifact contains `resource_id`, `format: svg`, `usage: alpha_mask`, actual rendered `geometry_revision`, `byte_length`, `bounds: [min_x, min_y, max_x, max_y]`, and an authenticated `url`. It contains no raw points or `path_d` text.

`pending: true` now means that no usable artifact exists for that current cycle. A valid same-cycle artifact remains ready even when `geometry_ahead: true` reports newer retained vendor geometry waiting for the next checkpoint. Keep other zones mounted; never substitute an earlier cycle.

## Individual resources

The URL has the form:

```text
/api/navimower/map/{entry_id}?zone_artifact={zone_id}&artifact_id={resource_id}
```

It returns an SVG alpha mask with a padded viewBox in mower-local X/Y metres. Its path is black with even-odd fill and a transparent background. The frontend chooses color/opacity when applying the mask and uses the same mower-local coordinate transform as zones and trails. It must not mistake SVG document Y orientation for geographic north, or change the integration's geometry.

Responses use `image/svg+xml`, a content-derived `ETag`, `Vary: Authorization`, and `Cache-Control: private, no-cache, must-revalidate`. An unchanged, still-valid resource supports HTTP 304. Authentication remains on the existing map view; no public files or secret-bearing query tokens are introduced.

The server validates current zone/cycle ownership before either HTTP 200 or 304. An old cycle or evicted resource returns HTTP 410 with no-store; refresh the manifest. Invalid resource identities return HTTP 400. Bodies are pre-encoded outside the event loop and HTTP reads never trigger rasterization.

Resource identity includes config entry, zone, confirmed cycle, actual rendered geometry, swath width, schema and bytes. It is stable across restart when the persisted artifact is unchanged. At most two ready revisions per current zone are retained, allowing a previously issued same-cycle URL to finish during a newer publication. Reset invalidates both revisions of only the affected zone.

## Frontend acceptance rules

1. Scope every cache and request by config entry and zone. Reject late responses after mower changes/disconnects.
2. Compare confirmed cycle IDs, not geometry revisions, for validity. A slightly older same-cycle render is valid; an old-cycle render is not.
3. Compare artifact resource IDs for freshness. Download only changed zones; keep unchanged zone elements/resources mounted.
4. Decode a new image before swapping it into the existing layer. Continue showing the previous valid same-cycle artifact while it loads.
5. On reset, drop the affected zone's old resource immediately; never clear another zone because a global map version or active session changed.
6. Keep live MQTT tail rendering and selected History sessions separate. Selecting an individual History session must not use the cumulative zone mask.

## Work scheduling and diagnostics

Coordinator observations request preparation without waiting. One builder per mower coalesces updates to the newest pending snapshot. Existing beta13 cycle/reset checks remain in the actual renderer. Unchanged zones retain their SVGs; all-vendor maps no longer load History points merely to discard them during source arbitration.

Vendor-owned geometry and mixed History fallback are both checkpoint-driven. Raw vendor geometry or History/MQTT point growth can advance while the published base stays frozen. History fallback checkpoints advance on physical zone exit and session-settled transitions; direct current-cycle consumers use the same checkpoint epoch, so a snapshot read cannot reintroduce live History churn into the expensive base render path. Map/cycle/ownership/width changes remain independent invalidation reasons.

The public vendor trail `revision` remains an opaque presentation-freshness counter and now includes completed publications. Diagnostics separately expose `geometry_store_revision` and `artifact_publication_revision`; neither is a cycle identity.

`polling.map_artifacts` diagnostics are cached-only: build/cache-hit counts, coalesced updates, failures, last preparation duration, prepared-zone count and byte size. Reading diagnostics does not schedule rendering or include coordinates/path data. Shutdown cancels the owned task; persisted VendorTrailStore artifacts remain available for restart.

Regression checks include 100 repeated prepared reads without another build, multiple callers sharing a slow build, unchanged-zone resource identity, moving-geometry coalescing, reset during rendering, restart reuse, map/width changes, private ETags and rejection before 304. These are controlled synthetic checks, not end-user latency benchmarks.
