"""Embed the shared dashboard assets into AppFoundry's single-file Flask app."""

from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
SERVER = HERE / "app.py"
INDEX = PROJECT / "docs" / "index.html"
SCRIPT = PROJECT / "docs" / "app.js"
OUTPUT = HERE / "standalone_app.py"

source = SERVER.read_text(encoding="utf-8")
asset_lines = (
    'INDEX_HTML = (ASSET_DIR / "index.html").read_text(encoding="utf-8") if (ASSET_DIR / "index.html").exists() else ""\n'
    'APP_JS = (ASSET_DIR / "app.js").read_text(encoding="utf-8") if (ASSET_DIR / "app.js").exists() else ""'
)
embedded_lines = f"INDEX_HTML = {INDEX.read_text(encoding='utf-8')!r}\nAPP_JS = {SCRIPT.read_text(encoding='utf-8')!r}"
if asset_lines not in source:
    raise SystemExit("Could not find the asset insertion point in app.py")
source = source.replace(asset_lines, embedded_lines, 1)
embedded_modules = 'import types\n'
for name in ('workflow', 'snapshot'):
    module_source = (PROJECT / (name + '.py')).read_text(encoding='utf-8')
    embedded_modules += f"_module = types.ModuleType({name!r})\nexec({module_source!r}, _module.__dict__)\nsys.modules[{name!r}] = _module\n"
source = source.replace('from snapshot import MAX_UPLOAD_BYTES, validate_snapshot', embedded_modules + 'from snapshot import MAX_UPLOAD_BYTES, validate_snapshot')
OUTPUT.write_text(source, encoding="utf-8")
print(f"Built {OUTPUT}")
