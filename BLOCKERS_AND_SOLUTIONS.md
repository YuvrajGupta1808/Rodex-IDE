# Blockers and Solutions

## Resolved

### 1. Blaxel SDK Python vs TypeScript
**Blocker**: Blaxel documentation examples are primarily TypeScript; Python SDK availability unclear.  
**Solution**: Used `openai-agents[blaxel]` Python package for agent+sandbox integration. Added `_MockSandbox` fallback in `SandboxManager` for local development without Blaxel credentials.

### 2. Agent Drive region restriction
**Blocker**: Agent Drive only available in `us-was-1` during preview.  
**Solution**: In-memory dict fallback in `AgentDrive` when Blaxel SDK unavailable. Production path uses `us-was-1`.

### 3. fastapply requires separate API key
**Blocker**: Blaxel `codegen.fastapply` requires `MORPH_API_KEY` or `RELACE_API_KEY`.  
**Solution**: Added `_local_apply` fallback that calls OpenAI GPT-4o directly to apply the edit when Morph is unavailable. Documented in `.env.example`.

## Open Issues

*(Document any blockers encountered during development)*
