from pathlib import Path
import json

ROOT = Path('.')
COMPONENT = ROOT / 'custom_components' / 'navimower'


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding='utf-8')
    if old not in text:
        raise SystemExit(f'anchor missing in {path}: {old[:120]!r}')
    path.write_text(text.replace(old, new, 1), encoding='utf-8')


def replace_n(path: Path, old: str, new: str, count: int) -> None:
    text = path.read_text(encoding='utf-8')
    if text.count(old) < count:
        raise SystemExit(f'expected {count} anchors in {path}, found {text.count(old)}: {old[:120]!r}')
    path.write_text(text.replace(old, new, count), encoding='utf-8')


# ---------------------------------------------------------------------------
# Coordinator: persist and arbitrate an authoritative problem/lift latch.
coord = COMPONENT / 'coordinator.py'
replace_once(
    coord,
    '''        self._mqtt_state_last_update: float | None = None
        self._mqtt_action_last_update: float | None = None
        self._mqtt_battery: int | None = None
''',
    '''        self._mqtt_state_last_update: float | None = None
        self._mqtt_action_last_update: float | None = None
        self._mqtt_named_state: str | None = None
        self._mqtt_named_state_last_update: float | None = None
        self._mqtt_battery: int | None = None
''',
)
replace_once(
    coord,
    '''        self._last_private_error: str | None = None
        self._last_oauth_error: str | None = None
        self._last_mqtt_error: str | None = None
        self.channels: list[NavimowerChannel] = parse_channels(
''',
    '''        self._last_private_error: str | None = None
        self._last_oauth_error: str | None = None
        self._last_mqtt_error: str | None = None
        self._problem_latched = False
        self._problem_latched_since_mono: float | None = None
        self._problem_source: str | None = None
        self._last_problem: dict[str, Any] | None = None
        self._problem_events: list[dict[str, Any]] = []
        self.channels: list[NavimowerChannel] = parse_channels(
''',
)
replace_once(
    coord,
    '''            telemetry = cached.get("telemetry")
            if isinstance(telemetry, dict):
                self._restored_telemetry = dict(telemetry)

        # Always expose a bootstrap snapshot before the network branches start.
''',
    '''            telemetry = cached.get("telemetry")
            if isinstance(telemetry, dict):
                self._restored_telemetry = dict(telemetry)
            problem = cached.get("problem")
            if isinstance(problem, dict):
                self._problem_latched = bool(problem.get("latched"))
                self._problem_source = str(problem.get("source") or "") or None
                last_problem = problem.get("last_problem")
                if isinstance(last_problem, dict):
                    self._last_problem = dict(last_problem)
                self._problem_events = [
                    dict(item)
                    for item in (problem.get("events") or [])
                    if isinstance(item, dict)
                ][-20:]
                if self._problem_latched:
                    self._problem_latched_since_mono = time.monotonic()

        # Always expose a bootstrap snapshot before the network branches start.
''',
)
replace_once(
    coord,
    '''            "state": "Unknown",
            "state_code": "",
            "activity": None,
            "online": None,
            "docked": None,
            "error": False,
            "battery": _as_int(self._restored_telemetry.get("battery")),
''',
    '''            "state": (
                str((self._last_problem or {}).get("state") or "Problem")
                if self._problem_latched
                else "Unknown"
            ),
            "state_code": (
                str((self._last_problem or {}).get("state_code") or "")
                if self._problem_latched
                else ""
            ),
            "activity": ACTIVITY_ERROR if self._problem_latched else None,
            "online": None,
            "docked": False if self._problem_latched else None,
            "error": bool(self._problem_latched),
            "error_text": (
                (self._last_problem or {}).get("error_text")
                if self._problem_latched
                else None
            ),
            "problem_latched": bool(self._problem_latched),
            "problem_source": self._problem_source,
            "last_problem": deepcopy(self._last_problem),
            "problem_event_count": len(self._problem_events),
            "battery": _as_int(self._restored_telemetry.get("battery")),
''',
)
replace_once(
    coord,
    '''            "map_cache_key": list(self._map_cache_key)
            if self._map_cache_key
            else None,
            "telemetry": {
''',
    '''            "map_cache_key": list(self._map_cache_key)
            if self._map_cache_key
            else None,
            "problem": {
                "latched": bool(self._problem_latched),
                "source": self._problem_source,
                "last_problem": deepcopy(self._last_problem),
                "events": deepcopy(self._problem_events[-20:]),
            },
            "telemetry": {
''',
)

