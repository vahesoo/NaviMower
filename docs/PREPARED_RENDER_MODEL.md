# Prepared backend render model

Navimower 0.4.5-beta21 adds an additive backend contract intended for a future
Navimower Map Card runtime. Existing Map API responses remain supported and the
current Map Card does not need to opt in.

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

## Live-route render resource

The live resource reuses the integration's existing authoritative
`trail_segments` semantics and converts the current segments to compact SVG
paths. It does not introduce another trail source, gap detector or cycle/reset
resolver.

While active, preparation is coalesced and rate-limited to at most one build per
30 seconds. Session, activity, physical-zone and trail-active transitions bypass
that cadence and publish immediately so lifecycle changes never wait for the next
periodic backbone refresh. The latest two content-addressed resources are retained
so a client already fetching the previous descriptor can complete safely.

The normal Map API remains backward compatible and keeps returning the full raw
`trail` and gap-aware `trail_segments`. Newer clients can opt into the
prepared short-tail contract with `prepared_live_tail=1`. The returned
`prepared_live_tail` object identifies the prepared live resource it extends and
contains only points added after that resource, including one overlap point per
changed segment so the SVG backbone and live tail connect without a gap.

A client that already renders the prepared SVG backbone can request
`prepared_live_tail_only=1`. When the backend can prove that the short tail is
aligned with the retained prepared resource, the response omits the full raw
`trail` and `trail_segments`. If the session changed, the route rewound, the
prepared resource is unavailable or the tail exceeds the safety limit, the
backend keeps the full raw trail as an automatic fallback.

## Current-cycle and History resources

The prepared manifest links rather than duplicates the existing backends:

- `?artifacts_only=1` for ZoneLedger/VendorTrailStore-owned per-zone
  current-cycle SVG resources;
- `/api/navimower/sessions/{entry_id}` for the retained History index;
- `/api/navimower/session-render/{entry_id}/{session_id}` for completed-session
  render archives.

This keeps one owner for reset/cycle semantics and one archive contract for
History.

## HTTP caching

Static/live prepared resources are content-addressed by SHA-256 and served from
the authenticated Map API. They support ETag / `If-None-Match`; stale resource
ids return HTTP 410 so clients refresh the manifest instead of reviving an
obsolete render.

## Diagnostics

Home Assistant diagnostics contains a `prepared_render_model` block with
cached-only health information:

- static/live ready state;
- build counts and publication revisions;
- unchanged/coalesced update counts;
- static/live failure counts and last errors;
- build durations;
- static/live resource byte sizes and ids;
- static geometry parity counts;
- live segment/point counts;
- manifest/resource read counters and first/last read ages;
- actual static/live resource body bytes served and 304 counts;
- live-tail request/success/fallback counters;
- latest/base/current short-tail point counts and maximum observed tail size.

No prepared SVG paths or local point arrays are copied into diagnostics.

The existing current-cycle `map_artifacts` diagnostics remains under private
polling, and beta21 also adds `session_render_archive` diagnostics for the
completed History render cache (build/cache/failure counters and latency).

These diagnostics are intended to be reviewed before changing the Map Card. A
healthy field test should normally show:

- `static_ready: true`;
- `live_ready: true`;
- `static_summary.parity_ok: true`;
- zero static/live failures;
- low static build count while live build/publication counters advance at the
  30-second active cadence or immediately on lifecycle transitions;
- current-cycle artifact readiness consistent with owned zones;
- session archive builds/cache hits after completed sessions are viewed or
  archived.

## Compatibility

This is additive. Navimower Map Card keeps using the existing contract until a
separate frontend change explicitly consumes the prepared manifest/resources.
