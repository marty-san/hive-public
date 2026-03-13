"""Proposal service: vote loop for agent addition/removal proposals.

Addition flow (propose_addition bid):
  1. Elaboration: proposing agent searches the agent library and decides:
       a. Match found  → propose adding that existing agent
       b. No match     → propose creating a new agent with composed characteristics
  2. Broadcast proposal.started (with is_new_agent flag)
  3. Debate phase: each non-proposer generates a brief statement (persisted)
  4. Vote phase: all current agents vote in parallel (Haiku structured tool call)
  5. Human vote: if human_votes=True, wait up to 120s via the vote endpoint
  6. Tally and decide
  7a. Approved + existing → add participant
  7b. Approved + new → proposer writes system prompt → auto-create agent → add participant
  8. Persist system message summarising outcome
  9. Broadcast proposal.resolved

Removal flow (propose_removal bid):
  Same flow without the elaboration step.
"""
import asyncio
import uuid
from typing import List, Optional, Tuple

import anthropic
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Agent, ConversationParticipant, Message
from app.services.websocket_manager import ws_manager
from app.services import proposal_state
from app.schemas import MessageResponse
from app.config import settings

logger = structlog.get_logger()

VOTE_HUMAN_TIMEOUT_SECONDS = 120.0


class ProposalService:
    """Orchestrates agent addition/removal proposals within a conversation."""

    # ── Public entry point ────────────────────────────────────────────────────

    async def run_proposal(
        self,
        proposer_id: str,
        proposal_type: str,              # "propose_addition" | "propose_removal"
        target_agent_id: Optional[str],  # For removal: agent to remove. For addition: may be None.
        need_description: str,           # Bid preview — describes the gap or removal rationale
        conversation_id: str,
        db: AsyncSession,
        participant_agents: List[Agent],
        participant_ids: List[str],
        all_agents: List[Agent],         # Full library, used for addition elaboration
        human_votes: bool = False,
    ) -> dict:
        """Run a complete proposal vote loop and return the outcome."""
        proposer = next((a for a in participant_agents if a.id == proposer_id), None)
        if not proposer:
            return {"approved": False, "outcome": "error", "reason": "proposer not found"}

        if proposal_type == "propose_removal":
            return await self._run_removal_proposal(
                proposer=proposer,
                target_agent_id=target_agent_id,
                rationale=need_description,
                conversation_id=conversation_id,
                db=db,
                participant_agents=participant_agents,
                participant_ids=participant_ids,
                human_votes=human_votes,
            )
        else:
            return await self._run_addition_proposal(
                proposer=proposer,
                need_description=need_description,
                conversation_id=conversation_id,
                db=db,
                participant_agents=participant_agents,
                participant_ids=participant_ids,
                all_agents=all_agents,
                human_votes=human_votes,
            )

    # ── Addition proposal ─────────────────────────────────────────────────────

    async def _run_addition_proposal(
        self,
        proposer: Agent,
        need_description: str,
        conversation_id: str,
        db: AsyncSession,
        participant_agents: List[Agent],
        participant_ids: List[str],
        all_agents: List[Agent],
        human_votes: bool,
    ) -> dict:
        proposal_id = str(uuid.uuid4())

        # ── Elaboration: search library, decide existing vs new ───────────────
        elaboration = await self._elaborate_addition(
            proposer=proposer,
            need_description=need_description,
            all_agents=all_agents,
            participant_ids=participant_ids,
        )

        is_new_agent = elaboration["type"] == "new"
        target_name = elaboration["name"]
        target_domain = elaboration.get("domain", "")
        full_rationale = need_description
        if elaboration.get("rationale") and elaboration["rationale"] != need_description:
            full_rationale = f"{need_description} — {elaboration['rationale']}"

        if is_new_agent:
            vote_question = (
                f"Create and add a new {target_domain} agent named {target_name}?"
            )
        else:
            vote_question = f"Add {target_name} ({target_domain}) to the conversation?"

        logger.info(
            "addition_proposal_elaborated",
            proposal_id=proposal_id,
            proposer=proposer.name,
            is_new_agent=is_new_agent,
            target_name=target_name,
        )

        # Debug event: show elaboration tool call result
        try:
            await ws_manager.send_debug_event(
                conversation_id=conversation_id,
                event_type="tool_call",
                data={
                    "tool": "select_candidate",
                    "agent_name": proposer.name,
                    "phase": "addition_elaboration",
                    "result": {
                        "type": elaboration["type"],
                        "name": target_name,
                        "domain": target_domain,
                        "rationale": elaboration.get("rationale", ""),
                    },
                },
            )
        except Exception:
            pass

        # ── Broadcast proposal.started ────────────────────────────────────────
        await ws_manager.broadcast_to_conversation(conversation_id, {
            "type": "proposal.started",
            "conversation_id": conversation_id,
            "proposal_id": proposal_id,
            "proposal_type": "propose_addition",
            "proposer_name": proposer.name,
            "target_agent_name": target_name,
            "target_agent_domain": target_domain,
            "is_new_agent": is_new_agent,
            "rationale": full_rationale,
            "vote_question": vote_question,
        })

        # ── Debate, vote, tally ───────────────────────────────────────────────
        approved, agent_vote_names, human_vote, decision_reason, approve_count, total_agents = \
            await self._run_vote_loop(
                proposal_id=proposal_id,
                proposal_type="propose_addition",
                proposer=proposer,
                target_name=target_name,
                target_domain=target_domain,
                rationale=full_rationale,
                is_new_agent=is_new_agent,
                conversation_id=conversation_id,
                db=db,
                participant_agents=participant_agents,
                human_votes=human_votes,
            )

        # ── Execute outcome ───────────────────────────────────────────────────
        outcome_content = ""
        if approved:
            try:
                if is_new_agent:
                    # Compose system prompt → create agent → add to conversation
                    new_system_prompt = await self._compose_system_prompt(
                        proposer=proposer,
                        agent_name=target_name,
                        agent_domain=target_domain,
                        rationale=full_rationale,
                        participant_agents=participant_agents,
                    )
                    new_agent = await self._create_agent(
                        name=target_name,
                        domain=target_domain,
                        system_prompt=new_system_prompt,
                        model=proposer.model,
                        db=db,
                    )
                    await self._add_participant(conversation_id, new_agent.id, db)
                    outcome_content = (
                        f"Proposal approved ({approve_count}/{total_agents} agents) — "
                        f"Created new agent {new_agent.name} ({target_domain}) "
                        f"and added to the conversation."
                    )
                else:
                    existing_agent_id = elaboration["agent_id"]
                    await self._add_participant(conversation_id, existing_agent_id, db)
                    outcome_content = (
                        f"Proposal approved ({approve_count}/{total_agents} agents) — "
                        f"{target_name} has joined the conversation."
                    )
                if decision_reason == "human_fast_tracked":
                    outcome_content = outcome_content.replace(
                        "Proposal approved", "Proposal fast-tracked by human"
                    )
            except Exception as e:
                logger.error("addition_proposal_execution_error", error=str(e))
                outcome_content = f"Proposal approved but execution failed: {e}"
                approved = False
        else:
            if decision_reason == "human_vetoed":
                outcome_content = f"Proposal vetoed by human — {target_name} not added."
            else:
                outcome_content = (
                    f"Proposal rejected ({approve_count}/{total_agents} approved) — "
                    f"{target_name} not added."
                )

        return await self._finalise(
            proposal_id=proposal_id,
            proposal_type="propose_addition",
            approved=approved,
            decision_reason=decision_reason,
            agent_vote_names=agent_vote_names,
            human_vote=human_vote,
            outcome_content=outcome_content,
            conversation_id=conversation_id,
            db=db,
        )

    # ── Removal proposal ──────────────────────────────────────────────────────

    async def _run_removal_proposal(
        self,
        proposer: Agent,
        target_agent_id: Optional[str],
        rationale: str,
        conversation_id: str,
        db: AsyncSession,
        participant_agents: List[Agent],
        participant_ids: List[str],
        human_votes: bool,
    ) -> dict:
        proposal_id = str(uuid.uuid4())

        if not target_agent_id:
            return {"approved": False, "outcome": "skipped", "reason": "no target specified for removal"}

        target_result = await db.execute(select(Agent).where(Agent.id == target_agent_id))
        target_agent = target_result.scalar_one_or_none()
        if not target_agent:
            return {"approved": False, "outcome": "skipped", "reason": "target agent not found"}

        if target_agent_id not in participant_ids:
            return {"approved": False, "outcome": "skipped", "reason": "agent not in conversation"}

        logger.info(
            "removal_proposal_started",
            proposal_id=proposal_id,
            proposer=proposer.name,
            target=target_agent.name,
        )

        await ws_manager.broadcast_to_conversation(conversation_id, {
            "type": "proposal.started",
            "conversation_id": conversation_id,
            "proposal_id": proposal_id,
            "proposal_type": "propose_removal",
            "proposer_name": proposer.name,
            "target_agent_name": target_agent.name,
            "target_agent_domain": target_agent.expertise_domain,
            "is_new_agent": False,
            "rationale": rationale,
            "vote_question": f"Remove {target_agent.name} from the conversation?",
        })

        approved, agent_vote_names, human_vote, decision_reason, approve_count, total_agents = \
            await self._run_vote_loop(
                proposal_id=proposal_id,
                proposal_type="propose_removal",
                proposer=proposer,
                target_name=target_agent.name,
                target_domain=target_agent.expertise_domain,
                rationale=rationale,
                is_new_agent=False,
                conversation_id=conversation_id,
                db=db,
                participant_agents=participant_agents,
                human_votes=human_votes,
            )

        outcome_content = ""
        if approved:
            try:
                await self._remove_participant(conversation_id, target_agent_id, db)
                outcome_content = (
                    f"Proposal approved ({approve_count}/{total_agents} agents) — "
                    f"{target_agent.name} has left the conversation."
                )
                if decision_reason == "human_fast_tracked":
                    outcome_content = outcome_content.replace(
                        "Proposal approved", "Proposal fast-tracked by human"
                    )
            except Exception as e:
                logger.error("removal_proposal_execution_error", error=str(e))
                outcome_content = f"Proposal approved but execution failed: {e}"
                approved = False
        else:
            if decision_reason == "human_vetoed":
                outcome_content = (
                    f"Proposal vetoed by human — {target_agent.name} stays in the conversation."
                )
            else:
                outcome_content = (
                    f"Proposal rejected ({approve_count}/{total_agents} approved) — "
                    f"{target_agent.name} stays in the conversation."
                )

        return await self._finalise(
            proposal_id=proposal_id,
            proposal_type="propose_removal",
            approved=approved,
            decision_reason=decision_reason,
            agent_vote_names=agent_vote_names,
            human_vote=human_vote,
            outcome_content=outcome_content,
            conversation_id=conversation_id,
            db=db,
        )

    # ── Shared vote loop ──────────────────────────────────────────────────────

    async def _run_vote_loop(
        self,
        proposal_id: str,
        proposal_type: str,
        proposer: Agent,
        target_name: str,
        target_domain: str,
        rationale: str,
        is_new_agent: bool,
        conversation_id: str,
        db: AsyncSession,
        participant_agents: List[Agent],
        human_votes: bool,
    ) -> Tuple[bool, dict, Optional[str], str, int, int]:
        """
        Run debate → agent votes → optional human vote → tally.

        Returns:
            (approved, agent_vote_names, human_vote, decision_reason,
             approve_count, total_agents)
        """
        # ── Debate ────────────────────────────────────────────────────────────
        other_agents = [a for a in participant_agents if a.id != proposer.id]
        if other_agents:
            debate_tasks = [
                self._generate_debate_statement(
                    agent=agent,
                    proposal_type=proposal_type,
                    proposer_name=proposer.name,
                    target_name=target_name,
                    target_domain=target_domain,
                    rationale=rationale,
                    is_new_agent=is_new_agent,
                )
                for agent in other_agents
            ]
            debate_statements = await asyncio.gather(*debate_tasks)

            for agent, statement in zip(other_agents, debate_statements):
                if not statement:
                    continue
                msg = Message(
                    conversation_id=conversation_id,
                    sender_type="agent",
                    sender_id=agent.id,
                    content=statement,
                    extra_data={"proposal_id": proposal_id, "proposal_phase": "debate"},
                )
                db.add(msg)
                await db.commit()
                await db.refresh(msg)
                await ws_manager.send_message_event(
                    conversation_id,
                    MessageResponse(**msg.__dict__, sender_name=agent.name).model_dump(mode="json"),
                )

        # ── Agent votes ───────────────────────────────────────────────────────
        # Build debate summary so votes are informed by the debate
        debate_summary = ""
        if other_agents:
            debate_lines = []
            for agent, statement in zip(other_agents, debate_statements):
                if statement:
                    debate_lines.append(f"  {agent.name}: {statement}")
            if debate_lines:
                debate_summary = "Debate statements:\n" + "\n".join(debate_lines)

        vote_tasks = [
            self._collect_agent_vote(
                agent=agent,
                proposal_type=proposal_type,
                proposer_name=proposer.name,
                target_name=target_name,
                target_domain=target_domain,
                rationale=rationale,
                is_new_agent=is_new_agent,
                debate_summary=debate_summary,
            )
            for agent in participant_agents
        ]
        raw_votes = await asyncio.gather(*vote_tasks)

        agent_votes = {a.id: v for a, v in zip(participant_agents, raw_votes)}
        agent_vote_names = {a.name: v for a, v in zip(participant_agents, raw_votes)}

        await ws_manager.broadcast_to_conversation(conversation_id, {
            "type": "proposal.votes_cast",
            "conversation_id": conversation_id,
            "proposal_id": proposal_id,
            "agent_votes": agent_vote_names,
        })

        # ── Human vote ────────────────────────────────────────────────────────
        human_vote: Optional[str] = None
        if human_votes:
            vote_event = proposal_state.create_vote_event(proposal_id)
            await ws_manager.broadcast_to_conversation(conversation_id, {
                "type": "proposal.vote_requested",
                "conversation_id": conversation_id,
                "proposal_id": proposal_id,
                "proposal_type": proposal_type,
                "proposer_name": proposer.name,
                "target_agent_name": target_name,
                "target_agent_domain": target_domain,
                "is_new_agent": is_new_agent,
                "rationale": rationale,
                "agent_votes": agent_vote_names,
                "timeout_seconds": int(VOTE_HUMAN_TIMEOUT_SECONDS),
            })
            try:
                await asyncio.wait_for(vote_event.wait(), timeout=VOTE_HUMAN_TIMEOUT_SECONDS)
                human_vote = proposal_state.get_human_vote(proposal_id)
            except asyncio.TimeoutError:
                human_vote = None
            finally:
                proposal_state.clear_proposal(proposal_id)

        # ── Tally ─────────────────────────────────────────────────────────────
        approve_count = sum(1 for v in agent_votes.values() if v == "approve")
        total_agents = len(agent_votes)
        agent_majority = approve_count > total_agents / 2

        if human_votes:
            if human_vote == "approve":
                approved, decision_reason = True, "human_fast_tracked"
            elif human_vote == "reject":
                approved, decision_reason = False, "human_vetoed"
            else:
                approved = agent_majority
                decision_reason = "agent_majority_human_abstained"
        else:
            approved = agent_majority
            decision_reason = "agent_majority"

        logger.info(
            "proposal_tallied",
            proposal_id=proposal_id,
            approve_count=approve_count,
            total_agents=total_agents,
            human_vote=human_vote,
            approved=approved,
            decision_reason=decision_reason,
        )

        return approved, agent_vote_names, human_vote, decision_reason, approve_count, total_agents

    # ── Finalise: persist + broadcast ─────────────────────────────────────────

    async def _finalise(
        self,
        proposal_id: str,
        proposal_type: str,
        approved: bool,
        decision_reason: str,
        agent_vote_names: dict,
        human_vote: Optional[str],
        outcome_content: str,
        conversation_id: str,
        db: AsyncSession,
    ) -> dict:
        system_msg = Message(
            conversation_id=conversation_id,
            sender_type="system",
            content=outcome_content,
            extra_data={
                "proposal_id": proposal_id,
                "proposal_phase": "result",
                "approved": approved,
            },
        )
        db.add(system_msg)
        await db.commit()
        await db.refresh(system_msg)

        await ws_manager.send_message_event(
            conversation_id,
            MessageResponse(**system_msg.__dict__, sender_name=None).model_dump(mode="json"),
        )

        await ws_manager.broadcast_to_conversation(conversation_id, {
            "type": "proposal.resolved",
            "conversation_id": conversation_id,
            "proposal_id": proposal_id,
            "approved": approved,
            "decision_reason": decision_reason,
            "agent_votes": agent_vote_names,
            "human_vote": human_vote,
            "outcome": outcome_content,
        })

        return {
            "approved": approved,
            "agent_votes": agent_vote_names,
            "human_vote": human_vote,
            "decision_reason": decision_reason,
        }

    # ── Elaboration: search library, decide existing vs new ───────────────────

    async def _elaborate_addition(
        self,
        proposer: Agent,
        need_description: str,
        all_agents: List[Agent],
        participant_ids: List[str],
    ) -> dict:
        """
        Proposing agent reviews the available agent library and decides:
        - Selects an existing agent if there is a good match, OR
        - Defines a new agent (name + domain) if no good match exists.

        Returns a dict:
          {"type": "existing", "agent_id": str, "name": str, "domain": str, "rationale": str}
          {"type": "new", "name": str, "domain": str, "rationale": str}
        """
        available = [a for a in all_agents if a.id not in participant_ids]

        agent_list_text = "\n".join(
            f"  - ID: {a.id} | Name: {a.name} | Domain: {a.expertise_domain}"
            for a in available
        ) or "  (No agents currently in the library — you must define a new one)"

        select_tool = {
            "name": "select_candidate",
            "description": "Select the best agent to add, or define a new one if no good match exists",
            "input_schema": {
                "type": "object",
                "properties": {
                    "selection_type": {
                        "type": "string",
                        "enum": ["existing", "new"],
                        "description": (
                            "existing: an agent in the library is a strong enough match; "
                            "new: no agent fits well enough — define a new one"
                        ),
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "ID of the existing agent (only when selection_type=existing)",
                    },
                    "proposed_name": {
                        "type": "string",
                        "description": "Name for the new agent (only when selection_type=new)",
                    },
                    "proposed_domain": {
                        "type": "string",
                        "description": "Expertise domain for the new agent (only when selection_type=new)",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "One sentence explaining why this is the best choice",
                    },
                },
                "required": ["selection_type", "rationale"],
            },
        }

        try:
            client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    system=(
                        f"You are {proposer.name}, an AI agent with expertise in "
                        f"{proposer.expertise_domain}.\n\n"
                        f"You have identified a gap in the current conversation: {need_description}\n\n"
                        f"Available agents in the library:\n{agent_list_text}\n\n"
                        "Review the available agents carefully. Select an existing agent if they are "
                        "a strong match for the identified gap. If none are a good fit, define a new "
                        "agent by providing a name and domain."
                    ),
                    messages=[{
                        "role": "user",
                        "content": "Which agent should be added to address this gap?",
                    }],
                    max_tokens=256,
                    temperature=0.3,
                    tools=[select_tool],
                    tool_choice={"type": "tool", "name": "select_candidate"},
                ),
            )

            for block in response.content:
                if block.type == "tool_use" and block.name == "select_candidate":
                    data = block.input
                    if data.get("selection_type") == "existing":
                        agent_id = data.get("agent_id", "")
                        match = next((a for a in available if a.id == agent_id), None)
                        if match:
                            return {
                                "type": "existing",
                                "agent_id": match.id,
                                "name": match.name,
                                "domain": match.expertise_domain,
                                "rationale": data.get("rationale", ""),
                            }
                    # New agent (or fallback from invalid existing selection)
                    return {
                        "type": "new",
                        "name": data.get("proposed_name") or f"New {data.get('proposed_domain', 'Specialist')}",
                        "domain": data.get("proposed_domain") or need_description[:80],
                        "rationale": data.get("rationale", need_description),
                    }

        except Exception as e:
            logger.error("elaboration_error", proposer=proposer.name, error=str(e))

        # Fallback: propose a new agent
        return {
            "type": "new",
            "name": "New Specialist",
            "domain": need_description[:80],
            "rationale": need_description,
        }

    # ── System prompt composition ─────────────────────────────────────────────

    async def _compose_system_prompt(
        self,
        proposer: Agent,
        agent_name: str,
        agent_domain: str,
        rationale: str,
        participant_agents: List[Agent],
    ) -> str:
        """
        Have the proposing agent write the new agent's system prompt.

        Uses the proposer's own model (not Haiku) for quality.
        Gives the proposer their own system prompt + one other agent's as reference.
        """
        # Provide one other participant's system prompt as format reference
        reference_agent = next(
            (a for a in participant_agents if a.id != proposer.id), None
        )
        reference_block = ""
        if reference_agent:
            reference_block = (
                f"\n\nFor format reference, here is another agent's system prompt:\n"
                f"--- {reference_agent.name} ---\n"
                f"{reference_agent.system_prompt}\n"
                f"---\n"
            )

        composition_prompt = (
            f"Your own system prompt for context:\n"
            f"--- {proposer.name} ---\n"
            f"{proposer.system_prompt}\n"
            f"---\n"
            f"{reference_block}\n"
            f"Write a complete system prompt for a new AI agent with these characteristics:\n"
            f"  Name: {agent_name}\n"
            f"  Domain: {agent_domain}\n"
            f"  Purpose in this conversation: {rationale}\n\n"
            f"The system prompt should define the agent's identity, domain expertise, "
            f"how they engage in collaborative multi-agent discussions, and their "
            f"communication style. Write ONLY the system prompt text — no preamble, "
            f"no explanation, just the prompt itself."
        )

        try:
            client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: client.messages.create(
                    model=proposer.model or settings.default_claude_model,
                    system=(
                        f"You are {proposer.name}. You are composing a system prompt "
                        f"for a new AI agent that will join your conversation."
                    ),
                    messages=[{"role": "user", "content": composition_prompt}],
                    max_tokens=1024,
                    temperature=0.7,
                ),
            )
            text = response.content[0].text.strip() if response.content else ""
            if text:
                logger.info(
                    "system_prompt_composed",
                    proposer=proposer.name,
                    agent_name=agent_name,
                    prompt_length=len(text),
                )
                return text
        except Exception as e:
            logger.error("system_prompt_composition_error", error=str(e))

        # Minimal fallback
        return (
            f"You are {agent_name}, an AI agent with expertise in {agent_domain}. "
            f"You participate in collaborative multi-agent discussions, contributing "
            f"your domain knowledge clearly and concisely."
        )

    # ── Agent creation ────────────────────────────────────────────────────────

    async def _create_agent(
        self,
        name: str,
        domain: str,
        system_prompt: str,
        model: Optional[str],
        db: AsyncSession,
    ) -> Agent:
        """Create a new agent in the database with embedding. Handles name collisions."""
        from app.api.routes.agents import generate_agent_embedding

        # Ensure unique name
        unique_name = name
        counter = 1
        while True:
            result = await db.execute(select(Agent).where(Agent.name == unique_name))
            if not result.scalar_one_or_none():
                break
            unique_name = f"{name} {counter}"
            counter += 1

        embedding = await generate_agent_embedding(
            expertise_domain=domain,
            system_prompt=system_prompt,
        )

        agent = Agent(
            name=unique_name,
            expertise_domain=domain,
            system_prompt=system_prompt,
            model=model,
            embedding=embedding,
            extra_data={"auto_created": True},
        )
        db.add(agent)
        await db.commit()
        await db.refresh(agent)

        logger.info("agent_auto_created", agent_id=agent.id, agent_name=agent.name)
        return agent

    # ── Debate and vote helpers ───────────────────────────────────────────────

    async def _generate_debate_statement(
        self,
        agent: Agent,
        proposal_type: str,
        proposer_name: str,
        target_name: str,
        target_domain: str,
        rationale: str,
        is_new_agent: bool,
    ) -> Optional[str]:
        """Generate an optional debate statement from a non-proposer agent."""
        if proposal_type == "propose_addition":
            if is_new_agent:
                action_desc = f"creating a new {target_domain} agent named {target_name}"
            else:
                action_desc = f"adding {target_name} ({target_domain}) to the conversation"
        else:
            action_desc = f"removing {target_name} from the conversation"

        try:
            client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    system=(
                        f"You are {agent.name}, an AI agent with expertise in {agent.expertise_domain}. "
                        f"{proposer_name} has proposed {action_desc}.\n"
                        f"Rationale: {rationale}\n\n"
                        "In 1-2 sentences, state your position on this proposal — support it with a "
                        "reason or object with a reason. Respond with only 'pass' if you have nothing "
                        "to add. This is a position statement, not your vote."
                    ),
                    messages=[{"role": "user", "content": f"What is your position on {action_desc}?"}],
                    max_tokens=150,
                    temperature=0.7,
                ),
            )
            text = response.content[0].text.strip() if response.content else ""
            if len(text) < 10 or text.lower() == "pass":
                return None
            return text
        except Exception as e:
            logger.error("debate_statement_error", agent=agent.name, error=str(e))
            return None

    async def _collect_agent_vote(
        self,
        agent: Agent,
        proposal_type: str,
        proposer_name: str,
        target_name: str,
        target_domain: str,
        rationale: str,
        is_new_agent: bool,
        debate_summary: str = "",
    ) -> str:
        """Collect a structured approve/reject vote from an agent via Haiku."""
        if proposal_type == "propose_addition":
            if is_new_agent:
                action_desc = f"creating and adding a new {target_domain} agent named {target_name}"
            else:
                action_desc = f"adding {target_name} ({target_domain}) to the conversation"
        else:
            action_desc = f"removing {target_name} from the conversation"

        debate_block = f"\n\n{debate_summary}" if debate_summary else ""

        vote_tool = {
            "name": "cast_vote",
            "description": "Cast your vote on the proposal",
            "input_schema": {
                "type": "object",
                "properties": {
                    "vote": {
                        "type": "string",
                        "enum": ["approve", "reject"],
                        "description": "approve: support the proposal; reject: oppose it",
                    },
                    "reason": {
                        "type": "string",
                        "description": "One sentence explaining your vote",
                    },
                },
                "required": ["vote", "reason"],
            },
        }
        try:
            client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    system=(
                        f"You are {agent.name}, an AI agent with expertise in {agent.expertise_domain}. "
                        f"{proposer_name} has proposed {action_desc}.\n"
                        f"Rationale: {rationale}"
                        f"{debate_block}\n\n"
                        "Vote based on the proposal rationale and the debate above. "
                        "If you stated a position during debate, your vote should be consistent with it."
                    ),
                    messages=[{"role": "user", "content": "Please vote on this proposal."}],
                    max_tokens=128,
                    temperature=0.3,
                    tools=[vote_tool],
                    tool_choice={"type": "tool", "name": "cast_vote"},
                ),
            )
            for block in response.content:
                if block.type == "tool_use" and block.name == "cast_vote":
                    return block.input.get("vote", "reject")
        except Exception as e:
            logger.error("agent_vote_error", agent=agent.name, error=str(e))
        return "reject"

    # ── Participant management ────────────────────────────────────────────────

    async def _add_participant(
        self, conversation_id: str, agent_id: str, db: AsyncSession
    ) -> None:
        part_result = await db.execute(
            select(ConversationParticipant)
            .where(ConversationParticipant.conversation_id == conversation_id)
            .where(ConversationParticipant.agent_id == agent_id)
        )
        existing = part_result.scalar_one_or_none()
        if existing:
            if not existing.is_active:
                existing.is_active = True
                await db.commit()
        else:
            db.add(ConversationParticipant(
                conversation_id=conversation_id,
                agent_id=agent_id,
            ))
            await db.commit()

    async def _remove_participant(
        self, conversation_id: str, agent_id: str, db: AsyncSession
    ) -> None:
        part_result = await db.execute(
            select(ConversationParticipant)
            .where(ConversationParticipant.conversation_id == conversation_id)
            .where(ConversationParticipant.agent_id == agent_id)
            .where(ConversationParticipant.is_active == True)
        )
        participant = part_result.scalar_one_or_none()
        if participant:
            participant.is_active = False
            await db.commit()


proposal_service = ProposalService()
