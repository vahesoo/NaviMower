# Multi-mower beta testing

Navimower `0.3.4-beta1` promotes the existing multi-mower architecture to a public beta for broader field testing.

## Supported setup

Create one Navimower config entry per mower. Each entry is keyed by that mower's serial number and owns its own coordinator, MQTT bridge, entities, map/history storage, OAuth mower identifier and unload/reload lifecycle.

During setup:

1. Sign in to the private Navimow app cloud.
2. Select the mower to add when the account exposes more than one mower.
3. Complete Smart Home OAuth and confirm that the same mower is visible there.
4. Repeat the integration setup for the next mower.

Home Assistant prevents the same serial number from being configured twice.

## Account requirement

Use a separate dedicated/shared private-cloud account for each simultaneously configured mower.

Reusing one private-cloud account across parallel config entries remains unsupported because the vendor login/session can invalidate or replace another active private session. Smart Home OAuth devices are matched to the selected serial number, but this does not remove the private-cloud session limitation.

## Services and actions

When more than one mower is configured, service calls must include the target mower's Home Assistant `device_id`.

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

```yaml
action: navimower.export_diagnostics
data:
  device_id: YOUR_MOWER_DEVICE_ID
  include_compressed_map: true
```

The native lawn-mower entity actions already target the selected entity/device and do not need a separate service selector.

## What to verify

For each mower, verify independently that:

- the correct mower name, serial-backed device and entities are created;
- map geometry, zone names, dock and live X/Y belong to the correct mower;
- battery, state, progress and session area update only from that mower;
- Mow, Pause and Dock affect only the selected mower;
- ordered-zone mowing and schedule writes affect only the selected mower;
- Map Card shows the correct map, current route and retained daily trails;
- reloading or removing one config entry does not interrupt the other mower;
- OAuth or private-cloud reauthentication can be completed for one mower without changing the other entry.

## Reporting beta issues

Open a GitHub issue and include:

- mower models and firmware versions;
- Home Assistant version;
- whether each mower uses a separate private-cloud account;
- exact reproduction steps;
- which mower was expected to react and which mower actually reacted;
- sanitized native diagnostics for every affected mower.

Do not publish credentials, OAuth tokens, email addresses, full serial numbers or unreviewed raw logs.

## Known beta limitations

- Multi-mower support still needs broader testing across mower families, regions and shared-owner arrangements.
- One private-cloud account must not be reused by parallel entries.
- The integration currently exposes one config entry per mower rather than one account-level entry containing multiple devices.
- Vendor-side account or OAuth behaviour may differ by region or firmware.
