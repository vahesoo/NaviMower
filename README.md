# Navimower

Home Assistant integration for Segway Navimow robot mowers.

Navimower combines the Navimow private app cloud with the official Smart Home OAuth/MQTT connection in one Home Assistant config entry. It provides model-aware mower controls, live telemetry, maps/zones, persistent mowing history, notifications, physical-gate helpers, geographic map alignment/underlays and an optional Home Assistant-owned zone scheduler.

Navimower does **not** require the older NavimowHA integration.

> [!WARNING]
> Navimower uses an undocumented private-cloud protocol. It is not affiliated with or supported by Segway, Ninebot, Navimow or Willand. Vendor endpoints, payloads and behavior can change without notice.
>
> A robotic mower is a moving machine with a cutting blade. Test mower commands, gate automations and other physical automations in a safe environment before relying on them unattended.

## Highlights

- **Two independent cloud connections** in one config entry:
  - private app cloud for map geometry, settings, native schedules, notifications, maintenance and stable cloud state;
  - official Smart Home OAuth + MQTT for dense live position, heading, battery and mower events.
- **Persistent mowing history** retained through normal pause/resume, charging, integration reloads and Home Assistant restarts.
- **Per-zone state** including Coverage, Area, Mowed area, Last mowed and conservative/monotonic Last completed timestamps.
- **Reset-based current-cycle rendering** prepared by the integration and published through a phased Map API.
- **Navimower Schedule**, an integration-owned one-zone-at-a-time scheduler with Automatic or positional Custom order, repeated rounds, retained-task ownership and reversible pause/resume.
- **Custom Areas** imported from temporary Navimow Off-limit polygons and stored locally.
- **Physical-gate support** with zone-pair travel intent and exact local-X/Y polygon Gate areas.
- **Georeferenced Map/Site API** for geographic underlays and nearby multi-mower site transforms.
- **Map underlay backend** for Estonia orthophoto/hybrid and optional Google Satellite through an authenticated Home Assistant proxy.
- **Merged notifications** combining the vendor Device feed with confirmed local Navimower context.
- **Mowing pause reason** status with conservative vendor-confirmed low-battery classification.
- **Model-aware settings** for mowing, weather, battery, lights, safety, navigation and supported family-specific features.
- **Native GPS Location device tracker** when the private cloud reports valid geographic coordinates.
- **Sanitized cached-only Download diagnostics** plus optional bounded Passive protocol discovery and a separate explicit raw development export.

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

A dedicated shared Navimow account is recommended for the private app-cloud connection.

```text
Owner account(s) -> Navimow phone app and/or official Smart Home OAuth
Shared account   -> Navimower private-cloud login
```

Share the mower from its primary owner account to the dedicated account before adding Navimower.

Do not normally sign the dedicated private-cloud account into the phone app after setup. Field testing has shown that a phone-app login can invalidate the private Home Assistant session.

The private-cloud password is used only for login/reauthentication and is not stored by Navimower.

The Smart Home OAuth account may be different from the private-cloud account as long as both accounts can access the same mower.

> [!IMPORTANT]
> Multiple mower config entries may use the **same dedicated private-cloud account**. Add each mower as its own Navimower config entry. Entries keep separate devices, entities, maps and histories while sharing the account-level private-cloud identity required by the vendor protocol.

See [docs/MULTI_MOWER.md](docs/MULTI_MOWER.md) for multi-mower setup and Site API behavior.

## Initial setup flow

When adding Navimower:

1. Enter the email and password of the dedicated private-cloud account.
2. Navimower reads the mowers available to that account.
   - If exactly **one unconfigured mower** is found, it is selected automatically.
   - If **two or more unconfigured mowers** are found, choose which mower to add.
   - If no mower is returned for a shared account, use the serial-number fallback shown by the setup flow.
3. Continue to official **Smart Home OAuth**.
4. Sign in with an account that can access the same mower.
5. Navimower validates the OAuth mower and stores both connection branches in the same config entry.

The two branches degrade independently. A temporary OAuth/MQTT problem does not necessarily remove cached/private-cloud functionality, while a temporary private-cloud problem does not necessarily remove already-running MQTT live telemetry.

## Important: Configure Navimower after setup

Many integration-owned features are configured through the **Options Flow**, not from the mower entity itself.

