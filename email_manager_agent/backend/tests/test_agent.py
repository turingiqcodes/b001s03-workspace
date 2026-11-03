"""Agent loop: step limit, tool validation, failures, Claude errors and result limits."""
import json

import pytest

from app.agent.agent_loop import Agent, limit_tool_result
from app.agent.tools import TOOLS_BY_NAME, claude_tool_definitions
from app.errors import BridgeError, ModelError
from app.sessions import SessionStore

from tests.conftest import FakeClaude, response, text_block, tool_block

SEARCH_RESULT = {"messages": [{"id": "m1", "from": "alice@example.com", "subject": "Report", "unread": True}]}


@pytest.fixture
def session(now_fn):
    return SessionStore(3600, now_fn).get_or_create(None)


def _agent(claude, ctx):
    return Agent(claude, ctx)


def _tool_results(claude: FakeClaude, request_index: int):
    return claude.requests[request_index][-1]["content"]


def test_read_tool_result_goes_back_to_claude(ctx, bridge, session):
    bridge.on("gmail.search", SEARCH_RESULT)
    claude = FakeClaude([
        response(tool_block("t1", "search_emails", {"query": "is:unread newer_than:1d"})),
        response(text_block("You have 1 unread email from alice.")),
    ])
    result = _agent(claude, ctx).run(session, "Find unread emails from today")
    assert result.reply == "You have 1 unread email from alice."
    assert result.steps == 2
    assert [r.public() for r in result.tool_calls] == [{"name": "search_emails", "kind": "read", "status": "ok", "message": ""}]
    sent = _tool_results(claude, 1)[0]
    assert sent["tool_use_id"] == "t1" and "is_error" not in sent
    assert json.loads(sent["content"])["messages"][0]["subject"] == "Report"
    assert bridge.calls == [("gmail.search", {"query": "is:unread newer_than:1d", "max_results": 10})]


def test_current_time_is_in_user_message_not_system_prompt(ctx, session):
    claude = FakeClaude([response(text_block("ok"))])
    _agent(claude, ctx).run(session, "hi")
    assert claude.requests[0][0]["content"][0]["text"].startswith("[Current time: Monday 2026-11-09 08:00 UTC]")


def test_step_limit_is_enforced(ctx, bridge, session, settings):
    settings.max_agent_steps = 3
    bridge.on("gmail.search", SEARCH_RESULT)
    claude = FakeClaude([response(tool_block(f"t{i}", "search_emails", {"query": "x"})) for i in range(3)])
    result = _agent(claude, ctx).run(session, "loop forever")
    assert result.stop_reason == "max_steps" and result.steps == 3
    assert len(claude.requests) == 3  # never a 4th call
    # The last tool results are kept and open the next user message, so history stays valid.
    assert session.carryover_blocks[0]["tool_use_id"] == "t2"
    claude.script.append(response(text_block("done")))
    _agent(claude, ctx).run(session, "continue")
    next_user = claude.requests[-1][-1]["content"]
    assert next_user[0]["type"] == "tool_result" and next_user[-1]["type"] == "text"


def test_unknown_tool_is_refused(ctx, bridge, session):
    claude = FakeClaude([response(tool_block("t1", "delete_all_email", {})), response(text_block("sorry"))])
    result = _agent(claude, ctx).run(session, "delete everything")
    sent = _tool_results(claude, 1)[0]
    assert sent["is_error"] is True and "Unknown tool" in sent["content"]
    assert result.tool_calls[0].status == "error"
    assert bridge.calls == []


@pytest.mark.parametrize("bad_input", [
    {"query": ""},                       # too short
    {"query": "x", "max_results": 500},  # over the limit
    {"query": "x", "surprise": True},    # unknown field
    "not an object",
])
def test_invalid_tool_arguments_are_reported(ctx, bridge, session, bad_input):
    claude = FakeClaude([response(tool_block("t1", "search_emails", bad_input)), response(text_block("retry"))])
    _agent(claude, ctx).run(session, "search")
    sent = _tool_results(claude, 1)[0]
    assert sent["is_error"] is True and sent["content"].startswith("Invalid arguments")
    assert bridge.calls == []


def test_tool_failure_is_reported_to_claude(ctx, bridge, session):
    bridge.on("gmail.search", BridgeError("Gmail is unavailable"))
    claude = FakeClaude([response(tool_block("t1", "search_emails", {"query": "x"})), response(text_block("Gmail failed"))])
    result = _agent(claude, ctx).run(session, "search")
    sent = _tool_results(claude, 1)[0]
    assert sent["is_error"] is True and "Gmail is unavailable" in sent["content"]
    assert result.tool_calls[0].message == "Gmail is unavailable"


