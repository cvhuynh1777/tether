# Tether

An MVP that tethers questions and answers to actual authoritative work. Every answer comes from a vetted collection of official documents and cites the exact pages it used, so you can read the original instead of taking the model's word for it.

**Live demo:** https://tether-chuynh.azurewebsites.net

The first collection covers the **UN Guidelines for the Long-term Sustainability of Outer Space Activities**: 20 documents, including the 2019 guidelines text and 2024–2026 working group, subcommittee and national papers.

## How it works

```
                 ingest.py (offline)
UN PDFs ──► page-level chunks + catalog metadata ──► embeddings ──► Azure AI Search index
 data/       (symbol, year, type, country, page)    text-embedding-3-small

                 per question (online)
Streamlit UI ──► service.py ──► hybrid search (keyword + vector) ──► top 6 chunks
                                                                        │
             ◄── answer + cited pages ◄── gpt-5-mini answers only from them, citing [n]
```

- **Own retrieval pipeline:** `ingest.py` chunks each PDF page, attaches the document's catalog metadata, embeds the chunks, and loads them into Azure AI Search. Only documents listed in the collection's catalog are ingested.
- **Hybrid search:** each question runs as keyword (BM25) and vector search at once; Azure AI Search fuses the results. Metadata fields (year, document type, submitting country) are filterable.
- **Page-level citations:** the model cites numbered sources; only sources it actually cites are shown, grouped by document, each page linking to that page of the official PDF. Answers the documents can't support come back as "The documents I have don't cover that." with no sources.
- **Collections registry** (`backend/collections.json`): each subject has its own catalog, search index and suggested questions. Adding one is configuration, not code.
- **Document catalog:** a browsable, searchable list of every document a collection answers from (`GET /collections/{id}/documents`).
- **Pluggable backends:** each collection declares a `backend`: `azure_ai_search` (current) or `foundry_agent` (the original version, using a Microsoft Foundry agent with managed file search).

## Run locally

Requires Python 3.11+ and an Azure AI Search service plus Azure OpenAI deployments for embeddings and chat.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill in the values (see comments in the file)

python -m streamlit run frontend/app.py
```

App: http://localhost:8501. To run the REST API too, start `python -m uvicorn backend.main:app --reload` and set `BACKEND_URL=http://127.0.0.1:8000` before starting Streamlit (API docs at http://127.0.0.1:8000/docs).

## Adding documents to an existing collection

1. **Add the PDF** to the collection's folder: `data/` for space sustainability, `data/<collection-id>/` for others. The `data/` folder is not committed.
2. **Catalog it** in the collection's metadata file, e.g. `backend/sources/lts.json`. The `filename` must match the PDF's name exactly:
   ```json
   {
     "filename": "AC105_C1_L423E.pdf",
     "symbol": "A/AC.105/C.1/L.423",
     "year": 2025,
     "type": "Working paper",
     "submitted_by": null,
     "title": "Draft final report of the LTS Working Group",
     "url": "https://docs.un.org/en/A/AC.105/C.1/L.423"
   }
   ```
3. **Check, then ingest.** Rerunning is safe: unchanged chunks are overwritten, not duplicated.
   ```bash
   python ingest.py --dry-run     # warnings flag uncatalogued PDFs or missing files
   python ingest.py
   ```
4. **Restart the app** (or redeploy) so the catalog and citation titles pick up the new entry.

Editing metadata for a document that's already ingested (e.g. fixing a title) works the same way: edit the JSON, rerun `python ingest.py`.

## Adding a new collection

1. Put its PDFs in `data/<id>/`, e.g. `data/debris/`.
2. Create `backend/sources/<id>.json` with one entry per PDF (same format as above).
3. Add or update its entry in `backend/collections.json`:
   ```json
   {
     "id": "debris",
     "name": "Space debris mitigation",
     "description": "UN, IADC and national debris mitigation guidelines and standards.",
     "status": "live",
     "backend": "azure_ai_search",
     "search_index": "debris-index",
     "agent_name": null,
     "retrieval": "Azure AI Search: hybrid keyword + vector search over page-level chunks",
     "sources_file": "sources/debris.json",
     "examples": [["Short button label", "Full question sent to the model"]]
   }
   ```
   Give each collection its own `search_index`. `null` falls back to `AZURE_SEARCH_INDEX`, which the space sustainability collection uses. The Azure AI Search free tier allows 3 indexes.
4. Build its index:
   ```bash
   python ingest.py --collection debris --dry-run
   python ingest.py --collection debris --recreate
   ```
5. Restart the app. The collection appears in the sidebar; `"status": "planned"` lists it as coming soon instead.

`--recreate` deletes and rebuilds the index. It's only needed for a new collection or after changing the index schema in `ingest.py`.

## Deployment

Azure App Service (Linux, Python 3.11, free tier) running Streamlit in standalone mode. Configuration comes from App Service application settings with the same names as `.env`; only the read-only search query key is deployed, never the admin key.

```bash
git archive -o tether.zip HEAD
az webapp deploy -g rg-space-rag -n tether-chuynh --src-path tether.zip --type zip
```

A `Dockerfile` is included for running the API and UI together in one container (e.g. Azure Container Apps).

## Project structure

```
ingest.py                 PDFs → chunks + metadata → embeddings → Azure AI Search
backend/
  search_backend.py       hybrid retrieval + answering with numbered citations
  agent_client.py         Foundry agent wrapper (original backend)
  service.py              core logic: registry, routing, filters, rate limit, citations
  main.py                 FastAPI: /health, /collections, /collections/{id}/documents, /chat
  collections.json        collection registry
  sources/lts.json        catalog metadata for the space sustainability collection
frontend/
  app.py                  Streamlit UI: chat and document catalog
.env.example              required settings, with placeholders
```

## Disclaimer

The LTS guidelines are voluntary and non-binding. Tether's answers are informational and are not legal advice.