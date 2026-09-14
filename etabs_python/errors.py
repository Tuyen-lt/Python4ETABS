"""Exception hierarchy of the engine."""
from .connection import EtabsConnectionError, ModelLockedError  # noqa: F401


class EngineError(Exception):
    """Base class of engine errors."""


class SourceError(EngineError):
    """Source cannot be opened or does not support the operation."""


class TableNotFound(EngineError):
    def __init__(self, table: str, source: str):
        super().__init__(f"Table '{table}' not found in {source} source. "
                         f"Export it from ETABS or check the table name.")
        self.table, self.source = table, source


class ResultNotAvailable(EngineError):
    """Analysis results are missing (model not run, or case/combo not finished)."""


class UnitError(EngineError):
    def __init__(self, table: str, column: str, unit: str):
        super().__init__(f"Unsupported unit '{unit}' in column '{column}' of table '{table}'. "
                         f"Only SI units are supported.")
        self.table, self.column, self.unit = table, column, unit