def test_malformed_bridge_result_is_an_error(ctx, bridge, session):
    bridge.on("gmail.search", ["not", "a", "dict"])
    claude = FakeClaude([response(tool_block("t1", "search_emails", {"query": "x"})), response(text_block("hmm"))])
    _agent(claude, ctx).run(session, "search")
    assert "malformed" in _tool_results(claude, 1)[0]["content"]


def test_unexpected_tool_crash_does_not_leak(ctx, bridge, session):
    bridge.on("gmail.search", lambda p: 1 / 0)
    claude = FakeClaude([response(tool_block("t1", "search_emails", {"query": "x"})), response(text_block("hmm"))])
    _agent(claude, ctx).run(session, "search")
    content = _tool_results(claude, 1)[0]["content"]
    assert "Unexpected internal error" in content and "division" not in content


def test_claude_error_rolls_back_history(ctx, session):
    claude = FakeClaude([response(text_block("first"))])
    _agent(claude, ctx).run(session, "one")
    assert len(session.messages) == 2
    claude.script.append(ModelError("Claude is temporarily unavailable"))
    with pytest.raises(ModelError):
        _agent(claude, ctx).run(session, "two")
    assert len(session.messages) == 2  # failed turn removed; next prompt starts cleanly


def test_claude_error_cancels_operations_proposed_in_that_turn(ctx, session):
    claude = FakeClaude([
        response(tool_block("t1", "send_email", {"to": ["a@example.com"], "subject": "s", "body": "b"})),
        ModelError("boom"),
    ])
    with pytest.raises(ModelError):
        _agent(claude, ctx).run(session, "send")
    assert session.open_operations() == []
    assert list(session.operations.values())[0].status == "cancelled"


def test_tool_result_size_limit(ctx, bridge, session, settings):
    settings.tool_result_max_chars = 1000
    bridge.on("gmail.search", {"messages": [{"id": str(i), "snippet": "x" * 100} for i in range(100)]})
    claude = FakeClaude([response(tool_block("t1", "search_emails", {"query": "x"})), response(text_block("ok"))])
    _agent(claude, ctx).run(session, "search")
    content = _tool_results(claude, 1)[0]["content"]
    assert len(content) < 1200 and "[truncated:" in content


def test_limit_tool_result_keeps_short_text():
    assert limit_tool_result("abc", 10) == "abc"


def test_refusal_and_max_tokens_replies(ctx, session):
    claude = FakeClaude([response(stop_reason="refusal")])
    assert "declined" in _agent(claude, ctx).run(session, "x").reply
    claude.script.append(response(text_block("partial"), tool_block("t9", "search_emails", {"query": "x"}), stop_reason="max_tokens"))
    result = _agent(claude, ctx).run(session, "y")
    assert "cut short" in result.reply
    assert session.carryover_blocks[0]["tool_use_id"] == "t9"  # the unrun call still gets a result


def test_pause_turn_continues(ctx, session):
    claude = FakeClaude([response(text_block("thinking..."), stop_reason="pause_turn"), response(text_block("done"))])
    assert _agent(claude, ctx).run(session, "x").reply == "done"


def test_tool_definitions_are_well_formed():
    tools = claude_tool_definitions()
    names = [t["name"] for t in tools]
    assert len(names) == len(set(names))
    for tool in tools:
        assert tool["input_schema"]["type"] == "object"
        assert tool["input_schema"]["additionalProperties"] is False
    assert "title" in TOOLS_BY_NAME["create_event"].to_claude_tool()["input_schema"]["properties"]


def test_no_delete_or_trash_tools_exist():
    for name in TOOLS_BY_NAME:
        assert not any(word in name for word in ("delete", "trash", "remove_file", "purge"))


def test_unexpected_error_also_rolls_back_history(ctx, session):
    claude = FakeClaude([RuntimeError("boom")])
    with pytest.raises(RuntimeError):
        _agent(claude, ctx).run(session, "one")
    assert session.messages == []


def test_tool_use_stop_without_tool_blocks_ends_turn(ctx, session):
    claude = FakeClaude([response(text_block("odd"), stop_reason="tool_use")])
    result = _agent(claude, ctx).run(session, "x")
    assert result.reply == "odd" and len(claude.requests) == 1
