# Navimower

Home Assistant integration for Segway Navimow robot mowers.

Navimower is an **unofficial community interoperability project**. It combines a Navimow account connection with the official Smart Home OAuth/MQTT connection in one Home Assistant config entry and provides mower controls, live telemetry, maps/zones, persistent mowing history, notifications, physical-gate helpers, geographic map alignment/underlays and an optional Home Assistant-owned zone scheduler.

Navimower does **not** require the older NavimowHA integration.

> [!WARNING]
> Navimower is not affiliated with, endorsed by, or supported by Segway, Ninebot, Navimow or Willand. Product and service behavior can change between models, firmware versions and regions.
>
> A robotic mower is a moving machine with a cutting blade. Test mower commands, gate automations and other physical automations in a safe environment before relying on them unattended.

## Highlights

- **Two independent connections** in one config entry:
  - Navimow account/cloud data for map geometry, settings, native schedules, notifications, maintenance and stable mower state;
  - official Smart Home OAuth + MQTT for dense live position, heading, battery and mower events.
- **Persistent current-cycle mowing state and History** retained per zone through normal pause/resume, charging, task changes, temporary cloud gaps, integration reloads and Home Assistant restarts.
- **Per-zone state** including Coverage, Area, Mowed area, Last mowed and conservative/monotonic Last completed timestamps.
- **Backend-prepared map rendering** for current-cycle, retained History and semantic live cutting/travel routes, with compatibility fallbacks for older frontends.
- **Navimower Schedule**, an integration-owned one-zone-at-a-time scheduler with Automatic or positional Custom order, repeated rounds, retained-task ownership and reversible pause/resume.
- **Custom Areas** imported from temporary Navimow Off-limit polygons and stored locally.
- **Physical-gate support** with zone-pair travel intent and exact local-X/Y polygon Gate areas.
- **Georeferenced Map/Site API** for geographic underlays and nearby multi-mower site transforms.
- **Map underlay backend** for Estonia orthophoto/hybrid and optional Google Satellite through an authenticated Home Assistant proxy.
- **Merged notifications** combining Navimow Device notifications with confirmed local Navimower context.
- **Mowing pause reason** status with conservative low-battery confirmation.
- **Model-aware settings** for mowing, weather, battery, lights, safety, navigation and supported family-specific features.
- **Native GPS Location device tracker** when the mower account reports valid geographic coordinates.
- **Curated privacy-safe Download diagnostics** for normal support, without complete vendor raw payloads or exact local property geometry.

## Installation

### HACS custom repository

1. Open **HACS -> Integrations -> three-dot menu -> Custom repositories**.
2. Add `https://github.com/vahesoo/NaviMower` as category **Integration**.
3. Install **Navimower**.
4. Restart Home Assistant.
5. Open **Settings -> Devices & services -> Add integration -> Navimower**.

### Manual installation

Copy `custom_components/navimower` to `/config/custom_components/navimower` and restart Home Assistant.

## Recommended account arrangement

A dedicated shared Navimow account is recommended for the account/cloud connection.

```text
Owner account(s) -> Navimow phone app and/or official Smart Home OAuth
Shared account   -> Navimower account login
```

Share the mower from its primary owner account to the dedicated account before adding Navimower. Keeping the Home Assistant account separate also reduces the chance that a new phone-app session interferes with the Home Assistant session.

The account password is used only for login/reauthentication and is not stored by Navimower.

The Smart Home OAuth account may be different from the account used for the other Navimower connection as long as both accounts can access the same mower.

> [!IMPORTANT]
> Multiple mower config entries may use the **same dedicated shared account**. Add each mower as its own Navimower config entry. Entries keep separate devices, entities, maps and histories while sharing the account-level session identity needed for multi-mower use.

See [docs/MULTI_MOWER.md](docs/MULTI_MOWER.md) for multi-mower setup and Site API behavior.

## Initial setup flow

When adding Navimower:

1. Enter the email and password of the dedicated shared account.
2. Navimower reads the mowers available to that account.
   - If exactly **one unconfigured mower** is found, it is selected automatically.
   - If **two or more unconfigured mowers** are found, choose which mower to add.
   - If no mower is returned for a shared account, use the serial-number fallback shown by the setup flow.
3. Continue to official **Smart Home OAuth**.
4. Sign in with an account that can access the same mower.
5. Navimower validates the OAuth mower and stores both connection branches in the same config entry.

