from matmaster.manifests.scanner import SkillHitRecord, scan_skill_hits


def test_scan_skill_hits_deduplicates_by_first_seen_order() -> None:
    events = [
        {"id": 1, "type": "query", "content": "skip"},
        {
            "id": 2,
            "type": "skill_hit",
            "content": {"skill_name": "pxrd", "created_at": "older"},
            "created_at": "2026-01-01T00:00:00",
        },
        {"id": 3, "type": "skill_hit", "content": {"skill_name": "mlip"}},
        {"id": 4, "type": "skill_hit", "content": {"skill_name": "pxrd"}},
        {"id": 5, "type": "skill_hit", "content": {"skill_name": ""}},
    ]

    records = scan_skill_hits(events)

    assert records == [
        SkillHitRecord(skill_name="pxrd", event_id=2, timestamp="2026-01-01T00:00:00"),
        SkillHitRecord(skill_name="mlip", event_id=3, timestamp=None),
    ]


def test_scan_skill_hits_accepts_legacy_string_content() -> None:
    assert scan_skill_hits([{"id": "7", "type": "skill_hit", "content": "search"}]) == [
        SkillHitRecord(skill_name="search", event_id=7, timestamp=None)
    ]
