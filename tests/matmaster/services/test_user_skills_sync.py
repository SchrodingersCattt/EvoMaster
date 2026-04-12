"""Tests for src.services.user_skills_sync."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from matmaster.config.exp import ExpConfig, ExpSkillsConfig


def _zip_bytes_with_skill_md(name: bytes = b'test-skill') -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr(
            'SKILL.md',
            b'---\nname: %s\n---\nbody\n' % name,
        )
    return buf.getvalue()


def test_merge_user_skill_roots_into_exp_config_appends_paths(tmp_path: Path) -> None:
    from src.services.user_skills_sync import merge_user_skill_roots_into_exp_config

    extra = tmp_path / 'a'
    extra.mkdir()
    cfg = ExpConfig(
        skills=ExpSkillsConfig(
            enabled=True,
            skills_root=['matmaster/skills'],
        )
    )
    out = merge_user_skill_roots_into_exp_config(cfg, [extra])
    roots = out.skills.skills_root
    assert isinstance(roots, list)
    assert 'matmaster/skills' in roots
    assert str(extra.resolve()) in roots


def test_materialize_empty_user_id() -> None:
    from src.services.user_skills_sync import materialize_user_skills_for_run

    assert materialize_user_skills_for_run('', project_root=Path('/tmp')) == []
    assert materialize_user_skills_for_run('   ', project_root=Path('/tmp')) == []


@patch('src.services.user_skills_sync.MATMASTER_TOOLS_SERVER', 'https://ts.example.com')
@patch('src.services.user_skills_sync.httpx.Client')
def test_materialize_downloads_and_extracts(
    client_cls: MagicMock, tmp_path: Path
) -> None:
    from src.services.user_skills_sync import materialize_user_skills_for_run

    zbytes = _zip_bytes_with_skill_md()

    list_resp = MagicMock()
    list_resp.raise_for_status = MagicMock()
    list_resp.json.return_value = {
        'code': 0,
        'data': [
            {
                'id': 'skill1',
                'status': 'ready',
                'artifact_id': 'art1',
                'content_sha256': None,
            }
        ],
    }

    dl_resp = MagicMock()
    dl_resp.raise_for_status = MagicMock()
    dl_resp.content = zbytes

    def _client_cm(*_a, **_k):
        inst = MagicMock()
        inst.__enter__ = MagicMock(return_value=inst)
        inst.__exit__ = MagicMock(return_value=False)

        def _get(url: str, **_kw):
            if '/users/' in url and url.endswith('/skills'):
                return list_resp
            if '/artifacts/' in url and url.endswith('/download'):
                return dl_resp
            raise AssertionError(f'unexpected url {url!r}')

        inst.get.side_effect = _get
        return inst

    client_cls.side_effect = _client_cm

    roots = materialize_user_skills_for_run('user-1', project_root=tmp_path)
    assert len(roots) == 1
    skill_dir = roots[0]
    assert (skill_dir / 'SKILL.md').is_file()
