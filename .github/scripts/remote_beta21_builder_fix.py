from pathlib import Path

source_path = Path(".github/scripts/remote_beta_builder.py")
source = source_path.read_text(encoding="utf-8")
old = '    "    def update_from_snapshot(\\n",\n'
new = '    "    def update_from_snapshot(",\n'
if old not in source:
    raise SystemExit("beta21 wrapper marker not found")
source = source.replace(old, new, 1)
exec(compile(source, str(source_path), "exec"))