problem_helpers = '''    def _private_problem_clear_confirmed(self) -> bool:
        """Return whether index2 confirmed a clear after the problem assertion.

        Cached private data must never clear a problem latch. A named MQTT
        ``isLifted`` state remains authoritative for its freshness window; when
        MQTT explicitly moves away from that state, require a newer private
        index2 success before clearing the latch.
        """
        if not self._problem_latched:
            return False
        status = self._endpoint_status.get("index2") or {}
        last_success = _as_float(status.get("last_success_mono"))
        if last_success is None:
            return False
        threshold = self._problem_latched_since_mono or 0.0
        named_at = self._mqtt_named_state_last_update
        if self._mqtt_named_state == "isLifted" and named_at is not None:
            threshold = max(threshold, named_at + MQTT_STATE_STALE_SECONDS)
        elif self._mqtt_named_state and named_at is not None:
            threshold = max(threshold, named_at)
        return last_success >= threshold

    def _set_problem_latch(
        self,
        active: bool,
        *,
        source: str,
        state: str | None = None,
        state_code: str | None = None,
        error_text: str | None = None,
        confirmed_clear: bool = False,
    ) -> None:
        """Record one bounded problem transition and keep the latest details."""
        now_utc = datetime.now(UTC).isoformat()
        if active:
            was_latched = self._problem_latched
            previous = dict(self._last_problem or {})
            if not was_latched:
                self._problem_latched_since_mono = time.monotonic()
            self._problem_latched = True
            self._problem_source = str(source)
            resolved_state = str(state or previous.get("state") or "Problem")
            resolved_code = str(state_code or previous.get("state_code") or "")
            resolved_text = str(error_text or previous.get("error_text") or "Problem")
            self._last_problem = {
                "active": True,
                "source": str(source),
                "state": resolved_state,
                "state_code": resolved_code,
                "error_text": resolved_text,
                "first_seen_utc": (
                    previous.get("first_seen_utc") if was_latched else now_utc
                ),
                "last_seen_utc": now_utc,
            }
            if not was_latched:
                self._problem_events.append(
                    {
                        "active": True,
                        "source": str(source),
                        "state": resolved_state,
                        "state_code": resolved_code,
                        "error_text": resolved_text,
                        "created_utc": now_utc,
                    }
                )
        elif confirmed_clear and self._problem_latched:
            self._problem_latched = False
            self._problem_latched_since_mono = None
            self._problem_source = str(source)
            self._problem_events.append(
                {
                    "active": False,
                    "source": str(source),
                    "state": state,
                    "state_code": str(state_code or ""),
                    "error_text": None,
                    "created_utc": now_utc,
                }
            )
        del self._problem_events[:-20]

    def _apply_problem_latch(self, snapshot: dict[str, Any]) -> None:
        """Make a latched problem authoritative over ordinary state refreshes."""
        snapshot["problem_latched"] = bool(self._problem_latched)
        snapshot["problem_source"] = self._problem_source
        snapshot["last_problem"] = deepcopy(self._last_problem)
        snapshot["problem_event_count"] = len(self._problem_events)
        if not self._problem_latched:
            return
        last = self._last_problem or {}
        snapshot["error"] = True
        snapshot["error_text"] = snapshot.get("error_text") or last.get("error_text") or "Problem"
        snapshot["activity"] = ACTIVITY_ERROR
        snapshot["docked"] = False
        snapshot["docked_source"] = "problem_latched"
        if last.get("state") == "Lifted" or self._fresh_mqtt_named_state() == "isLifted":
            snapshot["state"] = "Lifted"
        if not snapshot.get("state_code") and last.get("state_code"):
            snapshot["state_code"] = str(last.get("state_code"))

    def problem_diagnostics(self) -> dict[str, Any]:
        """Return persisted problem history without account identifiers."""
        return {
            "latched": bool(self._problem_latched),
            "source": self._problem_source,
            "last_problem": deepcopy(self._last_problem),
            "event_limit": 20,
            "events": deepcopy(self._problem_events[-20:]),
        }

'''
replace_once(coord, '    def _parse(self, raw: dict) -> dict:\n', problem_helpers + '    def _parse(self, raw: dict) -> dict:\n')

