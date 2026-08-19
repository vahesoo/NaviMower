from pathlib import Path

source_path = Path(".github/scripts/remote_beta_builder.py")
source = source_path.read_text(encoding="utf-8")
replacements = {
    '    "    def update_from_snapshot(\\n",\n': '    "    def update_from_snapshot(",\n',
    '    path.write_text(text[:start_at] + dedent(new).lstrip("\\n") + text[end_at:], encoding="utf-8")\n': '    path.write_text(text[:start_at] + new.lstrip("\\n") + text[end_at:], encoding="utf-8")\n',
}
for old, new in replacements.items():
    if old not in source:
        raise SystemExit(f"beta21 wrapper marker not found: {old!r}")
    source = source.replace(old, new, 1)
exec(compile(source, str(source_path), "exec"))