The two branches degrade independently. A temporary problem with one connection does not necessarily remove functionality already available from the other.

## Configure Navimower after setup

Open:

**Settings -> Devices & services -> Navimower -> Configure**

The current menu contains:

- **General and trail history**
- **Navimower Schedule**
- **Gates**
- **Custom areas**
- **Gate areas**
- **Map underlay**

General options include completed mowing-route retention (3, 7, 14 or 30 days, or unlimited) and whether return-to-dock route is retained.

## Map and zones

Navimower reads the mower map and exposes:

- real zone names and internal vendor map zone IDs;
- zone geometry and area;
- Off-limit areas;
- VF-off areas;
- mapped Channels;
- charging station/dock;
- supported map metadata;
- global/per-zone cutting-height context when reported reliably.

Map geometry is cached through temporary cloud outages. A map revision change invalidates stale decoded geometry and requests a fresh map instead of waiting for longer idle refresh intervals.

### Per-zone entities

For each discovered zone Navimower can expose:

- **Coverage**
- **Area**
- **Mowed area**
- **Last mowed**
- **Last completed**

Zone entity unique IDs are based on the mower's internal zone ID. Renaming a zone can therefore update its display name without creating a replacement entity.

Merging, splitting or recreating zones may create new IDs. Navimower removes stale Home Assistant zone-sensor registry rows only after a fresh versioned map confirms those old IDs no longer exist. Historical sessions are retained.

### Last completed semantics

**Last completed** is intentionally conservative and monotonic.

A zone is confirmed complete from fresh current-cycle per-zone coverage reaching 100%. Whole-task percentage, route progress, returning to dock or a stale historical 100% is not enough on its own. Once a newer trustworthy completion timestamp is known, later startup/cloud repair must not move the sensor backwards to an older completion.

A confirmed 100% zone is also protected from a later lower coverage sample when that zone's geometry is unchanged and no newer mowing cycle/reset has been observed. The lower source value remains available in diagnostics while Home Assistant keeps the completed zone at 100%.

The same completion model is authoritative for Navimower Schedule.

## Task, map and position telemetry

Navimower keeps counters with different meanings separate instead of turning every percentage into one generic progress value.

- **Task progress** — selected whole-task/work progress.
- **Task mowed area** — current task-area resolver.
- **Map coverage / Map mowed area** — current per-zone coverage snapshot.
- **Active-zone progress** — progress attributed to the currently owned work zone when the evidence is sufficient.

### Physical zone, target zone and planned zones

Navimower deliberately separates:

- the mapped polygon the mower is physically inside;
- the **Target zone**, meaning the immediate automation-safe work target;
- **Planned zones**, meaning the full selected multi-zone task;
- whole-task progress;
- active-zone progress.

Crossing another mapped zone therefore does not transfer task completion/ownership to the polygon under the wheels.

When confirmed docked/charging, Current physical zone is shown as virtual **Dock** instead of retaining a stale lawn-zone position.

## Live position and navigation fallback

Fresh official MQTT local X/Y is the preferred physical position source.

When MQTT pose is temporarily unavailable, a recent cloud local position can keep physical-zone and Channel/Gate-area display useful. Stale cloud coordinates are display-only and are not promoted to fresh gate-safety evidence. Risky cloud-only Gate-area OFF transitions require repeated distinct fresh reports.

Target freshness is tracked independently from pose freshness. A fresh mower position does not make an old cached work target fresh.

Navimower exposes separate health concepts including Private cloud connected, OAuth connected, MQTT connected, Live position valid, Position source, MQTT position stream and MQTT pose age.

## GPS Location device tracker

When the Navimow account reports valid latitude/longitude, Navimower creates a native Home Assistant **Location** `device_tracker`.

Dense MQTT X/Y remains mower-local Cartesian map data and is not converted into latitude/longitude for this entity. Geographic coordinates are redacted from normal Download diagnostics.

## Map georeference and underlays

Navimower can align mower-local maps with geographic providers while keeping the mower's own X/Y geometry authoritative.

A map revision is the normal automatic recalibration boundary; ordinary GPS drift must not continuously move an already validated map. Provider-ready frames allow different underlays to use the correct geographic presentation without adding mower-model offsets in the browser.

Open:

