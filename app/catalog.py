from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

CODE_RE = re.compile(r"[^A-Za-z0-9]")
CODE_SPLIT_RE = re.compile(r"^([A-Z]+)(\d+)$")
VN_HEADER_SENTINEL = "Mã hàng"


@dataclass
class CatalogContext:
    products: List[Dict[str, Any]]
    by_code: Dict[str, Dict[str, Any]]
    market: str = "US"


def normalize_code(code: str) -> str:
    return CODE_RE.sub("", (code or "").upper())


def parse_money(value: str) -> Optional[float]:
    raw = str(value or "").strip()
    if not raw:
        return None

    cleaned = raw.replace("$", "").replace("VNĐ", "").replace("VND", "")
    cleaned = cleaned.replace(" ", "")

    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    else:
        if "," in cleaned:
            parts = cleaned.split(",")
            if len(parts) > 1 and all(len(part) == 3 for part in parts[1:]):
                cleaned = "".join(parts)
            else:
                cleaned = cleaned.replace(",", ".")
        elif "." in cleaned:
            parts = cleaned.split(".")
            if len(parts) > 1 and all(len(part) == 3 for part in parts[1:]):
                cleaned = "".join(parts)

    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_thickness_cm(value: str) -> Optional[int]:
    text = (value or "").lower()
    if "3cm" in text or "1 1/4" in text or "1.25" in text:
        return 3
    if "2cm" in text or "3/4" in text:
        return 2

    match = re.search(r"(\d+(?:\.\d+)?)\s*cm", text)
    if match:
        parsed = float(match.group(1))
        if parsed >= 2.5:
            return 3
        if parsed >= 1.8:
            return 2

    return None


def normalize_color_group(value: str, color_hint: str = "") -> str:
    source = f"{value or ''} {color_hint or ''}".strip().lower()
    if "black" in source or "đen" in source:
        return "Black"
    if "grey" in source or "gray" in source or "silver" in source or "xám" in source:
        return "Grey"
    if "white" in source or "trắng" in source:
        return "White"
    return "Cream"


def infer_color_group(color: str) -> str:
    return normalize_color_group("", color)


def resolve_price_field(market: str, price_unit: Optional[str] = None) -> str:
    unit = (price_unit or "").strip().lower()
    normalized_market = (market or "US").strip().upper()

    if normalized_market == "VN":
        if unit == "slab":
            return "price_slab_vnd"
        if unit == "sf":
            return "price_sf_vnd"
        return "price_sqm_vnd"

    if unit == "slab":
        return "fob_slab_usd"
    if unit == "sqm":
        return "fob_sqm_usd"
    if unit == "sf":
        return "fob_sf_usd"
    return "fob_sf_usd"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _normalize_stock_bool(value: str, default: bool = True) -> bool:
    text = _clean(value).lower()
    if not text:
        return default
    if text in {"yes", "true", "1", "có", "co", "available", "in stock", "có sẵn"}:
        return True
    if text in {"no", "false", "0", "hết", "het", "out of stock"}:
        return False
    return default


def format_code_slug(code: str) -> str:
    normalized = normalize_code(code)
    match = CODE_SPLIT_RE.match(normalized)
    if not match:
        return normalized.lower()
    prefix, digits = match.groups()
    return f"{prefix.lower()}-{digits}"


def build_product_url(code: str) -> str:
    slug = format_code_slug(code)
    return f"https://luxquartzvietnam.com/product/{slug}/"


def _dedupe_variants(variants: List[Dict[str, Any]], market: str) -> List[Dict[str, Any]]:
    deduped: Dict[tuple[Optional[int], str], Dict[str, Any]] = {}
    required_price_field = "price_slab_vnd" if market == "VN" else "fob_slab_usd"

    for variant in variants:
        thickness = variant.get("thickness_cm")
        price_marker = variant.get(required_price_field)
        key = (thickness, str(price_marker))
        if key not in deduped:
            deduped[key] = variant

    return list(deduped.values())


