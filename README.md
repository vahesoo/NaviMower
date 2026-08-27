# Navimower

Home Assistant integration for Segway Navimow robot mowers.

Navimower combines the Navimow private app cloud with the official Smart Home
OAuth/MQTT connection in one Home Assistant config entry. The result is a
model-aware integration with live mower telemetry, map and zone data, persistent
mowing history, mower settings, notifications, gates, Custom Areas and an
optional Home Assistant-owned zone scheduler.

Navimower does **not** require the older NavimowHA integration.

> [!WARNING]
> Navimower uses an undocumented private cloud protocol. It is not affiliated
> with or supported by Segway, Ninebot, Navimow or Willand. Vendor endpoints,
> payloads and behavior can change without notice.
>
> A robotic mower is a moving machine with a cutting blade. Test mower commands,
> gate automations and other physical automations in a safe environment before
> relying on them unattended.

## Highlights

- **Two independent cloud connections** in one config entry:
  - private app cloud for map geometry, settings, schedules, notifications,
    maintenance and stable cloud state;
  - official Smart Home OAuth + MQTT for dense live position, heading, battery
    and mower events.
- **Persistent mowing history** stored by Navimower and retained through normal
  pause/resume, integration reloads and Home Assistant restarts.
- **Per-zone state** including Coverage, Area, Mowed area, Last mowed and
  trustworthy Last completed timestamps.
- **Backend current-cycle rendering** prepared by the integration for
  Navimower Map Card instead of rebuilding completed mowing geometry in every
  browser.
- **Navimower Schedule**, an integration-owned one-zone-at-a-time scheduler with
  Automatic or Custom order, safe interrupted-task resume and reversible
  pause/resume.
- **Custom Areas** imported from temporary Navimow Off-limit polygons and stored
  locally in Home Assistant.
- **Physical gate support** using zone-to-zone intent plus optional local X/Y
  Gate areas.
- **Merged notifications** combining the vendor Device feed with confirmed local
  Navimower activity context.
- **Model-aware mower settings** for mowing, weather, battery, lights, safety,
  navigation and supported model-specific features.
- **Native GPS Location device tracker** when the private cloud reports valid
  geographic coordinates.
- **Sanitized Download diagnostics** plus optional bounded Passive protocol
  discovery for controlled investigations.

## Installation

### HACS custom repository

1. Open **HACS -> Integrations -> three-dot menu -> Custom repositories**.
2. Add `https://github.com/vahesoo/NaviMower` as category **Integration**.
3. Install **Navimower**.
4. Restart Home Assistant.
5. Open **Settings -> Devices & services -> Add integration -> Navimower**.

### Manual installation

Copy:

```text
custom_components/navimower
```

to:

```text
/config/custom_components/navimower
```

and restart Home Assistant.

## Recommended account arrangement

A dedicated shared Navimow account is recommended for the private app-cloud
connection.

```text
Owner account(s) -> Navimow phone app and/or official Smart Home OAuth
Shared account   -> Navimower private-cloud login
```

Share the mower from its primary owner account to the dedicated account before
adding Navimower.

Do not normally sign the dedicated private-cloud account into the phone app after
setup. Field testing has shown that a phone-app login can invalidate the private
Home Assistant session.

The private-cloud password is used only for login/reauthentication and is not
stored by Navimower.

The Smart Home OAuth account may be different from the private-cloud account as
long as both accounts can access the same mower.

> [!IMPORTANT]
> Multiple mower config entries may use the **same dedicated private-cloud
> account**. Add each mower as its own Navimower config entry. Entries keep
> separate devices, entities, maps and histories while sharing the account-level
> private-cloud identity required by the vendor protocol.

## Initial setup flow

When adding Navimower:

1. Enter the email and password of the dedicated private-cloud account.
2. Navimower reads the mowers available to that account.
   - If exactly **one unconfigured mower** is found, Navimower selects it
     automatically and continues.
   - If **two or more unconfigured mowers** are found, choose which mower to add.
   - If no mower is returned for a shared account, use the serial-number fallback
     shown by the setup flow.