replace_once(
    coord,
    '''        # Error detection from index2's inline error array (the hint-error
        # endpoint returns a compressed blob we intentionally do not decode).
        error_list = index2.get("error_data") or _find(index2, "errorData", "error_list") or []
        has_error = bool(error_list)
        error_text = None
        if has_error and isinstance(error_list, list) and error_list:
            first = error_list[0]
            if isinstance(first, dict):
                error_text = str(
                    first.get("desc") or first.get("message") or first.get("code") or "error"
                )
''',
    '''        # Error arbitration. The compressed hint catalog is diagnostic only;
        # authoritative problem signals are inline private errors, private 0302,
        # and a fresh official MQTT named state of isLifted.
        error_list = index2.get("error_data") or _find(index2, "errorData", "error_list") or []
        error_text = None
        if isinstance(error_list, list) and error_list:
            first = error_list[0]
            if isinstance(first, dict):
                error_text = str(
                    first.get("desc") or first.get("message") or first.get("code") or "error"
                )
            elif first is not None:
                error_text = str(first)
        private_problem = bool(error_list) or state_code == STATE_LIFTED
        mqtt_named_state = self._fresh_mqtt_named_state()
        mqtt_problem = mqtt_named_state == "isLifted"
        if private_problem:
            self._set_problem_latch(
                True,
                source=(
                    "private_cloud_error_data" if bool(error_list) else "private_cloud_state"
                ),
                state="Lifted" if state_code == STATE_LIFTED else "Problem",
                state_code=state_code,
                error_text=error_text or ("Lifted" if state_code == STATE_LIFTED else "Problem"),
            )
        elif state_code and not mqtt_problem and self._private_problem_clear_confirmed():
            self._set_problem_latch(
                False,
                source="private_cloud_clear",
                state=VEHICLE_STATE_LABELS.get(state_code) or state_code,
                state_code=state_code,
                confirmed_clear=True,
            )
        if mqtt_problem:
            self._set_problem_latch(
                True,
                source="mqtt_state",
                state="Lifted",
                state_code=STATE_LIFTED,
                error_text="Lifted",
            )
        has_error = bool(private_problem or mqtt_problem or self._problem_latched)
        if has_error and not error_text:
            error_text = str((self._last_problem or {}).get("error_text") or "Problem")
''',
)
replace_once(
    coord,
    '''        state_label = VEHICLE_STATE_LABELS.get(state_code)
        if state_label is None:
            normalized = str(activity or "transitioning").replace("_", " ").title()
            state_label = f"{normalized} ({state_code})" if state_code else normalized

        docked, docked_source = self._resolved_docked_state(
            state_code, mqtt_vehicle_state, activity, pending_activity
        )
        self._last_docked_source = docked_source
''',
    '''        state_label = VEHICLE_STATE_LABELS.get(state_code)
        if state_label is None:
            normalized = str(activity or "transitioning").replace("_", " ").title()
            state_label = f"{normalized} ({state_code})" if state_code else normalized
        if mqtt_problem or (
            self._problem_latched and (self._last_problem or {}).get("state") == "Lifted"
        ):
            state_label = "Lifted"

        docked, docked_source = self._resolved_docked_state(
            state_code, mqtt_vehicle_state, activity, pending_activity
        )
        if has_error:
            docked = False
            docked_source = "problem_state"
        self._last_docked_source = docked_source
''',
)
replace_once(
    coord,
    '''            "error": has_error,
            "error_text": error_text,
            # progress / areas
''',
    '''            "error": has_error,
            "error_text": error_text,
            "problem_latched": bool(self._problem_latched),
            "problem_source": self._problem_source,
            "last_problem": deepcopy(self._last_problem),
            "problem_event_count": len(self._problem_events),
            # progress / areas
''',
)
replace_once(
    coord,
    '''    def _fresh_mqtt_vehicle_state(self) -> int | None:
        age = self.mqtt_state_age()
        if age is None or age > MQTT_STATE_STALE_SECONDS:
            return None
        return _as_int((self._mqtt_location or {}).get("vehicle_state"))

    def _fresh_mqtt_action(self) -> int | None:
''',
    '''    def _fresh_mqtt_vehicle_state(self) -> int | None:
        age = self.mqtt_state_age()
        if age is None or age > MQTT_STATE_STALE_SECONDS:
            return None
        return _as_int((self._mqtt_location or {}).get("vehicle_state"))

    def _fresh_mqtt_named_state(self) -> str | None:
        age = self._age_since(self._mqtt_named_state_last_update)
        if age is None or age > MQTT_STATE_STALE_SECONDS:
            return None
        value = str(self._mqtt_named_state or "").strip()
        return value or None

    def _fresh_mqtt_action(self) -> int | None:
''',
)

