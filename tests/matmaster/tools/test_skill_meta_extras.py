from matmaster.skills.registry import Skill, SkillMetaInfo


class TestSkillMetaInfoExtras:
    def test_extras_captures_unknown_fields(self):
        info = SkillMetaInfo(
            name="test-skill",
            description="A test skill",
            mcp_server="mat_sg",
            extras={"custom_flag": "true"},
        )
        assert info.mcp_server == "mat_sg"
        assert info.extras["custom_flag"] == "true"
        assert "mcp_server" not in info.extras

    def test_extras_defaults_empty(self):
        info = SkillMetaInfo(name="test", description="desc")
        assert info.extras == {}
        assert info.mcp_server is None
        assert info.skill_type is None
        assert info.depends_on == []

    def test_parse_frontmatter_extras(self, tmp_path):
        """SKILL.md with mcp_server frontmatter populates meta_info.mcp_server."""
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: A test\nmcp_server: mat_sg\n---\nBody\n"
        )
        skill = Skill(skill_dir)
        assert skill.meta_info.mcp_server == "mat_sg"
        assert "mcp_server" not in skill.meta_info.extras

    def test_parse_frontmatter_no_extras(self, tmp_path):
        """SKILL.md without extra fields has empty extras and no mcp_server."""
        skill_dir = tmp_path / "plain-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: plain-skill\ndescription: A plain skill\n---\nBody\n"
        )
        skill = Skill(skill_dir)
        assert skill.meta_info.extras == {}
        assert skill.meta_info.mcp_server is None
