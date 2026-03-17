"""
clear_fga_tuples.py — Delete all relationship tuples from the FGA store.

Usage:
    python -m scripts.clear_fga_tuples
"""

import asyncio

from dotenv import load_dotenv

load_dotenv()

from openfga_sdk import OpenFgaClient  # noqa: E402 — after load_dotenv
from openfga_sdk.client.models import ClientTuple, ClientWriteRequest
from openfga_sdk.models import ReadRequestTupleKey

from fga_config import fga_config  # noqa: E402


async def main() -> None:
    async with OpenFgaClient(fga_config()) as fga:
        all_tuples, continuation_token = [], None
        while True:
            options = {"continuation_token": continuation_token} if continuation_token else None
            resp = await fga.read(ReadRequestTupleKey(), options=options)
            all_tuples += [ClientTuple(user=t.key.user, relation=t.key.relation, object=t.key.object)
                           for t in (resp.tuples or [])]
            continuation_token = resp.continuation_token
            if not continuation_token:
                break

        if not all_tuples:
            print("No tuples found.")
            return

        print(f"Deleting {len(all_tuples)} tuples…")
        for i in range(0, len(all_tuples), 10):
            await fga.write(ClientWriteRequest(deletes=all_tuples[i:i + 10]))
        print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
