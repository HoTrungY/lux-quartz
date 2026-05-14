from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.catalog import build_product_url, load_catalog, normalize_color_group, save_catalog_json

try:
    import chromadb
except Exception:  # pragma: no cover
    chromadb = None


def normalize_catalog_color_groups(json_path: Path) -> int:
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    products = payload.get("products", [])
    changed = 0

    for product in products:
        before = (product.get("color_group") or "").strip()
        after = normalize_color_group(before, product.get("color") or "")
        if after != before:
            changed += 1
        product["color_group"] = after

        current_url = (product.get("product_url") or "").strip()
        if not current_url:
            product["product_url"] = build_product_url(product.get("code") or "")

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return changed


def build_chroma(json_path: Path, chroma_dir: Path) -> int:
    if chromadb is None:
        return 0

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    products = payload.get("products", [])

    client = chromadb.PersistentClient(path=str(chroma_dir))
    collection = client.get_or_create_collection("lux_quartz_us_products")

    ids = []
    docs = []
    metas = []

    for product in products:
        code = product.get("code")
        if not code:
            continue
        variant_prices = [
            v.get("fob_slab_usd") for v in product.get("variants", []) if v.get("fob_slab_usd") is not None
        ]
        price_min = min(variant_prices) if variant_prices else None

        description = product.get("description") or ""
        chunk = (
            f"Product {code} is {product.get('color_group')} quartz, color {product.get('color')}, "
            f"series {product.get('series')}, pattern {product.get('pattern')}. "
            f"{description}"
        ).strip()

        ids.append(product.get("code_normalized") or code)
        docs.append(chunk)
        metas.append(
            {
                "code": code,
                "color": product.get("color") or "",
                "color_group": product.get("color_group") or "",
                "series": product.get("series") or "",
                "pattern": product.get("pattern") or "",
                "price_min": float(price_min) if price_min is not None else -1.0,
                "market": "US",
                "source": product.get("description_source") or "",
            }
        )

    if ids:
        existing = collection.get(ids=ids)
        existing_ids = set(existing.get("ids", [])) if existing else set()
        to_delete = [i for i in ids if i in existing_ids]
        if to_delete:
            collection.delete(ids=to_delete)
        collection.add(ids=ids, documents=docs, metadatas=metas)

    return len(ids)


def main() -> None:
    parser = argparse.ArgumentParser(description="Preprocess Lux Quartz US catalog")
    parser.add_argument(
        "--input",
        default="c:/My Web Sites/clonelandingpage/Lux Quartz My.csv",
        help="Input CSV path",
    )
    parser.add_argument(
        "--json-output",
        default="c:/My Web Sites/clonelandingpage/data/lux_quartz_us_products.json",
        help="Output JSON path",
    )
    parser.add_argument(
        "--chroma-dir",
        default="c:/My Web Sites/clonelandingpage/data/chroma_us",
        help="Chroma persistence directory",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    json_path = Path(args.json_output)
    chroma_dir = Path(args.chroma_dir)

    catalog = load_catalog(input_path)
    save_catalog_json(catalog, json_path)
    normalized_count = normalize_catalog_color_groups(json_path)
    indexed = build_chroma(json_path, chroma_dir)

    print(f"Catalog products: {len(catalog.products)}")
    print(f"JSON output: {json_path}")
    print(f"Normalized color_group rows: {normalized_count}")
    print(f"Chroma indexed docs: {indexed}")


if __name__ == "__main__":
    main()
