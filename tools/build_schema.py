"""
Regenerate etabs_python/schema.json from a running ETABS instance.

schema.json maps, per table, the display header used in ETABS Excel exports to the field key used by
the DatabaseTables API. Only pairs not recoverable by removing spaces and "?" are stored.

Usage (ETABS open with any model):
    python tools/build_schema.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from etabs_python.connection import get_active_etabs  # noqa: E402


def main():
    sap = get_active_etabs()
    tables = sap.DatabaseTables
    ret = tables.GetAllTables()
    schema = {"_version": sap.GetVersion()[0], "tables": {}}
    for name in ret[1]:
        fields = tables.GetAllFieldsInTable(name)
        if fields[-1] != 0:
            continue
        mapping = {display: key for key, display in zip(fields[2], fields[3]) if display and re.sub(r"[\s?]", "", display) != key}
        if mapping:
            schema["tables"][name] = mapping
    out = ROOT / "etabs_python" / "schema.json"
    out.write_text(json.dumps(schema, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(f"{len(schema['tables'])} tables with renamed fields -> {out}")


if __name__ == "__main__":
    main()