**Settings -> Devices & services -> Navimower -> Configure -> Map underlay**

Available backend options include:

- Estonia orthophoto for Estonian sites;
- Estonia hybrid for Estonian sites;
- optional Google Satellite using a Google Map Tiles API key.

A Google key is account-scoped across mower entries using the same account. The key and Google session token remain on the Home Assistant backend; authenticated proxy endpoints serve tiles/viewport metadata to the frontend.

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

Navimower prepares completed current-cycle mowing geometry on the Home Assistant side. The phased Map API lets a compatible frontend load the base map separately from the compact current-cycle artifact.

Completed History sessions are also prepared on the backend. Retained completed sessions are prewarmed sequentially into immutable, content-addressed SVG-ready resources. A ready-only History manifest exposes those resource descriptors, while the exact timestamped session Stores remain the source of truth.

Active sessions stay on Prepared Live + short-tail transport. In beta27 the backend also prepares an **opt-in semantic live route** that separates confirmed blade-on cutting edges from travel/transit edges. A cutting edge is accepted only when both samples are blade-on and both belong to the same physical mowing zone; zone-boundary crossings and missing-zone movement remain travel. This fixes a general route-classification issue rather than an X3-specific one.

The existing beta15 all-movement Prepared Live resource remains unchanged and does not download the new semantic geometry. The legacy session-render endpoint also remains available while compatibility cleanup is staged.

### Trail retention

Completed history can be retained for 3, 7, 14 or 30 days, or unlimited. Shorter retention reduces Home Assistant storage and Map API payload size.

## Custom Areas

Custom Areas are Navimower-owned virtual polygons stored independently from normal mowing-zone IDs. They are useful for a gate approach, corridor, driveway, work-area boundary or other local map presence area.

### Create a Custom Area

1. Open **Configure -> Custom areas -> Add custom area**. Navimower refreshes the map and records the existing Off-limit geometry as the baseline.
2. In the Navimow app, create **exactly one** temporary Off-limit polygon in the desired shape and save the map.
3. Return to Home Assistant and continue the flow.
4. Navimower refreshes the map, detects the newly added polygon by geometry, then asks for a name.
5. Save the Custom Area and remove the temporary Off-limit polygon from the Navimow app if no longer needed.

The imported Custom Area remains local to Navimower. Polygon matching does not depend on list index, first vertex or clockwise/counter-clockwise order.

### Custom Area occupancy

Each Custom Area creates a binary sensor:

- **On** while fresh official MQTT X/Y is inside/on the polygon;
- **Off** while the fresh pose is outside;
- **Unavailable** when live MQTT pose is missing/stale.

Custom Area occupancy intentionally does **not** use cloud fallback. Gate areas have different, more conservative fallback semantics because they are designed for physical-gate workflows.

## Gates and Gate areas

### Zone-pair Gates

A **Gate** links two mapped zones and exposes a `Gate required` binary sensor. It may be bidirectional or one-way. The optional close delay keeps intent asserted briefly after arrival.

Gate intent uses freshness-aware target/position arbitration and fails safe when position/target evidence is stale or ambiguous.

Configure from **Configure -> Gates**.

### Gate areas

A **Gate area** is an integration-owned local-X/Y presence area. Current Gate areas support exact polygons; legacy rectangles remain compatible. When a polygon exists it is authoritative and min/max bounds are retained only for backward compatibility.

Gate areas can be created/edited through:

- a compatible Navimower Map Card visual editor;
- **Configure -> Gate areas** as a manual/fallback editor;
- `navimower.set_gate_area` / `navimower.delete_gate_area` for integration/frontend use.

Polygon writes are validated (3–64 unique points, non-self-intersecting, non-zero area) before being persisted.

A complete physical-gate guide, including the **one automation run owns the gate cycle** pattern, is in [docs/GATE_AUTOMATION.md](docs/GATE_AUTOMATION.md).

## State, Problem, Error and mowing pause reason

Navimower keeps mower activity, safety/fault state and interruption cause separate.

- **Status** — normalized mower activity/state.
- **Problem** — whether a problem/safety condition is active.
- **Error** — current mower fault detail when one exists.
- **Mowing pause reason** — retained mowing interruption classification.

`Mowing pause reason` may expose states such as:

- `none`;
- `low_battery_pending` — inferred from transition/battery context, **not automation-safe**;
- `low_battery` — requires a time-matched Navimow low-battery notification and is the conservative automation-safe state;
- `night`;
- `manual_dock`;
- `unknown` or another attributed interruption reason.

Generic Returning/Docked states remain cause-agnostic.

## Latest notification

Navimower exposes one merged **Latest notification** timeline containing Navimow Device rows plus confirmed Navimower/Home Assistant context. Local rows are created only when the integration has enough evidence to attribute activity; otherwise wording remains neutral.

Actions:

- `navimower.mark_notification_read`
- `navimower.mark_all_notifications_read`

## Target and planned zones

Navimower separates the mower's **immediate target** from the full selected task:

- **Target zone** is one automation-safe zone: the fresh vendor work target, or the first freshly commanded zone while the vendor target is still catching up. Multi-zone selection alone is not guessed into a target.
- **Planned zones** is the full active mowing-task selection reported by the command/vendor task state.

This makes state triggers such as `sensor.<mower>_target_zone -> Yard` fire when Yard is actually the immediate target, rather than as soon as Yard merely appears later in a multi-zone task. The richer internal navigation target used by Gate arbitration remains separate.

## Mower settings and controls

Available controls depend on mower model, firmware and reported capability. Navimower prefers hiding a control over guessing an unsupported command.

Depending on family/firmware, controls can include:

- native Mowing schedule enabled;
- Mowing cycle;
- Night mowing;
- Return-to-dock battery level;
- Charging limit;
- electronic/global cutting height where supported;
- Rain detection, Rain sensor, Rain forecast, Rain delay and related weather controls;
- Frost, snow, wind and high-temperature controls;
- Do not disturb, sound and lighting controls;
- Child lock, Lift alarm and supported anti-theft controls;
- obstacle/animal protection;
- Terrain adapt, Edge sense and TCS/traction;
- supported model-specific work/positioning controls.

Unknown cutting-height encodings are not converted into invented millimetre values.

Newer/less-tested mower-family controls are provisioned from positive capability evidence rather than model-name guesses or dormant shared fields. Not every reported field is remotely writable and not every related model exposes the same subset; uncertain controls remain hidden until their behavior is sufficiently established.

LiDAR terrain capability is **resource-driven**. A mower is advertised to frontends as supporting the LiDAR terrain/elevation overlay after Navimower has obtained and validated the vendor `type=2` terrain package. Model-family knowledge may reduce discovery latency, but it is not the authority for whether the Map Card should expose LiDAR controls. Unknown/future mower models can therefore gain the same frontend capability automatically when they expose the same valid vendor resource.

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

The active round keeps an immutable `round_queue` snapshot. Queue edits made after a round starts are staged for the next round instead of changing slot identity mid-round.

### Start/completion ownership

Successful command transport is not proof that mowing began. Navimower waits for mower/task state before claiming scheduler ownership/opening the local cycle.

Retained-task Resume is also fail-closed: stale scheduler state must not adopt an unrelated native/manual/multi-zone task. Different-zone/multi-zone/conflicting evidence invalidates old ownership rather than risking a wrong Resume.

Completion uses the same conservative per-zone Last completed semantics described above.

### Repeated rounds and interruptions

Both scheduler modes can begin another round after all selected zones/queue slots complete. Time window remains the hard outer boundary when configured.

Navimower does not disable mower-owned safety/weather behavior. For a retained low-battery task, Navimower prefers to let the mower resume itself after charging and only considers a safe Resume/`reset=false` fallback after the configured charging limit and grace period. Charging recovery never uses `reset=true`.

Turning the **Navimower schedule** switch Off pauses scheduler ownership non-destructively. **Reset schedule progress** / `navimower.reset_schedule` deliberately clears current round/runtime ownership without sending a mower command and is refused while the mower is mowing or returning.

After Schedule is configured, the **Navimower schedule status** sensor provides mode/window/order, queue, completed/active/upcoming slots, interruption/resume and error/suspension context.

## Home Assistant actions

