"""Notifications have to be readable and non-duplicating on a phone.

Two faults seen in production, from one PR review: the body arrived with its
markdown syntax intact and cut off mid-word, and the same PR produced two
ATTENTION pushes two minutes apart despite "outcomes only" being selected.
"""

from home_ops_agent.agent.tools.ntfy import _clean_body, _flatten_markdown
from home_ops_agent.workers import notifications
from home_ops_agent.workers.pr_monitor import _summarise

NL = chr(10)


# --- markdown is decoration the phone apps cannot render ---------------------


def test_headings_lose_their_hashes_but_keep_their_words():
    assert _flatten_markdown("## Summary" + NL + "text") == "Summary" + NL + "text"


def test_bold_loses_its_asterisks():
    assert _flatten_markdown("**Risk Level**: HIGH") == "Risk Level: HIGH"


def test_inline_code_loses_its_backticks():
    assert _flatten_markdown("bumps `ghcr.io/siderolabs/kubelet` now") == (
        "bumps ghcr.io/siderolabs/kubelet now"
    )


def test_flattening_runs_on_every_published_body():
    """It belongs at the publish choke point, not at each caller."""
    out = _clean_body("### Key Findings" + NL + "**Classification**: cluster")
    assert "#" not in out
    assert "*" not in out
    assert "Key Findings" in out
    assert "Classification: cluster" in out


def test_prose_with_arithmetic_asterisks_is_not_mangled():
    """Only paired ** is bold; a lone asterisk is left alone."""
    assert _flatten_markdown("2 * 3 = 6") == "2 * 3 = 6"


# --- truncation should look deliberate --------------------------------------


def test_long_text_is_cut_at_a_boundary_not_mid_word():
    out = _summarise("word " * 300)
    assert out.endswith("[...]")
    assert not out.replace(" [...]", "").endswith("wor")


def test_short_text_is_left_exactly_alone():
    assert _summarise("Auto-merged PR #921") == "Auto-merged PR #921"


def test_a_paragraph_break_is_preferred_to_a_word_break():
    text = "First paragraph here." + NL + NL + ("filler " * 200)
    assert _summarise(text).startswith("First paragraph here.")


# --- one PR, one push --------------------------------------------------------


def test_attention_cannot_be_filtered_by_any_level():
    """Which is why a premature ATTENTION is worse than a premature anything
    else -- "outcomes only" cannot save the user from it."""
    for level in notifications.LEVELS:
        assert notifications.should_send(notifications.ATTENTION, level), level


def test_routine_is_dropped_at_the_default_level():
    assert not notifications.should_send(notifications.ROUTINE, "outcomes")
