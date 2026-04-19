# Presentation Outline

## 1. Demo (5 min)
- Open landing page → upload `sql_injection.py`
- Watch 4 agents activate in real-time on the IDE right panel
- Show live thoughts streaming, tool calls appearing, findings dropping in
- Click a finding → Monaco squiggle jumps to line
- Click "Fix" → inline popup with explanation → Apply Fix → editor updates

## 2. Architecture (3 min)
- Coordinator delegates Security + Bug agents in parallel (asyncio.gather)
- Agent Drive: why shared FS instead of in-memory passing (separate sandboxes)
- Fix Agent: fastapply → py_compile verification → rollback on failure

## 3. Evaluation Results (2 min)
- Run `python evaluate.py` live
- Show F1 score, fix success rate per file

## 4. Blaxel Integration (2 min)
- No Docker needed — `blaxel/py-app:latest` replaces it
- Agent Drive mounts same codebase to all 4 sandboxes
- Log streaming → SSE events → UI in <100ms

## Interview Questions Prep
- Why asyncio.gather for parallel agents? → halves wall-clock time, no inter-dependency
- Why SSE over WebSocket? → simpler, reconnect with lastEventId built-in
- How to scale to 1000 concurrent reviews? → Redis event bus, horizontal Blaxel sandbox scaling
- What's the most fragile part? → JSON parsing of LLM findings output (mitigated by regex extraction)
