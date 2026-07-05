from sqlalchemy.types import UserDefinedType

try:
    from pgvector.sqlalchemy import Vector
except ImportError:

    class Vector(UserDefinedType):
        cache_ok = True

        def __init__(self, dimension: int) -> None:
            self.dimension = dimension

        def get_col_spec(self, **kw: object) -> str:
            return f"VECTOR({self.dimension})"

