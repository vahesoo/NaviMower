# Download diagnostics privacy

## Scope

Home Assistant **Download diagnostics** is the normal support path. It is designed to be cached-only and sanitized: downloading a report must not send mower commands or make additional service requests.

The current Download report uses `format: navimower-diagnostics-v2` and identifies the active sanitizer with `redaction_version: 4`.

## What is redacted

Field names are checked across snake_case, camelCase, acronyms and known compact aliases. This includes credentials, account/device identifiers, anti-theft PINs, SIM identifiers, geographic coordinates, API keys and edit-session user IDs. For example, `editMapUid`, `newPINCode`, `vehicleSn`, `deviceId`, `sessionKey` and `apiKey` do not avoid redaction through spelling variations.

Words are not arbitrary substrings: `mapping` and `spinning` must not be treated as PIN fields. Map/zone/task IDs, feature flags, cutting-height ranges, coverage, completion timestamps, command state and freshness metadata remain useful.

The report also handles nested JSON strings, common labelled credentials in free text, Bearer/Basic credentials, email addresses, UUIDs, MAC addresses and IPv4 addresses. Repeated known textual identifiers are removed from other values and dictionary keys. URL user information, paths, query strings and fragments are removed; only the service origin is retained. Large strings, opaque encoded strings, binary values and unsupported Python objects are summarized rather than exposing an arbitrary object representation.

Redaction is applied to the complete assembled report, including unloaded-entry reports, config options and curated cached state. Stable Download diagnostics does **not** export complete vendor raw payload bodies, raw notification/error bodies or arbitrary cached service responses. Instead it exposes selected state/capability/source-age fields plus a raw-cache shape/count summary. It creates a new object and does not mutate the original runtime caches or config entry.

## Georeference and map-underlay diagnostics

Map/georeference support needs enough information to diagnose alignment without publishing the mower's exact geographic position.

Normal Download diagnostics may therefore retain:

- geometry summaries such as polygon point counts and areas, but not polygon coordinates;
- georeference source/status and fit-quality metadata, but not full transform/control-point objects;
- sample/refinement counts, baseline/spatial-score information and validation error distances;
- bounded validation/error metrics and provider-frame status;
- provider-frame availability/source and relative frame offsets;
- country-level underlay capability such as `EE`;
- Google Map Tiles configuration/session health such as configured state, session-active state, expiry and generic last-error status.

Exact latitude/longitude values remain redacted. Google Map Tiles API keys and Google session tokens are never included in Download diagnostics, including as a redacted placeholder in stored-options output.

See [MAP_GEOREFERENCE_AND_UNDERLAYS.md](MAP_GEOREFERENCE_AND_UNDERLAYS.md) for the runtime model.

## Data that deliberately remains

Stable map/zone/task IDs, counts, progress values, source/freshness metadata and selected operational timestamps remain available because they are needed for support. Exact mower-local X/Y and polygon coordinates, full schedules/settings, user-authored mower/zone/Gate-area names and notification text are omitted from the stable Download report because they can expose property layout, routines, addresses, family names or other personal context. This is **not a promise of complete anonymity**; review a report before posting it publicly.

Redaction tests cannot prove that every future service payload is safe. When in doubt, review the downloaded JSON before sharing it publicly.

## Development captures

The shipped integration does not expose raw-data export or arbitrary endpoint-probe actions. Home Assistant **Download diagnostics** is the supported public troubleshooting path.

Maintainers may still arrange a separate private field-capture workflow for a specific compatibility investigation. Such captures are outside the integration UI/actions, may contain exact map/location values or identifiers, and must not be attached to a public GitHub issue or forum post.

Only synthetic fixtures belong in the public test suite. Private field captures used during development are not committed or published by the tests.

## Previously shared files

A software update does not sanitize a file that was already downloaded or shared. Review older public diagnostics and replace/remove affected uploads if necessary.

If a usable credential was disclosed, revoke or rotate it as appropriate; removing a link is not a guarantee that existing copies disappear.

## Verification

Permanent tests execute the real redactor and the loaded/unloaded Download handler with synthetic caches. They check naming variants, false positives, JSON/text/URL handling, retained support fields, georeference/underlay privacy, non-mutation and absence of service calls.

The tests do not use live mower accounts or private captures and cannot guarantee safety for unknown future payload shapes. The sanitizer and diagnostics contract must be reviewed whenever new raw fields or third-party credentials are added.
