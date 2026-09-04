"""The coordinator's tool definitions and control-loop scaffolding.

The coordinator used to run a fixed five-step script: delegate, collect,
fix, done. It could not react to what it found, could not decide that a
file needed a different kind of scrutiny, and could not respond to a fix
that failed to verify.

This module gives it a real decision surface. The coordinator is run as
an LLM tool-calling loop over the tools declared here; each tool maps to
something the system can actually do, and every call is reported to the
UI so the reasoning is visible rather than implied.
"""

from __future__ import annotations

from typing import Any

# Bounded so a confused model cannot spin: the loop stops at whichever
# limit is reached first.
MAX_ITERATIONS = 14
MAX_FIX_ATTEMPTS = 3

COORDINATOR_SYSTEM_PROMPT = """You are the coordinator of a multi-agent \
code review system. You do not analyse code yourself — you decide what \
work to do, dispatch specialists, judge what comes back, and decide when \
the review is complete.

Your specialists:
- `security`: injection, secrets, auth flaws, unsafe deserialisation, \
path traversal.
- `bug_detection`: null dereferences, logic errors, resource leaks, \
races, swallowed errors.

Working method:
1. Start by reading the code. Form a view of what it does and where it \
is most likely to be wrong. State that view plainly — it is shown to the \
user.
2. Dispatch specialists with a specific `focus` describing what you want \
scrutinised in THIS code. A focus like "check the SQL string built from \
`user_id` on line 4" is far more useful than "look for security issues".
3. When findings return, judge them. Use `dismiss_finding` on anything \
that is not a real defect — a false positive wastes the user's time, and \
you are the last line of defence against one.
4. Fix what is worth fixing with `apply_fix`. If a fix fails to verify, \
you are told why. Decide whether to retry with better guidance or to \
give up on that finding and say so.
5. Call `finish_review` when the work is genuinely done.

Be decisive and concise. Prefer a small number of well-aimed actions \
over exhaustive ones. Never claim work you did not do."""


def build_tools() -> list[dict[str, Any]]:
    """OpenAI-style tool schemas for the coordinator's control loop."""
    return [
        {
            "type": "function",
            "function": {
                "name": "inspect_code",
                "description": (
                    "Read the source under review, with line numbers. Call "
                    "this first to ground your plan in the actual code."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file": {
                            "type": "string",
                            "description": "Filename, or omit for all files.",
                        }
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_specialist",
                "description": (
                    "Dispatch a specialist agent to analyse the code. Give a "
                    "focus naming the specific constructs you want examined."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "agent": {
                            "type": "string",
                            "enum": ["security", "bug_detection"],
                        },
                        "focus": {
                            "type": "string",
                            "description": (
                                "What to scrutinise in this code, specifically."
                            ),
                        },
                    },
                    "required": ["agent", "focus"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "dismiss_finding",
                "description": (
                    "Reject a finding as a false positive. The user never "
                    "sees it. Give the reason it is not a real defect."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "finding_id": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["finding_id", "reason"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "apply_fix",
                "description": (
                    "Have the fix agent patch a finding. The patch must "
                    "compile and must provably remove the flagged pattern, "
                    "or it is rolled back and you are told why. Optionally "
                    "pass guidance to steer the attempt."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "finding_id": {"type": "string"},
                        "guidance": {
                            "type": "string",
                            "description": (
                                "Advice for this attempt — especially useful "
                                "after a previous attempt failed."
                            ),
                        },
                    },
                    "required": ["finding_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_command",
                "description": (
                    "Run a read-only shell command in the sandbox to check "
                    "your own work (e.g. python3 -m py_compile, grep). "
                    "Commands that modify state are rejected."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "purpose": {
                            "type": "string",
                            "description": "Why you are running it.",
                        },
                    },
                    "required": ["command", "purpose"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finish_review",
                "description": (
                    "End the review. Call this once the findings are judged "
                    "and the worthwhile fixes are applied."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": (
                                "One or two sentences on what was found and "
                                "fixed, for the user."
                            ),
                        }
                    },
                    "required": ["summary"],
                },
            },
        },
    ]


# Commands the coordinator may run to inspect its own work. Anything that
# writes, installs, or reaches the network is refused.
_ALLOWED_COMMAND_PREFIXES = (
    "python3 -m py_compile",
    "python -m py_compile",
    "python3 -c",
    "grep",
    "cat",
    "ls",
    "head",
    "tail",
    "wc",
)

_FORBIDDEN_FRAGMENTS = (
    ">", ">>", "|", ";", "&&", "rm ", "mv ", "curl", "wget", "pip",
    "chmod", "sudo", "$(", "`",
)


def command_is_allowed(command: str) -> tuple[bool, str]:
    """Whether the coordinator may run ``command`` in the sandbox."""
    stripped = command.strip()
    if not stripped:
        return False, "empty command"
    for fragment in _FORBIDDEN_FRAGMENTS:
        if fragment in stripped:
            return False, f"contains {fragment!r}; only read-only commands are allowed"
    if not stripped.startswith(_ALLOWED_COMMAND_PREFIXES):
        return False, "only read-only inspection commands are allowed"
    return True, ""
