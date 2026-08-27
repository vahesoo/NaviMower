# Gate automation example

A reliable physical-gate interlock can combine two Navimower signals:

- **Gate required** — Navimower has determined that the mower needs to cross the configured zone-pair Gate.
- **Custom Area** — a Navimower Custom Area placed around the physical gate reports that the mower has actually reached the gate passage.

Using both signals avoids acting too early: `Gate required` provides the travel intent, while the Custom Area confirms physical arrival at the gate. The automation below starts only when **both** signals are On.

The example is based on a real Home Assistant automation used with Navimower. Replace the example entity IDs with your own mower, Gate required, Custom Area and gate-cover entities.

## Where this pattern is suitable

This tested pattern is intended for tasks where Navimower has **one unambiguous mowing target zone**:

- **Navimower Schedule**, because it intentionally dispatches one zone at a time; or
- a manually started mowing task containing **one mowing zone**.

Do not treat this example as a general interlock for a multi-zone task started with several zones at once. In that situation the target-zone intent can be ambiguous during parts of the task, so the same `Gate required` assumption is not guaranteed.

Place the gate Custom Area so that it extends **slightly into the mowing zone** from which the mower approaches the gate. The mower must enter the Custom Area while it is still carrying the known single-zone travel intent; this lets `Gate required` and the Custom Area become On together before the mower reaches the physical gate.

This overlap is also why a Custom Area can be more useful than a very narrow line exactly at the gate. If the required shape must cross otherwise separate mowing zones, see the README section **Custom Areas across separate mowing zones** for the temporary merge/import/restore workflow.

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

`binary_sensor.my_mower_sliding_gate_area` in this example is the binary sensor created by the **Custom Area** around the gate. Give your Custom Area a clear name so its generated Home Assistant entity is easy to identify.

## Why the automation is structured this way

Both binary sensors are triggers because their order can vary slightly. The two state conditions then make the actual entry condition deterministic: the mower must both **require the Gate** and be **inside the gate Custom Area**.

The mower is paused before opening the physical gate. This prevents the mower from reaching a still-moving gate while the gate controller is opening it. The automation then waits for the `cover` entity to report fully `open` and adds a short two-second settling delay.

The mower state is captured before the pause:

- if it was returning/docking, the automation sends `lawn_mower.dock` after the gate is open;
- otherwise it waits until Navimow confirms the paused private state code `0211`, then sends `lawn_mower.start_mowing`.

Waiting for `0211` avoids racing a resume command against the vendor pause transition.

Finally the automation waits for the Custom Area to turn Off, meaning the mower has left the configured physical passage, and keeps a short additional safety delay. `mode: single` prevents repeated On transitions from starting overlapping copies of the interlock.

## Closing the physical gate

The tested example above intentionally does **not** issue `cover.close_cover`; the gate controller used with it handles closing separately.

If your gate does not have its own safe auto-close logic, do not close it immediately after the mower resumes. Add closing only after the gate Custom Area is Off **and** `Gate required` has cleared. For example, after the final delay:

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
2. the gate **Custom Area** covers the physical passage and extends far enough into the mowing zone to detect the mower before it reaches the gate;
3. the task uses Navimower Schedule or manual single-zone mowing so the target-zone intent is unambiguous;
4. the gate `cover` reports `open` only when the passage is genuinely clear;
5. Pause, Start mowing and Dock work correctly for your mower before adding physical gate movement;
6. the automation is tested while supervised before relying on unattended operation.

Gate timing and physical safety remain installation-specific. Navimower provides mower intent and position context; the Home Assistant automation is responsible for the actual gate controller and its safety inputs.
