# Gate automation example

Navimower can expose two complementary signals for a physical gate:

- **Gate required** — Navimower has determined that the mower needs to cross the configured zone-pair Gate.
- **Gate area** — the mower is physically inside the configured local gate passage area.

Using both signals makes a useful interlock: do not stop the mower merely because a gate may be needed later, and do not open the gate merely because the mower happens to be near it. The automation below starts only when **both** signals are On.

The example is based on a real Home Assistant automation used with Navimower. Replace the example entity IDs with your own mower, Gate required, Gate area and gate-cover entities.

## Tested interlock pattern

```yaml
alias: Navimow - gate interlock
description: Pause mower at sliding gate until gate is fully open
triggers:
  - trigger: state
    entity_id:
      - binary_sensor.my_mower_gate_required
      - binary_sensor.my_mower_sliding_gate_area
    to: "on"
conditions:
  - condition: state
    entity_id: binary_sensor.my_mower_gate_required
    state: "on"
  - condition: state
    entity_id: binary_sensor.my_mower_sliding_gate_area
    state: "on"
actions:
  - variables:
      mower_state_before_pause: "{{ states('lawn_mower.my_mower') }}"
      mower_was_returning: |
        {{ mower_state_before_pause in ['returning', 'docking', 'docked'] }}

  - action: lawn_mower.pause
    target:
      entity_id: lawn_mower.my_mower

  - choose:
      - conditions:
          - condition: template
            value_template: |
              {{ states('cover.my_gate') not in ['open', 'opening'] }}
        sequence:
          - action: cover.open_cover
            target:
              entity_id: cover.my_gate

  - wait_template: |
      {{ is_state('cover.my_gate', 'open') }}
    timeout: "00:00:20"
    continue_on_timeout: false

  - delay:
      seconds: 2

  - choose:
      - conditions:
          - condition: template
            value_template: "{{ mower_was_returning }}"
        sequence:
          - action: lawn_mower.dock
            target:
              entity_id: lawn_mower.my_mower
    default:
      - wait_template: |
          {{ state_attr('lawn_mower.my_mower', 'state_code') == '0211' }}
        timeout: "00:00:20"
        continue_on_timeout: false
      - action: lawn_mower.start_mowing
        target:
          entity_id: lawn_mower.my_mower

  - wait_template: |
      {{ is_state('binary_sensor.my_mower_sliding_gate_area', 'off') }}
    timeout: "00:01:00"
    continue_on_timeout: true

  - delay:
      seconds: 3

mode: single
```

## Why the automation is structured this way

Both binary sensors are triggers because their order can vary slightly. The two state conditions then make the actual entry condition deterministic: the mower must both **require the Gate** and be **inside the Gate area**.

The mower is paused before opening the physical gate. This prevents the mower from reaching a still-moving gate while the gate controller is opening it. The automation then waits for the `cover` entity to report fully `open` and adds a short two-second settling delay.

The mower state is captured before the pause:

- if it was returning/docking, the automation sends `lawn_mower.dock` after the gate is open;
- otherwise it waits until Navimow confirms the paused private state code `0211`, then sends `lawn_mower.start_mowing`.

Waiting for `0211` avoids racing a resume command against the vendor pause transition.

Finally the automation waits for the Gate area to turn Off, meaning the mower has left the configured physical passage, and keeps a short additional safety delay. `mode: single` prevents repeated On transitions from starting overlapping copies of the interlock.

## Closing the physical gate

The tested example above intentionally does **not** issue `cover.close_cover`; the gate controller used with it handles closing separately.

If your gate does not have its own safe auto-close logic, do not close it immediately after the mower resumes. Add closing only after Navimower no longer reports the mower in the Gate area **and** `Gate required` has cleared. For example, after the final delay:

```yaml
  - wait_template: |
      {{ is_state('binary_sensor.my_mower_sliding_gate_area', 'off')
         and is_state('binary_sensor.my_mower_gate_required', 'off') }}
    timeout: "00:01:30"
    continue_on_timeout: false

  - delay:
      seconds: 3

  - action: cover.close_cover
    target:
      entity_id: cover.my_gate
```

Use a real `cover` state or dedicated gate-open/closed sensor whenever possible. A fixed delay alone is a weaker safety signal because gate travel time can vary.

## Before using the example

Confirm that:

1. the Navimower **Gate** connects the correct two mowing zones and its direction matches the intended travel;
2. the **Gate area** covers the actual physical passage closely enough to become On only near/inside the gate;
3. the gate `cover` reports `open` only when the passage is genuinely clear;
4. Pause, Start mowing and Dock work correctly for your mower before adding physical gate movement;
5. the automation is tested while supervised before relying on unattended operation.

Gate timing and physical safety remain installation-specific. Navimower provides mower intent and position context; the Home Assistant automation is responsible for the actual gate controller and its safety inputs.
