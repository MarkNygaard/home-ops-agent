"""The PR agent must not merge or notify for itself.

Four consecutive merges arrived under four different title formats, and one
leaked the tail of a malformed tool call into the notification body, because
the model was composing notifications nothing had asked it for. Merging and
notifying are deterministic and belong in code; the verdict is the model's job.
"""

from home_ops_agent.agent.prompts import DEFAULT_PR_REVIEW
from home_ops_agent.workers.pr_monitor import WITHHELD_FROM_PR_AGENT


class _Tool:
    def __init__(self, name):
        self.name = name


def _register(tools):
    """The filter applied in check_prs."""
    return [t for t in tools if t.name not in WITHHELD_FROM_PR_AGENT]


def test_the_agent_cannot_merge_a_pull_request():
    """Merging is gated by _is_safe_to_auto_merge, which is readable and
    testable. A model deciding for itself is neither."""
    assert "github_merge_pr" in WITHHELD_FROM_PR_AGENT


def test_the_agent_cannot_send_notifications():
    """Every notification it sent was unprompted improvisation, which is how
    tool-call syntax ended up on the user's phone."""
    assert "ntfy_publish" in WITHHELD_FROM_PR_AGENT


def test_the_filter_removes_exactly_those_two():
    tools = [
        _Tool("github_get_pr"),
        _Tool("github_merge_pr"),
        _Tool("ntfy_publish"),
        _Tool("kubernetes_list_pods"),
    ]
    kept = [t.name for t in _register(tools)]
    assert kept == ["github_get_pr", "kubernetes_list_pods"]


def test_reviewing_and_commenting_survive():
    """The model keeps the judgement work; only the mechanical work moves."""
    tools = [_Tool(n) for n in ("github_get_pr_files", "github_post_pr_comment")]
    assert len(_register(tools)) == 2


def test_prompt_asks_for_the_verdict_the_gate_looks_for():
    """_is_safe_to_auto_merge requires the literal words SAFE_TO_MERGE in the
    stored review. If the prompt stops asking for them, every PR silently
    stops merging -- so the two must be pinned together."""
    assert "SAFE_TO_MERGE" in DEFAULT_PR_REVIEW


def test_prompt_tells_the_model_it_does_not_merge_or_notify():
    lowered = DEFAULT_PR_REVIEW.lower()
    assert "you do not merge" in lowered
    assert "you do not send notifications" in lowered


def test_the_chat_cannot_send_notifications():
    """A chat reply arrived as an ntfy push as well.

    The cluster context tells every agent to report what it did over ntfy —
    right for an unattended alert, and in a chat it duplicates the answer the
    person is already reading. Withheld rather than reworded, because the
    instruction lives in an editable prompt this deployment has customised, so
    changing the default text would not have reached it.
    """
    from home_ops_agent.api.chat import WITHHELD_FROM_CHAT

    assert "ntfy_publish" in WITHHELD_FROM_CHAT


def test_the_chat_keeps_the_tools_it_needs():
    """The withhold list is a scalpel: everything else the chat could do
    before, it still can."""
    from home_ops_agent.api.chat import WITHHELD_FROM_CHAT

    for name in ("k8s_get_pods", "k8s_restart_workload", "flux_reconcile", "code_fix"):
        assert name not in WITHHELD_FROM_CHAT


def test_the_withholding_is_applied_not_just_declared():
    """A frozenset nothing filters on is decoration."""
    import inspect

    from home_ops_agent.api import chat

    assert "WITHHELD_FROM_CHAT" in inspect.getsource(chat.websocket_chat)
