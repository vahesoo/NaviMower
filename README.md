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

## Installation

### HACS custom repository

1. Open **HACS -> Integrations -> three-dot menu -> Custom repositories**.
2. Add `https://github.com/vahesoo/NaviMower` as category **Integration**.
3. Install Navimower and restart Home Assistant.
4. Open **Settings -> Devices & services -> Add integration -> Navimower**.

Manual installation is also possible by copying `custom_components/navimower`
into `/config/custom_components/` and restarting Home Assistant.

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

Navimower exposes one merged **Latest notification** timeline. It combines the
Navimow app's Device feed with Home Assistant-side context that Navimower can
prove from confirmed mower state and fresh local command traces.

- up to **10 vendor** Device notifications are retained with `origin: vendor`;
- up to **20 Navimower** context rows are retained with `origin: navimower`;
- vendor rows keep their original title, content, timestamp, read state and code;
- Navimower rows explain confirmed Home Assistant starts, Resume/Dock actions,
  night interruptions and retained-task resumes without inventing vendor codes;
- a mowing start with no fresh command trace in this Home Assistant instance is
  shown neutrally as **Mowing task started** / observed start. It is not labelled
  as an app or "external" command because another HA instance, the mower itself
  or another control path may have issued it;
- low-battery return uses the vendor Device notification as the single visible
  charging-pause row. Navimower still retains the unfinished task context and can
  add **Mowing resumed after charging** when cutting really resumes;
- local activity rows are created only after confirmed vendor activity, not from
  an optimistic button state.

`navimower.mark_notification_read` marks the selected row using the correct
origin-specific path: local rows stay local, while vendor rows use the vendor
message flow. `navimower.mark_all_notifications_read` handles both retained local
rows and the vendor Device feed.

Vendor notification read state remains account-specific. These actions operate
on the private-cloud Navimow account used by that config entry and do not mark
messages read in other shared accounts.

Notification history is user-facing event information and does not replace the
live Problem/Error state model.

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
to the cloud. All three have been field-tested bidirectionally on H215.

Unknown encoded cutting-height markers are not converted into invented
millimetre values.

### i2 AWD experimental support

Initial i2 AWD support is capability-driven and may expose settings such as Eco
mode, Narrow zone adapt, Advanced slope mode, Grass pattern enhancement,
Progress retention, Mowing cycle interval, Headlight, Night animal protection,
Terrain adapt, Edge sense, TCS, positioning controls and Global cutting height
when the corresponding vendor fields are present.

> [!CAUTION]
> **i2 AWD controls remain experimental.** Diagnostics have provided useful
> capability evidence, but not every control/write path has been field-validated
> across i2 AWD models and firmware. Availability and write semantics can vary.

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

## Navimower Schedule

**Navimower Schedule** is an integration-owned scheduler for users who want Home
Assistant to decide **which zone should be mowed next** instead of relying on the
weekly schedule stored in the mower. It intentionally works one zone at a time:
Navimower selects the eligible zone with the oldest confirmed **Last completed**
time, starts that zone as a new mowing cycle, waits for genuine vendor completion,
and only then moves to the next zone.

This is separate from the mower's native **Mowing schedule enabled** setting. The
two schedulers must not control the mower at the same time. Turning Navimower
Schedule on disables the native mower schedule. If the native schedule is turned
back on later, Navimower Schedule disables itself instead of competing with it.

### First-time setup

Before a zone can be selected for Navimower Schedule, let the mower **fully
complete that zone once**. The integration must have a trustworthy `Last
completed` timestamp for it; a zone that has never completed is shown as
unavailable in the options flow. This prevents a newly discovered or historically
unknown zone from being started automatically without a proven completion cycle.

Then open **Settings -> Devices & services -> Navimower -> Configure -> Navimower
schedule** and:

1. select the zones that Navimower Schedule may control;
2. choose **Time window** or **24 hours**;
3. for Time window, set the allowed start and end time;
4. save the options;
5. turn on the **Navimower schedule** switch on the mower device.

Only the explicitly selected zones are enrolled. Adding another zone to the map
does not automatically add it to the scheduler.

Before this setup is saved, Navimower Schedule switch/time entities are intentionally
not created on the mower device. Saving the setup reloads the config entry and
exposes the controls for that mower. Turning the Schedule switch off later does
not remove the configured controls.

### How zone selection works

Within an allowed mowing period, Navimower chooses the selected eligible zone
whose confirmed completion is oldest. A newly dispatched zone uses a reset/new
cycle command. The scheduler does **not** consider a zone complete merely because
the mower docks, pauses, reaches a high route-progress value or changes target.
It waits until the integration's `Last completed` logic confirms fresh vendor
per-zone coverage at 100%.

