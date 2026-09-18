"""Tests for run progress — what the workflow diagram highlights.

This is cosmetic, which is exactly why it needs pinning: a progress report must
never be the thing that breaks a PR review, and a highlight left on after a run
died is worse than no highlight at all.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from home_ops_agent.workers import progress


def setup_function():
    progress.finish()


def test_nothing_running_reports_nothing():
    """None is what makes the diagram fall back to showing the shape of the
    flow, rather than a stale highlight from the last run."""
    assert progress.snapshot() is None


def test_a_run_reports_where_it_is():
    progress.begin("pr_review", "3 open PR(s)")
    progress.step("check_pr", "PR #1046")

    snap = progress.snapshot()
    assert snap["agent"] == "pr_review"
    assert snap["step"] == "check_pr"
    assert snap["detail"] == "PR #1046"


def test_steps_without_a_run_are_ignored_rather_than_raising():
    """Every call site is inside a worker doing real work. Reporting progress
    must never be what fails a review."""
    progress.step("decide")  # no run in flight
    progress.note_tool("github_get_pr")
    assert progress.snapshot() is None


def test_a_tool_means_different_things_in_different_flows():
    """`k8s_get_pods` is "check pods" while triaging an alert, and is not a step
    the PR diagram draws at all. A flat map lit the wrong circle whenever a
    review happened to look at the cluster."""
    progress.begin("alert")
    progress.note_tool("k8s_get_pods")
    assert progress.snapshot()["step"] == "check_pods"

    progress.begin("pr_review")
    progress.note_tool("k8s_get_pods")
    assert progress.snapshot()["step"] == "start"


def test_tool_calls_drive_the_steps_inside_a_review():
    """The worker cannot report these itself — they all happen inside one model
    call, and which tool is used when is the model's decision."""
    progress.begin("pr_review")

    progress.note_tool("github_get_pr")
    assert progress.snapshot()["step"] == "check_pr"

    progress.note_tool("github_get_pr_files")
    assert progress.snapshot()["step"] == "read_diff"

    progress.note_tool("github_get_release")
    assert progress.snapshot()["step"] == "release_notes"


def test_an_unmapped_tool_leaves_the_step_alone():
    """Most tools are detail the diagram does not draw. Flickering through every
    one of them would be noise rather than progress."""
    progress.begin("pr_review")
    progress.step("read_diff")
    progress.note_tool("k8s_get_pods")
    assert progress.snapshot()["step"] == "read_diff"


def test_detail_is_kept_when_a_step_does_not_supply_one():
    progress.begin("pr_review")
    progress.step("check_pr", "PR #1046")
    progress.step("decide")
    assert progress.snapshot()["detail"] == "PR #1046"


def test_finishing_clears_the_highlight():
    progress.begin("pr_review")
    progress.step("code_fix")
    progress.finish()
    assert progress.snapshot() is None


def test_a_run_that_stops_reporting_expires():
    """A crashed or cancelled run must not leave a circle pulsing forever."""
    progress.begin("pr_review")
    progress.step("code_fix")
    progress._current.updated_at = datetime.now(UTC) - progress.STALE_AFTER - timedelta(seconds=1)

    assert progress.snapshot() is None
    # And the expiry is not just hidden — the run is actually dropped.
    assert progress._current is None


def test_a_new_run_replaces_the_previous_one():
    progress.begin("pr_review")
    first = progress.snapshot()["run_id"]
    progress.begin("alert")
    snap = progress.snapshot()

    assert snap["run_id"] != first
    assert snap["agent"] == "alert"