Open:

**Settings -> Devices & services -> Navimower -> Configure**

The current menu contains:

- **General and trail history**
- **Navimower Schedule**
- **Gates**
- **Custom areas**
- **Gate areas**
- **Map underlay**

### General and trail history

Configure:

- completed mowing-route retention: 3, 7, 14 or 30 days, or unlimited;
- whether return-to-dock route is retained;
- **Passive protocol discovery** for short controlled diagnostics.

Passive protocol discovery is disabled by default. Enable it only while reproducing a specific model/protocol behavior, download diagnostics, then turn it off again.

## Map and zones

Navimower decodes the private-cloud map and exposes:

- real zone names and internal vendor zone IDs;
- zone geometry and area;
- Off-limit areas;
- VF-off areas;
- mapped Channels;
- charging station/dock;
- supported map metadata;
- global/per-zone cutting-height context when reported reliably.

Map geometry is cached through temporary private-cloud outages.

The vendor `mapVersion` is monitored as a fast revision signal. A change invalidates stale decoded geometry and triggers map/location refresh instead of waiting for longer idle cache intervals.

### Per-zone entities

For each discovered zone Navimower can expose:

- **Coverage**
- **Area**
- **Mowed area**
- **Last mowed**
- **Last completed**

Zone entity unique IDs are based on the internal vendor zone ID. Renaming a zone can therefore update its display name without creating a replacement entity.

Merging, splitting or recreating zones may create new vendor IDs. Navimower removes stale Home Assistant zone-sensor registry rows only after a fresh versioned map proves those old IDs no longer exist. Missing/empty/unversioned map data is never deletion evidence. Historical sessions are retained.

### Last completed semantics

**Last completed** is intentionally conservative and monotonic.

A zone is confirmed complete from fresh current-cycle vendor per-zone coverage reaching 100%. Whole-task percentage, route progress, returning to dock or a stale historical 100% is not enough on its own. Once a newer trustworthy completion timestamp is known, later startup/cloud repair must not move the sensor backwards to an older completion.

The same completion model is authoritative for Navimower Schedule.

## Task, map and position telemetry

Navimower keeps vendor counters with different meanings separate instead of turning every percentage into one generic progress value.

- **Task progress** — selected whole-task/work progress.
- **Task mowed area** — current task-area resolver.
- **Map coverage / Map mowed area** — current private-cloud per-zone coverage snapshot.
- **Active-zone progress** — progress attributed to the currently owned work zone when that can be proven.

### Physical zone vs target zone

Navimower deliberately separates:

- the mapped polygon the mower is physically inside;
- the work target/progress-owner zone;
- whole-task progress;
- active-zone progress.

Crossing another mapped zone therefore does not transfer task completion/ownership to the polygon under the wheels.

When confirmed docked/charging, Current physical zone is shown as virtual **Dock** instead of retaining a stale lawn-zone position.

## Live position and navigation fallback

Fresh official MQTT local X/Y is the preferred physical position source.

When MQTT pose is temporarily unavailable, a recent private-cloud local position can keep physical-zone and Channel/Gate-area display useful. Freshness is based on the vendor's own `report_time`, not merely the Home Assistant poll time.

Stale cloud coordinates are display-only and are not promoted to fresh gate-safety evidence. Risky cloud-only gate clear/arrival or Gate-area OFF transitions require distinct fresh vendor reports; duplicate/out-of-order/invalid timestamps do not advance the confirmation.

Target freshness is tracked independently from pose freshness. A fresh mower position does not make an old cached work target fresh.

### Position health

Navimower exposes separate health concepts including:

- Private cloud connected;
- OAuth connected;
- MQTT connected;
- Live position valid;
- Position source;
- MQTT position stream;
- MQTT pose age.

A connected MQTT broker does not automatically mean a continuous live pose stream is currently expected or valid.

## GPS Location device tracker

When the private cloud reports valid latitude/longitude, Navimower creates a native Home Assistant **Location** `device_tracker`.

The tracker uses vendor-reported private-cloud geographic coordinates. Dense MQTT X/Y remains mower-local Cartesian map data and is not converted into latitude/longitude for this entity.

Navimower does not invent a GPS accuracy radius, and geographic coordinates are redacted from normal Download diagnostics.

## Map georeference and underlays

