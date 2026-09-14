"""
Shared fixtures.

export_xlsx: a small synthetic workbook in the exact layout of an ETABS "Export Tables to Excel"
             (row 1 "TABLE:  <name>", row 2 display headers, row 3 units, data from row 4).
             No project data.
etabs_live:  LiveSource attached to a running ETABS, or skip.
"""
import openpyxl
import pytest

MODS_F = ["Area Modifier", "As2 Modifier", "As3 Modifier", "J Modifier", "I22 Modifier", "I33 Modifier",
          "Mass Modifier", "Weight Modifier"]
MODS_A = ["f11 Modifier", "f22 Modifier", "f12 Modifier", "m11 Modifier", "m22 Modifier", "m12 Modifier",
          "v13 Modifier", "v23 Modifier", "Mass Modifier", "Weight Modifier"]
FORCE_H = ["P", "V2", "V3", "T", "M2", "M3"]
FORCE_U = ["kN", "kN", "kN", "kN-m", "kN-m", "kN-m"]


def _beam_rows():
    rows = []
    for combo, sign in (("ULS1", 1), ("ULS2", -1)):
        for st in range(9):
            rows.append(["FL1", "B1", 10, combo, "Combination", float(st), 0, sign * (10 - st), 0, 0, 0, sign * st])
    for st in (0, 1.5, 3, 4.5, 6):
        rows.append(["FL1", "B2", 11, "ULS1", "Combination", st, 0, 1, 0, 0, 0, 10 * st])
    return rows


