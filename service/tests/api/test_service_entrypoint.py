from types import SimpleNamespace


def test_service_entrypoint_uses_local_settings_and_supports_shutdown(monkeypatch, tmp_path):
    import personalmemory.main as main_module

    captured = {}
    monkeypatch.setenv("PERSONALMEMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PERSONALMEMORY_API_TOKEN", "session-secret")
    monkeypatch.setenv("PERSONALMEMORY_HOST", "127.0.0.1")
    monkeypatch.setenv("PERSONALMEMORY_PORT", "19421")

    def fake_config(app, **options):
        captured.update(app=app, options=options)
        return SimpleNamespace(app=app)

    class FakeServer:
        def __init__(self, settings, config):
            self.config = config
            self.should_exit = False
            captured["server"] = self

        def run(self):
            self.config.app.state.request_shutdown()

    monkeypatch.setattr(main_module.uvicorn, "Config", fake_config)
    monkeypatch.setattr(main_module, "ConnectionAwareServer", FakeServer)
    monkeypatch.setattr(
        main_module.uvicorn,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy uvicorn.run entrypoint called")
        ),
    )

    main_module.run()

    assert captured["options"] == {
        "host": "127.0.0.1",
        "port": 19421,
        "access_log": False,
        "log_level": "warning",
        "log_config": None,
    }
    assert captured["app"].state.bootstrap_settings.api_token == "session-secret"
    assert captured["server"].should_exit is True
