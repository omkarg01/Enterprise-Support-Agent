from core.release_paths import behaviour_changes, check

BEHAVIOUR = [
    "config/models.yaml",
    "config/thresholds.yaml",
    "policies/tickets.cedar",
    "src/agents/prompts/billing.md",
    "src/core/orchestrator/decisions.py",
]
CODE_ONLY = ["README.md", "src/core/orchestrator/statechart.py", "tests/test_x.py"]


def test_behaviour_paths_are_flagged():
    assert behaviour_changes(BEHAVIOUR + CODE_ONLY) == sorted(BEHAVIOUR)


def test_code_only_changes_pass():
    assert behaviour_changes(CODE_ONLY) == []
    assert check(CODE_ONLY, allowed=False) == 0


def test_behaviour_change_needs_scheduled_release():
    assert check(BEHAVIOUR, allowed=False) == 1
    assert check(BEHAVIOUR, allowed=True) == 0
