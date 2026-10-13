# Tether

An MVP that tethers questions and answers to actual authoritative work. Every answer comes from vetted official documents and links back to its source, so you can read the original instead of taking the model's word for it.

The first collection covers the **UN Guidelines for the Long-term Sustainability of Outer Space Activities**: the 2019 guidelines text, plus UN documents from 2024–2026.

## How it works

```
Streamlit UI  ──►  FastAPI backend  ──►  Microsoft Foundry agent (gpt-5-mini)
 (chat, sources)    (routing, rate        └─ file search over the collection's PDFs
                     limit, citation
                     metadata)
```

- **Collections registry** (`backend/collections.json`): each subject of study has its own agent, metadata file and suggested questions. Adding a collection is a config change, not a code change.
- **Citations with provenance** (`backend/sources/*.json`): each cited file is mapped to its official title, UN document symbol, year and a link to the authoritative PDF.
- **Pluggable retrieval:** every collection declares a `backend`. Today that's `foundry_agent`; an Azure AI Search retriever slots into the same routing function later.
- **No keys in code:** the backend authenticates with `DefaultAzureCredential`, using `az login` locally and a managed identity when deployed.

## Roadmap

- **Now:** A FastAPI and Streamlit web app calling a Microsoft Foundry agent that retrieves from UN documents with file search.
- **Next:** Ingest more documents into the current collection, and add new collections such as space debris and space resources.
- **Later:** A vetted vector database on Azure AI Search for researchers (currently limited by my school Azure account).

## Run locally

Requires Python 3.11+, the Azure CLI, and access to a Microsoft Foundry project with a deployed agent.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill in PROJECT_ENDPOINT
az login

# terminal 1
python -m uvicorn backend.main:app --reload --reload-include "*.json"

# terminal 2
python -m streamlit run frontend/app.py
```

API docs: http://127.0.0.1:8000/docs · App: http://localhost:8501

## Project structure

```
backend/
  agent_client.py     Foundry agent wrapper (conversations, citation extraction)
  main.py             FastAPI app: /health, /collections, /chat
  collections.json    collection registry
  sources/lts.json    citation metadata for the space sustainability collection
frontend/
  app.py              Streamlit chat UI
.streamlit/config.toml
```

## Disclaimer

The LTS guidelines are voluntary and non-binding. Tether's answers are informational and are not legal advice.