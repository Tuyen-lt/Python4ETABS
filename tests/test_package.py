from etabs_python.errors import EngineError, TableNotFound, UnitError


def test_errors_hierarchy_and_messages():
    assert issubclass(TableNotFound, EngineError)
    e = TableNotFound("Story Definitions", "excel")
    assert "Story Definitions" in str(e) and "excel" in str(e)
    u = UnitError("T", "Fc", "ksi")
    assert "ksi" in str(u) and "Fc" in str(u)
