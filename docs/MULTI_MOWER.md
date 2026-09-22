# Multi-mower setup

Navimower supports multiple mowers while keeping the Home Assistant model simple: **one Navimower config entry per mower**.

Each entry is keyed by the mower serial number and owns its own coordinator, MQTT bridge, entities, map/history storage, official OAuth mower identifier and unload/reload lifecycle.

## Setup

For each mower:

1. Add the Navimower integration.
2. Sign in to the private Navimow app cloud.
3. If exactly one unconfigured mower remains on that account, Navimower selects it automatically. If several remain, choose the mower to add.
4. Complete Smart Home OAuth with an account that can access the same mower.
5. Repeat the integration setup for the next mower.

Home Assistant prevents the same mower serial number from being configured twice.

If a shared private-cloud account does not list a mower through the normal account response, use the serial-number fallback offered by the setup flow.

## Recommended account arrangement

A single dedicated/shared private-cloud account may contain several mowers. Navimower entries using that account reuse one deterministic private-cloud app/device identity, matching the vendor account-session model and preventing one mower entry from invalidating another entry's session.

```text
Primary owner account(s) -> Navimow phone app
                         -> share mower(s) to one HA account

Dedicated HA account     -> private-cloud login for each mower entry
                         -> Smart Home OAuth for every visible mower
```

Do not normally keep the dedicated private-cloud account signed into the phone app after setup, because a phone login may replace the vendor private-cloud session used by Home Assistant.

## Official OAuth and MQTT

Each mower entry has its own official mower identity and MQTT bridge. Mowers visible to the same Smart Home OAuth account are matched to the config entry by mower identity rather than by setup order.

`MQTT connected` describes the broker connection. A docked mower may stop publishing continuous position packets while state and battery messages continue, so live-position health is exposed separately from broker connectivity.

## Multi-mower site metadata

Navimower's authenticated Site API prepares the backend information needed to render nearby mower maps together without making the browser discover or align devices itself.

For an anchor mower, the integration includes only mower maps that:

- have a validated usable georeference; and
- are within 500 metres of the anchor mower.

Grouping is anchor-relative rather than transitive. A mower that is close to another member but outside the anchor rule is not pulled into the site indirectly.

For every member the integration can publish:

- mower/device/entity identifiers already resolved server-side;
- direct Map, Sessions, Prepared History manifest/resource, legacy Session-render and Site API paths;
- a local-map -> common-site transform and combined site bounds;
- stable member ordering based on map footprint (west to east), not the mower's changing live position;
- provider-ready geographic frame metadata so one underlay change moves the whole site consistently.

The site transform affects presentation only. Every mower keeps its own mower-local X/Y geometry, history, Gate areas, Custom Areas and command target.

Prepared History resources stay mower-local and content-addressed. A Multi-capable Map Card can therefore share one browser resource cache across Single/Multi views while still applying each member's existing site transform at render time.

See [MAP_GEOREFERENCE_AND_UNDERLAYS.md](MAP_GEOREFERENCE_AND_UNDERLAYS.md) for the georeference/provider-frame model.

## Map underlays with several mowers

The optional Google Map Tiles API key is scoped to the private-cloud account. Mower entries that use the same account therefore share one configured key rather than requiring one key per mower.

The API key and Google session token stay on the Home Assistant backend. Multi-mower frontend metadata exposes only provider availability and authenticated proxy/API paths.

Estonia orthophoto/hybrid availability and provider-frame selection are also calculated integration-side so the frontend does not need mower-model-specific alignment rules.

## Actions with multiple mowers

Entity actions such as Mow, Pause and Dock already target the selected lawn-mower entity.

For Navimower domain actions, provide the mower's Home Assistant `device_id` when more than one Navimower mower is configured. For example:

```yaml
action: navimower.mow
data:
  device_id: YOUR_MOWER_DEVICE_ID
  zones:
    - 13
    - 24
  reset: true
```

The values in `zones` are **internal vendor map zone IDs**, not the displayed name/number such as "Zone 2". When current map zones are available, Navimower validates explicit IDs before sending a mowing command and rejects unknown IDs. First-generation H-series mowers can still restrict mowing to the selected zones, but the mower chooses their order rather than following a user-defined sequence.

```yaml
action: navimower.set_schedule
data:
  device_id: YOUR_MOWER_DEVICE_ID
  day: monday
  enabled: true
  periods:
    - start: "09:00"
      end: "11:00"
      zones:
        - 13
```

The same `device_id` scoping applies to integration-owned actions such as `navimower.resume`, `navimower.set_schedule_queue`, `navimower.reset_schedule`, `navimower.set_gate_area`, `navimower.delete_gate_area`, `navimower.relearn_georeference` and the notification actions.

Use Home Assistant's native **Download diagnostics** action on each Navimower config entry when reporting a problem. Navimower no longer exposes the old development `export_diagnostics` service. The separate `navimower.export_raw_data` action is an intentionally unredacted development capture and must not be posted publicly.

## What to verify

For each mower, verify independently that:

- the correct mower name, serial-backed device and entities are created;
- map geometry, zone names, dock and live position belong to the correct mower;
- battery, state, progress and session area update only from that mower;
- Mow, Pause and Dock affect only the selected mower;
- Navimower domain actions target the intended `device_id`;
- ordered-zone behavior follows that mower family's capabilities;
- schedule writes and Navimower Schedule state remain scoped to the selected mower;
- map/site geometry and retained history belong to the correct mower;
- reloading or removing one config entry does not interrupt the other mower;
- OAuth or private-cloud reauthentication can be completed for one mower without changing another entry.

## Reporting multi-mower issues

Open a GitHub issue and include:

- mower models and firmware versions;
- Home Assistant version;
- whether the mowers use the same private-cloud and Smart Home OAuth accounts;
- exact reproduction steps;
- which mower was expected to react and which mower actually reacted;
- sanitized native Download diagnostics for every affected mower.

Do not publish credentials, OAuth tokens, email addresses, full serial numbers, geographic coordinates from raw captures or unreviewed raw logs.

## Current limitations

- The integration intentionally exposes one config entry and one MQTT bridge per mower rather than one account-level entry containing several mower devices.
- A combined site requires usable georeference evidence for each included map; a mower without a validated transform remains independently usable but cannot be safely placed in the common geographic site view.
- Vendor-side account, sharing or OAuth behavior may differ by region, firmware or mower family.