3. Continue to official **Smart Home OAuth**.
4. Sign in with an account that can access the same mower.
5. Navimower validates the OAuth mower and stores both connection branches in the
   same config entry.

The two branches degrade independently. A temporary OAuth/MQTT problem does not
necessarily remove cached/private-cloud functionality, while a temporary private
cloud problem does not necessarily remove already-running MQTT live telemetry.

## Important: Configure Navimower after setup

Many of Navimower's integration-owned features are configured through the
**Options Flow**, not from the mower entity itself.

Open:

**Settings -> Devices & services -> Navimower -> Configure**

The Configure menu contains:

- **General and trail history**
- **Navimower Schedule**
- **Gates**
- **Custom areas**
- **Gate areas**

If you install Navimower and only inspect the mower device/entities, you will
miss these features.

### General and trail history

Use this section to configure:

- completed mowing-route retention: 3, 7, 14 or 30 days, or unlimited;
- whether the return-to-dock route is retained;
- **Passive protocol discovery** for short controlled diagnostics.

Passive protocol discovery is disabled by default. Enable it only while
reproducing a specific protocol/model behavior, download diagnostics, then turn
it off again.

## Map and zones

Navimower decodes the private-cloud map and exposes:

- real zone names and vendor zone IDs;
- zone geometry and area;
- Off-limit areas;
- VF-off areas;
- mapped Channels;
- charging station/dock;
- supported map metadata;
- global and per-zone cutting-height context where the mower reports trustworthy
  values.

Map geometry is cached through temporary private-cloud outages.

The vendor `mapVersion` is monitored as a fast revision signal. When it changes,
Navimower invalidates stale decoded geometry and refreshes map/location data
instead of waiting for longer idle cache intervals.

### Per-zone entities

For each discovered zone Navimower can expose:

- **Coverage**
- **Area**
- **Mowed area**
- **Last mowed**
- **Last completed**

Zone entity unique IDs are based on the vendor zone ID. Renaming an existing zone
can therefore update its display name without creating a replacement entity.

Merging, splitting or recreating zones is different: the Navimow app can assign
new zone IDs. Navimower removes stale Home Assistant zone-sensor registry rows
once a freshly decoded, versioned map proves that those old IDs no longer exist.
A missing/empty/unversioned map is never used as deletion evidence.

Historical mowing/session data is retained even when a former map zone ID is no
longer present.

### Last completed semantics

**Last completed** is intentionally conservative.

A zone is confirmed complete from fresh current-cycle vendor per-zone coverage at
100%. Route progress, whole-task percentage, returning to the dock or a stale
historical 100% value is not enough on its own.

This completion model is also the authority used by Navimower Schedule.

## Task, map and position telemetry

Navimower keeps vendor counters with different meanings separate instead of
turning every percentage into one generic progress value.

### Task progress

**Task progress** represents the selected vendor whole-task/work progress.

It is not automatically treated as map coverage or proof that a particular zone
completed.

### Task mowed area

**Task mowed area** follows the selected current-task area source.

### Map coverage and Map mowed area

These are derived from the current private-cloud per-zone coverage snapshot.

### Physical zone vs target zone

Navimower deliberately separates:

- the mapped polygon the mower is physically inside;
- the work target/progress-owner zone;
- whole-task progress;
- active-zone progress.

A mower crossing another mapped zone therefore does not transfer task completion
or ownership to the polygon under its wheels.

When the mower is confirmed docked/charging, Current physical zone is shown as
the virtual **Dock** area instead of retaining a stale lawn-zone position.

## Live position and fallback behavior

Fresh official MQTT local X/Y is the preferred physical position source.

When live MQTT pose is temporarily unavailable, a recent private-cloud local
position can keep physical-zone and Channel/Gate-area display useful.

Private-cloud freshness is based on the vendor's own `report_time`, not merely on
when Home Assistant polled the endpoint.

Stale cloud coordinates are display-only and are not promoted to fresh gate
safety evidence.

For a risky cloud-only gate close/clear or Gate-area OFF transition, two distinct
fresh vendor position reports are required before a previously active state can
be cleared.

### Position health

Navimower exposes separate health concepts including:

