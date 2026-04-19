# Multi-Agent Code Review IDE

A Cursor-style IDE that uses 4 AI agents (powered by OpenAI GPT-4o + Blaxel sandboxes) to perform real-time security analysis, bug detection, and autonomous fix application on Python codebases.

## Features

- **Cursor IDE UI** — Monaco editor, file tree, live agent panel, findings feed
- **4 Agents** — Coordinator, Security, Bug Detection, Fix/Patch
- **Real-time streaming** — SSE events stream all agent thoughts, tool calls, and findings live
- **Autonomous fixes** — Blaxel `fastapply` edits files at 2000+ tokens/sec, verified via `py_compile`
- **Blaxel sandboxes** — No Docker needed; Blaxel provides the Python runtime
- **Agent Drive** — Shared POSIX filesystem mounts to all agent sandboxes simultaneously

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

Required keys:
| Key | Where to get |
|---|---|
| `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com) |
| `BL_API_KEY` | [blaxel.ai](https://blaxel.ai) |
| `BL_WORKSPACE` | Your Blaxel workspace name |
| `MORPH_API_KEY` | [morphllm.com](https://morphllm.com) (for fastapply) |

### 3. Start the server

```bash
uvicorn src.api.main:app --reload --port 8000
```

### 4. Open the IDE

```
http://localhost:8000
```

Upload files or paste code → click **Analyze Code** → watch agents work in real-time.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    COORDINATOR AGENT (gpt-4o)                │
│  Creates plan → delegates in parallel → merges findings      │
└─────┬─────────────────────┬─────────────────────┬───────────┘
      │                     │                     │
      ▼                     ▼                     ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐
│ SECURITY     │  │ BUG          │  │ FIX AGENT            │
│ (gpt-4o)     │  │ DETECTION    │  │ fastapply + py_compile│
│              │  │ (gpt-4o)     │  │ + sandbox exec       │
└──────────────┘  └──────────────┘  └──────────────────────┘
       ↕ Agent Drive (shared POSIX FS — Blaxel us-was-1)
       ↕ Blaxel py-app sandbox (Python 3.11, us-pdx-1)
```

### Key Files

| Path | Purpose |
|---|---|
| `src/agents/coordinator.py` | Orchestration: plan, delegate, consolidate |
| `src/agents/security_agent.py` | SQL injection, XSS, hardcoded secrets, auth flaws |
| `src/agents/bug_agent.py` | Null refs, logic errors, race conditions, leaks |
| `src/agents/fix_agent.py` | Proposes + applies + verifies fixes |
| `src/events/bus.py` | Async fan-out event bus → SSE |
| `src/sandbox/manager.py` | Blaxel sandbox lifecycle + streaming exec |
| `src/storage/agent_drive.py` | Shared filesystem for inter-agent communication |
| `src/api/main.py` | FastAPI entrypoint |
| `ui/ide.html` + `ui/src/ide.js` | Cursor-style IDE |

## Running Evaluation

```bash
python evaluate.py \
  --input test_cases/buggy_samples/ \
  --expected test_cases/expected_findings.json \
  --output metrics.json
```

Targets: **F1 ≥ 0.70**, **Fix Rate ≥ 50%**

## Event Schema

All agent events conform to:
```json
{
  "event_type": "finding_discovered",
  "agent_id": "security",
  "timestamp": "2026-04-19T10:23:45.123Z",
  "session_id": "uuid",
  "data": { ... }
}
```

Event types: `plan_created`, `agent_delegated`, `agent_started`, `thinking`, `tool_call_start`, `tool_call_result`, `finding_discovered`, `fix_proposed`, `fix_verified`, `agent_completed`, `findings_consolidated`, `review_completed`, `error`
