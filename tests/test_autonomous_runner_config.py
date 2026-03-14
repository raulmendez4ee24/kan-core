from workers.autonomous_runner import RunnerConfig


def test_runner_config_from_env_defaults(monkeypatch) -> None:
    monkeypatch.delenv("ENABLE_AUTONOMOUS_RUNNER", raising=False)
    monkeypatch.delenv("AUTONOMOUS_RUNNER_TICK_SECONDS", raising=False)

    cfg = RunnerConfig.from_env()
    assert cfg.enabled is False
    assert cfg.tick_seconds >= 5


def test_runner_config_parses_target_org_ids(monkeypatch) -> None:
    monkeypatch.setenv(
        "AUTONOMOUS_RUNNER_TARGET_ORG_IDS",
        "11111111-1111-1111-1111-111111111111, 22222222-2222-2222-2222-222222222222, bad",
    )
    cfg = RunnerConfig.from_env()
    assert len(cfg.target_org_ids) == 2

