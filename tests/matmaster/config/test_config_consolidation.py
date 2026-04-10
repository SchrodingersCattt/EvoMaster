"""Tests for PlaygroundManager config-dir routing."""

from __future__ import annotations


class TestConfigDirRouting:
    def test_config_dir_is_config(self, tmp_path):
        from matmaster.core.playground import PlaygroundManager

        mgr = PlaygroundManager(tmp_path)
        assert mgr._config_dir == tmp_path / "config"

    def test_get_or_create_uses_config_dir(self, tmp_path):
        """Verify get_or_create() reads config from config/."""
        import yaml

        from matmaster.core.playground import Playground, PlaygroundManager

        mgr = PlaygroundManager(tmp_path)
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        # Write a minimal config to parse
        (cfg_dir / "config.yaml").write_text(
            yaml.dump({"session": {"type": "local", "local": {"timeout": 42}}})
        )
        pg = mgr.get_or_create("test-session")
        assert isinstance(pg, Playground)
        assert pg._session_type == "local"
        # Session config comes from the YAML file we wrote
        assert pg._session_config.get("timeout") == 42
