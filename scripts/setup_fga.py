"""
setup_fga.py — Initialize Auth0 FGA from fga/model.fga.yaml

Usage:
    python -m scripts.setup_fga
"""

import asyncio
import re
from pathlib import Path

import yaml
from dotenv import load_dotenv
from openfga_sdk import OpenFgaClient
from openfga_sdk.client.models import ClientTuple, ClientWriteRequest
from openfga_sdk.models import (
    Metadata,
    ObjectRelation,
    RelationMetadata,
    RelationReference,
    TupleToUserset,
    TypeDefinition,
    Userset,
    Usersets,
    WriteAuthorizationModelRequest,
)

from auth0_llamaindex.fga_config import fga_config  # noqa: E402

YAML_PATH = Path(__file__).parent.parent / "fga" / "model.fga.yaml"


def _parse_leaf(expr: str) -> tuple[Userset, list[RelationReference]]:
    expr = expr.strip()
    if expr.startswith("["):
        types = [t.strip() for t in expr.strip("[]").split(",")]
        return Userset(this={}), [RelationReference(type=t) for t in types if t]
    if " from " in expr:
        computed, tupleset = [p.strip() for p in expr.split(" from ", 1)]
        return Userset(
            tuple_to_userset=TupleToUserset(
                tupleset=ObjectRelation(object="", relation=tupleset),
                computed_userset=ObjectRelation(object="", relation=computed),
            )
        ), []
    return Userset(computed_userset=ObjectRelation(object="", relation=expr)), []


def _parse_definition(definition: str) -> tuple[Userset, list[RelationReference]]:
    parts = [p.strip() for p in re.split(r"\bor\b", definition)]
    if len(parts) == 1:
        return _parse_leaf(parts[0])
    usersets, refs = [], []
    for part in parts:
        us, r = _parse_leaf(part)
        usersets.append(us)
        refs.extend(r)
    return Userset(union=Usersets(child=usersets)), refs


def parse_model_dsl(dsl: str) -> WriteAuthorizationModelRequest:
    schema_version = "1.1"
    type_defs = []
    current_type = None
    relations = {}
    metadata = {}

    for raw in dsl.splitlines():
        line = raw.strip()
        if not line or line in ("model", "relations"):
            continue
        if line.startswith("schema "):
            schema_version = line.split()[1]
        elif line.startswith("type "):
            if current_type:
                type_defs.append(
                    TypeDefinition(
                        type=current_type,
                        relations=relations,
                        metadata=Metadata(relations=metadata) if metadata else None,
                    )
                )
            current_type = line[5:].strip()
            relations, metadata = {}, {}
        elif line.startswith("define "):
            name, _, definition = line[7:].partition(":")
            name = name.strip()
            userset, refs = _parse_definition(definition.strip())
            relations[name] = userset
            if refs:
                metadata[name] = RelationMetadata(directly_related_user_types=refs)

    if current_type:
        type_defs.append(
            TypeDefinition(
                type=current_type,
                relations=relations,
                metadata=Metadata(relations=metadata) if metadata else None,
            )
        )

    return WriteAuthorizationModelRequest(
        schema_version=schema_version, type_definitions=type_defs
    )


async def main() -> None:
    load_dotenv()

    config = fga_config()
    if not config.store_id:
        raise ValueError(
            "FGA_STORE_ID is not set. Create a store at https://dashboard.fga.dev"
        )

    data = yaml.safe_load(YAML_PATH.read_text())

    async with OpenFgaClient(config) as fga:
        print("Creating authorization model…")
        resp = await fga.write_authorization_model(parse_model_dsl(data["model"]))
        print(f"  Model created: {resp.authorization_model_id}")

        tuples = [
            ClientTuple(
                user=str(t["user"]),
                relation=str(t["relation"]),
                object=str(t["object"]),
            )
            for t in data.get("tuples", [])
        ]
        if tuples:
            print(f"Writing {len(tuples)} tuples…")
            await fga.write(ClientWriteRequest(writes=tuples))

    print("FGA setup complete. Run the app with: uv pip install -e . && run-server")


if __name__ == "__main__":
    asyncio.run(main())