TABLES = {
    "Story Definitions": (["Tower", "Name", "Height"], [None, None, "m"],
                          [["T1", "FL2", 3.5], ["T1", "FL1", 4.0]]),
    "Tower and Base Story Definitions": (["Tower", "BSName", "BSElev"], [None, None, "m"], [["T1", "Base", 0]]),
    "Point Object Connectivity": (
        ["UniqueName", "Is Auto Point", "Story", "PointBay", "IsSpecial", "X", "Y", "Z"],
        [None] * 5 + ["m", "m", "m"],
        [[1, "No", "Base", 1, "No", 0, 0, 0], [2, "No", "FL1", 1, "No", 0, 0, 4.0],
         [3, "No", "FL2", 1, "No", 0, 0, 7.5], [4, "No", "FL1", 2, "No", 8, 0, 4.0]]),
    "Frame Assignments - Summary": (
        ["Story", "Label", "UniqueName", "Design Type", "Length", "Analysis Section", "Design Section",
         "Axis Angle", "Modifiers"],
        [None, None, None, None, "m", None, None, "deg", None],
        [["FL1", "B1", 10, "Beam", 8, "B30x60", "B30x60", 0, "Yes"],
         ["FL1", "B2", 11, "Beam", 6, "B30x60", "B30x60", 0, "No"],
         ["FL1", "C1", 12, "Column", 4, "C50x50", "C50x50", 0, "No"]]),
    "Frame Assignments - Property Modifiers": (
        ["Story", "Label", "UniqueName"] + MODS_F, [None] * 11,
        [["FL1", "B1", 10, 1, 1, 1, 0.1, 0.5, 0.5, 1, 1]]),
    "Frame Assignments - End Length Offsets": (
        ["Story", "Label", "UniqueName", "Offset Option", "Offset I", "Offset J", "Rigid Factor"],
        [None, None, None, None, "mm", "mm", None],
        [["FL1", "B1", 10, "User", 250, 250, 0], ["FL1", "B2", 11, "User", 250, 250, 0],
         ["FL1", "C1", 12, "User", 0, 0, 0]]),
    "Frame Section Property Definitions - Summary": (
        ["Name", "Material", "Shape", "Area", "I33", "I22", "Area Modifier", "As2 Modifier", "As3 Modifier",
         "J Modifier", "I33 Modifier", "I22 Modifier", "Mass Modifier", "Weight Modifier"],
        [None, None, None, "cm²", "cm⁴", "cm⁴"] + [None] * 8,
        [["B30x60", "C30", "Concrete Rectangular", 1800, 540000, 135000, 1, 1, 1, 1, 0.7, 1, 1, 1],
         ["C50x50", "C30", "Concrete Rectangular", 2500, 520833.3, 520833.3, 1, 1, 1, 1, 1, 1, 1, 1]]),
    "Frame Section Property Definitions - Concrete Circle": (  # before Rectangular: dims from both must merge
        ["Name", "Material", "Diameter"], [None, None, "mm"], [["D80", "C30", 800]]),
    "Frame Section Property Definitions - Concrete Rectangular": (
        ["Name", "Material", "Depth", "Width"], [None, None, "mm", "mm"],
        [["B30x60", "C30", 600, 300], ["C50x50", "C30", 500, 500]]),
    "Material Properties - General": (["Material", "Type", "Grade"], [None] * 3, [["C30", "Concrete", "C30/37"]]),
    "Material Properties - Basic Mechanical Properties": (
        ["Material", "UnitWeight", "E1", "G12", "U12"], [None, "kN/m³", "MPa", "MPa", None],
        [["C30", 25, 32000, 13333.3, 0.2]]),
    "Material Properties - Concrete Data": (["Material", "Fc"], [None, "MPa"], [["C30", 30]]),
    "Area Assignments - Summary": (
        ["Story", "Label", "UniqueName", "Section Property", "Property Type"], [None] * 5,
        [["FL1", "F1", 20, "S200", "Slab"], ["FL1", "W1", 21, "W300", "Wall"]]),
    "Area Assignments - Stiffness Modifiers": (
        ["Story", "Label", "UniqueName"] + MODS_A, [None] * 13,
        [["FL1", "F1", 20, 1, 1, 1, 0.25, 0.25, 0.25, 1, 1, 1, 1]]),
    "Area Section Property Definitions - Summary": (
        ["Name", "Type", "Element Type", "Material", "Total Thickness"], [None] * 4 + ["mm"],
        [["S200", "Slab", "Shell-Thin", "C30", 200], ["W300", "Wall", "Shell-Thin", "C30", 300]]),
    "Slab Property Definitions": (
        ["Name", "Modeling Type", "Property Type", "Material", "Slab Thickness"] + MODS_A,
        [None] * 4 + ["mm"] + [None] * 10,
        [["S200", "Shell-Thin", "Slab", "C30", 200, 1, 1, 1, 0.5, 0.5, 0.5, 1, 1, 1, 1]]),
    "Wall Property Definitions - Specified": (
        ["Name", "Modeling Type", "Material", "Wall Thickness"] + MODS_A, [None] * 3 + ["mm"] + [None] * 10,
        [["W300", "Shell-Thin", "C30", 300, 1, 1, 1, 0.7, 1, 1, 1, 1, 1, 1]]),
    "Element Forces - Beams": (
        ["Story", "Beam", "Unique Name", "Output Case", "Case Type", "Station"] + FORCE_H,
        [None] * 5 + ["m"] + FORCE_U, _beam_rows()),
    "Element Forces - Columns": (
        ["Story", "Column", "Unique Name", "Output Case", "Case Type", "Station"] + FORCE_H,
        [None] * 5 + ["m"] + FORCE_U,
        [["FL1", "C1", 12, "ULS1", "Combination", 0, -100, 0, 0, 0, 0, 0],
         ["FL1", "C1", 12, "ULS1", "Combination", 4, -90, 0, 0, 0, 0, 0]]),
    "Pier Forces": (
        ["Story", "Pier", "Output Case", "Case Type", "Location"] + FORCE_H, [None] * 5 + FORCE_U,
        [["FL1", "P1", "ULS1", "Combination", "Top", -500, 1, 2, 3, 4, 5],
         ["FL1", "P1", "ULS1", "Combination", "Bottom", -600, 1, 2, 3, 4, 5]]),
    "Joint Reactions": (
        ["Story", "Label", "Unique Name", "Output Case", "Case Type", "FX", "FY", "FZ", "MX", "MY", "MZ"],
        [None] * 5 + FORCE_U, [["Base", 1, 1, "ULS1", "Combination", 0, 0, 700, 0, 0, 0]]),
}


def write_export(path, tables=TABLES):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for i, (name, (headers, units, rows)) in enumerate(tables.items()):
        ws = wb.create_sheet(f"Sheet{i} {name}"[:31])  # truncated like ETABS
        ws.append([f"TABLE:  {name}"])
        ws.append(headers)
        ws.append(units)
        for row in rows:
            ws.append(row)
    wb.save(path)
    return path


@pytest.fixture(scope="session")
def export_xlsx(tmp_path_factory):
    return write_export(tmp_path_factory.mktemp("export") / "export.xlsx")


@pytest.fixture(scope="session")
def etabs_live():
    try:
        from etabs_python.sources import LiveSource
        return LiveSource()
    except Exception as e:  # ETABS not running or COM unavailable
        pytest.skip(f"ETABS not available: {e}")