# Both private-refresh MQTT merge and live-location MQTT ingestion have the same
# docked assignment anchor. Apply the problem latch after ordinary arbitration.
replace_n(
    coord,
    '''        snapshot["docked"] = docked
        snapshot["docked_source"] = docked_source
        self._last_docked_source = docked_source
        snapshot.update(self._connectivity_fields())
''',
    '''        snapshot["docked"] = docked
        snapshot["docked_source"] = docked_source
        self._apply_problem_latch(snapshot)
        self._last_docked_source = str(snapshot.get("docked_source") or docked_source)
        snapshot.update(self._connectivity_fields())
''',
    2,
)

old_ingest_state = '''    def ingest_mqtt_state(self, state: dict[str, Any]) -> None:
        """Merge the official MQTT state packet used for dense battery data."""
        if not isinstance(state, dict):
            return
        battery = _as_int(state.get("battery"))
        state_name = str(state.get("state") or "").strip()
        if battery is None or not 0 <= battery <= 100:
            return
        previous_snapshot = dict(self.data or {})
        previous_state = str(previous_snapshot.get("state") or "")
        self._mqtt_battery = battery
        self._mqtt_battery_last_update = time.monotonic()
        self._mqtt_connected = True
        snapshot = dict(self.data or self._bootstrap_snapshot())
        if state_name == "isLifted":
            snapshot["state"] = "Lifted"
            snapshot["activity"] = ACTIVITY_ERROR
            snapshot["docked"] = False
            snapshot["docked_source"] = "mqtt_lifted_state"
        self._stabilize_telemetry(snapshot, previous_snapshot)
        snapshot.update(self._connectivity_fields())
        self._schedule_state_save(snapshot)
        self.async_set_updated_data(snapshot)
        if state_name == "isLifted" and previous_state != "Lifted":
            self.request_fast_refresh("MQTT state changed to isLifted")

'''
new_ingest_state = '''    def ingest_mqtt_state(self, state: dict[str, Any]) -> None:
        """Merge official MQTT battery and named state independently."""
        if not isinstance(state, dict):
            return
        battery = _as_int(state.get("battery"))
        state_name = str(state.get("state") or "").strip()
        valid_battery = battery is not None and 0 <= battery <= 100
        if not valid_battery and not state_name:
            return
        previous_snapshot = dict(self.data or {})
        previous_named_state = self._fresh_mqtt_named_state()
        now_monotonic = time.monotonic()
        if valid_battery:
            self._mqtt_battery = battery
            self._mqtt_battery_last_update = now_monotonic
        if state_name:
            self._mqtt_named_state = state_name
            self._mqtt_named_state_last_update = now_monotonic
        self._mqtt_connected = True
        snapshot = dict(self.data or self._bootstrap_snapshot())
        if state_name == "isLifted":
            self._set_problem_latch(
                True,
                source="mqtt_state",
                state="Lifted",
                state_code=STATE_LIFTED,
                error_text="Lifted",
            )
            self.clear_pending_activity()
            snapshot["state"] = "Lifted"
            snapshot["activity"] = ACTIVITY_ERROR
            snapshot["error"] = True
            snapshot["error_text"] = "Lifted"
            snapshot["docked"] = False
            snapshot["docked_source"] = "mqtt_lifted_state"
        self._apply_problem_latch(snapshot)
        self._stabilize_telemetry(snapshot, previous_snapshot)
        snapshot.update(self._connectivity_fields())
        self._schedule_state_save(snapshot)
        self.async_set_updated_data(snapshot)
        if state_name == "isLifted" and previous_named_state != "isLifted":
            self.request_fast_refresh("MQTT state changed to isLifted")
        elif (
            state_name
            and previous_named_state == "isLifted"
            and state_name != "isLifted"
        ):
            self.request_fast_refresh("MQTT state changed away from isLifted")

'''
replace_once(coord, old_ingest_state, new_ingest_state)