Navimower can align mower-local maps with geographic providers while keeping the mower's own X/Y geometry authoritative.

The integration builds/validates a local-map -> geographic transform from the best evidence available for the map, including vendor transform/static/RTK metadata and learned private-cloud local-X/Y + GPS evidence. A map revision is the normal automatic recalibration boundary; ordinary GPS drift must not continuously move a validated map.

Provider-ready frames allow different underlays to use the correct geographic presentation without adding mower-model offsets in the browser.

Open:

**Settings -> Devices & services -> Navimower -> Configure -> Map underlay**

Available backend options include:

- Estonia orthophoto for Estonian sites;
- Estonia hybrid for Estonian sites;
- optional Google Satellite using a Google Map Tiles API key.

A Google key is account-scoped across mower entries using the same private-cloud account. The key and Google session token remain on the Home Assistant backend; authenticated proxy endpoints serve tiles/viewport metadata to the frontend.

Detailed behavior and troubleshooting: [docs/MAP_GEOREFERENCE_AND_UNDERLAYS.md](docs/MAP_GEOREFERENCE_AND_UNDERLAYS.md).

## Persistent mowing history

Navimower stores dense mowing-route samples in its own Home Assistant storage instead of relying on the browser or Recorder for exact route history.

History survives normal pause/resume, charging interruptions, short integration reloads and Home Assistant restarts. Missing sections remain separate segments; Navimower does not invent a straight line across a telemetry gap. Multi-zone mowing remains one logical task across normal zone changes.

### Current mowing cycle

Current-cycle history is **reset-based, not calendar-day based**.

For each zone:

- a confirmed new/reset cycle replaces that zone's previous completed current-cycle swath;
- pause/resume and `reset=false` continuation remain in the same cycle;
- charging technical splits remain in the same cycle;
- in a multi-zone reset task, a zone is cleared only when the new cycle actually enters that zone;
- untouched zones retain their previous current-cycle trail.

A current-cycle trail can remain visible across midnight until that zone begins a confirmed new cycle.

### Backend/phased current-cycle rendering

Navimower prepares completed current-cycle mowing geometry on the Home Assistant side. The Map API can return the normal combined response or allow a compatible frontend to load the base map without current-cycle geometry and fetch the compact current-cycle artifact independently.

This keeps base-map loading responsive without changing retained history. Completed session archives remain the source of truth for History/session highlighting.

### Trail retention

Completed history can be retained for 3, 7, 14 or 30 days, or unlimited. Shorter retention reduces Home Assistant storage and Map API payload size.

## Custom Areas

Custom Areas are Navimower-owned virtual polygons stored independently from normal mowing-zone IDs. They are useful for a gate approach, corridor, driveway, work-area boundary or other local map presence area.

### Create a Custom Area

1. Open **Configure -> Custom areas -> Add custom area**. Navimower refreshes the map and immediately captures existing Off-limit geometry as the baseline.
2. In the Navimow app, create **exactly one** temporary Off-limit polygon in the desired shape and save the map.
3. Return to Home Assistant and continue the flow.
4. Navimower refreshes the map, detects the newly added polygon by geometry, then asks for a name.
5. Save the Custom Area and remove the temporary Off-limit polygon from the Navimow app if no longer needed.

The imported Custom Area remains local to Navimower. Polygon matching does not depend on list index, first vertex or clockwise/counter-clockwise order.

Deleting a Custom Area removes only Navimower's local virtual area; it does not edit the mower map.

### Across separate mowing zones

The Navimow app normally requires an Off-limit polygon to remain inside an existing mowing zone. If the desired Custom Area must cross separate zones, temporarily **merge** the required mowing zones, save the merge, start **Add custom area** to capture that merged baseline, create/import the temporary Off-limit polygon, then delete it and restore the original mowing-zone layout.

The imported Custom Area is independent from later zone IDs/layout. Restoring/recreating zones does not remove it.

### Custom Area occupancy

Each Custom Area creates a binary sensor:

- **On** while fresh official MQTT X/Y is inside/on the polygon;
- **Off** while the fresh pose is outside;
- **Unavailable** when live MQTT pose is missing/stale.

Custom Area occupancy intentionally does **not** use private-cloud fallback. Gate areas have different, more conservative fallback semantics because they are designed for physical-gate workflows.

