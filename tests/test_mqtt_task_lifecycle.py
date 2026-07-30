"""Static regression checks for long-lived Navimower MQTT tasks."""

from __future__ import annotations

import ast
from pathlib import Path


MQTT_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "navimower"
    / "mqtt.py"
)


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _method(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == name:
                return item
    raise AssertionError(f"Method {name} was not found")


def _task_calls(method: ast.FunctionDef) -> set[str | None]:
    return {
        _call_name(node)
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and _call_name(node) in {
            "async_create_background_task",
            "async_create_task",
        }
    }


def main() -> None:
    tree = ast.parse(MQTT_PATH.read_text(encoding="utf-8"))
    for method_name in ("schedule_start_retry", "_ensure_watchdog"):
        calls = _task_calls(_method(tree, method_name))
        assert calls == {"async_create_background_task"}, (method_name, calls)
    print("mqtt task lifecycle tests passed")


if __name__ == "__main__":
    main()
