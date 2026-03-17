"""
retriever.py — Authorization-aware document retrieval.

This module implements a two-layer FGA authorization strategy:

  Layer 1 — list_objects (LlamaCloudRetriever)
    Ask FGA "what paychecks can this user view?" before fetching any content.
    This avoids loading documents the user has no access to in the first place.

  Layer 2 — batch_check (FGARetriever)
    After fetching the candidate documents, FGARetriever verifies each one
    individually. This is a defence-in-depth check: even if list_objects
    returned something unexpected, no document reaches the LLM without an
    explicit authorization confirmation.

The two FGA calls together mirror the "verify then use" pattern common in
secure systems — list_objects is efficient; batch_check is the authoritative gate.
"""

import asyncio
import os

from auth0_ai_llamaindex import FGARetriever
from llama_cloud import AsyncLlamaCloud
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode
from openfga_sdk import OpenFgaClient
from openfga_sdk.client.models import ClientBatchCheckItem
from openfga_sdk.models import ListObjectsRequest

from fga_config import fga_config

# Single shared LlamaCloud client — connection pool is reused across requests.
llama = AsyncLlamaCloud(api_key=os.getenv("LLAMA_CLOUD_API_KEY"))


class LlamaCloudRetriever(BaseRetriever):
    """
    Custom LlamaIndex retriever that:
      1. Asks FGA which paycheck objects the user can view (list_objects).
      2. Fetches the original filenames and parsed markdown content for those
         objects from LlamaCloud in parallel.

    The LlamaCloud file_id is used as the FGA object identifier, so the same
    ID is used in the FGA tuple written at upload time and in the check here.
    """

    def __init__(self, user_id: str):
        self._user_id = user_id
        super().__init__()

    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        raise NotImplementedError("Use _aretrieve")

    async def _aretrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        # ── Layer 1: FGA list_objects ──────────────────────────────────────
        # list_objects answers: "what objects of type 'paycheck' does this
        # user have the 'can_view' relation on?"
        #
        # The 'can_view' relation is derived in the FGA model — employees
        # satisfy it as owners; managers satisfy it transitively through
        # their department's manager relation.
        async with OpenFgaClient(fga_config()) as fga:
            resp = await fga.list_objects(ListObjectsRequest(
                user=f"user:{self._user_id}",
                relation="can_view",
                type="paycheck",
            ))

        # FGA returns fully qualified objects like "paycheck:<file_id>".
        # Strip the type prefix to get the bare LlamaCloud file IDs.
        file_ids = [obj.split(":", 1)[-1] for obj in (resp.objects or [])]

        if not file_ids:
            return []

        # ── Fetch from LlamaCloud ──────────────────────────────────────────
        # Retrieve filenames and parsed content in parallel to minimise latency.
        # LlamaParse caches results, so parse() returns immediately for
        # documents that were already parsed at upload time.
        file_names: dict[str, str] = {}
        async for f in llama.files.list(file_ids=file_ids):
            file_names[f.id] = f.name

        parse_results = await asyncio.gather(*[
            llama.parsing.parse(
                file_id=fid,
                tier="fast",
                version="latest",
                expand=["markdown_full"],  # return the full parsed markdown
            )
            for fid in file_ids
        ])

        # Build LlamaIndex nodes. The file_id becomes the node ID so it can
        # be matched against FGA objects in the batch_check layer below.
        return [
            NodeWithScore(
                node=TextNode(
                    text=result.markdown_full or "",
                    id_=fid,
                    metadata={"filename": file_names.get(fid, fid)},
                ),
                score=1.0,
            )
            for fid, result in zip(file_ids, parse_results)
            if result.markdown_full
        ]


def build_fga_retriever(user_id: str) -> FGARetriever:
    """
    Wrap LlamaCloudRetriever with FGARetriever (from auth0-ai-llamaindex).

    FGARetriever calls LlamaCloudRetriever to get the candidate nodes, then
    runs a batch_check against FGA for every node before returning them.

    ── Layer 2: FGA batch_check ─────────────────────────────────────────────
    batch_check answers: "does user X have relation R on object Y?" for each
    document individually. This is the explicit, per-document authorization
    gate that ensures no document reaches the LLM without confirmation.

    The build_query function maps each LlamaIndex node to an FGA check item.
    node.id_ is the LlamaCloud file_id, which matches the object written to
    FGA at upload time: "paycheck:<file_id>".
    """
    def build_query(node):
        return ClientBatchCheckItem(
            user=f"user:{user_id}",
            object=f"paycheck:{node.id_}",
            relation="can_view",
        )

    return FGARetriever(LlamaCloudRetriever(user_id), build_query=build_query)


async def get_department_members(user_id: str) -> tuple[list[str], list[str]]:
    """
    Return (department_names, member_user_ids) for every department the user manages.

    This is used in the synthesize step to give the LLM context about the
    team when a manager asks department-level questions.

    FGA model relations used:
      - manager: user → department   (is this user a manager of the department?)
      - department: department → user (which users belong to this department?)

    The second relation is stored as tuples in the form:
      {user: "department:devrel", relation: "department", object: "user:john"}
    so list_objects(user="department:devrel", relation="department", type="user")
    returns all members of that department.
    """
    async with OpenFgaClient(fga_config()) as fga:
        # Find departments where this user is listed as manager.
        dept_resp = await fga.list_objects(ListObjectsRequest(
            user=f"user:{user_id}", relation="manager", type="department",
        ))
        dept_objects = dept_resp.objects or []

        if not dept_objects:
            return [], []

        # For each managed department, find all members.
        members: set[str] = set()
        for dept in dept_objects:
            resp = await fga.list_objects(ListObjectsRequest(
                user=dept, relation="department", type="user",
            ))
            for obj in (resp.objects or []):
                members.add(obj.split(":", 1)[-1])  # strip "user:" prefix

    departments = [d.split(":", 1)[-1] for d in dept_objects]  # strip "department:" prefix
    return departments, list(members)