## Gates and Gate areas

Navimower provides two related but separate concepts.

### Zone-pair Gates

A **Gate** links two mapped zones and exposes a `Gate required` binary sensor. It may be bidirectional or one-way. The optional close delay keeps intent asserted briefly after arrival.

Gate intent uses freshness-aware target/position arbitration. A stale opposite-zone target is not allowed to keep an old latch active after a fresh same-zone command proves the mower is staying in its current zone, while unknown/stale position and active channel crossings remain fail-safe.

Configure from **Configure -> Gates**.

### Gate areas

A **Gate area** is an integration-owned local-X/Y presence area. Current Gate areas support exact polygons; legacy rectangles remain compatible. When a polygon exists it is authoritative and min/max bounds are retained only for backward compatibility.

Gate areas can be created/edited through:

- a compatible Navimower Map Card visual editor (preferred for normal polygon drawing);
- **Configure -> Gate areas** as a manual/fallback editor;
- `navimower.set_gate_area` / `navimower.delete_gate_area` for integration/frontend use.

Polygon writes are validated (3–64 unique points, non-self-intersecting, non-zero area) before being persisted.

Fresh MQTT is preferred for occupancy. Sufficiently fresh private-cloud position can provide conservative fallback; clearing a previously active Gate area through cloud-only evidence requires repeated distinct reports.

A complete physical-gate guide, including the helperless **single automation run owns open+close** pattern currently being field-tested with an optimistic/closed-magnet gate cover, is in [docs/GATE_AUTOMATION.md](docs/GATE_AUTOMATION.md).

## State, Problem, Error and mowing pause reason

Navimower keeps mower activity, safety/fault state and interruption cause separate.

- **Status** — normalized mower activity/state.
- **Problem** — whether a problem/safety condition is active.
- **Error** — canonical private-cloud vendor fault detail when one exists.
- **Mowing pause reason** — retained mowing interruption classification.

Private-cloud `error_data` remains the canonical active numeric fault source. MQTT error-state edges can request a refresh but do not temporarily replace canonical fault detail. A safety state such as Lifted is not assigned an invented numeric error code when the vendor did not report one.

`Mowing pause reason` may expose states such as:

- `none`;
- `low_battery_pending` — inferred from transition/battery context, **not automation-safe**;
- `low_battery` — requires a time-matched vendor low-battery (`1502`) notification and is the conservative automation-safe state;
- `night`;
- `manual_dock`;
- `unknown` or another attributed interruption reason.

Generic Returning/Docked states remain cause-agnostic.

## Latest notification

Navimower exposes one merged **Latest notification** timeline containing:

- vendor Device rows with `origin: vendor`;
- confirmed Navimower/Home Assistant context with `origin: navimower`.

Vendor rows retain their vendor title/content/timestamp/read state/code. Local rows are created only when the integration has enough evidence to attribute activity; otherwise wording remains neutral.

Low-battery return uses the vendor Device notification as the visible interruption row while Navimower retains unfinished-task context for later resume attribution.

Actions:

- `navimower.mark_notification_read`
- `navimower.mark_all_notifications_read`

## Mower settings and controls

Available controls depend on mower model, firmware and vendor-reported evidence. Navimower prefers hiding a control over guessing an unsupported command.

### Mowing and battery

Depending on family/firmware, controls can include:

- native Mowing schedule enabled;
- Mowing cycle;
- Night mowing;
- Return-to-dock battery level;
- Charging limit;
- electronic/global cutting height where supported.

### Weather-adaptive mowing

Current display terminology follows the vendor feature semantics where the mower family actually supports the control:

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

Shared vendor fields are not treated as proof that every family has the same UI controls. For example, i1/i2 LiDAR rain controls are capability-gated rather than created merely because a dormant field exists.

### General, safety and navigation

Depending on capability evidence, controls can include:

- Do not disturb period, sound and lighting;
- Child lock and Lift alarm;
- anti-theft/geo-fence controls;
- obstacle/animal protection;
- Terrain adapt;
- Edge sense;
- TCS/traction;
- other model-specific work/positioning controls.

Unknown cutting-height encodings are not converted into invented millimetre values.

## i2 AWD and capability-driven support