# ---------------------------------------------------------------------------
# Passive MQTT discovery: preserve legacy current-mower helper, add wider opt-in
# subscription, but retain samples only for current mower or account-level topics.
discovery = COMPONENT / 'discovery.py'
replace_once(
    discovery,
    '''def mqtt_discovery_topic(device_id: str) -> str:
    """Return the current mower-only downlink wildcard used in discovery mode."""
    return f"/downlink/vehicle/{device_id}/#"


def _is_sensitive_key(key: str) -> bool:
''',
    '''def mqtt_discovery_topic(device_id: str) -> str:
    """Return the legacy current mower-only discovery wildcard."""
    return f"/downlink/vehicle/{device_id}/#"


def mqtt_discovery_topics(device_id: str) -> tuple[str, ...]:
    """Return wider opt-in downlink subscriptions for notification research."""
    del device_id
    return ("/downlink/#",)


def _is_sensitive_key(key: str) -> bool:
''',
)

mqtt = COMPONENT / 'mqtt.py'
replace_once(
    mqtt,
    'from .discovery import mqtt_discovery_topic, sanitize_discovery_payload, structure_summary\n',
    'from .discovery import (\n    mqtt_discovery_topic,\n    mqtt_discovery_topics,\n    sanitize_discovery_payload,\n    structure_summary,\n)\n',
)
replace_once(
    mqtt,
    '''        if self._discovery_enabled:
            try:
                sdk._mqtt.client.subscribe(mqtt_discovery_topic(device_id))
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Could not subscribe to Navimower passive discovery: %s", err)
''',
    '''        if self._discovery_enabled:
            for discovery_topic in mqtt_discovery_topics(device_id):
                try:
                    sdk._mqtt.client.subscribe(discovery_topic)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning(
                        "Could not subscribe to Navimower passive discovery %s: %s",
                        discovery_topic,
                        err,
                    )
''',
)
replace_once(
    mqtt,
    '''        current_device = bool(
            incoming_device_id == self._device_id
            or (self._device_id and f"/vehicle/{self._device_id}/" in str(topic))
        )
        if not self._discovery_enabled or not current_device:
            return
''',
    '''        topic_text = str(topic)
        current_device = bool(
            incoming_device_id == self._device_id
            or (self._device_id and f"/vehicle/{self._device_id}/" in topic_text)
        )
        account_event = bool(
            topic_text.startswith("/downlink/") and "/vehicle/" not in topic_text
        )
        if not self._discovery_enabled or not (current_device or account_event):
            return
''',
)
replace_once(
    mqtt,
    '''        wildcard = mqtt_discovery_topic(self._device_id) if self._device_id else None
        if wildcard and self._device_id:
            wildcard = wildcard.replace(self._device_id, "<device>")
        return {
            "enabled": self._discovery_enabled,
            "scope": "current_device_only",
            "wildcard_topic": wildcard,
''',
    '''        wildcard = mqtt_discovery_topic(self._device_id) if self._device_id else None
        if wildcard and self._device_id:
            wildcard = wildcard.replace(self._device_id, "<device>")
        wildcard_topics = list(mqtt_discovery_topics(self._device_id))
        if self._device_id:
            wildcard_topics = [
                item.replace(self._device_id, "<device>") for item in wildcard_topics
            ]
        return {
            "enabled": self._discovery_enabled,
            "scope": "current_device_and_account_events",
            "wildcard_topic": wildcard,
            "wildcard_topics": wildcard_topics,
''',
)

