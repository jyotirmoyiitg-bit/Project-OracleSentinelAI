import importlib.util
import json
from pathlib import Path

import pytest

from sentinel.config.settings import Settings


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    helper = Path(__file__).resolve().parents[2] / "test_real_phase2" / "real_runtime.py"
    spec = importlib.util.spec_from_file_location("real_test_runtime", helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    folder = tmp_path / "test_real_phase2"
    folder.mkdir()
    (tmp_path / "tns").mkdir()
    (tmp_path / "tns" / "tnsnames.ora").write_text(
        "TEST_ALIAS=(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=localhost)(PORT=1521))"
        "(CONNECT_DATA=(SERVICE_NAME=test)))", encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "ORACLE_PASSWORD=fixture-only\nSYNTHETIC_MODE=true\n", encoding="utf-8",
    )
    (folder / ".env").write_text(
        "DATABASE_URL=postgresql+psycopg2://localhost/sentinel\n"
        "SYNTHETIC_MODE=false\nSCHEDULER_ENABLED=true\nTNS_ADMIN=tns\n", encoding="utf-8",
    )
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "__file__", str(folder / "real_runtime.py"))
    monkeypatch.setitem(Settings.model_config, "env_file", Settings.model_config["env_file"])
    # Track every environment value that the helper intentionally clears.
    for key in list(module.os.environ):
        if key.upper() in {name.upper() for name in Settings.model_fields}:
            monkeypatch.setenv(key, module.os.environ[key])
    monkeypatch.setenv("DATABASE_URL", "sqlite:///old-demo.db")
    monkeypatch.setenv("SYNTHETIC_MODE", "true")
    monkeypatch.setenv("TNS_ADMIN", "old-demo")
    return module


def test_real_launcher_uses_layered_files_not_inherited_demo(runtime):
    settings = runtime.load_settings()
    assert settings.synthetic_mode is False
    assert settings.scheduler_enabled is True
    assert settings.database_url == "postgresql+psycopg2://localhost/sentinel"
    assert settings.oracle_password == "fixture-only"
    assert settings.tns_admin == str((runtime.ROOT / "tns").resolve())


def test_real_launcher_requires_tns_file(runtime):
    (runtime.ROOT / "tns" / "tnsnames.ora").unlink()
    with pytest.raises(ValueError, match="tnsnames"):
        runtime.load_settings()


def test_real_launcher_rejects_sqlite(runtime):
    (Path(runtime.__file__).parent / ".env").write_text(
        "DATABASE_URL=sqlite:///wrong.db\nSYNTHETIC_MODE=false\nSCHEDULER_ENABLED=true\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="PostgreSQL"):
        runtime.load_settings()


def test_real_launcher_requires_credentials(runtime):
    (runtime.ROOT / ".env").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="ORACLE_PASSWORD"):
        runtime.load_settings()


def test_real_launcher_preflight_lists_alias_without_connection(runtime, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["real_runtime.py", "preflight"])
    runtime.main()
    assert json.loads(capsys.readouterr().out) == {"aliases": ["TEST_ALIAS"]}
