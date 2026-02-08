import os

import yaml
import cocoindex
from psycopg_pool import ConnectionPool


def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "cocoindex.yaml")
    with open(config_path) as f:
        return yaml.safe_load(f)


CONFIG = load_config()


@cocoindex.op.function()
def extract_extension(filename: str) -> str:
    return os.path.splitext(filename)[1]


@cocoindex.transform_flow()
def code_to_embedding(text: cocoindex.DataSlice[str]) -> cocoindex.DataSlice[list[float]]:
    return text.transform(
        cocoindex.functions.SentenceTransformerEmbed(
            model="sentence-transformers/all-MiniLM-L6-v2",
        )
    )


@cocoindex.flow_def(name="CodeEmbedding")
def code_embedding_flow(
    flow_builder: cocoindex.FlowBuilder, data_scope: cocoindex.DataScope
):
    data_scope["files"] = flow_builder.add_source(
        cocoindex.sources.LocalFile(
            path="..",
            included_patterns=CONFIG["patterns"]["included"],
            excluded_patterns=CONFIG["patterns"]["excluded"],
        )
    )

    code_embeddings = data_scope.add_collector()

    with data_scope["files"].row() as file:
        file["extension"] = file["filename"].transform(extract_extension)
        file["chunks"] = file["content"].transform(
            cocoindex.functions.SplitRecursively(),
            language=file["extension"],
            chunk_size=1000,
            chunk_overlap=300,
        )

        with file["chunks"].row() as chunk:
            chunk["embedding"] = chunk["text"].call(code_to_embedding)
            code_embeddings.collect(
                filename=file["filename"],
                location=chunk["location"],
                code=chunk["text"],
                embedding=chunk["embedding"],
            )

    code_embeddings.export(
        "code_embeddings",
        cocoindex.storages.Postgres(),
        primary_key_fields=["filename", "location"],
        vector_indexes=[
            cocoindex.VectorIndexDef(
                field_name="embedding",
                metric=cocoindex.VectorSimilarityMetric.COSINE_SIMILARITY,
            )
        ],
    )


def search(pool: ConnectionPool, query: str, top_k: int = 5):
    table_name = cocoindex.utils.get_target_storage_default_name(
        code_embedding_flow, "code_embeddings"
    )
    query_vector = code_to_embedding.eval(query)
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT filename, code, embedding <=> %s::vector AS distance
                FROM {table_name} ORDER BY distance LIMIT %s
                """,
                (query_vector, top_k),
            )
            return [
                {"filename": row[0], "code": row[1], "score": round(1.0 - row[2], 4)}
                for row in cur.fetchall()
            ]