- Private cloud connected;
- OAuth connected;
- MQTT connected;
- Live position valid;
- Position source;
- MQTT position stream;
- MQTT pose age.

A connected MQTT broker does not automatically mean a continuous live pose
stream is currently expected or valid.

## GPS Location device tracker

When the private cloud reports valid latitude/longitude, Navimower creates a
native Home Assistant **Location** `device_tracker` on the mower device.

This allows the mower to appear on Home Assistant's built-in Map and participate
in normal Home Assistant geographic zones.

The tracker uses vendor-reported private-cloud geographic coordinates. Dense MQTT
`X/Y` remains mower-local Cartesian map coordinates and is not converted into
latitude/longitude.

Navimower does not invent a GPS accuracy radius, and geographic coordinates are
redacted from Download diagnostics.

## Persistent mowing history

Navimower stores dense mowing-route samples in its own Home Assistant storage
rather than relying on the browser or Recorder for exact route history.

History survives:

- normal pause/resume;
- charging interruptions;
- short integration reloads;
- Home Assistant restarts.

Missing route sections remain separate segments. Navimower does not draw an
invented straight line across a telemetry gap.

Multi-zone mowing remains one logical task across normal zone changes.

### Current mowing cycle

Current-cycle history is **reset-based, not calendar-day based**.

For each zone:

- a confirmed new/reset cycle replaces that zone's previous completed
  current-cycle mowing swath;
- pause/resume and `reset=false` continuation remain part of the same cycle;
- charging-related technical splits remain part of the same cycle;
- in a multi-zone reset task, a zone is cleared only when the new cycle actually
  enters that zone;
- untouched zones retain their previous current-cycle trail.

A current-cycle trail can therefore remain visible across midnight until that
zone begins a confirmed new cycle.

### Backend current-cycle rendering

Navimower prepares the expensive completed current-cycle mowing geometry on the
Home Assistant side.

The authenticated Map API exposes a ready-to-render
`current_cycle_render.mowed_area.path_d` SVG path. Navimower Map Card can render
that path directly while keeping the active live trail as a separate lightweight
overlay.

The render is cached by session/cycle/map state and the expensive SVG swath build
is kept out of Home Assistant's event loop.

Completed session archives remain separate for History and session highlighting.

### Trail retention

Retained completed history can be configured to:

- 3 days;
- 7 days;
- 14 days;
- 30 days;
- unlimited.

Shorter retention reduces Home Assistant storage and Map API payload size.

## Custom Areas

Custom Areas are Navimower-owned virtual polygons stored independently from the
mower's normal mowing-zone IDs.

They are useful when an automation needs a local map area that is not a normal
Navimow mowing zone, for example a gate passage, narrow corridor, driveway,
work-area boundary or another virtual presence area.

### Create a Custom Area

1. Open **Settings -> Devices & services -> Navimower -> Configure -> Custom areas -> Add custom area**.
2. Navimower immediately refreshes the current map and captures the existing
   Off-limit geometry as a baseline.
3. Open the Navimow app and create **exactly one** temporary Off-limit area in the
   desired Custom Area shape.
4. Save the map in the Navimow app.
5. Return to Home Assistant and continue the Custom Area flow.
6. Navimower refreshes the map and identifies the newly added polygon by
   geometry.
7. Give the detected area a name and save it.
8. Delete the temporary Off-limit area from the Navimow app if it is no longer
   needed there.

The imported Custom Area remains stored locally in Navimower.

Polygon matching does not depend on the vendor preserving the same Off-limit list
index, starting vertex or clockwise/counter-clockwise point order.

Deleting a Custom Area in Home Assistant removes only Navimower's local virtual
area. It does not write to or edit the mower map.

### Custom Areas across separate mowing zones

The Navimow app only allows an Off-limit area to be created **inside an existing
mowing zone**. An Off-limit polygon cannot extend outside the mowing zone or
bridge separate mowing zones that do not touch each other.

If the Custom Area you want needs to span two or more separate mowing zones:

1. In the Navimow app, temporarily **merge the required mowing zones into one
   larger zone**.
