class QueryProcessor:
    def normalize(self, query: str) -> str:
        return " ".join(query.strip().split())

