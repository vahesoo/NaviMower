# Download diagnostics and the explicit raw development export

## Scope of this cleanup

The privacy cleanup changes Home Assistant **Download diagnostics**, not the
mower protocol, authentication, runtime telemetry, mapping, schedule or the
explicit `navimower.export_raw_data` action.

It follows the field-name redaction approach described in
[navimow_pro v0.5.1](https://github.com/ilguala/navimow_pro/releases/tag/v0.5.1).
The upstream author's [response to issue #13](https://github.com/ilguala/navimow_pro/issues/13)
acknowledges diagnostic disclosure and asks for concrete details about the other
concerns. This cleanup does not claim vendor approval or settle those questions.

## Download diagnostics

The report continues to use `format: navimower-diagnostics-v2` and additionally
identifies its redaction policy with `redaction_version: 3`.

Field names are checked across snake_case, camelCase, acronyms and known compact
aliases. This includes credentials, account/device identifiers, anti-theft PINs,
SIM identifiers, geographic coordinates and edit-session user IDs. For example,
`editMapUid`, `newPINCode`, `vehicleSn`, `deviceId`, `sessionKey` and `apiKey` no
longer avoid redaction through spelling variations.

Words are not arbitrary substrings: `mapping` and `spinning` must not be treated
as PIN fields. Map/zone/task IDs, feature flags, cutting-height ranges, coverage,
completion timestamps, command state and freshness metadata remain useful.

The report also handles nested JSON strings, common labelled credentials in
free text, Bearer/Basic credentials, email addresses, UUIDs, MAC addresses and
IPv4 addresses. Repeated known textual identifiers are removed from other
values and dictionary keys. URL user information, paths, query strings and
fragments are removed; only the service origin is retained. Large strings,
opaque encoded strings, binary values and unsupported Python objects are
summarized rather than exposing an arbitrary object representation.

Redaction is applied to the complete assembled report, including unloaded-entry
reports, config options, cached MQTT samples and nested vendor responses. It
creates a new object and does not mutate the original runtime caches or entry.

### No H5 research in ordinary diagnostics

The inactive maintenance/error H5 placeholder blocks and historical executable-
looking string markers are removed from the Download handler. Retired
`maintenance_h5_discovery`, `error_h5_discovery` and `command_discovery` blocks
are omitted recursively even when an older cache still contains them.

Ordinary Download diagnostics stays cached-only: no research crawler, new
vendor request, mower command or raw-export action is invoked. The standalone
research modules are not removed by this change; they are not imported or
executed by Download diagnostics.

### Data that deliberately remains

Local map X/Y coordinates, local polygons, user-chosen mower/zone names and
activity times remain available for the existing map, gate and schedule support
workflow. This is **not a promise of complete anonymity**. Review those fields
before posting a report, especially names containing personal information.
Redaction tests cannot prove that every future opaque vendor payload is safe.

## Raw export remains unchanged

`navimower.export_raw_data` is a separate, explicitly invoked development action.
It retains exact vendor payloads, local/geographic map data and identifiers in a
file below `/config/navimower_diagnostics/raw`. Its existing warning not to post
it publicly still applies. This cleanup does not add automatic uploads, new
confirmation steps, permission changes or filtering to that action.

Only synthetic test fixtures belong in the public test suite. Private field
captures used during development are not committed or published by the tests.

## Previously shared files

A software update does not sanitize a file that was already downloaded or
shared. Review older public diagnostics and replace/remove affected uploads.
If a usable credential was disclosed, revoke or rotate it as appropriate;
removing a link is not a guarantee that existing copies disappear. The upstream
v0.5.1 incident is not, by itself, evidence that a Navimower user credential has
been publicly disclosed.

## Verification

Permanent tests execute the real redactor and the loaded/unloaded Download
handler with synthetic caches. They check naming variants, false positives,
JSON/text/URL handling, retained support fields, non-mutation and absence of
research/vendor calls. They do not use live mower accounts or private captures.