2. Save the merged map completely.
3. Only after the merge is saved, open **Configure -> Custom areas -> Add custom area** in Home Assistant. This order is important because selecting Add custom area captures the baseline map immediately.
4. In the Navimow app, create and save the temporary Off-limit polygon in the
   desired Custom Area shape. Because the former separate zones are temporarily
   one mowing zone, the polygon can cross their former boundary.
5. Return to Home Assistant and detect/name the Custom Area.
6. Delete the temporary Off-limit area from the Navimow app.
7. Restore the original mowing zones in the Navimow app.

The imported Navimower Custom Area is independent of the later mowing-zone layout,
so restoring or recreating the original zones does not remove the Custom Area.

Navimow may assign new zone IDs during the merge/restore process. Navimower's
stale-zone cleanup removes obsolete Home Assistant zone-sensor registry rows once
the final fresh map proves that the old zone IDs are gone.

### Custom Area occupancy

Every configured Custom Area creates a Home Assistant binary sensor.

The sensor is:

- **On** while a fresh official MQTT local X/Y pose is inside or exactly on the
  stored polygon boundary;
- **Off** while the fresh pose is outside;
- **Unavailable** when the live MQTT pose is missing or stale.

Custom Area occupancy intentionally does not use the slower private-cloud pose
fallback. This prevents an old cloud coordinate from being presented as fresh
virtual-area presence.

Custom Area geometry is also included directly in the authenticated Map API so
Navimower Map Card does not need to reconstruct polygons from Entity Registry
attributes.

## Gates and Gate areas

Navimower supports two related but different local automation concepts.

### Zone-pair Gates

A Gate links two mapped zones and exposes a **required** binary sensor.

Configure Gates from:

**Settings -> Devices & services -> Navimower -> Configure -> Gates**

A gate can be:

- bidirectional; or
- one-way from Zone A to Zone B.

The configured close delay can keep Gate required active briefly after the mower
reaches the destination side.

Gate intent is designed for automations that open a real physical gate between
mapped mowing areas.

### Gate areas

Gate areas are local X/Y rectangles and are configured from:

**Settings -> Devices & services -> Navimower -> Configure -> Gate areas**

They can be used for:

- a physical gate passage;
- camera or light automations;
- driveway/corridor presence;
- another local mower-presence use case.

Fresh MQTT is preferred. A sufficiently fresh private-cloud position may provide
a conservative fallback, with additional confirmation before a previously-active
Gate area is allowed to clear from cloud-only position data.

## State, Problem and Error

Navimower keeps mower activity and fault/safety state separate.

The mower state model recognizes normal activity such as mowing, paused,
returning/docked and relevant safety/error states.

Additional entities include:

- **Status**
- **Problem**
- **Error**

**Problem** answers whether an active problem/safety condition exists.

**Error** exposes the current private-cloud vendor fault detail when one exists.
Private-cloud `error_data` remains the canonical active error source; MQTT Error
events may trigger a refresh but do not temporarily replace the canonical detail.

When there is no active vendor fault, Error reports **No errors**.

A safety state such as a lifted mower is not assigned an invented numeric fault
code when the vendor did not report one.

## Latest notification

Navimower exposes one merged **Latest notification** timeline.

It can contain:

- vendor Device notifications with `origin: vendor`;
- confirmed Navimower/Home Assistant activity context with
  `origin: navimower`.

Vendor rows retain their original title, content, timestamp, read state and code.

Navimower rows are created only when the integration has enough evidence to
attribute confirmed activity. A mowing start with no fresh command trace in this
Home Assistant instance is shown neutrally as **Mowing task started** instead of
guessing whether it came from the phone app, another Home Assistant instance or
another control path.

Low-battery return uses the vendor Device notification as the visible charging
interruption row. Navimower retains unfinished task context and can add a resume
row when mowing genuinely continues.

The entity keeps a small Recorder-safe recent list in state attributes while the
integration retains wider internal history for card/diagnostic use.

### Notification actions

`navimower.mark_notification_read`

Marks one merged notification row read using the correct origin-specific path.

`navimower.mark_all_notifications_read`

Marks both retained Navimower-local rows and the vendor Device feed read.

