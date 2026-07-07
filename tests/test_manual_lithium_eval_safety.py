import importlib.util
from pathlib import Path

import pytest


def load_manual_eval_module():
    module_path = Path(".tools/manual_lithium_eval.py")
    spec = importlib.util.spec_from_file_location("manual_lithium_eval", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_manual_eval_allows_local_llm_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_manual_eval_module()

    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1234/v1")
    monkeypatch.delenv("ALLOW_EXTERNAL_LLM_EVAL", raising=False)

    module.require_external_eval_consent()


def test_manual_eval_blocks_external_llm_endpoint_without_consent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_manual_eval_module()

    monkeypatch.setenv("LLM_BASE_URL", "https://token-plan-sgp.xiaomimimo.com/v1")
    monkeypatch.delenv("ALLOW_EXTERNAL_LLM_EVAL", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        module.require_external_eval_consent()

    assert "retrieved private knowledge-base context" in str(exc_info.value)


def test_manual_eval_allows_external_llm_endpoint_with_explicit_consent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_manual_eval_module()

    monkeypatch.setenv("LLM_BASE_URL", "https://token-plan-sgp.xiaomimimo.com/v1")
    monkeypatch.setenv("ALLOW_EXTERNAL_LLM_EVAL", module.EXTERNAL_EVAL_CONSENT)

    module.require_external_eval_consent()