# ---------------------------------------------------------------------------
# Diagnostics: expose bounded problem transitions and latest problem metadata.
diag = COMPONENT / 'diagnostics_export.py'
replace_once(
    diag,
    '''    last_mow_command = (
        coordinator.mow_command_diagnostics()
''',
    '''    problem_history = (
        coordinator.problem_diagnostics()
        if hasattr(coordinator, "problem_diagnostics")
        else None
    )

    last_mow_command = (
        coordinator.mow_command_diagnostics()
''',
)
replace_once(
    diag,
    '''        "last_mow_command": sanitize(deepcopy(last_mow_command)),
        "mower": {
''',
    '''        "last_mow_command": sanitize(deepcopy(last_mow_command)),
        "problem_history": sanitize(deepcopy(problem_history)),
        "mower": {
''',
)
replace_once(
    diag,
    '''            "state_code": data.get("state_code"),
            "activity": data.get("activity"),
            "private_cloud_connected": data.get("private_cloud_connected"),
''',
    '''            "state_code": data.get("state_code"),
            "activity": data.get("activity"),
            "problem": data.get("error"),
            "error_text": data.get("error_text"),
            "problem_source": data.get("problem_source"),
            "last_problem": sanitize(deepcopy(data.get("last_problem"))),
            "private_cloud_connected": data.get("private_cloud_connected"),
''',
)
replace_once(
    diag,
    '            "Passive discovery is opt-in and current-mower scoped; samples are bounded and sanitized.",\n',
    '            "Passive discovery is opt-in; samples are bounded and sanitized, and other mower vehicle topics are excluded from sampled discovery data.",\n',
)

# ---------------------------------------------------------------------------
# Version, changelog and release notes.
manifest_path = COMPONENT / 'manifest.json'
manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
manifest['version'] = '0.4.1-beta9'
manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')

changelog = ROOT / 'CHANGELOG.md'
old_changelog = changelog.read_text(encoding='utf-8')
if not old_changelog.startswith('# Changelog\n\n'):
    raise SystemExit('unexpected changelog header')
header = '''# Changelog

## 0.4.1-beta9 - authoritative problem state and notification discovery

- Make private state `0302`, inline private error data and official MQTT `state=isLifted` authoritative for the Problem entity and error activity.
- Process MQTT named state independently of battery, so a lift event is not discarded when the packet omits battery data.
- Persist the Problem latch across reload/restart and clear it only after a newer successful private `index2` confirmation reports a non-problem state.
- Expose the latest problem source/details and a bounded transition history in diagnostics.
- Widen opt-in passive MQTT discovery to `/downlink/#` for notification research while sampling only the current mower and non-vehicle account-level topics; other mower vehicle payloads remain excluded from discovery samples.
- Keep passive discovery off by default and preserve all existing redaction and sample limits.

'''
changelog.write_text(header + old_changelog[len('# Changelog\n\n'):], encoding='utf-8')

notes = ROOT / '.github' / 'release-notes' / '0.4.1-beta9.md'
notes.write_text('''title: Navimower 0.4.1-beta9

## Authoritative problem state and wider notification discovery

This beta finishes the lifted/problem-state work started in beta8 and extends the opt-in discovery mode used for notification research.

- Private-cloud state `0302`, inline private error data and official MQTT `state=isLifted` now all turn the **Problem** entity on and force error activity immediately. MQTT named state is processed even when the same packet has no usable battery value.
- A detected problem is latched and persisted across Home Assistant reloads/restarts. Missing or cached cloud data cannot clear it; only a newer successful private-cloud `index2` confirmation of a non-problem state clears the latch.
- Diagnostics now expose the latest problem source, state/code, error text and timestamps plus a bounded history of problem-on/problem-clear transitions.
- **Passive protocol discovery** remains opt-in and off by default, but its MQTT subscription is widened to `/downlink/#` to look for notification/push traffic. Sanitized samples are retained only for the configured mower and non-vehicle account-level downlink topics; other mower vehicle topics are excluded from sampled discovery data.
- Existing credential/account/device/GPS redaction, payload bounding and per-topic sample limits remain unchanged.

No mower command behavior or Navimower Map Card code changes are included.
''', encoding='utf-8')