Vendor read state is account-specific and applies to the private-cloud account
used by that config entry.

## Mower settings and controls

Available controls depend on mower model, firmware and vendor-reported
capabilities. Navimower does not create a setting merely because another mower
model is known to support it.

### Mowing and battery

Depending on the mower, controls can include:

- Mowing schedule enabled;
- Mowing cycle;
- Night mowing;
- Return-to-dock battery level;
- Charging limit;
- Global cutting height where supported.

### Weather-adaptive mowing

Current display terminology follows the vendor feature semantics:

- **Rain detection**
- **Rain sensor**
- **Rain forecast**
- **Rain forecast sensitivity**
- **Rain delay**
- **Rain delay duration**
- **Frost detection**
- **Frost delay**
- **Snow detection**
- **Snow delay**
- **Wind detection**
- **Max temp detection**
- **Max temperature**

### Do not disturb and general controls

- **Do not disturb**
- **Do not disturb start**
- **Do not disturb end**
- Sound
- Energy saver
- Night light and supported brightness controls

### Safety and navigation

Depending on capability evidence, Navimower may expose settings such as:

- Child lock;
- Lift alarm;
- Geo-fence controls;
- Camera positioning / EFLS-related controls;
- obstacle avoidance;
- traction control / TCS;
- animal protection;
- Terrain adapt;
- Edge sense;
- other model-specific navigation/work-mode settings.

**Night mowing, Rain detection and Rain sensor** use a proven robot-first plus
cloud-persist write path so the mower's onboard setting does not immediately
restore an older cloud value.

Unknown cutting-height encodings are not converted into invented millimetre
values.

## i2 AWD and capability-driven support

i2 AWD support is capability-driven and remains more experimental than the
longer-tested H-series paths.

Depending on what the mower actually reports, Navimower may expose controls such
as:

- Eco mode;
- Narrow zone adapt;
- Advanced slope mode;
- Grass pattern enhancement;
- Progress retention;
- Mowing cycle interval;
- Headlight;
- Night animal protection;
- Terrain adapt;
- Edge sense;
- TCS;
- positioning controls;
- Global cutting height.

> [!CAUTION]
> Not every model-specific write path has been field-validated across every i2
> AWD model/firmware combination. Availability and write semantics may vary.

## Navimower Schedule

**Navimower Schedule** is a Home Assistant/integration-owned scheduler for users
who want Navimower to decide **which selected zone should mow next** instead of
relying only on the weekly plan stored in the mower.

It intentionally works one zone at a time and uses trustworthy per-zone
completion state as its authority.

Navimower Schedule is separate from the mower's native **Mowing schedule
enabled** setting.

The two schedulers are mutually exclusive:

- enabling Navimower Schedule disables the native mower schedule;
- enabling the native mower schedule while Navimower Schedule is active disables
  Navimower Schedule instead of allowing two schedulers to compete.

### First-time Schedule setup

Before a zone can be enrolled, let it complete one trustworthy mowing cycle.
Zones with no confirmed Last completed timestamp remain unavailable for automatic
scheduling.

Then open:

**Settings -> Devices & services -> Navimower -> Configure -> Navimower Schedule**

and:

1. select the zones Navimower may control;
2. choose **Time window** or **24 hours**;
3. choose **Automatic order** or **Custom order**;
4. if Time window is selected, configure Start and End;
5. save the options;
6. enable the **Navimower schedule** switch when you want the scheduler active.

Newly created zones are never auto-enrolled.

The Navimower schedule switch, Schedule Status sensor and **Reset schedule
progress** button are created after Schedule has been configured for that mower.

### Automatic order

Automatic order selects the eligible selected zone whose trustworthy
**Last completed** timestamp is oldest.

A genuinely new scheduler zone is started as a new/reset cycle.

### Custom order

Custom order uses a persistent ordered queue.

The same zone may appear more than once. Every occurrence has its own queue slot
and is completed independently.

The queue can be edited from Navimower Map Card or with:

`navimower.set_schedule_queue`

Changing the queue does not itself enable or start the scheduler.

### Confirmed start and completion

