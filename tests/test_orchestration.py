"""Tests for the coordinator's tool surface and command guard."""

import pytest

from src.agents.orchestration import build_tools, command_is_allowed


def test_every_tool_the_loop_dispatches_is_declared():
    names = {tool["function"]["name"] for tool in build_tools()}
    assert names == {
        "inspect_code",
        "run_specialist",
        "dismiss_finding",
        "apply_fix",
        "run_command",
        "finish_review",
    }


def test_specialist_tool_only_offers_real_agents():
    tools = {t["function"]["name"]: t["function"] for t in build_tools()}
    agents = tools["run_specialist"]["parameters"]["properties"]["agent"]["enum"]
    assert agents == ["security", "bug_detection"]


def test_required_arguments_are_declared():
    tools = {t["function"]["name"]: t["function"] for t in build_tools()}
    assert tools["run_specialist"]["parameters"]["required"] == ["agent", "focus"]
    assert tools["dismiss_finding"]["parameters"]["required"] == [
        "finding_id",
        "reason",
    ]


@pytest.mark.parametrize(
    "command",
    [
        "python3 -m py_compile /tmp/m.py",
        "grep -n 'execute' /tmp/m.py",
        "cat /tmp/m.py",
        "wc -l /tmp/m.py",
    ],
)
def test_read_only_inspection_is_allowed(command):
    allowed, _ = command_is_allowed(command)
    assert allowed is True


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "cat /tmp/m.py > /tmp/other.py",
        "curl https://example.com",
        "pip install requests",
        "grep x /tmp/m.py; rm /tmp/m.py",
        "echo $(whoami)",
        "python3 -m py_compile /tmp/m.py && rm /tmp/m.py",
        "chmod 777 /tmp",
        "",
    ],
)
def test_state_changing_or_network_commands_are_refused(command):
    allowed, reason = command_is_allowed(command)
    assert allowed is False
    assert reason


def test_an_unlisted_binary_is_refused_even_if_harmless_looking():
    allowed, _ = command_is_allowed("git status")
    assert allowed is False
