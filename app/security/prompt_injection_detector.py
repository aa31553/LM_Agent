class PromptInjectionDetector:
    suspicious_phrases = (
        "ignore previous instructions",
        "reveal system prompt",
        "bypass policy",
        "disable safety",
    )

    def is_suspicious(self, text: str) -> bool:
        normalized = text.lower()
        return any(phrase in normalized for phrase in self.suspicious_phrases)

