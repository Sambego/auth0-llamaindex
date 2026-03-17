import asyncio
import contextlib
import json
import os
from pathlib import Path

import httpx

from .api import InsightsRequest


async def upload_files_from_dir(
    client: httpx.AsyncClient, directory: str, user_id: str
) -> None:
    files = [
        Path(directory) / f
        for f in os.listdir(directory)
        if os.path.isfile(Path(directory) / f)
    ]
    with contextlib.ExitStack() as stack:
        http_files = [
            (
                "files",
                (
                    fp.name,
                    stack.enter_context(open(fp, "rb")),
                    "application/pdf",
                ),
            )
            for fp in files
        ]
        response = await client.post(
            f"/pay/upload/{user_id}",
            files=http_files,
        )
        response.raise_for_status()
        print(json.dumps(response.json(), indent=2))


async def get_insight(
    client: httpx.AsyncClient,
    question: str,
) -> None:
    response = await client.post(
        "/pay/insights", json=InsightsRequest(question=question).model_dump()
    )
    response.raise_for_status()
    print(json.dumps(response.json(), indent=2))


async def run() -> None:
    user_id = input("Your user ID: ")
    while True:
        send_request_to = input("Send request to [uploads/insights]: ")
        if send_request_to.strip().lower() == "uploads":
            upload_directory = input("Directory from which to upload files: ")
            async with httpx.AsyncClient(base_url="http://0.0.0.0:8000") as client:
                await upload_files_from_dir(
                    client, upload_directory.strip(), user_id.strip()
                )
        else:
            question = input("Your question: ")
            async with httpx.AsyncClient(base_url="http://0.0.0.0:8000") as client:
                await get_insight(client, question.strip())


def main() -> None:
    asyncio.run(run())
