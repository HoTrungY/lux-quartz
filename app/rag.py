from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from app.catalog import CatalogContext, get_product_by_code

try:
    import chromadb
except Exception:  # pragma: no cover
    chromadb = None


class RAGRetriever:
    def __init__(self, catalog: CatalogContext, chroma_dir: Path, collection_name: str) -> None:
        self.catalog = catalog
        self.collection = None
        self.collection_name = collection_name

        if chromadb is None:
            return
        if not chroma_dir.exists():
            return

        try:
            client = chromadb.PersistentClient(path=str(chroma_dir))
            self.collection = client.get_collection(collection_name)
        except Exception:
            self.collection = None

    def query(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            return []

        if self.collection is not None:
            try:
                res = self.collection.query(query_texts=[q], n_results=max(1, limit))
                ids = (res.get("ids") or [[]])[0]
                docs = (res.get("documents") or [[]])[0]
                metas = (res.get("metadatas") or [[]])[0]
                distances = (res.get("distances") or [[]])[0]

                hits: List[Dict[str, Any]] = []
                for idx, item_id in enumerate(ids):
                    meta = metas[idx] if idx < len(metas) else {}
                    code = meta.get("code") if isinstance(meta, dict) else None
                    product = get_product_by_code(self.catalog, code or item_id)
                    if not product:
                        continue
                    hits.append(
                        {
                            "code": product.get("code"),
                            "score": distances[idx] if idx < len(distances) else None,
                            "document": docs[idx] if idx < len(docs) else "",
                            "metadata": meta,
                        }
                    )
                if hits:
                    return hits
            except Exception:
                pass

        return self._lexical_fallback(q, limit)

    def _lexical_fallback(self, query: str, limit: int) -> List[Dict[str, Any]]:
        tokens = [t for t in query.lower().split() if t]
        if not tokens:
            return []

        scored: List[tuple[int, Dict[str, Any]]] = []
        for product in self.catalog.products:
            blob = " ".join(
                [
                    str(product.get("code") or ""),
                    str(product.get("color") or ""),
                    str(product.get("color_group") or ""),
                    str(product.get("series") or ""),
                    str(product.get("pattern") or ""),
                    str(product.get("description") or ""),
                ]
            ).lower()
            score = sum(1 for token in tokens if token in blob)
            if score > 0:
                scored.append(
                    (
                        score,
                        {
                            "code": product.get("code"),
                            "score": score,
                            "document": product.get("description") or "",
                            "metadata": {
                                "color": product.get("color") or "",
                                "color_group": product.get("color_group") or "",
                                "series": product.get("series") or "",
                            },
                        },
                    )
                )

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[: max(1, limit)]]
