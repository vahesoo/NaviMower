from pathlib import Path

path = Path('custom_components/navimower/sensor.py')
text = path.read_text()
import_anchor = 'from .entity import NavimowEntity\n'
import_line = 'from .schedule_status import schedule_status_snapshot\n'
if import_line not in text:
    if import_anchor not in text:
        raise SystemExit('sensor import anchor missing')
    text = text.replace(import_anchor, import_anchor + import_line, 1)
setup_old = '    entities.append(NavimowerMapDataSensor(coordinator, entry.entry_id))\n    async_add_entities(entities)\n'
setup_new = '    controller = getattr(coordinator, "navimower_schedule", None)\n    if controller is not None and controller.configured:\n        entities.append(NavimowerScheduleStatusSensor(coordinator))\n    entities.append(NavimowerMapDataSensor(coordinator, entry.entry_id))\n    async_add_entities(entities)\n'
if 'NavimowerScheduleStatusSensor(coordinator)' not in text:
    if setup_old not in text:
        raise SystemExit('sensor setup anchor missing')
    text = text.replace(setup_old, setup_new, 1)
class_anchor = '\n\nclass NavimowerMapDataSensor(NavimowEntity, SensorEntity):\n'
status_class = '''\n\nclass NavimowerScheduleStatusSensor(NavimowEntity, SensorEntity):
    """Card-friendly state and ordered zone queue for Navimower Schedule."""

    _attr_has_entity_name = True
    _attr_name = "Navimower schedule status"
    _attr_icon = "mdi:calendar-sync"

    def __init__(self, coordinator: NavimowCoordinator) -> None:
        super().__init__(coordinator, "navimower_schedule_status")
        self.controller = coordinator.navimower_schedule

    @property
    def native_value(self) -> str:
        return str(schedule_status_snapshot(self.controller)["state"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        snapshot = schedule_status_snapshot(self.controller)
        snapshot.pop("state", None)
        return snapshot
'''
if 'class NavimowerScheduleStatusSensor' not in text:
    if class_anchor not in text:
        raise SystemExit('map data class anchor missing')
    text = text.replace(class_anchor, status_class + class_anchor, 1)
path.write_text(text)
