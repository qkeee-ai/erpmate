"""distribution.yaml owns exactly what the repo ships.

`hermes profile update` replaces every `distribution_owned` path with
rmtree + copytree (hermes-agent hermes_cli/profile_distribution.py,
_copy_dist_payload). So an owned path must never contain a skill the
profile writes itself (skills/qkeee-erp-learned/), and every skill the
repo ships must be owned, or an update never delivers it.

Run with `python -m pytest tests` from the repo root.
"""

from pathlib import Path, PurePosixPath

import yaml

REPO = Path(__file__).resolve().parent.parent


def _owned() -> list[PurePosixPath]:
    manifest = yaml.safe_load((REPO / "distribution.yaml").read_text(encoding="utf-8"))
    return [PurePosixPath(str(p).strip().strip("/")) for p in manifest["distribution_owned"]]


def _is_owned(rel: PurePosixPath, owned: list[PurePosixPath]) -> bool:
    return any(rel == o or o in rel.parents for o in owned)


def test_every_shipped_skill_is_owned():
    owned = _owned()
    unowned = []
    for skill_md in sorted((REPO / "skills").rglob("SKILL.md")):
        rel = PurePosixPath(skill_md.parent.relative_to(REPO).as_posix())
        if not _is_owned(rel, owned):
            unowned.append(str(rel))
    assert unowned == [], f"skills not in distribution_owned: {unowned}"


def test_every_shipped_category_description_is_owned():
    owned = _owned()
    unowned = [
        str(rel) for rel in (
            PurePosixPath(p.relative_to(REPO).as_posix())
            for p in sorted((REPO / "skills").glob("*/DESCRIPTION.md")))
        if not _is_owned(rel, owned)
    ]
    assert unowned == [], f"category DESCRIPTION.md not in distribution_owned: {unowned}"


def test_skills_root_is_not_owned_wholesale():
    assert PurePosixPath("skills") not in _owned()


def test_learned_skills_are_not_owned():
    # Instance Notes (ADR 0005, D12) belong to the Deployment.
    owned = _owned()
    assert not _is_owned(PurePosixPath("skills/qkeee-erp-learned/demo/SKILL.md"), owned)
