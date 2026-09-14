import pandas as pd
import pytest

from etabs_python.errors import SourceError, TableNotFound
from etabs_python.sources import ExcelSource, FileSource, coerce_types, header_to_key, write_snapshot


def test_header_mapping():
    assert header_to_key("Frame Assignments - Summary", "Design Type") == "Type"
    assert header_to_key("Frame Section Property Definitions - Summary", "I33 Modifier") == "I3Mod"
    assert header_to_key("Some Unknown Table", "Is Auto Point?") == "IsAutoPoint"


def test_excel_source_reads_keys_units_and_ids(export_xlsx):
    src = ExcelSource(export_xlsx)
    assert "Frame Assignments - Summary" in src.tables()
    df = src.table("Frame Assignments - Summary")
    assert list(df.columns[:6]) == ["Story", "Label", "UniqueName", "Type", "Length", "AnalysisSect"]
    assert df["UniqueName"].tolist() == ["10", "11", "12"]
    assert df["Length"].dtype.kind in "if" and df.attrs["units"]["Length"] == "m"
    assert df.attrs["table"] == "Frame Assignments - Summary"


def test_excel_source_returns_copies(export_xlsx):
    src = ExcelSource(export_xlsx)
    a = src.table("Frame Assignments - Summary")
    a.loc[0, "Label"] = "changed"
    a.attrs["units"]["Length"] = "changed"
    b = src.table("Frame Assignments - Summary")
    assert b.loc[0, "Label"] == "B1" and b.attrs["units"]["Length"] == "m"


def test_excel_source_filters_output_case(export_xlsx):
    src = ExcelSource(export_xlsx)
    df = src.table("Element Forces - Beams", combos=["ULS2"])
    assert set(df["OutputCase"]) == {"ULS2"} and len(df) == 9


def test_excel_source_missing_table_and_file(export_xlsx, tmp_path):
    with pytest.raises(TableNotFound):
        ExcelSource(export_xlsx).table("Story Drifts")
    with pytest.raises(SourceError):
        ExcelSource(tmp_path / "missing.xlsx")


def test_excel_source_reads_several_files(tmp_path):
    from conftest import TABLES, write_export
    stories = {k: TABLES[k] for k in ("Story Definitions", "Tower and Base Story Definitions")}
    forces = {k: v for k, v in TABLES.items() if k not in stories}
    a = write_export(tmp_path / "stories.xlsx", stories)
    b = write_export(tmp_path / "forces.xlsx", forces)
    src = ExcelSource([a, b])
    assert set(src.tables()) == set(TABLES)
    assert src.table("Story Definitions")["Story"].tolist() == ["FL2", "FL1"]
    assert len(src.table("Element Forces - Beams", combos=["ULS1"])) == 14
    src.close()


def test_excel_source_first_file_wins_for_duplicate_tables(tmp_path):
    from conftest import write_export
    first = write_export(tmp_path / "a.xlsx", {"Story Definitions": (["Tower", "Name", "Height"], [None, None, "m"], [["T1", "A", 1]])})
    second = write_export(tmp_path / "b.xlsx", {"Story Definitions": (["Tower", "Name", "Height"], [None, None, "m"], [["T1", "B", 2]])})
    src = ExcelSource([first, second])
    assert src.table("Story Definitions")["Story"].tolist() == ["A"]
    assert src.table_files()["Story Definitions"] == [str(first), str(second)]


def test_excel_source_concatenates_results_split_across_files(tmp_path):
    from conftest import TABLES, write_export
    headers, units, rows = TABLES["Element Forces - Beams"]
    uls1 = write_export(tmp_path / "uls1.xlsx", {"Element Forces - Beams": (headers, units, [r for r in rows if r[3] == "ULS1"])})
    uls2 = write_export(tmp_path / "uls2.xlsx", {"Element Forces - Beams": (headers, units, [r for r in rows if r[3] == "ULS2"])})
    src = ExcelSource([uls1, uls2])
    df = src.table("Element Forces - Beams")
    assert len(df) == len(rows) and sorted(set(df["OutputCase"])) == ["ULS1", "ULS2"]
    assert df.attrs["units"]["M3"] == "kN-m"
    assert len(src.table("Element Forces - Beams", combos="ULS2")) == 9


def test_excel_source_aligns_units_of_split_results(tmp_path):
    from conftest import TABLES, write_export
    headers, units, rows = TABLES["Element Forces - Beams"]
    n_units = [u.replace("kN", "N") if u else u for u in units]
    n_rows = [r[:6] + [v * 1000 for v in r[6:]] for r in rows if r[3] == "ULS2"]
    kn = write_export(tmp_path / "kn.xlsx", {"Element Forces - Beams": (headers, units, [r for r in rows if r[3] == "ULS1"])})
    n = write_export(tmp_path / "n.xlsx", {"Element Forces - Beams": (headers, n_units, n_rows)})
    df = ExcelSource([kn, n]).table("Element Forces - Beams", combos="ULS2")
    assert df["M3"].tolist() == [-float(s) for s in range(9)] and df.attrs["units"]["M3"] == "kN-m"
    bad = write_export(tmp_path / "bad.xlsx", {"Element Forces - Beams": (headers, [u and "kg" for u in units], rows)})
    with pytest.raises(SourceError):
        ExcelSource([kn, bad]).table("Element Forces - Beams")


SPECIAL_NAMES = ["1_ULSE1   1.35D+1.5L", "80% Wind Y_Eurocode_50y", "~LLRF", "2.SLS21 (LongTerm)", "A/B-C:D*E?", "1"]


def test_case_names_with_special_characters(tmp_path):
    from conftest import TABLES, write_export
    headers, units, rows = TABLES["Joint Reactions"]
    special_rows = [[r[0], r[1], r[2], name] + r[4:] for name in SPECIAL_NAMES for r in rows]
    path = write_export(tmp_path / "special.xlsx", {"Joint Reactions": (headers, units, special_rows)})
    src = ExcelSource(path)
    assert src.table("Joint Reactions")["OutputCase"].tolist() == SPECIAL_NAMES
    for name in SPECIAL_NAMES:
        assert src.table("Joint Reactions", combos=[name])["OutputCase"].tolist() == [name]
    assert src.table("Joint Reactions", cases=["1_ULSE1 1.35D+1.5L"]).empty  # exact match, spaces matter
    assert len(src.table("Joint Reactions", combos=[1])) == 1                 # non-str names are compared as str


def test_coerce_types_keeps_ids():
    df = coerce_types(pd.DataFrame({"Label": ["65"], "UniqueName": [83], "X": ["1.5"], "Shape": ["Rect"]}))
    assert df["Label"][0] == "65" and df["UniqueName"][0] == "83"
    assert df["X"][0] == 1.5 and df["Shape"][0] == "Rect"


def test_file_source_roundtrip(tmp_path):
    df = pd.DataFrame({"UniqueName": ["10"], "Length": [8.0]})
    df.attrs = {"table": "Steel I/Wide Flange", "units": {"Length": "m"}}
    write_snapshot(tmp_path, {"Steel I/Wide Flange": df}, {"force": "kN"})
    src = FileSource(tmp_path)
    assert src.tables() == ["Steel I/Wide Flange"]
    back = src.table("Steel I/Wide Flange")
    assert back["UniqueName"][0] == "10" and back["Length"][0] == 8.0
    assert back.attrs["units"] == {"Length": "m"}
