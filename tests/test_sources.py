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