i2 AWD and other newer/less-tested mower-family controls are provisioned from positive capability evidence rather than from model-name guesses or dormant shared `set-list` fields.

Depending on model, firmware and proven vendor fields, Navimower may expose controls such as Eco/work mode, Narrow zone adapt, Advanced slope mode, Grass pattern enhancement, Progress retention, Mowing cycle interval, Headlight, Night animal protection, Terrain adapt, Edge sense, TCS/traction, positioning-related controls and electronic cutting height.

Not every reported field is remotely writable and not every related model exposes the same subset. Navimower keeps unverified controls hidden until documentation or controlled field evidence proves the semantics/write path.

The same rule applies outside i2 AWD. For example, i1 can report cutting-height range metadata while the current height field is not treated as proof of the physical manual-knob position, so Navimower does not invent a remote cutting-height writer for that behavior.

## Navimower Schedule

**Navimower Schedule** is a Home Assistant/integration-owned one-zone-at-a-time scheduler. It is separate from the mower's native weekly schedule and the two schedulers are mutually exclusive.

Before a zone can be enrolled, let it complete one trustworthy cycle so it has a confirmed Last completed timestamp.

Configure:

**Settings -> Devices & services -> Navimower -> Configure -> Navimower Schedule**

Choose:

1. zones Navimower may control;
2. **Time window** or **24 hours**;
3. **Automatic order** or **Custom order**;
4. Start/End when using Time window.

Newly created zones are never auto-enrolled.

### Automatic order

Automatic order picks the eligible selected zone whose trustworthy **Last completed** is oldest.

### Custom order

Custom order is a positional queue. The same zone may appear multiple times, e.g. `[36, 37, 36]`; each occurrence is a separate slot.

The active round keeps an immutable `round_queue` snapshot. Queue edits made after a round starts are staged for the next round instead of changing slot identity mid-round. Once a slot has been confirmed started, ownership loss must not cause Navimower to restart that same slot with a fresh `reset=true` command.

The queue can be edited by a compatible Map Card or with `navimower.set_schedule_queue`.

### Start/completion ownership

Successful command transport is not proof that mowing began. Navimower waits for real vendor mower/task state before claiming scheduler ownership/opening the local cycle.

Retained-task Resume is also fail-closed: stale scheduler state must not adopt an unrelated native/manual/multi-zone task. Different-zone/multi-zone/conflicting evidence invalidates old ownership rather than risking a wrong Resume.

Completion uses the same conservative per-zone Last completed semantics described above.

### Repeated rounds

Both scheduler modes can begin another round after all selected zones/queue slots complete:

- **Time window** may repeat rounds while the current window remains open; a new round waits for a normal idle start boundary and the configured window end remains the hard outer boundary.
- **24 hours** has no Navimower daily start/end boundary and can continue rounds subject to mower/vendor charging/weather/night/safety behavior.

### Rain, night and charging interruptions

Navimower does not disable mower-owned safety/weather logic. Night mowing, Rain detection, Rain sensor, Rain forecast and Rain delay behavior remain mower/vendor-owned restrictions when supported and enabled.

For a retained low-battery task, Navimower prefers to let the mower resume by itself after charging. If it has not resumed after reaching the configured **Charging limit**, Navimower waits an additional grace period before considering its safe Resume/`reset=false` fallback. The Time window is rechecked immediately before any Navimower Resume/continue command.

If the **vendor itself auto-resumes** a retained task after the Time window has already closed, Navimower does not claim that it sent Resume; the closed-window guard observes the mower moving outside the allowed window and sends Dock/Home while retaining unfinished task context.

Charging recovery never uses `reset=true`.

### Schedule On/Off and reset

Turning the **Navimower schedule** switch Off pauses scheduler ownership non-destructively. Round position, Custom queue slot, active-zone ownership and retained interruption context are preserved. Turning it back On resumes/adopts the same task only when evidence is safe.

**Reset schedule progress** / `navimower.reset_schedule` deliberately clears current round/queue runtime and retained scheduler ownership without sending a mower command. It is refused while the mower is mowing or returning.

### Schedule Status

After Schedule is configured, the **Navimower schedule status** sensor provides mode/window/order, queue, completed/active/upcoming slots, interruption/resume and error/suspension context for dashboards/frontends.

## Home Assistant actions

