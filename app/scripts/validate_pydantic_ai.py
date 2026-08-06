"""Validate the PydanticAI runtime installation used by LM_Agent."""

import re
from importlib import metadata

EXPECTED_VERSION = "1.107.1"


def validate_installation() -> list[str]:
    """Return compatibility errors without importing application services."""

    errors: list[str] = []
    versions: dict[str, str] = {}
    for distribution in (
        "pydantic-ai-slim",
        "pydantic-graph",
        "pydantic",
        "openai",
        "opentelemetry-api",
        "tenacity",
    ):
        try:
            versions[distribution] = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            errors.append(f"Missing required distribution: {distribution}")

    if versions.get("pydantic-ai-slim") not in {None, EXPECTED_VERSION}:
        errors.append(
            "pydantic-ai-slim version mismatch: "
            f"expected {EXPECTED_VERSION}, found {versions['pydantic-ai-slim']}"
        )
    if versions.get("pydantic-graph") not in {None, EXPECTED_VERSION}:
        errors.append(
            "pydantic-graph version mismatch: "
            f"expected {EXPECTED_VERSION}, found {versions['pydantic-graph']}"
        )

    try:
        full_version = metadata.version("pydantic-ai")
    except metadata.PackageNotFoundError:
        full_version = None
    if full_version is not None and full_version != EXPECTED_VERSION:
        errors.append(
            "pydantic-ai version mismatch: "
            f"expected {EXPECTED_VERSION}, found {full_version}"
        )

    minimum_versions = {
        "pydantic": "2.12.0",
        "openai": "2.29.0",
        "opentelemetry-api": "1.28.0",
        "tenacity": "8.2.3",
    }
    for distribution, minimum in minimum_versions.items():
        actual = versions.get(distribution)
        if actual is not None and not _version_at_least(actual, minimum):
            errors.append(
                f"{distribution} is too old: expected >= {minimum}, found {actual}"
            )

    try:
        from pydantic_ai import Agent  # noqa: F401
        from pydantic_ai.models.openai import OpenAIChatModel  # noqa: F401
        from pydantic_ai.providers.openai import OpenAIProvider  # noqa: F401
    except (ImportError, ModuleNotFoundError) as exc:
        errors.append(f"PydanticAI import failed: {type(exc).__name__}: {exc}")

    return errors


def _version_at_least(actual: str, minimum: str) -> bool:
    """Compare the numeric release portions used by these Python packages."""

    def release(value: str) -> tuple[int, ...]:
        match = re.match(r"\d+(?:\.\d+)*", value)
        return tuple(int(part) for part in match.group(0).split(".")) if match else ()

    actual_release = release(actual)
    minimum_release = release(minimum)
    width = max(len(actual_release), len(minimum_release))
    return actual_release + (0,) * (width - len(actual_release)) >= minimum_release + (
        0,
    ) * (width - len(minimum_release))


def main() -> None:
    errors = validate_installation()
    if errors:
        details = "\n".join(f"- {error}" for error in errors)
        raise SystemExit(f"PydanticAI installation is incompatible:\n{details}")
    print(f"PydanticAI {EXPECTED_VERSION} compatibility check passed.")


if __name__ == "__main__":
    main()
