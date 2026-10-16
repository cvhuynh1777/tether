# create_index.py
import os
from dotenv import load_dotenv
from azure.core.credentials import AzureKeyCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex, SimpleField, SearchableField, SearchField, SearchFieldDataType,
    VectorSearch, HnswAlgorithmConfiguration, VectorSearchProfile,
    SemanticSearch, SemanticConfiguration, SemanticPrioritizedFields, SemanticField,
)

load_dotenv()
client = SearchIndexClient(os.environ["AZURE_SEARCH_ENDPOINT"],
                           AzureKeyCredential(os.environ["AZURE_SEARCH_ADMIN_KEY"]))

DIMS = 1536  # must match your embedding model

index = SearchIndex(
    name=os.environ["AZURE_SEARCH_INDEX"],
    fields=[
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SearchableField(name="title", type=SearchFieldDataType.String),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SimpleField(name="source", type=SearchFieldDataType.String, filterable=True),
        SearchField(name="embedding",
                    type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                    searchable=True, vector_search_dimensions=DIMS,
                    vector_search_profile_name="vec"),
    ],
    vector_search=VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="hnsw")],
        profiles=[VectorSearchProfile(name="vec", algorithm_configuration_name="hnsw")],
    ),
    semantic_search=SemanticSearch(configurations=[SemanticConfiguration(
        name="default",
        prioritized_fields=SemanticPrioritizedFields(
            title_field=SemanticField(field_name="title"),
            content_fields=[SemanticField(field_name="content")]),
    )]),
)

client.create_or_update_index(index)
print("created", index.name)