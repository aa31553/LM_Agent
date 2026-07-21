from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

try:
    from pgvector.sqlalchemy import Vector as PostgreSQLVector
except ImportError:
    PostgreSQLVector = None


def Vector(dimension: int):
    portable = JSON()
    if PostgreSQLVector is None:
        return portable
    return portable.with_variant(PostgreSQLVector(dimension), "postgresql")


def JsonObject():
    return JSON().with_variant(JSONB(), "postgresql")
