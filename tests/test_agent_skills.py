from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
SKILLS = ROOT / "skills"
REFERENCE_LINK = re.compile(r"\[[^]]+\]\((references/[^)#]+)(?:#[^)]+)?\)")


def _frontmatter(skill_file: Path) -> dict[str, str]:
    lines = skill_file.read_text(encoding="utf-8").splitlines()
    assert lines and lines[0] == "---"
    closing = lines.index("---", 1)
    fields: dict[str, str] = {}
    for line in lines[1:closing]:
        key, separator, value = line.partition(":")
        assert separator, f"invalid frontmatter line in {skill_file}: {line}"
        fields[key.strip()] = value.strip()
    return fields


def test_agent_skills_are_discoverable_and_self_contained() -> None:
    skill_dirs = sorted(path for path in SKILLS.iterdir() if path.is_dir())
    assert skill_dirs

    for skill_dir in skill_dirs:
        skill_file = skill_dir / "SKILL.md"
        fields = _frontmatter(skill_file)
        assert fields == {
            "name": skill_dir.name,
            "description": fields["description"],
        }
        assert fields["description"]

        content = skill_file.read_text(encoding="utf-8")
        assert "[TODO" not in content
        for reference in REFERENCE_LINK.findall(content):
            assert (skill_dir / reference).is_file(), reference

        for discovery_root in (ROOT / ".agents" / "skills", ROOT / ".claude" / "skills"):
            link = discovery_root / skill_dir.name
            assert link.is_symlink()
            assert link.resolve() == skill_dir.resolve()

        metadata = skill_dir / "agents" / "openai.yaml"
        assert metadata.is_file()
        assert f"${skill_dir.name}" in metadata.read_text(encoding="utf-8")