| Action | Purpose |
| --- | --- |
| `navimower.mow` | Start selected internal map zone IDs now; choose new/reset cycle or `reset=false` continuation |
| `navimower.resume` | Send the dedicated vendor Resume command for a retained task |
| `navimower.set_schedule` | Replace one weekday of the mower's native weekly schedule |
| `navimower.set_schedule_queue` | Persist positional Navimower Schedule Custom order; duplicate zones are allowed |
| `navimower.reset_schedule` | Explicitly clear managed-scheduler round/runtime ownership |
| `navimower.set_gate_area` | Create/update one validated exact Gate-area polygon |
| `navimower.delete_gate_area` | Delete one integration-owned Gate area by slug |
| `navimower.mark_notification_read` | Mark one merged notification row read |
| `navimower.mark_all_notifications_read` | Mark all retained local/vendor notification rows read |
| `navimower.relearn_georeference` | Clear only learned map georeference calibration and relearn from fresh samples |
| `navimower.export_raw_data` | Write an explicit sensitive unredacted development capture under `/config/navimower_diagnostics/raw/` |

### Important `navimower.mow` zone-ID rule

`zones` means **internal vendor map zone IDs**, not a displayed label/ordinal such as "Zone 2".

When current map zone IDs are available, Navimower validates explicit values before encoding/sending the vendor command. Unknown IDs are rejected in Home Assistant instead of being sent to the mower.

First-generation H-series mowers (H500/H500E/H800/H800E/H1500/H1500E/H3000/H3000E) can mow a selected subset of zones but do not support user-defined custom sequencing; the mower decides their order.

The standard Home Assistant lawn-mower entity also provides normal start/mow, pause and return-to-dock actions where supported.

## Connectivity and polling

Dense MQTT pushes are handled independently from normal private-cloud refresh so frequent live coordinates do not starve settings/schedule/coverage/cloud-state polling.

When pose degrades, Navimower keeps useful MQTT state/progress traffic instead of rebuilding the entire client solely because position is missing. Location re-subscribe attempts are rate-limited while private-cloud fallback remains available.

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
| Physical Gate intent/area safety | fresh MQTT pose/targets | fresh private-cloud evidence with conservative confirmation where supported |
| Custom Area occupancy | fresh MQTT pose | none; becomes unavailable |

Navimower does not generate synthetic/interpolated battery percentages.

## Regional routing

Private app-cloud routing is discovered from the vendor Passport region instead of hard-wired to one European host.

Observed/canonical region families include:

- Europe (`fra` / `eu`);
- Asia-Pacific (`sg` / `sea`);
- Americas (`us` / `ore`);
- mainland China (`bj`).

Official Smart Home OAuth/MQTT routing remains independent and uses official service/host information returned for that connection.

For observed US accounts, Passport may report raw region `ore`; Navimower preserves that raw value where mower-cloud authentication requires it while using the canonical Americas routing family for host selection.

Regional support remains evidence-driven and not every model/region combination is equally hardware-tested.

## Navimower Map Card

The interactive dashboard map is maintained separately:

