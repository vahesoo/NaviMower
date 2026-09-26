# Prepared backend render model

Navimower 0.4.5 provides an additive prepared-render backend contract used by
Navimower Map Card 0.3.7. Existing Map API responses remain supported through
compatibility fallbacks while the card progressively consumes prepared resources.

The goal is to move deterministic, style-independent work out of every browser
instance while keeping presentation and interaction in the frontend.

## Discovery

Normal Map API responses advertise:

```json
{
  "prepared_render_model": {
    "schema_version": 1,
    "scope": "prepared_map_render_model",
    "coordinate_space": "map_xy_m",
    "manifest_url": "/api/navimower/map/{entry_id}?render_model_manifest=1",
    "ready_only": true
  }
}
```

The same manifest path is also exposed in integration-owned frontend metadata,
including the Site API member metadata used by multi-mower views.

The manifest is small and ready-only. It may queue preparation but does not wait
for CPU rendering. It points at the latest prepared static and live-route JSON
resources and at the already existing current-cycle/history resources.

## Static render resource

The static resource contains no colors or card configuration. It prepares:

- zone SVG `path_d`, bounds, centroid and point count;
- Off-limit and VF-off SVG paths;
- Channel SVG paths and retained connection/type metadata;
- Gate area SVG paths, including legacy rectangle-to-polygon normalization;
- Custom Area SVG paths;
- charging-station metadata;
- map metadata and normalized georeference;
- two layouts matching the current card's 1000 x 1000, 5% padding transform:
  one without Gate areas and one with Gate areas included in bounds.

The transform is supplied as an SVG matrix in mower-local X/Y metres. Y is
inverted exactly once by the matrix, matching the current Map Card layout.

The resource intentionally does not decide color, opacity, visibility, label
collision policy, zoom, pan, mower artwork, selection or dialogs.

## Live-route render resources

The legacy prepared live resource reuses the integration's existing authoritative
`trail_segments` semantics and converts the current all-movement route to
compact SVG paths. This contract remains unchanged for Map Card beta15 and older
prepared-live consumers.

Beta27 additionally prepares a separate content-addressed
`live_semantic_route` resource from the exact timestamped active History
session. It classifies route edges with the same backend rules used by
current-cycle and completed History rendering:

- MQTT cutting action is preferred over normalized activity;
- both edge endpoints must be confirmed blade-on;
- both endpoints must have a physical mowing-zone id;
- both endpoints must belong to the **same** physical zone;
- zone-boundary crossings, missing-zone samples, pause/return/transit and unknown
  states remain travel.

The semantic resource contains separate SVG-ready `cutting_segments` and
`travel_segments`. It is prepared at the same 30-second cadence as the legacy
live backbone but is **not** downloaded by beta15. The manifest only advertises
its descriptor; a future card opts into that resource explicitly with
`live_semantic_route_render=<resource_id>`.

While active, preparation is coalesced and rate-limited to at most one build per
30 seconds. Session, activity, physical-zone and trail-active transitions bypass
that cadence and publish immediately so lifecycle changes never wait for the next
periodic backbone refresh. The latest two content-addressed resources of each
live kind are retained so an in-flight client can complete safely.

The normal Map API remains backward compatible. The beta25 short-tail contracts
`prepared_live_tail=1` and `prepared_live_tail_only=1` are unchanged.

Beta27 adds a separate semantic short-tail contract:

- `prepared_live_semantic_tail=1` returns only the classified active-session
  points added after the semantic backbone, with separate cutting/travel SVG
  paths;
- `prepared_live_semantic_tail_only=1` additionally omits full raw
  `trail`/`trail_segments` when the semantic tail is safely aligned;
- session mismatch, rewind, missing semantic base or the 128-point safety limit
  leaves raw trail available as fallback.

Keeping semantic transport separate means beta15 neither downloads nor computes
the new semantic short tail on its normal Map API requests.

## Current-cycle and History resources

Current-cycle rendering remains owned by the existing ZoneLedger/VendorTrailStore
artifact backend at `?artifacts_only=1`.

Completed History is prepared independently from the active live route:

- `/api/navimower/sessions/{entry_id}` remains the lightweight legacy/session
  metadata index and advertises Prepared History discovery;
- `/api/navimower/history-manifest/{entry_id}` is the new ready-only retained
  History manifest;
- every completed retained session with at least two points is prewarmed
  sequentially after startup;
- ready sessions expose immutable content-addressed descriptors;
- `/api/navimower/history-resource/{entry_id}/{resource_id}` serves the compact
  SVG-ready session artifact;
- `/api/navimower/session-render/{entry_id}/{session_id}` remains available as
  the beta14/legacy fallback until the prepared History frontend has been field
  tested.

The timestamped session Stores remain the source of truth. Prepared History is a
derived cache only. Active sessions never become History resources; they remain
on the Prepared Live SVG + short-tail path until the session settles.

The prepared History resource identity excludes the archive's `generated_at`
timestamp, so rebuilding the same immutable completed session produces the same
resource id.

## HTTP caching

Static/live/semantic-live prepared resources and Prepared History resources are
content-addressed by SHA-256 and served from authenticated Home Assistant APIs.
Prepared History resources use `Cache-Control: private, max-age=31536000,
immutable` and support ETag / `If-None-Match` with HTTP 304 responses.

## Diagnostics

Home Assistant diagnostics contains a `prepared_render_model` block with
cached-only health information:

- static/live ready state;
- build counts and publication revisions;
- unchanged/coalesced update counts;
- static/live failure counts and last errors;
- build durations;
- static/live/semantic-live resource byte sizes and ids;
- static geometry parity counts;
- legacy live segment/point counts plus semantic cutting/travel segment counts;
- manifest/resource read counters and first/last read ages;
- actual static/live/semantic-live resource body bytes served and 304 counts;
- legacy live-tail request/success/fallback counters;
- semantic live-tail request/success/fallback counters and latest
  cutting/travel segment counts;
- latest/base/current short-tail point counts and maximum observed tail size.

No prepared SVG paths or local point arrays are copied into diagnostics.

The existing current-cycle `map_artifacts` diagnostics remains under private
polling. Prepared History is exposed as `prepared_history` while the
`session_render_archive` diagnostics key is retained as a compatibility alias.
The block reports retained/eligible/ready/pending session counts, prewarm
completion, cache/build counters, publication revision, manifest/resource reads,
ready/served bytes, 304 responses and failures.

These diagnostics are intended to be reviewed before changing the Map Card. A
healthy field test should normally show:

- `static_ready: true`;
- `live_ready: true`;
- `static_summary.parity_ok: true`;
- zero static/live failures;
- low static build count while live build/publication counters advance at the
  30-second active cadence or immediately on lifecycle transitions;
- current-cycle artifact readiness consistent with owned zones;
- `prepared_history.prewarm_complete: true`;
- `prepared_history.ready_session_count` equals
  `prepared_history.eligible_session_count`;
- `prepared_history.pending_session_count: 0`;
- zero Prepared History failures;
- legacy session-render reads can remain zero until an older card requests one.

## Compatibility

Navimower Map Card 0.3.7 remains compatible with the legacy prepared-live
resource and short-tail response while also consuming the newer semantic resource
and semantic tail when available.

The Prepared History contract remains unchanged from its beta26 introduction. Because the later semantic classifier tightens
the shared cutting rules, retained completed-session render caches without
`classifier_version: 2` are rebuilt once during prewarm; the public render
schema remains version 2.

Frontend presentation remains intentionally out of scope here. Matching active
cutting opacity to mowed-area opacity and restoring the selected-session 3x glow
are Map Card composition/interaction changes, not backend geometry work.
