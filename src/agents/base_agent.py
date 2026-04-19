from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..events.emitter import EventEmitter
from ..events.schemas import Finding, FixProposal, FixVerification
from ..sandbox.manager import SandboxManager
from ..storage.agent_drive import AgentDrive


@dataclass
class AgentResult:
    agent_id: str
    findings: list[Finding] = field(default_factory=list)
    fix_proposals: list[FixProposal] = field(default_factory=list)
    fix_verifications: list[FixVerification] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SharedContext:
    session_id: str
    files: dict[str, str]  # filename -> source
    existing_findings: list[Finding] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseAgent(ABC):
    def __init__(
        self,
        agent_id: str,
        emitter: EventEmitter,
        sandbox_manager: SandboxManager,
        agent_drive: AgentDrive,
    ) -> None:
        self.agent_id = agent_id
        self.emitter = emitter
        self.sandbox_manager = sandbox_manager
        self.agent_drive = agent_drive

    @abstractmethod
    def system_prompt(self) -> str:
        raise NotImplementedError

    @abstractmethod
    async def analyze(self, context: SharedContext) -> AgentResult:
        raise NotImplementedError

    def _make_finding_id(self) -> str:
        return str(uuid.uuid4())[:8]

    async def _run_openai_agent(
        self,
        context: SharedContext,
        instructions: str,
        tools: list[Any],
    ) -> str:
        """Run an OpenAI Agents SDK agent and return the final text response."""
        try:
            from agents import Agent, Runner  # type: ignore
            agent = Agent(
                name=self.agent_id,
                instructions=instructions,
                tools=tools,
                model="gpt-4o",
            )
            result = await Runner.run(agent, input=self._build_prompt(context))
            return result.final_output or ""
        except ImportError:
            # Fallback: direct OpenAI API call
            return await self._run_openai_direct(context, instructions)

    async def _run_openai_direct(
        self, context: SharedContext, instructions: str
    ) -> str:
        from openai import AsyncOpenAI
        client = AsyncOpenAI()
        files_text = "\n\n".join(
            f"=== {name} ===\n{content}"
            for name, content in context.files.items()
        )
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": f"Analyze the following Python code:\n\n{files_text}"},
            ],
            temperature=0,
        )
        return response.choices[0].message.content or ""

    def _build_prompt(self, context: SharedContext) -> str:
        files_text = "\n\n".join(
            f"=== {name} ===\n{content}"
            for name, content in context.files.items()
        )
        return f"Session: {context.session_id}\n\nFiles to analyze:\n\n{files_text}"
