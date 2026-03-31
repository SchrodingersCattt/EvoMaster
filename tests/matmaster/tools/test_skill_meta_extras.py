from evomaster.skills.base import Skill, SkillMetaInfo


class TestSkillMetaInfoExtras:
    def test_extras_captures_unknown_fields(self):
        info = SkillMetaInfo(
            name="test-skill",
            description="A test skill",
            extras={"mcp_server": "mat_sg", "custom_flag": "true"},
        )
        assert info.extras["mcp_server"] == "mat_sg"
        assert info.extras["custom_flag"] == "true"

    def test_extras_defaults_empty(self):
        info = SkillMetaInfo(name="test", description="desc")
        assert info.extras == {}

    def test_parse_frontmatter_extras(self, tmp_path):
        """SKILL.md with mcp_server in frontmatter puts it in extras."""
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: A test\nmcp_server: mat_sg\n---\nBody\n"
        )
        skill = Skill(skill_dir)
        assert skill.meta_info.extras.get("mcp_server") == "mat_sg"

    def test_parse_frontmatter_no_extras(self, tmp_path):
        """SKILL.md without extra fields has empty extras."""
        skill_dir = tmp_path / "plain-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: plain-skill\ndescription: A plain skill\n---\nBody\n"
        )
        skill = Skill(skill_dir)
        assert skill.meta_info.extras == {}