def _load_catalog_us(rows: List[Dict[str, Any]]) -> CatalogContext:
    grouped: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        code = _clean(row.get("Code"))
        if not code:
            continue

        code_key = normalize_code(code)
        color = _clean(row.get("color"))
        series = _clean(row.get("Series"))
        thickness_text = _clean(row.get("Thickness (cm/inch)"))

        product = grouped.setdefault(
            code_key,
            {
                "code": code,
                "code_normalized": code_key,
                "color": color,
                "color_group": normalize_color_group(_clean(row.get("Color_Group")), color),
                "series": series,
                "pattern": _clean(row.get("Pattern")),
                "application": _clean(row.get("Application")),
                "finish": _clean(row.get("Finish")),
                "photo_url": _clean(row.get("Photo_URL")),
                "product_url": _clean(row.get("Product_URL")) or build_product_url(code),
                "in_stock": _normalize_stock_bool(_clean(row.get("In_Stock")), default=True),
                "weight_per_slab": _clean(row.get("Weight_per_Slab")),
                "description": _clean(row.get("Product_Description")),
                "description_source": _clean(row.get("Description_Clean_Source")),
                "market": "US",
                "currency": "USD",
                "slab_area_sf": 55.11,
                "slab_size": "160 x 320cm / 63 x 126in",
                "variants": [],
            },
        )

        if not product["series"] and series:
            product["series"] = series
        if not product["description"]:
            product["description"] = _clean(row.get("Product_Description"))
        if not product["photo_url"]:
            product["photo_url"] = _clean(row.get("Photo_URL"))
        if not product["product_url"]:
            product["product_url"] = _clean(row.get("Product_URL")) or build_product_url(code)

        variant = {
            "thickness_text": thickness_text,
            "thickness_cm": parse_thickness_cm(thickness_text),
            "fob_sqm_usd": parse_money(_clean(row.get("FOB/ Sqm"))),
            "fob_sf_usd": parse_money(_clean(row.get("FOB/ SF"))),
            "fob_slab_usd": parse_money(_clean(row.get('FOB/ slab (160 x 320cm/ 63 x 126")'))),
            "price_sqm_vnd": None,
            "price_sf_vnd": None,
            "price_slab_vnd": None,
        }
        product["variants"].append(variant)

    products = sorted(grouped.values(), key=lambda p: p["code_normalized"])
    for product in products:
        product["variants"] = _dedupe_variants(product.get("variants", []), market="US")

    by_code = {p["code_normalized"]: p for p in products}
    return CatalogContext(products=products, by_code=by_code, market="US")


def _load_catalog_vn(rows: List[Dict[str, Any]]) -> CatalogContext:
    grouped: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        code = _clean(row.get("Mã hàng"))
        if not code:
            continue

        code_key = normalize_code(code)
        series = _clean(row.get("Tên sản phẩm"))
        color = _clean(row.get("Màu"))
        thickness_text = _clean(row.get("Độ dày"))

        product = grouped.setdefault(
            code_key,
            {
                "code": code,
                "code_normalized": code_key,
                "color": color,
                "color_group": normalize_color_group(color, color),
                "series": series,
                "pattern": _clean(row.get("Hoa văn")),
                "application": _clean(row.get("Ứng dụng")),
                "finish": _clean(row.get("Bề mặt hoàn thiện")),
                "photo_url": _clean(row.get("Ảnh sản phẩm")),
                "product_url": build_product_url(code),
                "in_stock": _normalize_stock_bool(_clean(row.get("Tồn kho")), default=True),
                "weight_per_slab": "",
                "description": _clean(row.get("Mô tả sản phẩm")),
                "description_source": "VN",
                "market": "VN",
                "currency": "VND",
                "slab_area_sf": 55.11,
                "slab_size": _clean(row.get("Kích thước")) or "330x195cm",
                "variants": [],
            },
        )

        if not product["series"] and series:
            product["series"] = series
        if not product["description"]:
            product["description"] = _clean(row.get("Mô tả sản phẩm"))
        if not product["photo_url"]:
            product["photo_url"] = _clean(row.get("Ảnh sản phẩm"))
        if not product["pattern"]:
            product["pattern"] = _clean(row.get("Hoa văn"))
        if not product["application"]:
            product["application"] = _clean(row.get("Ứng dụng"))
        if not product["finish"]:
            product["finish"] = _clean(row.get("Bề mặt hoàn thiện"))

        price_sqm_vnd = parse_money(_clean(row.get("Đơn giá (VND/m2)(VAT 8%)")))
        price_slab_vnd = parse_money(_clean(row.get("Đơn giá (VND/tấm)(VAT 8%)")))
        price_sf_vnd = parse_money(_clean(row.get("Đơn giá quy đổi (VND/SF)")))

        variant = {
            "thickness_text": thickness_text,
            "thickness_cm": parse_thickness_cm(thickness_text),
            "fob_sqm_usd": None,
            "fob_sf_usd": None,
            "fob_slab_usd": None,
            "price_sqm_vnd": price_sqm_vnd,
            "price_sf_vnd": price_sf_vnd,
            "price_slab_vnd": price_slab_vnd,
        }
        product["variants"].append(variant)

    products = sorted(grouped.values(), key=lambda p: p["code_normalized"])
    for product in products:
        product["variants"] = _dedupe_variants(product.get("variants", []), market="VN")

    by_code = {p["code_normalized"]: p for p in products}
    return CatalogContext(products=products, by_code=by_code, market="VN")


