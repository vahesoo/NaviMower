# Physical gate automation

Navimower exposes two different gate-related signals:

- **Gate required** — travel intent between a configured pair of mowing zones;
- **Gate area** — mower presence inside a Navimower-owned local X/Y gate polygon.

They solve different problems. A physical gate automation can use one or both depending on how the mower reaches the gate and how much target-zone intent the automation can safely assume.

> [!WARNING]
> A robotic mower and a moving physical gate can both cause injury or damage. Test the complete automation while supervised. Use the gate controller's real safety inputs and obstacle protection; Navimower does not replace physical gate safety.

## Gate areas

Current Navimower Gate areas support exact mower-local polygons. Legacy rectangular `x_min/x_max/y_min/y_max` areas remain compatible, but the polygon is authoritative when present.

Gate areas may be created/edited visually by a compatible Navimower Map Card. The integration also keeps the Home Assistant Options Flow as a manual/fallback editor and exposes `navimower.set_gate_area` / `navimower.delete_gate_area` for frontend or advanced automation use.

Fresh official MQTT X/Y is preferred for Gate-area presence. A sufficiently fresh private-cloud X/Y may be used conservatively when MQTT pose is unavailable. A previously active Gate area is not cleared from one cloud-only outside sample; Navimower requires distinct fresh vendor reports before the risky OFF transition is accepted.

## Recommended pattern: one automation run owns the gate cycle

This pattern is useful when the **physical Gate area itself** is enough to decide when to stop/open the gate. It does not require a helper and it avoids a common ownership bug: an independently triggered exit automation must not close a gate that was already open before the mower arrived.

The key rule is simple:

1. trigger only when the mower enters the Gate area;
2. continue only if the physical gate is exactly `closed`;
3. the same automation run pauses the mower, opens the gate, resumes the mower, waits for the mower to leave the Gate area, and closes the gate;
4. if the gate was already open/opening/not-closed at entry, the run ends immediately and can never close that gate later.

This makes the running automation itself the ownership token: **only the run that opened the gate is allowed to close it**.

### Current field-test Gate Area interlock

The example below reflects the current working field-test pattern. It has behaved correctly in initial testing, but gate hardware/state reporting is installation-specific and the pattern should still be supervised and validated on each installation before unattended use.

Replace the example entity IDs with your own Gate-area binary sensor, mower and gate cover.

```yaml
alias: Navimow - Gate Area interlock
description: Control the physical gate only when this run opened it
triggers:
  - trigger: state
    entity_id: binary_sensor.my_mower_gate_area
    from: "off"
    to: "on"
    for:
      seconds: 1

conditions: []

actions:
  # If the gate was already open/opening/unknown, stop here. This run then owns
  # nothing and cannot close somebody else's gate later.
  - condition: state
    entity_id: cover.my_gate
    state: "closed"

  - variables:
      mower_state_before_pause: "{{ states('lawn_mower.my_mower') }}"
      mower_was_returning: >
        {{ mower_state_before_pause in ['returning', 'docking', 'docked'] }}
      mower_was_mowing: >
        {{ mower_state_before_pause == 'mowing' }}

  - action: lawn_mower.pause
    target:
      entity_id: lawn_mower.my_mower

  - action: cover.open_cover
    target:
      entity_id: cover.my_gate

  # Optional compatibility wait for a pulse/optimistic cover whose state can
  # briefly report open and then closed while it is still on the closed magnet.
  - wait_template: >
      {{ is_state('cover.my_gate', 'closed') }}
    timeout:
      seconds: 5
    continue_on_timeout: true

  # Treat the gate as physically clear only after open is stable.
  - wait_for_trigger:
      - trigger: state
        entity_id: cover.my_gate
        to: "open"
        for:
          seconds: 2
    timeout:
      seconds: 30
    continue_on_timeout: false

  # Installation-specific clearance time after the real open indication.
  - delay:
      seconds: 20

  - choose:
      - conditions:
          - condition: template
            value_template: >
              {{ mower_was_returning
                 and states('lawn_mower.my_mower') in ['paused', 'returning'] }}
        sequence:
          - action: lawn_mower.dock
            target:
              entity_id: lawn_mower.my_mower

      - conditions:
          - condition: template
            value_template: >
              {{ mower_was_mowing
                 and states('lawn_mower.my_mower') in ['paused', 'mowing'] }}
        sequence:
          - action: lawn_mower.start_mowing
            target:
              entity_id: lawn_mower.my_mower

  # The same run that opened the gate waits until the mower has really left the
  # Gate area. Requiring Off for a while also filters short position flicker.
  - wait_for_trigger:
      - trigger: state
        entity_id: binary_sensor.my_mower_gate_area
        from: "on"
        to: "off"
        for:
          seconds: 10

  - condition: template
    value_template: >
      {{ not is_state('cover.my_gate', 'closed') }}

  - action: cover.close_cover
    target:
      entity_id: cover.my_gate

mode: parallel
max: 4
```

### Why `mode: parallel` is intentional here