A successful cloud command transport is not treated as proof that mowing began.
Navimower waits for real vendor mower/task state before claiming the zone as
active and before opening its local history cycle.

Likewise, the scheduler does not consider a zone complete merely because:

- the mower docks;
- it changes target;
- it reaches a high route-progress value;
- it begins a normal return transition.

Completion waits for the integration's trustworthy per-zone Last completed
logic.

### Direct zone handoff

After a scheduler zone genuinely completes, Navimower can hand off directly to
the next queued zone while the mower is already in its normal returning
transition rather than requiring a round trip to the dock first.

Low-battery charging returns and unrelated/manual returning states remain
protected from direct handoff.

### Time window

**Time window** is a hard Home Assistant outer boundary.

When the window closes while the mower is Mowing or Paused:

- Navimower sends Home/Dock;
- active zone/cycle/progress context is retained.

When the next window opens:

- Navimower first attempts to resume the retained task;
- if normal Resume cannot be confirmed, the safe one-zone continuation fallback
  uses `reset=false`;
- an interrupted task is never silently converted into a new `reset=true` cycle.

A window may cross midnight and is handled as one logical mowing period.

### 24 hours

**24 hours** removes Navimower's own daily start/end boundary.

After the selected queue/round finishes, another round can begin.

It does not bypass mower-owned restrictions. Charging, rain, night rules and
safety behavior remain mower/vendor-owned.

### Rain, night and charging interruptions

Navimower Schedule does not disable mower weather or safety logic.

Rain detection, Rain sensor, Rain forecast, Rain delay, Night mowing and other
mower-owned rules continue to decide whether the robot itself is allowed to mow.

An interrupted zone remains scheduler-owned until it genuinely completes or the
user explicitly resets scheduler progress.

For notification-confirmed low-battery charging, Navimower preserves the active
zone/cycle and prefers to let the mower resume its retained task itself. If the
mower has not resumed after reaching the configured **Charging limit**, Navimower
waits an additional grace period before considering the safe Resume/`reset=false`
fallback.

The Time window is rechecked immediately before every scheduler Resume/continue
command.

Charging recovery never uses `reset=true`.

### Schedule On/Off is pause/resume

Turning the **Navimower schedule** switch Off is non-destructive.

Off preserves:

- the current scheduler round;
- Custom queue position;
- active-zone ownership;
- retained interruption state.

Turning it back On resumes scheduler ownership. When enough retained-task
evidence exists, Navimower adopts the unfinished task and uses Resume or
`reset=false` continuation instead of starting the zone from scratch.

### Reset schedule progress

The mower device exposes a **Reset schedule progress** button after Navimower
Schedule has been configured.

It uses the same behavior as:

`navimower.reset_schedule`

Reset clears the current scheduler round/queue position and retained scheduler
ownership. It does **not** delete Schedule configuration or selected zones, and it
does not send a mower command itself.

Reset is unavailable/refused while the mower is mowing or returning.

If Schedule remains enabled after a reset, the next eligible scheduler zone is a
genuinely new cycle and may therefore use `reset=true`.

### Schedule Status

After Schedule is configured, Navimower exposes a **Navimower schedule status**
sensor for dashboard/card use.

It includes information such as:

- mode and configured time window;
- window-open state;
- order mode;
- ordered queue;
- completed, active and upcoming zones/slots;
- active/interrupted task context;
- resume/error/suspension information.

This lets Navimower Map Card visualize scheduler state without duplicating
scheduler policy in JavaScript.

## Home Assistant actions

Navimower provides these integration actions:

| Action | Purpose |
| --- | --- |
| `navimower.mow` | Start selected zones now; choose new/reset cycle or `reset=false` continuation |
| `navimower.resume` | Send the dedicated vendor Resume command for a retained task |
| `navimower.set_schedule` | Replace one weekday of the mower's native weekly schedule |
| `navimower.set_schedule_queue` | Persist Navimower Schedule Custom order; duplicate zones are allowed |
| `navimower.reset_schedule` | Explicitly clear managed-scheduler round/runtime ownership |
| `navimower.mark_notification_read` | Mark one merged notification row read |
| `navimower.mark_all_notifications_read` | Mark all retained local/vendor notification rows read |

