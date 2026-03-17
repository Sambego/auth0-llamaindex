"""
workflow.py — LlamaIndex Workflow for FGA-authorized paycheck RAG.

A LlamaIndex Workflow is a graph of async steps connected by typed events.
Each step declares the event type it consumes and the event type it emits,
making the data flow explicit and easy to follow.

This workflow has two steps:

  retrieve  — fetches the authorized paycheck documents for the caller
  synthesize — builds a prompt from those documents and calls Claude

The authorization happens entirely inside the retriever (see retriever.py),
so by the time the synthesize step runs it only ever sees documents the
current user is permitted to view.
"""

import asyncio
import os
from typing import Annotated

from dotenv import load_dotenv
from llama_index.core.schema import NodeWithScore, QueryBundle
from llama_index.core.workflow import Event, StartEvent, StopEvent, Workflow, step
from llama_index.llms.anthropic import Anthropic
from pydantic import Field
from workflows.resource import Resource

from .retriever import build_fga_retriever, get_department_members

# ── Events ────────────────────────────────────────────────────────────────────
# Events are the typed messages that flow between workflow steps.
# RetrievedEvent carries everything the synthesize step needs.


class InputEvent(StartEvent):
    user_id: str
    query: str


class RetrievedEvent(Event):
    query: str
    user_id: str
    nodes: list[NodeWithScore] = Field(default_factory=list)  # FGA-filtered docs
    departments: list[str] = Field(default_factory=list)  # departments user manages
    members: list[str] = Field(default_factory=list)  # member IDs in those depts


# ── Resources ────────────────────────────────────────────────────────────────────


def get_llm() -> Anthropic:
    load_dotenv(".env")
    return Anthropic(
        model="claude-4-6-sonnet",
        base_url=os.getenv("ANTHROPIC_BASE_URL"),
        max_tokens=4096,
    )


# ── Workflow ──────────────────────────────────────────────────────────────────


class RAGWorkflow(Workflow):
    @step
    async def retrieve(self, ev: InputEvent) -> RetrievedEvent:
        """
        Fetch the authorized paycheck documents and department context in parallel.

        build_fga_retriever returns a retriever that:
          1. Calls FGA list_objects to find accessible paycheck IDs.
          2. Fetches those documents from LlamaCloud.
          3. Runs FGA batch_check on each document as a second authorization layer.

        get_department_members is only relevant for managers — it enriches the
        synthesize prompt with team context when the user manages a department.
        Both calls run concurrently with asyncio.gather.
        """
        query = ev.query
        user_id = ev.user_id

        nodes, (departments, members) = await asyncio.gather(
            build_fga_retriever(user_id)._aretrieve(QueryBundle(query_str=query)),
            get_department_members(user_id),
        )
        return RetrievedEvent(
            query=query,
            user_id=user_id,
            nodes=nodes,
            departments=departments,
            members=members,
        )

    @step
    async def synthesize(
        self, ev: RetrievedEvent, llm: Annotated[Anthropic, Resource(get_llm)]
    ) -> StopEvent:
        """
        Build a prompt from the authorized documents and get an answer from Claude.

        The prompt is structured so Claude knows:
          - Who the current user is (for "my pay" type questions).
          - Which department and members are relevant (for managers).
          - The full text of every authorized paycheck record.

        Each document is labeled with its original filename so Claude can
        attribute amounts to the correct employee.

        No authorization logic lives here — if a document is in ev.nodes,
        it has already passed both FGA checks in the retrieve step.
        """
        if not ev.nodes:
            return StopEvent(result=f"No paycheck records found for '{ev.user_id}'.")

        # Label each record with the original filename (e.g. "john_paycheck_01.pdf")
        # so Claude can attribute pay data to the right employee.
        context = "\n\n".join(
            f"[{n.node.metadata.get('filename', n.node.id_)}]\n{n.node.get_content()}"
            for n in ev.nodes
        )

        # Only include department context when the user is a manager.
        members_note = (
            f"Department: {', '.join(ev.departments)}\n"
            f"Department members: {', '.join(ev.members)}\n\n"
            if ev.departments
            else ""
        )

        prompt = (
            "You are a helpful payroll assistant. Answer the user's question "
            "using only the paycheck records provided below. Each record is labeled "
            "with the employee it belongs to. Be specific about amounts, dates, and "
            "pay periods. If the records don't contain enough information, say so clearly.\n\n"
            f"Current user: {ev.user_id}\n"
            f"{members_note}"
            f"=== Paycheck Records ===\n{context}\n\n"
            f"=== Question ===\n{ev.query}"
        )

        result = await llm.acomplete(prompt)

        return StopEvent(result=str(result))
