from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.catalog import (
    filter_products,
    find_variant,
    get_product_by_code,
    load_catalog,
    normalize_code,
    resolve_price_field,
    save_catalog_json,
)


VN_CSV_PATH = Path(__file__).resolve().parents[1] / "Lux Quartz VN.csv"


def test_load_vn_catalog_detects_market():
    catalog = load_catalog(VN_CSV_PATH)
    assert catalog.market == "VN"
    assert len(catalog.products) > 0


def test_lookup_vn_product_lq504():
    catalog = load_catalog(VN_CSV_PATH)
    product = get_product_by_code(catalog, "lq-504")
    assert product is not None
    assert product["code"] == "LQ 504"
    assert product["market"] == "VN"
    assert product["currency"] == "VND"
    assert product["color"] == "Trắng"
    assert product["series"] != ""


def test_vn_variant_has_vnd_prices():
    catalog = load_catalog(VN_CSV_PATH)
    product = get_product_by_code(catalog, "LQ 504")
    assert product is not None

    variant = find_variant(product, 2, required_price_field="price_slab_vnd")
    assert variant is not None
    assert variant["price_sqm_vnd"] == 2203200.0
    assert variant["price_slab_vnd"] == 14177592.0
    assert variant["price_sf_vnd"] == 204682.27


def test_filter_vn_products_uses_vnd_market_default():
    catalog = load_catalog(VN_CSV_PATH)
    matches = filter_products(
        catalog,
        color="trắng",
        thickness_cm=2,
        max_price=2300000,
        market="VN",
        limit=10,
    )
    assert len(matches) > 0
    assert all(item["price_unit"] == "sqm" for item in matches)
    assert all(item["price_currency"] == "VND" for item in matches)
    assert all(item["unit_price_vnd"] is not None for item in matches)


def test_filter_vn_products_supports_slab_price_unit():
    catalog = load_catalog(VN_CSV_PATH)
    matches = filter_products(
        catalog,
        color="trắng",
        thickness_cm=2,
        max_price=15000000,
        market="VN",
        price_unit="slab",
        limit=10,
    )
    assert len(matches) > 0
    assert all(item["price_unit"] == "slab" for item in matches)
    assert all(item["price_currency"] == "VND" for item in matches)


def test_resolve_price_field_for_vn_units():
    assert resolve_price_field("VN", None) == "price_sqm_vnd"
    assert resolve_price_field("VN", "sf") == "price_sf_vnd"
    assert resolve_price_field("VN", "slab") == "price_slab_vnd"


def test_normalize_code_still_applies_for_vn_codes():
    assert normalize_code("LQ 9314") == "LQ9314"
    assert normalize_code(" lc-311 ") == "LC311"


def test_save_catalog_json_keeps_vn_market(tmp_path):
    catalog = load_catalog(VN_CSV_PATH)
    output_path = tmp_path / "vn.json"
    save_catalog_json(catalog, output_path)
    payload = output_path.read_text(encoding="utf-8")
    assert '"market": "VN"' in payload
