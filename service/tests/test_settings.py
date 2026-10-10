def test_default_data_dir_is_separate_from_current_user_install(monkeypatch, tmp_path):
    from personalmemory.settings import default_data_dir

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert default_data_dir() == tmp_path / "PersonalMemoryData"


def test_llm_settings_from_env(monkeypatch):
    from personalmemory.settings import Settings

    monkeypatch.setenv("PERSONALMEMORY_LLM_ENABLED", "true")
    monkeypatch.setenv("PERSONALMEMORY_LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("PERSONALMEMORY_LLM_MODEL", "mock-model")
    monkeypatch.setenv("PERSONALMEMORY_LLM_TIMEOUT_SECONDS", "45")

    settings = Settings()

    assert settings.llm_enabled is True
    assert settings.llm_base_url == "https://example.test/v1"
    assert settings.llm_model == "mock-model"
    assert settings.llm_timeout_seconds == 45.0
