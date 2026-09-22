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

Static/live prepared resources and Prepared History resources are
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
- static/live resource byte sizes and ids;
- static geometry parity counts;
- live segment/point counts;
- manifest/resource read counters and first/last read ages;
- actual static/live resource body bytes served and 304 counts;
- live-tail request/success/fallback counters;
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

The beta26 History backend is additive for Map Card beta14. The existing
sessions and session-render endpoints remain available. A later Map Card beta
can switch to the ready-only History manifest and content-addressed resources;
only after that frontend path is field-tested should the remaining legacy
History/daily-trail compatibility code be removed.