The standard Home Assistant lawn-mower entity also provides normal mower actions
such as start/mow, pause and return to dock where supported.

## Connectivity and polling

Dense MQTT pushes are handled independently from the normal private-cloud refresh
schedule so frequent live coordinates do not starve settings, schedule, coverage
and cloud-state polling.

When the pose stream degrades, Navimower keeps useful MQTT state/progress traffic
instead of rebuilding the entire MQTT client solely because position is missing.
Location re-subscribe attempts are rate-limited while private-cloud fallback
remains available.

| Data | Preferred source | Fallback |
| --- | --- | --- |
| Local X/Y and heading | fresh official MQTT | recent private-cloud position, then retained display context |
| Live activity | official MQTT | private-cloud state |
| Battery while active | fresh official MQTT battery | private-cloud SOC, then last-known |
| Battery while docked/charging | private-cloud SOC | fresh MQTT battery, then last-known |
| Task progress | fresh vendor task progress | zone-model / retained fallback |
| Task mowed area | current task-area resolver | calculated/zone fallback where needed |
| Map coverage / mowed area | current private-cloud per-zone coverage | retained last-known zone context |
| Map, settings and native schedule | private cloud | persisted/local cache where supported |
| Physical gate close/clear | fresh MQTT pose | fresh private-cloud pose with conservative confirmation |
| Custom Area occupancy | fresh MQTT pose | none; becomes unavailable |

Navimower does not generate synthetic/interpolated battery percentages.

## Regional routing

Private app-cloud routing is discovered from the vendor Passport region instead
of being hard-wired to one European host.

Observed/canonical routing includes the vendor region families used for:

- Europe (`fra` / `eu`);
- Asia-Pacific (`sg` / `sea`);
- Americas (`us` / `ore`);
- mainland China (`bj`).

Official Smart Home OAuth/MQTT routing remains independent from the private-cloud
region and uses the official service/host information returned for that
connection.

For US accounts, Passport can report the raw region `ore`. Navimower preserves
that raw region for mower-cloud authentication while using the canonical US
routing family for host selection. This fixes the observed case where sending
`us` to mower login failed but `ore` returned a valid session identity.

Regional support is evidence-driven and not every region/model combination has
been equally hardware-tested.

## Navimower Map Card

The supported interactive dashboard map is maintained separately:

