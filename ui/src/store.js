/**
 * Reactive store: single source of truth for IDE state.
 * Dispatch events → update state → notify subscribers.
 */

export const store = {
  sessionId: null,
  files: {},         // filename -> content
  activeFile: null,
  agents: {          // agentId -> { state, findingCount }
    coordinator: { state: 'idle', findingCount: 0 },
    security:    { state: 'idle', findingCount: 0 },
    bug_detection: { state: 'idle', findingCount: 0 },
    fix:         { state: 'idle', findingCount: 0 },
  },
  planSteps: [],     // [{step, description, status}]
  thoughts: [],      // [{agentId, text}]  (last 100)
  // Structured activity timeline: coordinator reasoning, delegated steps,
  // decisions and retries, in the order they happened. Steps nest the
  // activity that occurred while they were open.
  activity: [],      // [{kind, agentId, ...}]
  _openStepId: null,
  toolCalls: [],     // [{agentId, toolName, inputs, output, durationMs, ok}]
  findings: [],      // Finding objects
  fixProposals: {},  // findingId -> FixProposal
  fixVerifications: {}, // findingId -> FixVerification
  fixedFiles: {},    // filename -> fixed source code
  _subscribers: [],

  subscribe(fn) {
    this._subscribers.push(fn);
    return () => { this._subscribers = this._subscribers.filter(s => s !== fn); };
  },

  notify(key) {
    this._subscribers.forEach(fn => fn(key, this));
  },

  handleEvent(event) {
    const { event_type, agent_id, data } = event;

    switch (event_type) {
      case 'plan_created':
        this.planSteps = (data.steps || []).map(s => ({ ...s, status: 'pending' }));
        this._setAgentState('coordinator', 'thinking');
        this.notify('plan');
        break;

      case 'agent_delegated':
        (data.agents || []).forEach(id => this._setAgentState(id, 'thinking'));
        this._advancePlan();
        this.notify('agents');
        break;

      case 'agent_started':
        this._setAgentState(agent_id, 'thinking');
        this.notify('agents');
        break;

      case 'thinking':
        this.thoughts = [...this.thoughts.slice(-99), { agentId: agent_id, text: data.text }];
        this._pushActivity({ kind: 'thought', agentId: agent_id, text: data.text });
        if (data.state) this._setAgentState(agent_id, data.state);
        this.notify('thoughts');
        break;

      case 'coordinator_reasoning':
        this.thoughts = [...this.thoughts.slice(-99), { agentId: agent_id, text: data.text }];
        this._pushActivity({ kind: 'reasoning', agentId: agent_id, text: data.text });
        this.notify('thoughts');
        break;

      case 'step_started':
        this._pushActivity({
          kind: 'step', agentId: agent_id, stepId: data.step_id,
          title: data.title, detail: data.detail, outcome: null,
          summary: '', children: [],
        });
        this.notify('thoughts');
        break;

      case 'step_completed': {
        const step = this._findStep(data.step_id);
        if (step) {
          step.outcome = data.outcome;
          step.summary = data.summary;
        }
        this._openStepId = null;
        this.notify('thoughts');
        break;
      }

      case 'decision_made':
        this._pushActivity({
          kind: 'decision', agentId: agent_id,
          choice: data.choice, rationale: data.rationale,
        });
        this.notify('thoughts');
        break;

      case 'retry_scheduled':
        this._pushActivity({
          kind: 'retry', agentId: agent_id, target: data.target,
          attempt: data.attempt, maxAttempts: data.max_attempts,
          reason: data.reason,
        });
        this.notify('thoughts');
        break;

      case 'tool_call_start':
        this._setAgentState(agent_id, 'tool_calling');
        this.toolCalls = [{
          agentId: agent_id,
          toolName: data.tool_name,
          inputs: data.inputs,
          output: null,
          durationMs: null,
          ok: null,
          ts: new Date().toLocaleTimeString(),
        }, ...this.toolCalls.slice(0, 49)];
        this.notify('tools');
        this.notify('agents');
        break;

      case 'tool_call_result': {
        const latest = this.toolCalls.find(t => t.toolName === data.tool_name && t.output === null);
        if (latest) {
          latest.output = data.output;
          latest.durationMs = data.duration_ms;
          latest.ok = true;
        }
        if (data.state) this._setAgentState(agent_id, data.state);
        this.notify('tools');
        break;
      }

      case 'finding_discovered':
        this.findings = [...this.findings, data];
        this.agents[agent_id] = { ...this.agents[agent_id], findingCount: (this.agents[agent_id]?.findingCount || 0) + 1 };
        this.notify('findings');
        break;

      case 'fix_proposed':
        this.fixProposals[data.finding_id] = data;
        this.notify('fixes');
        break;

      case 'fix_verified':
        this.fixVerifications[data.finding_id] = data;
        this.notify('fixes');
        break;

      case 'agent_completed':
        this._setAgentState(agent_id, 'completed');
        this._advancePlan();
        this.notify('agents');
        break;

      case 'findings_consolidated':
        this._advancePlan();
        this.notify('consolidated');
        break;

      case 'review_completed':
        this.fixedFiles = data?.fixed_files || {};
        this._setAllCompleted();
        this.notify('plan');
        this.notify('completed');
        break;

      case 'error':
        this._setAgentState(agent_id, 'error');
        this.notify('agents');
        break;
    }
  },

  // Activity entries land inside the currently open step when there is
  // one, so a delegation reads as a unit of work with its own detail
  // rather than as loose lines interleaved with everything else.
  _pushActivity(entry) {
    if (entry.kind === 'step') {
      this.activity = [...this.activity, entry];
      this._openStepId = entry.stepId;
      return;
    }
    const open = this._openStepId ? this._findStep(this._openStepId) : null;
    if (open) {
      open.children = [...open.children, entry];
      this.activity = [...this.activity];   // new identity so renderers update
    } else {
      this.activity = [...this.activity, entry];
    }
  },

  _findStep(stepId) {
    for (const entry of this.activity) {
      if (entry.kind === 'step' && entry.stepId === stepId) return entry;
    }
    return null;
  },

  _setAgentState(agentId, state) {
    if (this.agents[agentId]) {
      this.agents[agentId] = { ...this.agents[agentId], state };
    }
  },

  _advancePlan() {
    const idx = this.planSteps.findIndex(s => s.status === 'pending');
    if (idx !== -1) {
      this.planSteps[idx] = { ...this.planSteps[idx], status: 'active' };
      if (idx > 0) this.planSteps[idx - 1] = { ...this.planSteps[idx - 1], status: 'done' };
    }
    this.notify('plan');
  },

  _setAllCompleted() {
    this.planSteps = this.planSteps.map(s => ({ ...s, status: 'done' }));
    Object.keys(this.agents).forEach(id => this._setAgentState(id, 'completed'));
  },
};