[**vahesoo/navimower-map-card**](https://github.com/vahesoo/navimower-map-card)

Install it through HACS as a **Dashboard** custom repository.

Minimal configuration:

```yaml
type: custom:navimower-map-card
entity: lawn_mower.my_mower
```

### Version compatibility

**Navimower Map Card 0.3.5 requires Navimower integration 0.4.3 or newer.**

That remains the released stable baseline. The current 0.4.4 development line adds newer backend contracts such as georeferenced multi-mower site metadata/provider frames, phased Map API loading and exact polygon Gate-area write services used by the 0.3.6 Map Card beta line. The exact next stable pairing/release notes will be finalized only after the integration and card are reviewed together.

### Legacy Map Camera

The old built-in SVG **Legacy Map Camera** has been removed. Existing dashboards should use Navimower Map Card instead. This does not remove camera/VisionFence-related mower settings; only the old Home Assistant SVG map-camera entity was removed.

## Diagnostics

Open the Navimower config-entry menu and choose **Download diagnostics**.

Normal Download diagnostics is **cached-only and sanitized**. It does not start H5 research, make exploratory vendor requests, mark notifications read, send mower commands or invoke the raw export.

The report includes useful support context such as connectivity/routing, capabilities, polling/source freshness, MQTT navigation, map/georeference/underlay health, zone/completion/current-cycle state, Gate/Gate-area state, Navimower Schedule, errors, notifications and bounded sanitized raw structure.

Credentials/account identifiers/GPS and other sensitive fields are redacted. Google Map Tiles API keys and session tokens are not included.

See [docs/DIAGNOSTICS_PRIVACY.md](docs/DIAGNOSTICS_PRIVACY.md) for the privacy boundary and the difference between Download diagnostics and `navimower.export_raw_data`.

## Troubleshooting

### Schedule is waiting

Check Navimower schedule status, whether the window is open, selected zones have trustworthy Last completed values, native mower schedule is disabled, and whether charging/rain/night/vendor safety currently retains the task.

The scheduler deliberately prefers waiting over an unsafe new `reset=true` start when ownership is ambiguous.

### Mower position appears stale

Check MQTT connected, Live position valid, MQTT position stream, Position source and MQTT pose age. Private-cloud fallback can keep display/navigation context useful, but stale cloud coordinates are not promoted to fresh safety evidence.

### Map underlay is offset

Determine whether the mower is wrong relative to its own zones/dock or whether the whole local map group is consistently offset against the geographic imagery. See [docs/MAP_GEOREFERENCE_AND_UNDERLAYS.md](docs/MAP_GEOREFERENCE_AND_UNDERLAYS.md) before considering any manual coordinate workaround.

### A setting/entity is missing

Settings are capability-driven. A control may be absent because the mower/firmware does not support it or Navimower does not yet have enough evidence to expose the write path safely.

### Reporting a problem

Include:

1. Navimower version;
2. mower model and firmware;
3. country/region if authentication/routing may matter;
4. expected behavior;
5. actual behavior;
6. relevant Home Assistant log output;
7. a fresh **Download diagnostics** export.

For a short protocol investigation, enable Passive protocol discovery only during reproduction and disable it again afterwards.

Do not remove diagnostics redaction or publish account credentials/tokens. Do not post `navimower.export_raw_data` output publicly.

## Current limitations

- The private app-cloud protocol is undocumented and may change.
- Model/firmware capabilities vary significantly.
- Less-tested model-specific controls remain best-effort until field evidence proves them.
- Exact dense mowing history currently depends on live MQTT pose samples; missing samples are not reconstructed.
- Custom Area occupancy requires fresh MQTT pose.
- Geographic Location uses vendor private-cloud GPS rather than transforming dense MQTT X/Y.
- Geographic underlay quality depends on map/model georeference evidence; a mower without a validated transform cannot be safely placed in a combined geographic Site view.
- Destructive mower-map/boundary editing is deliberately not implemented. Integration-owned Gate-area polygons are local Navimower configuration, not arbitrary vendor map writes.
- Maintenance reset/error-recovery commands discovered only partially during protocol research are not exposed without a sufficiently proven contract.

For release-by-release development history, see [CHANGELOG.md](CHANGELOG.md) and `.github/release-notes/`.

## Project origins

The private-cloud foundation used by Navimower was created by **Roberto Gualandris** in [ilguala/navimow_pro](https://github.com/ilguala/navimow_pro). That project reverse-engineered the Navimow mobile application's private-cloud communication and provided the authentication, encrypted protocol, map handling and command foundation on which this integration builds.

Navimower extends that foundation with official Smart Home OAuth/MQTT live data, source freshness arbitration, persistent history/current-cycle rendering, per-zone completion semantics, physical navigation/gates, georeference/site/underlay support, merged notifications, integration-owned scheduling and capability-aware Home Assistant entities.

Earlier persistent-route and Gate-area work also developed from [vahesoo/NavimowHA](https://github.com/vahesoo/NavimowHA).

## Credits and licence

- [ilguala/navimow_pro](https://github.com/ilguala/navimow_pro) — private-cloud protocol foundation by Roberto Gualandris.
- [vahesoo/NavimowHA](https://github.com/vahesoo/NavimowHA) — earlier Home Assistant route/Gate-area work.
- [vahesoo/navimower-map-card](https://github.com/vahesoo/navimower-map-card) — standalone dashboard map UI.

See [NOTICE.md](NOTICE.md) and [LICENSE](LICENSE).

Navimower is distributed under the MIT License.
