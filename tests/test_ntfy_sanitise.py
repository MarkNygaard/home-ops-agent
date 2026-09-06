"""Tests for ntfy body sanitising.

These pin the two ways a model-authored notification arrives unreadable, both
seen in production: a malformed tool call leaking its own XML into the body,
and a double-escaped body showing literal backslash-n instead of line breaks.
"""

from home_ops_agent.agent.tools.ntfy import _clean_body, _clean_title

BS = chr(92)
LIT_N = BS + "n"
NL = chr(10)

# Verbatim shape of a real notification that reached the user's phone.
LEAKED = (
    "PR #922 has been merged (commit 941e53c)"
    + LIT_N
    + LIT_N
    + "Component: kube-prometheus-stack (patch)"
    + LIT_N
    + "Change: Updates kube-state-metrics Docker tag to v8.4.2"
    + LIT_N
    + "CI: All checks passed"
    + LIT_N
    + LIT_N
    + "Flux will reconcile the new version shortly."
    + chr(34)
    + ","
    + NL
    + '<parameter name="tags">["white_check_mark", "robot"]</parameter>'
    + NL
    + '<parameter name="priority">3</parameter>'
    + NL
    + "</invoke>"
)


def test_tool_call_xml_never_reaches_the_user():
    out = _clean_body(LEAKED)
    assert "<parameter" not in out
    assert "invoke" not in out


def test_the_dangling_json_quote_goes_with_it():
    """The model was mid-string when it switched syntax; the stray quote and
    comma are part of the plumbing, not the sentence."""
    assert _clean_body(LEAKED).endswith("shortly.")


def test_double_escaped_body_becomes_real_line_breaks():
    out = _clean_body(LEAKED)
    assert LIT_N not in out
    # The report reads as five lines with two blank separators, not one
    # unbroken paragraph -- which is the whole point of the fix.
    assert out.splitlines() == [
        "PR #922 has been merged (commit 941e53c)",
        "",
        "Component: kube-prometheus-stack (patch)",
        "Change: Updates kube-state-metrics Docker tag to v8.4.2",
        "CI: All checks passed",
        "",
        "Flux will reconcile the new version shortly.",
    ]


def test_a_body_that_is_already_correct_is_left_alone():
    """A real newline means it was never double-escaped, so a literal
    backslash-n in it is deliberate and must survive."""
    text = "Line one" + NL + "Line two mentioning " + LIT_N + " on purpose"
    assert _clean_body(text) == text


def test_ordinary_body_is_untouched():
    assert _clean_body("Auto-merged PR #921") == "Auto-merged PR #921"


def test_title_never_carries_a_newline():
    """ntfy sends the title as an HTTP header; a newline there fails the whole
    request and loses the notification."""
    assert NL not in _clean_title("Multi" + NL + "line title")


def test_title_strips_xml_too():
    assert "<parameter" not in _clean_title('Done<parameter name="x">1</parameter>')


def test_empty_body_gets_a_fallback():
    """ntfy rejects an empty body, which would drop the notification."""
    assert _clean_body("   ") == "(empty notification)"


def test_non_string_body_does_not_explode():
    assert _clean_body(None) == "None"