[**vahesoo/navimower-map-card**](https://github.com/vahesoo/navimower-map-card)

Install it through HACS as a **Dashboard** custom repository.

Minimal configuration:

```yaml
type: custom:navimower-map-card
entity: lawn_mower.tont
```

### Version compatibility

**Navimower Map Card 0.3.5 requires Navimower integration 0.4.3 or newer.**

The current card relies on integration-side features introduced in the 0.4.3
line, including:

- backend current-cycle SVG rendering;
- Custom Area geometry in the Map API;
- Navimower Schedule Status and queue metadata;
- server-side frontend entity metadata;
- mower model metadata.

The integration resolves card-facing entity/device metadata server-side so card
instances do not need to scan the full Home Assistant Entity Registry in the
browser.

### Legacy Map Camera

The old built-in SVG **Legacy Map Camera** has been removed.

Existing dashboards should use Navimower Map Card instead. This does not remove
camera/VisionFence-related mower settings; it only removes the old Home Assistant
SVG map-camera entity.

## Home Assistant diagnostics

Open the Navimower config-entry menu and choose **Download diagnostics**.

Normal Download diagnostics is **cached-only**. Downloading it does not start H5
research crawls, make exploratory vendor requests, mark notifications read or
send mower commands.

The sanitized document contains relevant support context including:

- config/model/capability state;
- private/OAuth/MQTT connectivity;
- regional routing status;
- polling and source freshness;
- MQTT navigation state;
- map revision and decoded map context;
- zone/completion/current-cycle state;
- Gate/Gate-area state;
- Navimower Schedule state;
- Problem/Error context;
- notification-center state;
- bounded sanitized raw/vendor structure needed for support.

Sensitive values such as password, email, tokens, UID, full mower serial, GPS
coordinates and other account/network identifiers are redacted.

### Passive protocol discovery

When a mower/model/protocol behavior cannot be explained by normal diagnostics,
temporarily enable:

**Settings -> Devices & services -> Navimower -> Configure -> General and trail history -> Passive protocol discovery**

It reuses the bounded/sanitized collector for current-device MQTT downlink samples
and related protocol evidence.

Use it only during a controlled test and turn it back off afterwards.

## Troubleshooting

### Schedule is waiting

Check:

- **Navimower schedule status**;
- Navimower schedule switch attributes;
- whether selected zones have a trustworthy Last completed timestamp;
- whether the Time window is open;
- whether the native mower schedule was enabled;
- whether the mower is charging or retained by rain/night behavior;
- **Download diagnostics**.

The scheduler deliberately prefers waiting over issuing an unsafe new
`reset=true` command when state is ambiguous.

### Mower position appears stale

Check:

- MQTT connected;
- Live position valid;
- MQTT position stream;
- Position source;
- MQTT pose age.

Private-cloud fallback can keep display/navigation context useful, but stale
cloud coordinates are not promoted to fresh safety evidence.

### A setting/entity is missing

Settings are capability-driven.

A control may be absent because:

- the model does not support it;
- the firmware does not report the required vendor field;
- Navimower does not yet have enough evidence to expose the write path safely.

Navimower prefers not exposing a control over guessing an unsupported command.

### Reporting a problem

When opening a GitHub issue, include:

1. Navimower version;
2. mower model and firmware;
3. country/region if authentication/routing may be relevant;
4. what you expected;
5. what actually happened;
6. relevant Home Assistant log output if available;
7. a fresh **Download diagnostics** export.

For a short protocol investigation, enable Passive protocol discovery only while
reproducing the specific event, download diagnostics, then disable it again.

Do not manually remove diagnostics redaction or publish account credentials or
tokens.

## Current limitations

- The private app-cloud protocol is undocumented and may change.
- Model and firmware capabilities vary significantly.
- i2 AWD and less-tested model-specific controls remain best-effort/experimental.
- Exact dense mowing history depends on live MQTT pose samples; missing samples
  are not reconstructed.
- Custom Area occupancy requires fresh MQTT pose.
- Geographic Location uses the vendor private-cloud GPS report rather than
  transforming dense mower-local MQTT X/Y.
- Destructive map editing, boundary modification and arbitrary mower-map writes
  are deliberately not implemented.
- Maintenance reset/error-recovery commands discovered only partially during
  protocol research are not exposed unless their vendor contract is sufficiently
  proven.

For release-by-release development history, see [CHANGELOG.md](CHANGELOG.md).

## Project origins

The private-cloud foundation used by Navimower was created by **Roberto
Gualandris** in
[ilguala/navimow_pro](https://github.com/ilguala/navimow_pro).

That project reverse-engineered the Navimow mobile application's private-cloud
communication and provided the authentication, encrypted protocol, map handling
and command foundation on which this integration builds.

Navimower extends that foundation with:

- official Smart Home OAuth/MQTT live data;
- freshness-aware source selection;
- persistent mowing history and current-cycle rendering;
- trustworthy per-zone completion semantics;
- physical-zone and gate logic;
- Custom Areas;
- merged notifications;
- integration-owned scheduling;
- model/capability-aware Home Assistant entities;
- a separately maintained Navimower Map Card.

Earlier persistent-route and Gate-area work also developed from
[vahesoo/NavimowHA](https://github.com/vahesoo/NavimowHA).

## Credits and licence

- [ilguala/navimow_pro](https://github.com/ilguala/navimow_pro) — private-cloud
  protocol foundation by Roberto Gualandris.
- [vahesoo/NavimowHA](https://github.com/vahesoo/NavimowHA) — earlier Home
  Assistant route/Gate-area work.
- [vahesoo/navimower-map-card](https://github.com/vahesoo/navimower-map-card) —
  current standalone dashboard map UI.

See [NOTICE.md](NOTICE.md) and [LICENSE](LICENSE).

Navimower is distributed under the MIT License.