The long-running owner copy may still be waiting for the mower to leave the Gate area. If position briefly produces a new `off -> on` entry while the physical gate is already open, a new parallel run is allowed to start, immediately fails the `cover == closed` condition and ends. It does not disturb the original owner run.

Using a separate `on -> off` close trigger is weaker because that new run no longer knows who opened the physical gate. It can accidentally close a gate that a person, another automation or the gate controller itself had already opened before the mower arrived.

### Cover-state caveat

The intermediate five-second `closed` wait exists for a specific type of gate controller that behaves like this after `open_cover`:

1. reports an optimistic `open` immediately;
2. reports `closed` again while the gate is still physically on the closed-position magnet;
3. reports the real `open` only after the gate has moved away from that magnet.

If your `cover` has a proper independent fully-open contact and never has that optimistic transition, the intermediate `closed` wait may be unnecessary. Keep the important part: require a trustworthy fully-open indication to remain stable before resuming the mower.

If stable `open` is never confirmed, `continue_on_timeout: false` aborts the run and leaves the mower paused. That is intentionally fail-safe. Do not replace this with a blind resume timer.

## Alternative pattern: Gate required + Custom Area

The older pattern combines travel intent and physical arrival:

- **Gate required** says the mower intends to cross the configured zone pair;
- a **Custom Area** around the gate says the mower has physically reached the passage.

This can be useful when the gate passage should only react to a known A-to-B/B-to-A mowing transition and the same physical area can be entered for other reasons.

This alternative pattern is intended for tasks where Navimower has **one unambiguous mowing target zone**:

- **Navimower Schedule**, because it intentionally dispatches one zone at a time; or
- a manually started mowing task containing **one mowing zone**.

Place the gate Custom Area so that it extends **slightly into the mowing zone** from which the mower approaches the gate. The mower must enter the area while the single-zone target intent is still known, before it reaches the physical barrier.

Do not treat this as a general target-intent interlock for a multi-zone task started with several zones at once.

Example opening interlock:

```yaml
alias: Navimow - Gate required + arrival area
description: Pause mower at the gate until the physical gate is open
triggers:
  - trigger: state
    entity_id:
      - binary_sensor.my_mower_gate_required
      - binary_sensor.my_mower_gate_arrival
    to: "on"

conditions:
  - condition: state
    entity_id: binary_sensor.my_mower_gate_required
    state: "on"
  - condition: state
    entity_id: binary_sensor.my_mower_gate_arrival
    state: "on"

actions:
  - variables:
      mower_state_before_pause: "{{ states('lawn_mower.my_mower') }}"
      mower_was_returning: >
        {{ mower_state_before_pause in ['returning', 'docking', 'docked'] }}

  - action: lawn_mower.pause
    target:
      entity_id: lawn_mower.my_mower

  - choose:
      - conditions:
          - condition: state
            entity_id: cover.my_gate
            state: "closed"
        sequence:
          - action: cover.open_cover
            target:
              entity_id: cover.my_gate

  - wait_for_trigger:
      - trigger: state
        entity_id: cover.my_gate
        to: "open"
        for:
          seconds: 2
    timeout:
      seconds: 30
    continue_on_timeout: false

  - choose:
      - conditions:
          - condition: template
            value_template: "{{ mower_was_returning }}"
        sequence:
          - action: lawn_mower.dock
            target:
              entity_id: lawn_mower.my_mower
    default:
      - action: lawn_mower.start_mowing
        target:
          entity_id: lawn_mower.my_mower

mode: single
```

If this alternative also needs automatic closing, do not add an unrelated exit-only close automation unless you separately track gate ownership. Either keep the open/close lifecycle in one run or use an explicit helper that records that the mower automation actually opened the gate.

## Gate required safety semantics

Gate intent is MQTT-first and target freshness is tracked separately from pose freshness. A fresh mower position does not make an old cached work target fresh.

Navimower also protects against a stale cross-zone latch when a fresh one-zone Home Assistant/Navimower Schedule command targets the mower's current physical zone. Unknown/stale position, an active mapped-channel crossing and unconfirmed cloud-only arrival remain fail-safe instead of being guessed as a completed hand-over.

Private-cloud-only gate clear/arrival confirmation requires strictly newer vendor pose reports; duplicate, out-of-order, non-finite or implausibly future timestamps are not allowed to advance the safety transition.

## Before using either pattern

Confirm that:

1. the Gate area/arrival area covers the real physical passage and becomes active before the mower reaches the moving barrier;
2. any configured zone-pair Gate connects the correct zones and direction;
3. the physical `cover` state means what the automation assumes — preferably with real open/closed contacts;
4. Pause, Start mowing and Dock work correctly for the mower before adding physical gate movement;
5. a gate that was already open is not accidentally closed by the mower automation;
6. sensor loss/timeouts leave the system in the safer state rather than blindly resuming/closing;
7. the complete sequence is tested repeatedly while supervised.

Gate timing, manual overrides, vehicle/people detection, photocells and physical obstruction safety remain installation-specific. Navimower supplies mower position/intent context; Home Assistant and the gate controller remain responsible for the physical automation.