def load_catalog(catalog_path: Path, market: Optional[str] = None) -> CatalogContext:
    if not catalog_path.exists():
        raise FileNotFoundError(f"Catalog file not found: {catalog_path}")

    with catalog_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = [dict(r) for r in reader]
        headers = reader.fieldnames or []

    explicit_market = (market or "").strip().upper()
    if explicit_market == "VN":
        return _load_catalog_vn(rows)
    if explicit_market == "US":
        return _load_catalog_us(rows)

    if VN_HEADER_SENTINEL in headers:
        return _load_catalog_vn(rows)
    return _load_catalog_us(rows)


def save_catalog_json(catalog: CatalogContext, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "market": catalog.market,
        "slab_area_sf": 55.11,
        "count": len(catalog.products),
        "products": catalog.products,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def get_product_by_code(catalog: CatalogContext, code: str) -> Optional[Dict[str, Any]]:
    return catalog.by_code.get(normalize_code(code))


def find_variant(
    product: Dict[str, Any],
    thickness_cm: Optional[int],
    required_price_field: str = "fob_slab_usd",
) -> Optional[Dict[str, Any]]:
    variants = product.get("variants", [])
    if thickness_cm is None:
        return next((v for v in variants if v.get(required_price_field) is not None), None)
    return next(
        (
            v
            for v in variants
            if v.get("thickness_cm") == thickness_cm and v.get(required_price_field) is not None
        ),
        None,
    )


def filter_products(
    catalog: CatalogContext,
    color: Optional[str] = None,
    thickness_cm: Optional[int] = None,
    max_price: Optional[float] = None,
    market: str = "US",
    price_unit: Optional[str] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    color_query = (color or "").strip().lower()
    matches: List[Dict[str, Any]] = []
    normalized_market = (market or catalog.market or "US").strip().upper() or "US"
    price_field = resolve_price_field(normalized_market, price_unit)

    for product in catalog.products:
        if color_query:
            color_tokens = [
                (product.get("color") or "").lower(),
                (product.get("color_group") or "").lower(),
                (product.get("series") or "").lower(),
                (product.get("pattern") or "").lower(),
            ]
            if not any(color_query in token for token in color_tokens):
                continue

        variant = find_variant(product, thickness_cm, required_price_field=price_field)
        if not variant:
            continue

        unit_price = variant.get(price_field)
        if max_price is not None:
            if unit_price is None or unit_price > max_price:
                continue

        resolved_unit = "sqm" if price_field.endswith("sqm_vnd") or price_field == "fob_sqm_usd" else "sf" if price_field.endswith("sf_vnd") or price_field == "fob_sf_usd" else "slab"
        resolved_currency = "VND" if price_field.endswith("_vnd") else "USD"

        matches.append(
            {
                "code": product.get("code"),
                "color": product.get("color"),
                "color_group": product.get("color_group"),
                "series": product.get("series"),
                "pattern": product.get("pattern"),
                "description": product.get("description"),
                "photo_url": product.get("photo_url"),
                "product_url": product.get("product_url"),
                "variant": variant,
                "market": normalized_market,
                "price_unit": resolved_unit,
                "price_currency": resolved_currency,
                "unit_price": unit_price,
                "unit_price_usd": unit_price if resolved_currency == "USD" else None,
                "unit_price_vnd": unit_price if resolved_currency == "VND" else None,
            }
        )

        if len(matches) >= max(1, limit):
            break

    return matches
