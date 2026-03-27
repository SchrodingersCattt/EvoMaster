import json

import matmaster.tools.schema_cache as schema_cache_module
from matmaster.tools.schema_cache import ToolSchemaCache


class TestToolSchemaCache:
    def test_load_existing_cache(self, tmp_path):
        schemas = [
            {'name': 'build_bulk', 'description': 'Build bulk', 'input_schema': {}},
            {
                'name': 'build_surface',
                'description': 'Build surface',
                'input_schema': {},
            },
        ]
        (tmp_path / 'mat_sg.json').write_text(json.dumps(schemas))
        cache = ToolSchemaCache(tmp_path)
        result = cache.load('mat_sg')
        assert result is not None
        assert len(result) == 2
        assert result[0]['name'] == 'build_bulk'

    def test_load_missing_cache(self, tmp_path):
        cache = ToolSchemaCache(tmp_path)
        result = cache.load('nonexistent')
        assert result is None

    def test_load_empty_dir(self, tmp_path):
        cache = ToolSchemaCache(tmp_path)
        result = cache.load('any_server')
        assert result is None

    def test_load_applies_tool_exclude_filter_when_configured(
        self, tmp_path, monkeypatch
    ):
        schemas = [
            {'name': 'build_bulk', 'description': 'Build bulk', 'input_schema': {}},
            {
                'name': 'get_structure_info',
                'description': 'Get info',
                'input_schema': {},
            },
        ]
        (tmp_path / 'mat_sg.json').write_text(json.dumps(schemas))
        monkeypatch.setattr(
            schema_cache_module,
            '_SCHEMA_CACHE_TOOL_EXCLUDE',
            {'mat_sg': {'get_structure_info'}},
        )
        cache = ToolSchemaCache(tmp_path)
        result = cache.load('mat_sg')
        assert result is not None
        assert [tool['name'] for tool in result] == ['build_bulk']
