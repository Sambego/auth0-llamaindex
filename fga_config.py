"""
fga_config.py — Shared Auth0 FGA client configuration.

Auth0 FGA (Fine-Grained Authorization) is a relationship-based access control
system. Every call to the FGA API is authenticated with OAuth2 client credentials,
which means each request automatically exchanges the client_id/secret for a
short-lived access token before hitting the API.

This module centralises the configuration so every FGA client across the app
(retriever, upload, scripts) is set up identically.
"""

import os

from openfga_sdk import ClientConfiguration
from openfga_sdk.credentials import CredentialConfiguration, Credentials


def fga_config() -> ClientConfiguration:
    """
    Build a ClientConfiguration from environment variables.

    FGA_API_URL defaults to the US1 region; override it if your store is in
    a different region (e.g. https://api.eu1.fga.dev).
    """
    return ClientConfiguration(
        api_url=os.getenv("FGA_API_URL", "https://api.us1.fga.dev"),
        store_id=os.getenv("FGA_STORE_ID"),
        credentials=Credentials(
            method="client_credentials",
            configuration=CredentialConfiguration(
                api_issuer=os.getenv("FGA_API_TOKEN_ISSUER", "auth.fga.dev"),
                api_audience=os.getenv("FGA_API_AUDIENCE", "https://api.us1.fga.dev/"),
                client_id=os.getenv("FGA_CLIENT_ID"),
                client_secret=os.getenv("FGA_CLIENT_SECRET"),
            ),
        ),
    )
