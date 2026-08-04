# Multi-mower beta testing

Navimower `0.3.4-beta2` fixes account-session handling for multiple mowers and keeps the existing one-config-entry-per-mower model for field testing.

## Supported setup

Create one Navimower config entry per mower. Each entry is keyed by that mower's serial number and owns its own coordinator, MQTT bridge, entities, map/history storage, OAuth mower identifier and unload/reload lifecycle.

During setup:

1. Sign in to the private Navimow app cloud.
2. Confirm the mower to add in the mower selector. The selector is shown even when only one unconfigured mower remains.
3. Complete Smart Home OAuth and confirm that the same mower is visible there.
4. Repeat the integration setup for the next mower.

Home Assistant prevents the same serial number from being configured twice.

## Account arrangement

A single dedicated/shared private-cloud account may contain several mowers. Every Navimower entry using that account now reuses one deterministic private-cloud app/device identity, matching the vendor account-session model and preventing one mower entry from invalidating another entry's session.

Existing beta1 entries that already have different private-cloud device identities are aligned automatically on the next integration reload or Home Assistant restart. The identity is selected deterministically from the values already stored for that account; mower serial numbers, entity unique IDs, options, maps and retained history are not changed.

Recommended arrangement:

```text
Primary owner account(s) -> Navimow phone app
                         -> share Tont and Niidu to one HA account

Dedicated HA account     -> private-cloud login for both mower entries
                         -> Smart Home OAuth for every visible mower
```

Do not keep the dedicated HA private-cloud account signed into the phone app after setup, because a phone login may still replace the vendor session used by Home Assistant.

## Official OAuth and MQTT

Each mower entry currently keeps its own OAuth session and MQTT bridge, while the selected official mower is matched by serial number and stored official device ID. This has been proven to work with several mowers visible to the same Smart Home OAuth account, but broader testing is still required.

`MQTT connected` describes the broker connection. A docked mower may stop publishing continuous position packets while state and battery messages continue. In beta2, `Live position valid` no longer reports a false Disconnected state when a live pose is not expected; it becomes unknown until the mower is active again.

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

## Upgrade verification

After updating from beta1:

1. Restart Home Assistant or reload both Navimower config entries.
2. Confirm that neither mower repeatedly requests private-cloud reauthentication.
3. Confirm that both entries show `Private cloud connected`, `Smart Home OAuth connected` and `MQTT connected`.
4. Start each mower separately and verify that its `MQTT position stream` becomes live and its position source changes to MQTT.
5. Reconfigure one mower and verify that the other mower remains connected.

A reauthentication flow already created by beta1 may remain visible until it is completed or dismissed. Beta2 prevents the underlying different-device-ID conflict from being recreated.

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
- whether the mowers use the same private-cloud and Smart Home OAuth accounts;
- exact reproduction steps;
- which mower was expected to react and which mower actually reacted;
- sanitized native diagnostics for every affected mower.

Do not publish credentials, OAuth tokens, email addresses, full serial numbers or unreviewed raw logs.

## Known beta limitations

- Multi-mower support still needs broader testing across mower families, regions and shared-owner arrangements.
- The integration exposes one config entry and one MQTT bridge per mower rather than one account-level entry containing several devices.
- A pre-existing beta1 reauth notification may need to be completed or dismissed once after the upgrade.
- Vendor-side account or OAuth behaviour may differ by region or firmware.
