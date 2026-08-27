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

Use Home Assistant's native **Download diagnostics** action on each Navimower config entry when reporting a problem. Navimower no longer exposes the old development `export_diagnostics` service.

## What to verify

For each mower, verify independently that:

- the correct mower name, serial-backed device and entities are created;
- map geometry, zone names, dock and live position belong to the correct mower;
- battery, state, progress and session area update only from that mower;
- Mow, Pause and Dock affect only the selected mower;
- ordered-zone mowing and schedule writes affect only the selected mower;
- Navimower Map Card shows the correct map and retained history;
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

Do not publish credentials, OAuth tokens, email addresses, full serial numbers or unreviewed raw logs.

## Current limitations

- The integration intentionally exposes one config entry and one MQTT bridge per mower rather than one account-level entry containing several mower devices.
- Vendor-side account, sharing or OAuth behavior may differ by region, firmware or mower family.
