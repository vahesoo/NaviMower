# Download diagnostics and the explicit raw development export

## Scope

Home Assistant **Download diagnostics** is the normal support path. It is designed to be cached-only and sanitized: downloading a report must not make exploratory vendor requests, send mower commands, mark notifications read or invoke the raw development export.

The separate `navimower.export_raw_data` action is intentionally different. It is an explicit local development capture for protocol/map investigations and must be treated as sensitive.

The current Download report uses `format: navimower-diagnostics-v2` and identifies the active sanitizer with `redaction_version: 3`.

## Download diagnostics

Field names are checked across snake_case, camelCase, acronyms and known compact aliases. This includes credentials, account/device identifiers, anti-theft PINs, SIM identifiers, geographic coordinates, API keys and edit-session user IDs. For example, `editMapUid`, `newPINCode`, `vehicleSn`, `deviceId`, `sessionKey` and `apiKey` do not avoid redaction through spelling variations.

Words are not arbitrary substrings: `mapping` and `spinning` must not be treated as PIN fields. Map/zone/task IDs, feature flags, cutting-height ranges, coverage, completion timestamps, command state and freshness metadata remain useful.

The report also handles nested JSON strings, common labelled credentials in free text, Bearer/Basic credentials, email addresses, UUIDs, MAC addresses and IPv4 addresses. Repeated known textual identifiers are removed from other values and dictionary keys. URL user information, paths, query strings and fragments are removed; only the service origin is retained. Large strings, opaque encoded strings, binary values and unsupported Python objects are summarized rather than exposing an arbitrary object representation.

Redaction is applied to the complete assembled report, including unloaded-entry reports, config options, cached MQTT samples and nested vendor responses. It creates a new object and does not mutate the original runtime caches or config entry.

### Georeference and map-underlay diagnostics

Map/georeference support needs enough information to diagnose alignment without publishing the mower's exact geographic position.

Normal Download diagnostics may therefore retain:

- mower-local X/Y coordinates and local map polygons;
- georeference source/status and fit-quality metadata;
- sample/refinement counts, baseline/spatial-score information and validation error distances;
- local-frame comparisons expressed as relative metre offsets;
- provider-frame availability/source and relative frame offsets;
- country-level underlay capability such as `EE`;
- Google Map Tiles configuration/session health such as configured state, session-active state, expiry and generic last-error status.

Exact latitude/longitude values remain redacted. Google Map Tiles API keys and Google session tokens are never included in Download diagnostics, including as a redacted placeholder in stored-options output.

See [MAP_GEOREFERENCE_AND_UNDERLAYS.md](MAP_GEOREFERENCE_AND_UNDERLAYS.md) for the runtime model.

### No H5 research in ordinary diagnostics

Inactive maintenance/error H5 placeholder blocks and historical executable-looking string markers were removed from the Download handler. Retired `maintenance_h5_discovery`, `error_h5_discovery` and `command_discovery` blocks are omitted recursively even when an older cache still contains them.

Ordinary Download diagnostics stays cached-only: no research crawler, new vendor request, mower command or raw-export action is invoked. Development/research code is not imported or executed by the Download handler merely because a user requests diagnostics.

### Passive protocol discovery

**Passive protocol discovery** is a separate, disabled-by-default Options Flow tool for short controlled investigations. It can retain bounded/sanitized current-device MQTT downlink structure/samples in the normal diagnostics report.

Use it only while reproducing the specific behavior, download diagnostics, then disable it again. Enabling passive discovery is not the same as invoking `navimower.export_raw_data` and does not turn normal Download diagnostics into an unredacted capture.

### Data that deliberately remains

Local map X/Y coordinates, local polygons, user-chosen mower/zone/Gate-area names and activity times remain available for the existing map, gate and schedule support workflow. This is **not a promise of complete anonymity**. Review those fields before posting a report, especially names containing personal information.

Redaction tests cannot prove that every future opaque vendor payload is safe. When in doubt, review the downloaded JSON before sharing it publicly.

## Raw export

`navimower.export_raw_data` is a separate, explicitly invoked development action. It writes an unredacted capture below:

```text
/config/navimower_diagnostics/raw/
```

The capture is intended to preserve exact evidence needed for controlled reverse-engineering and map/georeference work, including fresh read-only private-cloud responses, cached raw structures, map/calibration data and exact MQTT payload evidence.

Because it can contain geographic data, identifiers and other private vendor/account context, **do not attach a raw export to a public GitHub issue or forum post**. Use normal Home Assistant Download diagnostics for public support unless a maintainer has explicitly requested a private controlled capture.

Only synthetic test fixtures belong in the public test suite. Private field captures used during development are not committed or published by the tests.

## Previously shared files

A software update does not sanitize a file that was already downloaded or shared. Review older public diagnostics and replace/remove affected uploads if necessary.

If a usable credential was disclosed, revoke or rotate it as appropriate; removing a link is not a guarantee that existing copies disappear.

The 0.4.4 privacy hardening was informed by the field-name redaction approach described in [navimow_pro v0.5.1](https://github.com/ilguala/navimow_pro/releases/tag/v0.5.1) and the discussion around its issue #13. This does not claim vendor approval or imply that every concern in another project applies to Navimower.

## Verification

Permanent tests execute the real redactor and the loaded/unloaded Download handler with synthetic caches. They check naming variants, false positives, JSON/text/URL handling, retained support fields, georeference/underlay privacy, non-mutation and absence of research/vendor calls.

The tests do not use live mower accounts or private captures and cannot guarantee safety for unknown future vendor payload shapes. The sanitizer and diagnostics contract must be reviewed whenever new raw protocol fields or third-party credentials are added.
