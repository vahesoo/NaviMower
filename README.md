# Navimower

Home Assistant integration for Segway Navimow robot mowers.

Navimower combines two cloud connections in one config entry:

- **Private app cloud** — map geometry, real zone names and IDs, Off-limit and
  VF-off areas, mapped Channels, settings, schedules, commands, maintenance,
  notifications and stable cloud state.
- **Official Smart Home OAuth + MQTT** — dense live `X`, `Y`, heading, battery
  and mower events used for route history, physical-zone detection, progress
  context and gate automations.

Navimower does **not** require the older NavimowHA integration.

> [!WARNING]
> Navimower uses an undocumented private cloud protocol. It is not affiliated
> with or supported by Segway, Ninebot, Navimow or Willand. A mower is a moving
> machine with a cutting blade; verify commands and physical-gate automations in
> a safe environment.

## Project origins

The private-cloud foundation used by Navimower was created by **Roberto
Gualandris** in [ilguala/navimow_pro](https://github.com/ilguala/navimow_pro).
That project reverse-engineered the Navimow mobile application's private-cloud
communication and implemented the authentication, encrypted protocol, map
handling and control foundation on which this integration builds.

Navimower extends that foundation with official Smart Home OAuth/MQTT live data,
persistent mowing history, physical-zone and gate logic, richer Home Assistant
entities, and a separately maintained
[Navimower Map Card](https://github.com/vahesoo/navimower-map-card).

## Recommended account arrangement

Use a dedicated shared Navimow account for the private app-cloud session and
share the mower or mowers to it from their primary owner accounts:

```text
Owner account(s) -> official phone app and Smart Home OAuth
Shared account   -> Navimower private-cloud login only
```

Do not normally sign the dedicated shared account into the phone app after
setup. Field testing showed that a phone login can invalidate the Home Assistant
private session. The private-cloud password is used only during setup or
reauthentication and is not stored.

The Smart Home OAuth account may be different from the private-cloud account as
long as both can access the same mower.

> [!IMPORTANT]
> Multiple mower config entries may use the **same dedicated private-cloud
> account**. Add each mower as its own Navimower config entry. Entries using the
> same account share one stable app/device identity at private-cloud account
> scope while keeping separate entities, maps and histories.

## Main features

### Map and zones

- decoded private-cloud map geometry;
- real zone names, IDs and areas;
- Off-limit areas, VF-off areas, mapped Channels and charging station;
- temporary app obstacle/doodle metadata;
- global and per-zone cutting-height context where the mower reports trustworthy
  remote height data;
- cached map data retained through temporary private-cloud outages;
- authenticated map/session APIs for the standalone Map Card.

### Task, Map and zone telemetry

Navimower keeps the different vendor counters separate instead of treating every
percentage as the same value:

- **Task progress** follows the selected vendor task progress;
- **Task mowed area** follows the selected task area counter;
- **Map coverage** and **Map mowed area** come from the current per-zone vendor
  coverage snapshot;
- active-zone/work progress remains separate from whole-task progress;
- physical mower position is separate from the work-target/progress-owner zone.

A mower crossing through another mapped zone can therefore no longer move task
progress onto the physical polygon under the mower.

### Persistent mowing history

Navimower records dense route samples in Home Assistant storage rather than in
the browser or Recorder. Sessions survive normal pause/resume, short integration
reloads and Home Assistant restarts. Route gaps remain separate segments so the
Map Card does not draw an invented straight line across missing data.

Multi-zone mowing remains one logical task session across normal zone changes.
A confirmed per-zone cycle reset is retained as a boundary inside the task and
clears only that zone's same-day trail when the new cycle actually enters it.
Unrelated zones keep their completed daily trails.

Repeated deliveries of the same vendor pose are deduplicated. Completed-session
SVG footprints use the mower's reported `mowingPathWidth` when available instead
of assuming one universal swath width.

Trail retention is configurable to 3, 7, 14 or 30 days, or unlimited.

### Position fallback, Channels and Gate areas

Fresh official MQTT `X/Y` is always the preferred live position. When the MQTT
pose stream is temporarily unavailable, a recent private-cloud position may keep
Current physical zone and Current channel useful.

Private-cloud pose freshness is based on the mower/vendor `report_time`, not on
when Home Assistant happened to poll the endpoint. Stale cloud coordinates are
display-only and never override a fresh MQTT pose.

Gate and Gate-area safety is deliberately conservative. Under private-cloud
fallback, an already-open gate is not closed from one position sample. Two
distinct fresh vendor position reports must confirm a cloud-based close/clear or
Gate-area OFF transition.

Gate areas are generic local-coordinate presence areas. They may be used for a
physical gate passage, camera automation or another local mower-presence use
case.

### State, Problem and Error

Navimower separates mower control states from safety/fault states more clearly:

- `0103` is handled as **Idle**;
- `0210` as **Mowing**;
- `0211` as **Paused**;
- `0220` as **Returning**;
- `0302` as the separate **Lifted** safety state;
- `0301` as the active numeric-fault state.

The **Problem** binary sensor answers whether an active problem/safety condition
is present. The **Error** sensor provides vendor fault detail when the mower
reports a numeric error object. Real field captures such as `6108` (Mower got
stuck) and `6106` (Motion planning error) have supplied their vendor title and
content directly through live private-cloud state.

Lifted remains a safety state without inventing a numeric error/event code. A
code is exposed only when the vendor actually reports one.

### Latest notification

Navimower exposes the Navimow app's main **Notification -> Device** feed as the
**Latest notification** sensor.

- state: newest vendor notification title;
- attributes: content, timestamp, read state, level/type/style and vendor code;
- `recent`: up to five newest normalized messages;
- poll interval: at most once per minute;
- transient failures retain the last successful snapshot;
- vendor notification codes are kept as strings, including alphanumeric values
  such as `150A`;
- vendor-native app jump URLs are deliberately not retained or exposed.

Field testing with a mower shared to multiple Navimow accounts confirmed that the
vendor `read` state is **account-specific**: reading notifications under the same
Navimow account used by the HA private-cloud integration changes the feed entries
from `read: false` to `read: true` on a later refresh.

**0.4.2-beta1 does not yet mark notifications read from Home Assistant.** The beta
adds a bounded read-only public-H5 inspection to Download diagnostics so we can
recover the official app request structure for `clearBatchMessageRead`, including
whether the app supports both one-message and all-message read actions. No
notification mutation endpoint is called by this beta.

Notification history is user-facing event information and does not replace the
live Problem/Error state model. Field testing also confirmed that mower
fault/safety events can appear in the Device feed, but the notification code and
live mower Error/Problem state remain separate vendor concepts.

### Settings and controls

Depending on mower model and firmware, Navimower can expose:

- Lawn mower start, pause and return-to-dock controls;
- Mowing schedule enabled;
- charging limit and return battery level;
- cyclic mowing;
- Night mowing;
- Rain and physical rain-sensor controls;
- weather sensitivity and post-rain delay;
- frost, snow, strong-wind and high-temperature controls;
- Do not disturb / quiet period;
- Energy saver, Sound and lights;
- Child lock, Lift alarm and Geo-fence controls;
- obstacle/navigation/traction/animal-protection settings when reported;
- global cutting height on models that report supported remote heights.

**Night mowing, Rain and Rain sensor** use a robot-first plus cloud-persist write
path. This prevents the mower's onboard copy from later restoring an older value
to the cloud. All three were field-tested bidirectionally on H215 during the
0.4.1 beta cycle.

Unknown encoded cutting-height markers are not converted into invented
millimetre values.

### i2 AWD experimental support

0.4.1 introduced an initial capability profile derived from an i208 AWD
diagnostic and the i2 documentation. It may expose settings such as Eco mode,
Narrow zone adapt, Advanced slope mode, Grass pattern enhancement, Progress
retention, Mowing cycle interval, Headlight, Night animal protection, Terrain
adapt, Edge sense, TCS, positioning controls and Global cutting height when
corresponding vendor fields are present.

> [!CAUTION]
> **i2 AWD support is experimental and has not yet been field-tested on a live
> i2 AWD mower through Navimower.** The controls remain included so owners can
> test and report behavior, but availability, labels and write semantics may vary
> by firmware. H215 is the primary field-tested model and X390 provides secondary
> model validation.

Default battery-setting ranges are 5–20% for return-to-dock and 70–100% for the
charging limit unless the mower reports its own supported min/max limits.

### Entity reference and model support

Primary field testing has been performed on an **H215** in the European/FRA
region. An **X390** has been used for secondary map, telemetry, notification,
multi-mower and model-specific settings validation. First-generation H-series
compatibility is retained for zone selection and map fallback behavior. Other
models are capability-driven and best effort.

Representative capability-dependent controls include **Night light brightness**,
**Terrain adapt**, Edge sense, weather controls, battery thresholds and remote
cutting height. Entities are exposed only when the model/capability mapping and
reported vendor settings support them.

The private and OAuth endpoints currently target the European/FRA service.

## Installation

### HACS custom repository

1. Open **HACS -> Integrations -> three-dot menu -> Custom repositories**.
2. Add `https://github.com/vahesoo/NaviMower` as category **Integration**.
3. Install Navimower and restart Home Assistant.
4. Open **Settings -> Devices & services -> Add integration -> Navimower**.

Manual installation is also possible by copying `custom_components/navimower`
into `/config/custom_components/` and restarting Home Assistant.

## Setup flow

1. Enter the email and password of the dedicated private-cloud account.
2. Select the mower, or enter its serial when a shared mower is not returned by
   the normal mower list.
3. Continue to official Smart Home OAuth.
4. Sign in with an account that can access the same mower.
5. Home Assistant stores both connection branches in one Navimower config entry.

The two branches degrade independently: cached/private-cloud functionality may
remain usable during an OAuth/MQTT outage, and live MQTT/history may remain
useful during a temporary private-cloud outage.

## Connectivity and polling

`MQTT connected` and `Live position valid` are separate health signals. A broker
connection can remain healthy while a continuous position stream is not expected
or is temporarily unavailable.

Dense MQTT pushes no longer starve the normal private-cloud refresh schedule. A
guarded private polling task ensures settings, schedule, coverage and cloud state
continue to refresh while the mower is actively sending frequent pose packets.

When the pose stream degrades, Navimower keeps useful MQTT state/progress traffic
instead of rebuilding the complete MQTT client solely because pose is missing.
Location re-subscribe attempts are rate-limited while private-cloud position
fallback remains available.

| Data | Preferred source | Fallback |
| --- | --- | --- |
| X/Y and heading | fresh official MQTT | recent private-cloud position, then last-known display context |
| live activity | official MQTT | private-cloud state |
| battery while active | fresh official MQTT battery | private-cloud SOC, then last-known |
| battery while docked/charging | private-cloud SOC | fresh MQTT battery, then last-known |
| Task progress | fresh vendor task progress | zone-model fallback / last-known |
| Task mowed area | fresh vendor task area | calculated/zone fallback where needed |
| Map coverage / mowed area | current per-zone vendor coverage snapshot | retained last-known zone data |
| map, settings and schedule | private cloud | persisted/local cache where supported |
| Gate close/clear safety | fresh MQTT pose | fresh private-cloud pose with two-report confirmation |

No synthetic/interpolated battery percentages are generated.

## Navimower Map Card

The supported interactive map UI is distributed separately:

- [vahesoo/navimower-map-card](https://github.com/vahesoo/navimower-map-card)

Install it through HACS as a **Dashboard** custom repository. A minimal card
normally needs only the mower entity:

```yaml
type: custom:navimower-map-card
entity: lawn_mower.tont
```

The integration itself provides authenticated local APIs for map data, session
indexes, exact route details and completed-session render archives.

### Built-in Map Camera removal

The old SVG **Legacy Map Camera** was deprecated in 0.4.1 and is **removed from
0.4.2-beta1 onward**. The camera platform, renderer and camera entity translation
are no longer part of the integration. Existing dashboards should use Navimower
Map Card instead.

This removal does not affect camera/VisionFence-related mower settings such as
Camera positioning (EFLS); it removes only the old Home Assistant SVG map-camera
entity.

## Options

Open **Settings -> Devices & services -> Navimower -> Configure**.

### General and trail history

- completed trail retention;
- include/exclude the return-to-dock route.

### Gates

Add, edit or delete zone-pair gates. Each gate can be bidirectional or one-way
and can retain the required signal for 0, 10, 20 or 30 seconds after arrival.

### Gate areas

Add, edit or delete local X/Y rectangles used as mower-presence sensors.

Development-only passive protocol discovery and custom diagnostics-export
options from the 0.4.1 beta cycle remain removed.

## Home Assistant diagnostics

Use Home Assistant's normal **Download diagnostics** action from the Navimower
integration/config-entry menu.

The generated document remains sanitized. It contains general mower,
connectivity, positioning, map, telemetry, history, capability, maintenance,
schedule, Problem/Error and latest-notification context. Tokens, password, email,
UID, full mower serial, GPS coordinates and other sensitive account/network
identifiers are redacted.

For **0.4.2-beta1 only**, Download diagnostics also performs a bounded inspection
of public unauthenticated H5 HTML/JavaScript to locate the official app's
notification read-state request structure. It sends no account credentials,
Navimow token/cookie, device ID, mower serial or encrypted p:101 business
payload, and it executes **no** `clearBatchMessageRead` or other notification
mutation call. Only bounded sanitized source context is retained in the
`notification_read_h5_discovery` diagnostics section.

The older passive MQTT discovery, broad endpoint probing, state-transition
capture and custom `navimower.export_diagnostics` / `mark_discovery_event`
actions remain removed.

## 0.4.2 beta development

### 0.4.2-beta1

- removes the deprecated Legacy Map Camera platform and renderer;
- keeps Navimower Map Card as the supported map UI;
- keeps Latest notification and the existing read-only Device feed unchanged;
- confirms/documented notification `read` state as account-specific from field
  testing;
- adds a targeted Download diagnostics H5 inspection to recover the official
  notification read mutation request for future **Mark as read** and **Mark all
  as read** support;
- does not yet send notification write/mutation requests.

## Upgrade from 0.4.0 to 0.4.1

0.4.1 is a cumulative stability and data-correctness release built directly on
0.4.0. No beta releases need to be installed individually.

Highlights include:

- guarded private-cloud polling under dense MQTT traffic;
- clearer raw vendor Task/Map/zone telemetry ownership;
- multi-zone session and same-day trail stability;
- route/history pose deduplication and mower-specific mowing footprint width;
- freshness-aware private-cloud position fallback with conservative Gate safety;
- corrected Idle, Lifted and numeric-fault handling;
- vendor detail on the Error sensor for reported numeric faults;
- the new Latest notification Device-feed sensor;
- initial experimental/unverified i2 AWD capability support;
- persistent bidirectional Night mowing, Rain and Rain sensor writes;
- removal of beta-only diagnostics/discovery controls;
- Legacy Map Camera deprecation notice ahead of its 0.4.2-beta1 removal.

See [CHANGELOG.md](CHANGELOG.md) and
[`.github/release-notes/0.4.1.md`](.github/release-notes/0.4.1.md) for the release
summary.

### v0.3.4

The 0.3.4 release established the current capability-driven settings model and
field-tested H215 control baseline. 0.4.1 retains those controls while adding the
runtime, notification, error and model-support changes described above.

## Current limitations

- Private-cloud behavior is undocumented and may change without notice.
- Model and firmware fields differ; unsupported settings are not invented.
- i2 AWD support is experimental and not yet field-tested through Navimower.
- Exact dense route history depends on live MQTT pose samples; missing samples
  are not reconstructed.
- Map writes, boundary edits and other destructive map editing are deliberately
  not included.
- The old built-in SVG Map Camera is removed from the 0.4.2 beta line; use
  Navimower Map Card.
- Notification read actions are not yet exposed in HA; beta1 only gathers the
  public H5 request structure needed to implement them safely in a later beta.

## Credits and licence

The private-cloud foundation used by Navimower was created by **Roberto
Gualandris** in [ilguala/navimow_pro](https://github.com/ilguala/navimow_pro).
Persistent route and Gate-area/gate work also developed from
[vahesoo/NavimowHA](https://github.com/vahesoo/NavimowHA). The current dashboard
UI is developed separately in
[vahesoo/navimower-map-card](https://github.com/vahesoo/navimower-map-card).

See [NOTICE.md](NOTICE.md) and [LICENSE](LICENSE). The project is distributed
under the MIT License.