Normal charging is left to the mower. The scheduler does not try to replace the
robot's battery/charging logic and does not mark a charging interruption as a
completed zone.

### Time window

**Time window** is a hard Home Assistant outer boundary. While the window is open,
the scheduler may start or continue its selected zones. When the window closes
while the mower is Mowing or Paused, Navimower sends it Home/Dock and remembers
the interrupted zone, cycle and progress context.

When the next window opens, Navimower first tries to **resume the retained task**.
If the normal resume command is not confirmed, it may send a one-zone continue
command with `reset=false`. It deliberately refuses to turn an interrupted task
into an automatic new/reset cycle merely because a resume acknowledgement was
lost. This protects partial progress across Home Assistant restarts and vendor
state delays.

A time window may cross midnight; its window token is handled as one logical
mowing period.

### 24 hours

**24 hours** means Navimower itself has no daily start/end boundary. After all
selected eligible zones have completed once, it starts another round and again
selects the oldest zone. It can therefore keep cycling through the selected zones
continuously while the mower is available.

24 hours does **not** mean "force the mower to cut regardless of its own safety or
environment rules". Charging, mower-side pauses and vendor safety behavior remain
robot-owned.

### Night mowing

The **Night mowing** mower setting remains independent. Navimower Schedule does
not switch Night mowing on and does not bypass the mower's night restrictions.
Therefore **24 hours** only removes the Navimower time-window restriction; if
Night mowing is disabled and the mower itself pauses/returns because of night
rules, the scheduler waits rather than manufacturing a new completed cycle or
forcing a reset start.

If you want mowing to be allowed overnight, configure the mower's **Night mowing**
setting accordingly. If you prefer a guaranteed Home Assistant cut-off regardless
of the mower setting, use **Time window**.

### Rain, Rain delay and weather interruptions

Rain handling is also mower-owned. **Rain detection / Rain sensor**, weather
forecast controls and **Rain delay** continue to decide when the mower should stop
and when it is allowed to continue. Navimower Schedule does not shorten the rain
delay, fake progress or mark the interrupted zone complete.

If rain interrupts a zone, that zone remains the scheduler's active work until it
actually completes. When a Time window closes during the interruption, the
scheduler retains the interrupted task and attempts to resume it when the next
window opens. In 24 hours mode, the scheduler leaves the retained active zone in
place and waits for the mower/vendor state to continue rather than starting a
fresh reset cycle over it.

### Useful state and troubleshooting

The **Navimower schedule** switch exposes attributes such as mode, start/end,
selected and eligible zone IDs, whether the window is open, active/interrupted
zone, last command, last error and any suspended reason. These attributes and the
normal Home Assistant **Download diagnostics** output are the first places to
check if a schedule appears to be waiting.

Common intentional waiting states include: the selected zone has never completed
once, the native mower schedule was enabled, the Time window is closed, an active
zone is interrupted by rain/charging/night behavior, or a start/resume command
was not safely confirmed. The controller prefers waiting over blindly issuing a
new reset command.

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

### Legacy Map Camera

The old built-in SVG **Legacy Map Camera** is removed. Existing dashboards should
use Navimower Map Card instead. This does not affect camera/VisionFence-related
mower settings such as Camera positioning (EFLS); only the old Home Assistant SVG
map-camera entity was removed.

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

The generated document is snapshot-only and sanitized. It contains general
mower, connectivity, positioning, map, telemetry, history, capability,
maintenance, Navimower Schedule, Problem/Error and notification-center context.
Tokens, password, email, UID, full mower serial, GPS coordinates and other
sensitive account/network identifiers are redacted.

Downloading diagnostics does not execute mower commands or notification read
mutations. Older development-only passive discovery/export controls are not part
of the normal integration UI.

## Current limitations

- Private-cloud behavior is undocumented and may change without notice.
- Model and firmware fields differ; unsupported settings are not invented.
- i2 AWD control support remains experimental and is not equally field-tested
  across models/firmware.
- Exact dense route history depends on live MQTT pose samples; missing samples
  are not reconstructed.
- Map writes, boundary edits and other destructive map editing are deliberately
  not included.

For release-by-release changes, see [CHANGELOG.md](CHANGELOG.md).

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

## Credits and licence

The private-cloud foundation used by Navimower was created by **Roberto
Gualandris** in [ilguala/navimow_pro](https://github.com/ilguala/navimow_pro).
Persistent route and Gate-area/gate work also developed from
[vahesoo/NavimowHA](https://github.com/vahesoo/NavimowHA). The current dashboard
UI is developed separately in
[vahesoo/navimower-map-card](https://github.com/vahesoo/navimower-map-card).

See [NOTICE.md](NOTICE.md) and [LICENSE](LICENSE). The project is distributed
under the MIT License.
