"""
api.py — FastAPI application exposing two endpoints:

  POST /pay/upload/{user_id}  — upload paycheck PDFs to LlamaCloud and register
                                 ownership in Auth0 FGA.

  POST /pay/insights          — ask a question about paycheck data; the caller's
                                 identity is taken from the JWT bearer token and
                                 used to enforce FGA authorization throughout the
                                 RAG pipeline.

Authentication
--------------
Both endpoints require a Bearer token in the Authorization header. The token is
a standard JWT; the app decodes the payload (without verification — this is a
demo) and extracts the `sub` claim as the user identifier. In production you
would verify the token signature against your Auth0 JWKS endpoint.

Upload flow
-----------
  1. Upload the PDF to LlamaCloud (files.create).
  2. Parse it with LlamaParse — converts the PDF to structured markdown.
  3. Write an FGA tuple: user:{user_id} → owner → paycheck:{file_id}
     This is what grants the user access to their own paycheck.

Insights flow
-------------
  See workflow.py and retriever.py for the full authorization-aware RAG pipeline.
"""

import asyncio
import base64
import json
import os

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from llama_cloud import AsyncLlamaCloud
from openfga_sdk import OpenFgaClient
from openfga_sdk.client.models import ClientTuple, ClientWriteRequest
from pydantic import BaseModel

from fga_config import fga_config
from workflow import RAGWorkflow

app = FastAPI()

# Shared instances — created once at startup so connection pools are reused.
workflow = RAGWorkflow(timeout=120)
llama = AsyncLlamaCloud(api_key=os.getenv("LLAMA_CLOUD_API_KEY"))
security = HTTPBearer()


class InsightsRequest(BaseModel):
    question: str


def get_user_id(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    """
    Extract the user ID from the JWT bearer token.

    JWTs are three base64url-encoded segments separated by dots:
      header.payload.signature

    We decode only the payload (index 1) and read the `sub` (subject) claim,
    which is the standard JWT field for the user identifier.

    Note: In this demo the token is decoded but NOT verified. Production
    apps should validate the signature using Auth0's JWKS endpoint.
    """
    try:
        segment = credentials.credentials.split(".")[1]
        segment += "=" * (4 - len(segment) % 4)  # restore base64 padding
        return json.loads(base64.b64decode(segment))["sub"]
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


async def upload_and_parse(file: UploadFile, user_id: str) -> dict:
    """
    Upload a single paycheck PDF and register the user as its owner in FGA.

    Steps:
      1. files.create  — store the raw PDF in LlamaCloud.
      2. parsing.parse — extract structured markdown from the PDF using LlamaParse.
                         Results are cached, so re-requesting the same file is fast.
      3. fga.write     — create the FGA ownership tuple so the user can later
                         retrieve this document through the RAG pipeline.

    The LlamaCloud file_id is used as the FGA object identifier:
      paycheck:<file_id>
    This same ID is used in the retriever when checking authorization.
    """
    content = await file.read()

    # Step 1: Store the PDF in LlamaCloud.
    file_obj = await llama.files.create(
        file=(file.filename, content, "application/pdf"),
        purpose="parse",
    )

    # Step 2: Parse the PDF into markdown. LlamaParse handles table extraction,
    # layout preservation, and multi-page documents automatically.
    result = await llama.parsing.parse(
        file_id=file_obj.id,
        tier="fast",
        version="latest",
        expand=["markdown_full"],
    )

    # Step 3: Grant ownership in Auth0 FGA.
    # This tuple says: "user:{user_id} is the owner of paycheck:{file_id}".
    # The FGA model derives can_view from owner, so this single write is
    # enough to let the user retrieve their own paycheck in the RAG pipeline.
    async with OpenFgaClient(fga_config()) as fga:
        await fga.write(ClientWriteRequest(writes=[
            ClientTuple(
                user=f"user:{user_id}",
                relation="owner",
                object=f"paycheck:{file_obj.id}",
            ),
        ]))

    return {"file": file.filename, "file_id": file_obj.id, "markdown": result.markdown_full}


@app.post("/pay/insights")
async def pay_insights(body: InsightsRequest, user_id: str = Depends(get_user_id)):
    """
    Answer a payroll question using only documents the caller is authorized to see.

    The user_id from the JWT sub claim is passed into the RAG workflow, which
    uses it to query FGA and filter documents before they reach the LLM.
    """
    result = await workflow.run(query=body.question, user_id=user_id)
    return {"answer": str(result)}


@app.post("/pay/upload/{user_id}")
async def upload_paychecks(
    user_id: str,
    files: list[UploadFile] = File(...),
    _: str = Depends(get_user_id),  # require a valid token from the uploader
):
    """
    Upload one or more paycheck PDFs for a given employee.

    The user_id in the URL is the employee the paychecks belong to.
    The bearer token identifies who is performing the upload (e.g. an HR admin).

    All files are uploaded and parsed in parallel with asyncio.gather.
    """
    results = await asyncio.gather(*[upload_and_parse(f, user_id) for f in files])
    return {"uploads": results}
