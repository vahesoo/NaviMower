# Changelog

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