def test_every_step_the_workers_report_is_drawn_somewhere():
    """A step with no node is a run the diagram silently fails to show.

    Parsed out of the TSX rather than duplicated here, so the two cannot drift.
    """
    import re
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "web"
        / "src"
        / "components"
        / "dashboard"
        / "agent-flow.tsx"
    ).read_text(encoding="utf-8")
    # Two forms: inline on a node, and the NOTIFY_STEPS lookup that gives the
    # three duplicated Notify terminals their distinct steps.
    drawn = set(re.findall(r"(?:step|n\d): '([a-z_]+)'", source))

    reported = {
        "check_pr",
        "read_diff",
        "release_notes",
        "decide",
        "in_scope",
        "code_fix",
        "re_review",
        "deep_review",
        "merge_safe",
        "merge_after_fix",
        "merge_after_deep",
        "notify_scope",
        "notify_rereview",
        "notify_deep",
    }
    assert reported <= drawn, f"steps with no node: {sorted(reported - drawn)}"


def test_the_tool_map_only_names_tools_that_exist():
    """A typo here is silent: the step simply never fires."""
    from home_ops_agent.agent.skills import init_registry, registry

    init_registry()
    known = set()
    for skill in registry.get_all():
        for tool in skill.get_tools({}):
            known.add(tool.name)

    named = {name for per_agent in progress.TOOL_STEPS.values() for name in per_agent}
    assert named <= known, sorted(named - known)


def test_the_indicator_does_not_go_backwards():
    """Observed on the first live run: check_pr, read_diff, check_pr.

    The model read the diff and then went back to checking CI, which is a
    reasonable thing to do and a confusing thing to watch — the highlight jumped
    back two circles.
    """
    progress.begin("pr_review")
    progress.step("check_pr", "PR #1057")
    progress.step("read_diff")
    progress.step("check_pr")
    assert progress.snapshot()["step"] == "read_diff"


def test_a_new_pr_starts_over():
    """Only within one subject. The next PR in the cycle genuinely does begin
    again at check_pr."""
    progress.begin("pr_review")
    progress.step("read_diff", "PR #1057")
    progress.step("check_pr", "PR #1058")
    snap = progress.snapshot()
    assert snap["step"] == "check_pr"
    assert snap["detail"] == "PR #1058"


def test_an_unordered_step_always_applies():
    """The ordering is a refinement, not a gate — a step added later must work
    without being listed."""
    progress.begin("pr_review")
    progress.step("re_review", "PR #1")
    progress.step("something_new")
    assert progress.snapshot()["step"] == "something_new"


def test_going_backwards_still_counts_as_activity():
    """The step is refused but the run is not stale — it is doing work."""
    progress.begin("pr_review")
    progress.step("read_diff", "PR #1")
    before = progress.snapshot()["updated_at"]
    progress.step("check_pr")
    assert progress.snapshot()["updated_at"] >= before


def test_every_ordered_step_is_one_a_worker_reports():
    """A name that drifted would silently stop ordering that step."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "home_ops_agent"
    reported = set()
    for path in (root / "workers").glob("*.py"):
        reported |= set(
            re.findall(r'progress\.step\(\s*"([a-z_]+)"', path.read_text(encoding="utf-8"))
        )
    for per_agent in progress.TOOL_STEPS.values():
        reported |= set(per_agent.values())
    reported.add("start")

    assert set(progress.STEP_ORDER) == reported, {
        "ordered but never reported": sorted(set(progress.STEP_ORDER) - reported),
        "reported but unordered": sorted(reported - set(progress.STEP_ORDER)),
    }


def test_both_flows_light_their_own_nodes():
    """Every step the alert workers report needs a node in the alert diagram,
    the same guarantee the PR flow has. A step with no node is a run the diagram
    silently fails to show.
    """
    import re
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "web"
        / "src"
        / "components"
        / "dashboard"
        / "agent-flow.tsx"
    ).read_text(encoding="utf-8")
    drawn = set(re.findall(r"(?:step|n\d): '([a-z_]+)'", source))

    alert_steps = {
        "check_pods",
        "read_logs",
        "metrics",
        "triage",
        "alert_fix",
        "apply_fix",
        "notify_fixed",
        "notify_user",
        "ignore",
    }
    assert alert_steps <= drawn, f"alert steps with no node: {sorted(alert_steps - drawn)}"
