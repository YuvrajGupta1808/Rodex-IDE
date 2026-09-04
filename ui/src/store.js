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
  lastError: null,
  running: false,
  telemetry: null,
  summary: '',
  dismissed: [],
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
        this.running = true;
        this._setAgentState(agent_id, 'thinking');
        this.notify('agents');
        this.notify('plan');
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
        // The coordinator chooses its own steps, so the execution plan is
        // built from what it actually does rather than a fixed script.
        this.planSteps = [
          ...this.planSteps,
          {
            step: this.planSteps.length + 1,
            stepId: data.step_id,
            description: data.title,
            status: 'active',
          },
        ];
        this.notify('thoughts');
        this.notify('plan');
        break;

      case 'step_completed': {
        const step = this._findStep(data.step_id);
        if (step) {
          step.outcome = data.outcome;
          step.summary = data.summary;
        }
        this._openStepId = null;
        this.planSteps = this.planSteps.map(s =>
          s.stepId === data.step_id
            ? { ...s, status: data.outcome === 'failed' ? 'failed' : 'done' }
            : s
        );
        this.notify('thoughts');
        this.notify('plan');
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
          stepId: data.step_id || null,
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
        // The backend already measures the review and records what the
        // coordinator concluded and rejected; surface it instead of
        // discarding it.
        this.telemetry = data?.telemetry || this.telemetry;
        this.summary = data?.summary || '';
        this.dismissed = data?.dismissed || [];
        this.running = false;
        this._setAllCompleted();
        this.notify('plan');
        this.notify('cost');
        this.notify('completed');
        break;

      case 'telemetry_updated':
        // Cost accrues during the review, not only at the end.
        this.telemetry = data || null;
        this.notify('cost');
        break;

      case 'error':
        this._setAgentState(agent_id, 'error');
        // Surface the failure in the timeline too. Tinting a status dot
        // red left the panel looking merely idle, with the run button
        // stuck on "Analyzing..." and no way to tell what went wrong.
        this._pushActivity({
          kind: 'failure', agentId: agent_id, text: data?.message || 'Unknown error',
        });
        this.lastError = data?.message || 'Unknown error';
        this.running = false;
        this.notify('thoughts');
        this.notify('agents');
        this.notify('failed');
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
    // Steps now open and close on their own events; nothing to advance.
    this.notify('plan');
  },

  _setAllCompleted() {
    // Leave failed steps failed — a completed review may still contain
    // work that did not succeed.
    this.planSteps = this.planSteps.map(s =>
      s.status === 'failed' ? s : { ...s, status: 'done' }
    );
    Object.keys(this.agents).forEach(id => this._setAgentState(id, 'completed'));
  },
};