| Action | Purpose |
| --- | --- |
| `navimower.mow` | Start selected internal map zone IDs now; choose new/reset cycle or `reset=false` continuation |
| `navimower.continue_task` | Continue a confirmed interrupted task using the backend-selected ordered-run or vendor Resume strategy |
| `navimower.continue_last_ordered_run` | Continue the latest retained ordered run by sending only unfinished zones, in their original order, with `reset=false` |
| `navimower.resume` | Send the low-level vendor Resume command for a retained task |
| `navimower.set_schedule` | Replace one weekday of the mower's native weekly schedule |
| `navimower.set_schedule_queue` | Persist positional Navimower Schedule Custom order; duplicate zones are allowed |
| `navimower.reset_schedule` | Explicitly clear managed-scheduler round/runtime ownership |
| `navimower.set_gate_area` | Create/update one validated exact Gate-area polygon |
| `navimower.delete_gate_area` | Delete one integration-owned Gate area by slug |
| `navimower.mark_notification_read` | Mark one merged notification row read |
| `navimower.mark_all_notifications_read` | Mark all retained notification rows read |
| `navimower.relearn_georeference` | Clear only learned map georeference calibration and relearn from fresh samples |

### Smart task continuation

`navimower.continue_task` is the preferred UI-facing continuation action. Navimower decides whether the current task can be resumed and which mechanism is safest. A proven unfinished ordered run uses the retained ordered-zone continuation path; otherwise a confirmed vendor-retained task uses the dedicated vendor Resume command. Returning-to-dock and resumable error states are allowed when task evidence remains, while map editing, active mowing and confirmed completed tasks are not offered as resumable.

The same backend decision is exposed on **Task progress** as `resume_available`, `resume_strategy`, `resume_reason` and `resume_evidence`, and through the Map API frontend metadata. This lets dashboard cards render Resume without duplicating mower-state policy in JavaScript.

`navimower.continue_task` is the preferred UI-facing continuation action. The low-level `navimower.resume` and `navimower.continue_last_ordered_run` actions remain available for automations and backward compatibility.

### Continue the last ordered run

`navimower.continue_last_ordered_run` is different from the vendor Resume command.
Navimower remembers the latest successfully sent ordered zone list and tracks which
of those zones later receive a confirmed 100% completion. Dock/Home does not erase
this retained list.

When the action is called, already completed zones are removed and only the
unfinished zones are sent again in their original order with `reset=false`.
This keeps the mower's retained per-zone progress while avoiding a second pass
over zones that already completed during the ordered run. The tracker is stored
with the integration state and survives Home Assistant restarts.

Starting another Mow command supersedes the retained ordered run; Resume and
Dock/Home do not.

### Important `navimower.mow` zone-ID rule

`zones` means **internal vendor map zone IDs**, not a displayed label/ordinal such as "Zone 2".

When current map zone IDs are available, Navimower validates explicit values before sending the command. Unknown IDs are rejected in Home Assistant instead of being sent to the mower.

First-generation H-series mowers can mow a selected subset of zones but do not support user-defined custom sequencing; the mower decides their order.

## Connectivity and polling

Dense MQTT pushes are handled independently from normal account/cloud refresh so frequent live coordinates do not starve settings/schedule/coverage/cloud-state polling.

When pose degrades, Navimower keeps useful MQTT state/progress traffic while cloud fallback remains available.

| Data | Preferred source | Fallback |
| --- | --- | --- |
| Local X/Y and heading | fresh official MQTT | recent cloud position, then retained display context |
| Live activity | official MQTT | cloud state |
| Battery while active | fresh official MQTT battery | cloud SOC, then last-known |
| Battery while docked/charging | cloud SOC | fresh MQTT battery, then last-known |
| Task progress | fresh task progress | zone-model / retained fallback |
| Task mowed area | current task-area resolver | calculated/zone fallback where needed |
| Map coverage / mowed area | current per-zone coverage | retained last-known zone context |
| Map, settings and native schedule | Navimow account/cloud | persisted/local cache where supported |
| Physical Gate intent/area safety | fresh MQTT pose/targets | fresh cloud evidence with conservative confirmation where supported |
| Custom Area occupancy | fresh MQTT pose | none; becomes unavailable |

Navimower does not generate synthetic/interpolated battery percentages.

## Regional support

Navimower selects the appropriate account service family from the account's reported region. Europe, Asia-Pacific, the Americas and mainland China are supported by the routing layer, while official Smart Home OAuth/MQTT routing remains independent.

Regional support is capability/evidence-driven and not every model/region combination has equal field coverage.

## Navimower Map Card

The interactive dashboard map is maintained separately:

[**vahesoo/navimower-map-card**](https://github.com/vahesoo/navimower-map-card)

Install it through HACS as a **Dashboard** custom repository.

Minimal configuration:

```yaml
type: custom:navimower-map-card
entity: lawn_mower.my_mower
```

**Navimower Map Card 0.3.5 requires Navimower integration 0.4.3 or newer.** The current 0.4.4 / 0.3.6 beta lines add multi-mower Site metadata, phased Map API loading and polygon Gate-area editing.

The old built-in SVG **Legacy Map Camera** has been removed. Existing dashboards should use Navimower Map Card instead.

## Diagnostics

Open the Navimower config-entry menu and choose **Download diagnostics**.

Normal Download diagnostics is **cached-only and sanitized**. Downloading a report does not send mower commands or make additional service requests.

The report includes useful support context such as connectivity/routing, capabilities, polling/source freshness, MQTT navigation, map/georeference/underlay health, zone/completion/current-cycle state, Gate/Gate-area state, Navimower Schedule, errors, notifications and bounded sanitized raw structure.

Credentials, account/device identifiers, exact geographic coordinates and other sensitive fields are redacted. Google Map Tiles API keys and session tokens are not included.

See [docs/DIAGNOSTICS_PRIVACY.md](docs/DIAGNOSTICS_PRIVACY.md) before sharing diagnostics publicly.

## Troubleshooting

### Schedule is waiting

Check Navimower schedule status, whether the window is open, selected zones have trustworthy Last completed values, native mower schedule is disabled, and whether charging/rain/night/safety behavior currently retains the task.

### Mower position appears stale

Check MQTT connected, Live position valid, MQTT position stream, Position source and MQTT pose age. Cloud fallback can keep display/navigation context useful, but stale cloud coordinates are not promoted to fresh safety evidence.

### Map underlay is offset

Determine whether the mower is wrong relative to its own zones/dock or whether the whole local map group is consistently offset against the geographic imagery. See [docs/MAP_GEOREFERENCE_AND_UNDERLAYS.md](docs/MAP_GEOREFERENCE_AND_UNDERLAYS.md) before considering a manual coordinate workaround.

### A setting/entity is missing

Settings are capability-driven. A control may be absent because the mower/firmware does not support it or Navimower does not yet have enough evidence to expose it safely.

### Reporting a problem

Include:

1. Navimower version;
2. mower model and firmware;
3. country/region if authentication/routing may matter;
4. expected behavior;
5. actual behavior;
6. relevant Home Assistant log output;
7. a fresh **Download diagnostics** export.

Review the downloaded JSON before posting it publicly and never publish account credentials or tokens.

## Current limitations

- Product/service behavior can change with app, cloud and firmware updates.
- Model/firmware capabilities vary significantly.
- Less-tested model-specific controls remain best-effort until their behavior is sufficiently established.
- Exact dense mowing history depends on live MQTT pose samples; missing samples are not reconstructed.
- Custom Area occupancy requires fresh MQTT pose.
- Geographic Location uses cloud GPS rather than transforming dense MQTT X/Y.
- Geographic underlay quality depends on map/model georeference evidence; a mower without a validated transform cannot be safely placed in a combined geographic Site view.
- Destructive mower-map/boundary editing is deliberately not implemented. Integration-owned Gate-area polygons are local Navimower configuration, not arbitrary mower-map writes.
- Uncertain maintenance/error-recovery controls are not exposed until their behavior is sufficiently established.

For release-by-release development history, see [CHANGELOG.md](CHANGELOG.md) and GitHub Releases.

## Credits and licence

Navimower is an independent interoperability project and includes/adapts MIT-licensed work from:

- [ilguala/navimow_pro](https://github.com/ilguala/navimow_pro) by Roberto Gualandris;
- [vahesoo/NavimowHA](https://github.com/vahesoo/NavimowHA);
- [vahesoo/navimower-map-card](https://github.com/vahesoo/navimower-map-card) — standalone dashboard map UI.

See [NOTICE.md](NOTICE.md) and [LICENSE](LICENSE).

Navimower is distributed under the MIT License.


## Backend map snapshot images

Each mower exposes **Map snapshot** plus an optional disabled-by-default **Map snapshot dark** image. Only enabled variants are rendered. Zone labels use the integration-installed **Noto Sans** font, so international names render independently of fonts available in the Home Assistant host/container.
