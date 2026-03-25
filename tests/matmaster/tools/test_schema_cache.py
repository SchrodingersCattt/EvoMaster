import json
from pathlib import Path

from matmaster.tools.schema_cache import ToolSchemaCache


class TestToolSchemaCache:
    def test_load_existing_cache(self, tmp_path):
        schemas = [
            {"name": "build_bulk", "description": "Build bulk", "input_schema": {}},
            {"name": "build_surface", "description": "Build surface", "input_schema": {}},
        ]
        (tmp_path / "mat_sg.json").write_text(json.dumps(schemas))
        cache = ToolSchemaCache(tmp_path)
        result = cache.load("mat_sg")
        assert result is not None
        assert len(result) == 2
        assert result[0]["name"] == "build_bulk"

    def test_load_missing_cache(self, tmp_path):
        cache = ToolSchemaCache(tmp_path)
        result = cache.load("nonexistent")
        assert result is None

    def test_load_empty_dir(self, tmp_path):
        cache = ToolSchemaCache(tmp_path)
        result = cache.load("any_server")
        assert result is None
