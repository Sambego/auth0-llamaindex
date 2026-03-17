# Auth0 FGA + LlamaCloud — Paycheck RAG API

A FastAPI application that combines **LlamaCloud** for document storage and parsing with **Auth0 / Okta FGA** for fine-grained authorization on paycheck RAG queries, using the [`auth0-ai-llamaindex`](https://github.com/auth0/auth0-ai-python/tree/main/packages/auth0-ai-llamaindex) SDK and **Anthropic Claude** for answer synthesis.

## What it demonstrates

Employees query their paycheck information through a REST API. Every request is authorized by Okta FGA — employees can only see their own paychecks, while department managers can see their whole team's:

| User | Role              | Accessible paychecks                |
| ---- | ----------------- | ----------------------------------- |
| john | Software Engineer | Own paychecks only                  |
| jane | Software Engineer | Own paychecks only                  |
| mary | Eng. Manager      | All department paychecks (via FGA manager relation) |

The caller's identity is taken from the `sub` claim of the bearer token — no separate login flow required.

## How it works

### Upload

```
POST /pay/upload/{user_id}
    │
    ├─ Upload PDF(s) to LlamaCloud
    ├─ Parse with LlamaParse
    └─ Write FGA tuple: user:{user_id} → owner → paycheck:{file_id}
```

### Insights

```
POST /pay/insights  (bearer token → sub → user_id)
    │
    ▼
[retrieve step]
    ├─ FGA list_objects  — get all paycheck IDs the user can view
    ├─ Fetch filenames + parsed content from LlamaCloud
    └─ FGARetriever batch_check — explicit per-document authorization gate
    │
    ▼
RetrievedEvent (authorized nodes only)
    │
    ▼
[synthesize step]
    ├─ FGA list_objects  — resolve department membership (for managers)
    └─ Anthropic Claude answers using only authorized paycheck content
    │
    ▼
{"answer": "..."}
```

Authorization is modelled in `fga/model.fga.yaml`. Mary's access to team paychecks is derived automatically: she is the `manager` of `department:devrel`, and `can_view` on each paycheck includes `manager from owner`.

## Prerequisites

- Python 3.11+
- An [Anthropic API key](https://console.anthropic.com/)
- A [LlamaCloud API key](https://cloud.llamaindex.ai/)
- A free [Okta FGA store](https://dashboard.fga.dev/) with a client ID and secret

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Copy and fill in your credentials
cp .env.example .env

# 3. Initialise the FGA authorization model from fga/model.fga.yaml
python -m scripts.setup_fga

# 4. Start the API
uvicorn api:app
```

## API

### `POST /pay/upload/{user_id}`

Upload one or more paycheck PDFs for a given user. Requires a valid bearer token.

```bash
curl -X POST http://localhost:8000/pay/upload/john \
  -H "Authorization: Bearer <token>" \
  -F "files=@john_paycheck_01.pdf" \
  -F "files=@john_paycheck_02.pdf"
```

Files are uploaded and parsed by LlamaCloud. An FGA `owner` tuple is written for each file using the LlamaCloud `file_id` as the paycheck object identifier.

### `POST /pay/insights`

Ask a question about paycheck data. The user is identified from the `sub` claim of the bearer token.

```bash
curl -X POST http://localhost:8000/pay/insights \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"question": "What is my total net pay across all paychecks?"}'
```

## Project structure

```
.
├── api.py                       # FastAPI app — upload and insights endpoints
├── workflow.py                  # LlamaIndex Workflow (retrieve → synthesize)
├── retriever.py                 # LlamaCloudRetriever + FGARetriever
├── fga_config.py                # Shared Okta FGA client configuration
├── fga/
│   └── model.fga.yaml           # FGA authorization model, tuples, and tests
├── paychecks/                   # Sample paycheck PDFs
├── scripts/
│   ├── setup_fga.py             # Initialize FGA model and tuples from YAML
│   ├── clear_fga_tuples.py      # Delete all tuples from the FGA store
│   └── generate_paychecks.py    # Generates sample paycheck PDFs
├── requirements.txt
└── .env.example
```

## Environment variables

| Variable              | Description                              |
| --------------------- | ---------------------------------------- |
| `ANTHROPIC_API_KEY`   | Anthropic API key                        |
| `ANTHROPIC_BASE_URL`  | Optional custom Anthropic endpoint       |
| `LLAMA_CLOUD_API_KEY` | LlamaCloud API key                       |
| `FGA_STORE_ID`        | Okta FGA store ID                        |
| `FGA_CLIENT_ID`       | Okta FGA OAuth2 client ID                |
| `FGA_CLIENT_SECRET`   | Okta FGA OAuth2 client secret            |
| `FGA_API_URL`         | FGA API URL (defaults to US1 region)     |

## License

Apache 2.0