# ---------------------------------------------------------------------------
# Historical beta8 test: keep beta8 contracts, stop pinning the current version.
beta8_test = ROOT / 'tests' / 'test_v041_beta8.py'
replace_once(
    beta8_test,
    '''def test_beta8_version_and_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.4.1-beta8"
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta8.md").read_text()
''',
    '''def test_beta8_version_and_notes() -> None:
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta8.md").read_text()
''',
)

release_test = ROOT / 'tests' / 'test_v034_release.py'
replace_once(
    release_test,
    '''    assert manifest["version"] == "0.4.1-beta8"
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta8.md").read_text()
    assert notes.startswith("title: Navimower 0.4.1-beta8")
    assert "Lifted" in notes
    assert "isLifted" in notes
    assert "get-hint-error-compress" in notes
''',
    '''    assert manifest["version"] == "0.4.1-beta9"
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta9.md").read_text()
    assert notes.startswith("title: Navimower 0.4.1-beta9")
    assert "Problem" in notes
    assert "isLifted" in notes
    assert "index2" in notes
    assert "/downlink/#" in notes
''',
)

beta9_test = ROOT / 'tests' / 'test_v041_beta9.py'
beta9_test.write_text('''"""Regression contracts for Navimower 0.4.1-beta9."""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "navimower"


def _load_discovery():
    path = COMPONENT / "discovery.py"
    spec = importlib.util.spec_from_file_location("navimower_beta9_discovery", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_beta9_version_and_notes() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["version"] == "0.4.1-beta9"
    notes = (ROOT / ".github" / "release-notes" / "0.4.1-beta9.md").read_text()
    assert "Problem" in notes
    assert "index2" in notes
    assert "/downlink/#" in notes
    assert "off by default" in notes


def test_problem_latch_is_persisted_and_cloud_cleared() -> None:
    source = (COMPONENT / "coordinator.py").read_text()
    assert "self._problem_latched = False" in source
    assert 'problem = cached.get("problem")' in source
    assert '"problem": {' in source
    assert "_private_problem_clear_confirmed" in source
    assert 'source="private_cloud_clear"' in source
    assert "confirmed_clear=True" in source
    assert "problem_diagnostics" in source
    assert 'snapshot["error"] = True' in source
    ast.parse(source)


def test_mqtt_named_state_does_not_depend_on_battery() -> None:
    source = (COMPONENT / "coordinator.py").read_text()
    assert "valid_battery = battery is not None and 0 <= battery <= 100" in source
    assert "if not valid_battery and not state_name:" in source
    assert 'state_name == "isLifted"' in source
    assert 'source="mqtt_state"' in source
    assert "_fresh_mqtt_named_state" in source


def test_wider_discovery_keeps_legacy_helper_and_filters_vehicle_samples() -> None:
    discovery = _load_discovery()
    assert discovery.mqtt_discovery_topic("ABC123") == "/downlink/vehicle/ABC123/#"
    assert discovery.mqtt_discovery_topics("ABC123") == ("/downlink/#",)
    mqtt = (COMPONENT / "mqtt.py").read_text()
    assert "for discovery_topic in mqtt_discovery_topics(device_id):" in mqtt
    assert "account_event = bool(" in mqtt
    assert '"/vehicle/" not in topic_text' in mqtt
    assert '"scope": "current_device_and_account_events"' in mqtt
    ast.parse(mqtt)


def test_problem_history_is_exposed_in_diagnostics() -> None:
    source = (COMPONENT / "diagnostics_export.py").read_text()
    assert "coordinator.problem_diagnostics()" in source
    assert '"problem_history": sanitize(deepcopy(problem_history))' in source
    assert '"problem_source": data.get("problem_source")' in source
    ast.parse(source)
''', encoding='utf-8')
