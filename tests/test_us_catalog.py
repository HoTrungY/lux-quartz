from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.catalog import (
    load_catalog,
    normalize_code,
    get_product_by_code,
    filter_products,
    find_variant,
    resolve_price_field,
    normalize_color_group,
    build_product_url,
)
from app.quote import calculate_quote, sqm_to_sf


CSV_PATH = Path(__file__).resolve().parents[1] / "Lux Quartz My.csv"


def test_normalize_code_variants():
    assert normalize_code("LQ 504") == "LQ504"
    assert normalize_code("lq-504") == "LQ504"
    assert normalize_code(" LC 311 ") == "LC311"


def test_load_catalog_and_lookup_lq504():
    catalog = load_catalog(CSV_PATH)
    assert len(catalog.products) > 0

    product = get_product_by_code(catalog, "lq-504")
    assert product is not None
    assert product["code"] == "LQ 504"
    assert product["color_group"] == "White"

    v2 = find_variant(product, 2)
    v3 = find_variant(product, 3)
    assert v2 is not None and v2["fob_slab_usd"] == 266.24
    assert v3 is not None and v3["fob_slab_usd"] == 317.44


def test_filter_products_by_color_and_thickness():
    catalog = load_catalog(CSV_PATH)
    matches = filter_products(catalog, color="white", thickness_cm=3, limit=5)
    assert len(matches) > 0
    for item in matches:
        variant = item["variant"]
        assert variant["thickness_cm"] == 3
        assert variant["fob_slab_usd"] is not None


def test_calculate_quote_base_and_buffer():
    quote = calculate_quote(
        code="LQ 504",
        thickness_cm=2,
        area_sf=120,
        fob_slab_usd=266.24,
        buffer_ratio=1.05,
    )

    assert quote["base_quote"]["slabs"] == 3
    assert quote["base_quote"]["total_usd"] == 798.72
    assert quote["buffer_quote"]["slabs"] == 3
    assert quote["buffer_quote"]["total_usd"] == 798.72


def test_calculate_quote_reject_invalid_area():
    try:
        calculate_quote(
            code="LQ 504",
            thickness_cm=2,
            area_sf=0,
            fob_slab_usd=266.24,
        )
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "area_sf" in str(exc)


def test_quote_formula_transparency_fields_present():
    quote = calculate_quote(
        code="LQ 504",
        thickness_cm=2,
        area_sf=120,
        fob_slab_usd=266.24,
        buffer_ratio=1.05,
    )
    assert "ceil(120.00 / 55.11)" in quote["base_quote"]["formula"]
    assert quote["base_quote"]["slabs"] == 3
    assert "ceil((120.00 * 1.05) / 55.11)" in quote["buffer_quote"]["formula"]
    assert quote["buffer_quote"]["slabs"] == 3


def test_filter_products_uses_market_default_unit():
    catalog = load_catalog(CSV_PATH)

    us_matches = filter_products(
        catalog,
        color="white",
        thickness_cm=2,
        max_price=4.9,
        market="US",
        limit=20,
    )
    assert len(us_matches) > 0
    assert all(item["price_unit"] == "sf" for item in us_matches)
    assert all(item["price_currency"] == "USD" for item in us_matches)
    assert all(item["unit_price_usd"] <= 4.9 for item in us_matches)


def test_filter_products_respects_explicit_price_unit_override():
    catalog = load_catalog(CSV_PATH)
    matches = filter_products(
        catalog,
        color="white",
        thickness_cm=2,
        max_price=280,
        market="US",
        price_unit="slab",
        limit=10,
    )
    assert len(matches) > 0
    assert all(item["price_unit"] == "slab" for item in matches)


def test_resolve_price_field_by_market_and_unit():
    assert resolve_price_field("US", None) == "fob_sf_usd"
    assert resolve_price_field("VN", None) == "price_sqm_vnd"
    assert resolve_price_field("VN", "slab") == "price_slab_vnd"
    assert resolve_price_field("US", "slab") == "fob_slab_usd"


def test_normalize_color_group_enforces_allowed_values():
    assert normalize_color_group("White", "") == "White"
    assert normalize_color_group("Blue", "Blue Ripple") == "Cream"
    assert normalize_color_group("Grey", "") == "Grey"
    assert normalize_color_group("", "Pure Black") == "Black"
    assert normalize_color_group("", "Warm Beige") == "Cream"


def test_catalog_has_product_url():
    catalog = load_catalog(CSV_PATH)
    product = get_product_by_code(catalog, "lq-501")
    assert product is not None
    assert product["product_url"] == "https://luxquartzvietnam.com/product/lq-501/"


def test_build_product_url_from_code():
    assert build_product_url("LQ 501") == "https://luxquartzvietnam.com/product/lq-501/"
    assert build_product_url("LC-311") == "https://luxquartzvietnam.com/product/lc-311/"


def test_sqm_to_sf_conversion_factor_exact():
    assert round(sqm_to_sf(1), 3) == 10.764


def test_calculate_quote_with_sqm_converted_area():
    area_sqm = 12
    converted_sf = sqm_to_sf(area_sqm)
    quote = calculate_quote(
        code="LQ 504",
        thickness_cm=2,
        area_sf=converted_sf,
        fob_slab_usd=266.24,
        buffer_ratio=1.05,
    )
    assert quote["area_sf"] == round(converted_sf, 2)
    assert quote["base_quote"]["slabs"] == 3
