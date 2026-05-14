from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import (
    app,
    _technical_specs_reply,
    _is_product_related_query,
    _detect_explicit_price_unit,
    _clarify_non_product_reply,
    _project_services_reply,
    _correction_recovery_reply,
    _sanitize_chat_style,
    _feng_shui_color_reply,
    _ensure_non_product_guard,
    _ensure_smart_followup,
    _extract_phone_contact,
    tool_calculate_quote,
)
from app.session_store import SessionStore


def test_session_key_is_market_scoped():
    us_key = SessionStore.make_key("US", "abc")
    vn_key = SessionStore.make_key("VN", "abc")
    assert us_key != vn_key
    assert us_key == "US:abc"
    assert vn_key == "VN:abc"


def test_session_history_isolated_by_market_key():
    store = SessionStore(max_turns=3)
    us_key = SessionStore.make_key("US", "shared")
    vn_key = SessionStore.make_key("VN", "shared")

    store.append_turn(us_key, "hello us", "reply us")
    store.append_turn(vn_key, "hello vn", "reply vn")

    us_history = store.get_history(us_key)
    vn_history = store.get_history(vn_key)

    assert len(us_history) == 2
    assert len(vn_history) == 2
    assert us_history[0]["content"] == "hello us"
    assert vn_history[0]["content"] == "hello vn"


def test_chat_market_supports_vn_market():
    client = TestClient(app)
    res = client.post(
        "/api/chat",
        json={
            "sessionId": "t-1",
            "market": "VN",
            "message": "bao gia lq 504",
        },
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload["market"] == "VN"
    assert "reply" in payload


def test_chat_allows_us_market():
    client = TestClient(app)
    res = client.post(
        "/api/chat",
        json={
            "sessionId": "t-2",
            "market": "US",
            "message": "Quote LQ 504 2cm for 120 sf",
        },
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload["market"] == "US"
    assert "reply" in payload


def test_chat_quality_query_includes_technical_specs():
    original = main_module.ORCHESTRATOR
    main_module.ORCHESTRATOR = None
    try:
        client = TestClient(app)
        res = client.post(
            "/api/chat",
            json={
                "sessionId": "t-3",
                "market": "US",
                "message": "How is the durability and hardness of Lux Quartz?",
                "page": {"url": "https://luxquartzvietnam.com/en/", "title": "Lux Quartz US"},
            },
        )
        assert res.status_code == 200
        reply = res.json()["reply"]
        assert "6.0-7.0 Mohs" in reply
        assert "<0.05%" in reply
        assert "35.0-55.0 MPa" in reply
        assert "C4" in reply
    finally:
        main_module.ORCHESTRATOR = original


def test_technical_specs_reply_recommends_3cm_for_premium_island():
    reply = _technical_specs_reply("Need premium island recommendation", language="en")
    assert "recommend 3cm" in reply
    assert "2cm" in reply


def test_non_product_query_detection():
    assert _is_product_related_query("price LQ 504") is True
    assert _is_product_related_query("bạn tư vấn cho tôi loại đá nào cho bếp 20m2") is True
    assert _is_product_related_query("gemini") is False


def test_detect_explicit_price_unit_prefers_sf_for_us_query():
    assert _detect_explicit_price_unit("under $10/sf", "US") == "sf"
    assert _detect_explicit_price_unit("budget under 50 usd/sqm", "US") == "sqm"
    assert _detect_explicit_price_unit("need quote per slab", "US") == "slab"


def test_clarify_non_product_reply_rotates_naturally_vi():
    r1 = _clarify_non_product_reply(message="gemini details please support me with context", language="vi")
    r2 = _clarify_non_product_reply(message="gemini details please support me with context", language="vi")
    assert r1 != ""
    assert r2 != ""
    assert r1 == r2


def test_clarify_non_product_reply_is_brief_for_short_message_en():
    reply = _clarify_non_product_reply(message="gemini", language="en")
    assert "quote per sf" in reply.lower()


def test_greeting_message_returns_friendly_sales_bridge_vi():
    reply = _ensure_non_product_guard(reply="placeholder", message="xin chào", language="vi")
    normalized = reply.lower()
    assert "lux quartz xin chào" in normalized
    assert "trợ lý ảo" in normalized
    assert "sf" in normalized


def test_project_services_reply_contains_core_services_vi():
    reply = _project_services_reply(language="vi")
    assert "Private Label" in reply
    assert "Video Call" in reply
    assert "Bundle" in reply
    assert "Kiểm kho" in reply


def test_correction_recovery_reply_has_apology_and_recalc_vi():
    reply = _correction_recovery_reply(language="vi")
    assert "xin lỗi" in reply
    assert "tính lại" in reply


def test_sanitize_chat_style_removes_forbidden_markdown_markers():
    cleaned = _sanitize_chat_style("## Title\n**Bold** and __underline__ and *star* text")
    assert "##" not in cleaned
    assert "**" not in cleaned
    assert "__" not in cleaned


def test_sanitize_chat_style_removes_images_and_localizes_product_links():
    cleaned = _sanitize_chat_style(
        "![slab](https://luxquartzvietnam.com/wp-content/uploads/a.jpg) "
        "[ảnh mẫu](https://luxquartzvietnam.com/product/calacatta-michelo-classic-lq-914/)"
    )
    assert "![" not in cleaned
    assert "<img" not in cleaned
    assert "wp-content/uploads" not in cleaned
    assert "[Xem chi tiết sản phẩm tại đây]" in cleaned
    assert "https://luxquartzvietnam.com/product/calacatta-michelo-classic-lq-914/" in cleaned
    assert cleaned.count("[Xem chi tiết sản phẩm tại đây]") == 1


def test_feng_shui_reply_rejects_nonexistent_green_and_suggests_black_grey():
    reply = _feng_shui_color_reply("mệnh mộc hợp mã xanh lá nào", language="vi")
    assert reply is not None
    normalized = reply.lower()
    assert "chưa có color_group xanh lá" in normalized
    assert "black/grey" in normalized
    assert "lq 809" in normalized


def test_feng_shui_reply_mentions_sqm_to_sf_conversion():
    reply = _feng_shui_color_reply("phong thủy mệnh mộc báo giá theo m2", language="vi")
    assert reply is not None
    normalized = reply.lower()
    assert "x10.764" in reply
    assert "m²" in reply
    assert "sf" in normalized


def test_tool_calculate_quote_supports_area_sqm_conversion():
    out = tool_calculate_quote(code="LQ 504", thickness_cm=2, area_sqm=12)
    assert out["ok"] is True
    quote = out["quote"]
    assert quote["source_area_unit"] == "sqm"
    assert quote["conversion_factor"] == 10.764
    assert quote["area_sqm"] == 12
    assert quote["area_sf"] == 129.17


def test_tool_calculate_quote_15sqm_uses_3_slabs_for_cost_optimized_plan():
    out = tool_calculate_quote(code="LQ 504", thickness_cm=2, area_sqm=15)
    assert out["ok"] is True
    quote = out["quote"]
    assert quote["source_area_unit"] == "sqm"
    assert quote["base_quote"]["slabs"] == 3
    assert quote["buffer_quote"]["slabs"] == 3
    assert quote["buffer_quote"]["total"] == quote["price_per_slab"] * 3


def test_extract_phone_contact_accepts_only_phone_like_message():
    assert _extract_phone_contact("0372704441") == "0372704441"
    assert _extract_phone_contact("0372 704 441") == "0372704441"
    assert _extract_phone_contact("LQ 504") is None
    assert _extract_phone_contact("120 sf") is None


def test_chat_phone_only_message_acknowledges_contact_instead_of_fallback():
    original = main_module.ORCHESTRATOR
    main_module.ORCHESTRATOR = None
    try:
        client = TestClient(app)
        res = client.post(
            "/api/chat",
            json={
                "sessionId": "t-phone-1",
                "market": "US",
                "message": "0372704441",
                "page": {"url": "https://luxquartzvietnam.com/", "title": "Lux Quartz"},
            },
        )
        assert res.status_code == 200
        payload = res.json()
        reply = payload["reply"].lower()
        assert "đã nhận được số điện thoại của mình" in reply
        assert "i may have missed your intent" not in reply
        assert "zalo" in reply
        assert payload["structured"]["intent"] == "lead_capture"
    finally:
        main_module.ORCHESTRATOR = original


def test_chat_special_context_returns_consultative_sales_reply():
    original = main_module.ORCHESTRATOR
    main_module.ORCHESTRATOR = None
    try:
        client = TestClient(app)
        res = client.post(
            "/api/chat",
            json={
                "sessionId": "t-context-1",
                "market": "US",
                "message": "nhà gần biển và bếp ngoài trời thì nên chọn thế nào",
            },
        )
        assert res.status_code == 200
        reply = res.json()["reply"].lower()
        assert "0.05" in reply
        assert "35.0" in reply
        assert "mohs" in reply
        assert "ngoài mẫu này" not in reply
    finally:
        main_module.ORCHESTRATOR = original


def test_chat_vi_response_removes_english_template_tail():
    original = main_module.ORCHESTRATOR
    main_module.ORCHESTRATOR = None
    try:
        client = TestClient(app)
        res = client.post(
            "/api/chat",
            json={
                "sessionId": "t-vi-1",
                "market": "US",
                "message": "bao gia lq 504",
            },
        )
        assert res.status_code == 200
        payload = res.json()
        reply = payload["reply"].lower()
        assert payload["language"] == "vi"
        assert "would you like me to send real slab photos" not in reply
        assert "please share your whatsapp" not in reply
        assert reply.count("ngoài mẫu này") <= 1
    finally:
        main_module.ORCHESTRATOR = original


def test_chat_kitchen_area_query_returns_suggestions_instead_of_generic_clarify():
    original = main_module.ORCHESTRATOR
    main_module.ORCHESTRATOR = None
    try:
        client = TestClient(app)
        res = client.post(
            "/api/chat",
            json={
                "sessionId": "t-kitchen-20m2",
                "market": "VN",
                "message": "bạn tư vấn cho tôi loại đá nào mà có thể dùng cho phòng bếp với diện tích 20m2 đi",
            },
        )
        assert res.status_code == 200
        reply = res.json()["reply"].lower()
        assert "20 m²" in reply or "20m²" in reply or "20m2" in reply
        assert "gợi ý" in reply
        assert "lq" in reply or "lc" in reply
        assert "ceil(" not in reply
        assert "tấm" in reply
        assert "chia sẻ giúp em khu vực lắp đặt" not in reply
    finally:
        main_module.ORCHESTRATOR = original


def test_ensure_smart_followup_dedupes_duplicate_feng_shui_question():
    duplicated = (
        "Dạ em gợi ý mã LQ 504 phù hợp.\n\n"
        "Ngoài mẫu này, Anh/Chị có muốn em tư vấn về màu hợp phong thủy hay cách bảo dưỡng mặt đá không ạ?\n\n"
        "Ngoài mẫu này, Anh/Chị có muốn em tư vấn về màu hợp phong thủy hay cách bảo dưỡng mặt đá không ạ?"
    )
    normalized = _ensure_smart_followup(duplicated, message="báo giá LQ 504", language="vi").lower()
    assert normalized.count("ngoài mẫu này, anh/chị có muốn em tư vấn về màu hợp phong thủy hay cách bảo dưỡng mặt đá không ạ?") == 1


def test_chat_area_only_still_returns_required_slab_estimation():
    original = main_module.ORCHESTRATOR
    main_module.ORCHESTRATOR = None
    try:
        client = TestClient(app)
        res = client.post(
            "/api/chat",
            json={
                "sessionId": "t-area-only-1",
                "market": "VN",
                "message": "mình có diện tích 20m2",
            },
        )
        assert res.status_code == 200
        reply = res.json()["reply"].lower()
        assert "ceil(" not in reply
        assert "tấm" in reply
    finally:
        main_module.ORCHESTRATOR = original


def test_chat_reuses_session_area_context_without_asking_area_again():
    original = main_module.ORCHESTRATOR
    main_module.ORCHESTRATOR = None
    try:
        client = TestClient(app)
        first = client.post(
            "/api/chat",
            json={
                "sessionId": "t-area-memory-1",
                "market": "VN",
                "message": "bếp nhà mình 20m2",
            },
        )
        assert first.status_code == 200

        second = client.post(
            "/api/chat",
            json={
                "sessionId": "t-area-memory-1",
                "market": "VN",
                "message": "mã LQ 504 ổn không em",
            },
        )
        assert second.status_code == 200
        reply = second.json()["reply"].lower()
        assert "ceil(" not in reply
        assert "cho em xin diện tích" not in reply
        assert "chia sẻ giúp em khu vực lắp đặt" not in reply
    finally:
        main_module.ORCHESTRATOR = original


def test_chat_calacatta_and_carrara_query_returns_representative_codes_instead_of_not_found():
    original = main_module.ORCHESTRATOR
    main_module.ORCHESTRATOR = None
    try:
        client = TestClient(app)
        res = client.post(
            "/api/chat",
            json={
                "sessionId": "t-representative-1",
                "market": "VN",
                "message": "mình thích calacatta hoặc carrara cho bếp 20m2",
            },
        )
        assert res.status_code == 200
        reply = res.json()["reply"].lower()
        assert "lq 914" in reply
        assert "lq 703" in reply
        assert "không tìm thấy" not in reply
    finally:
        main_module.ORCHESTRATOR = original


def test_chat_calacatta_carrara_with_markdown_area_shows_three_slabs_and_no_auto_fengshui_followup():
    original = main_module.ORCHESTRATOR
    main_module.ORCHESTRATOR = None
    try:
        client = TestClient(app)
        res = client.post(
            "/api/chat",
            json={
                "sessionId": "t-representative-15m2",
                "market": "VN",
                "message": "anh đang phân vân giữa dòng Calacatta và Carrara, tổng diện tích khoảng 15 $m^2$",
            },
        )
        assert res.status_code == 200
        reply = res.json()["reply"].lower()
        assert "lq 914" in reply
        assert "lq 703" in reply
        assert "ceil(" not in reply
        assert "3 tấm" in reply
        assert "ngoài mẫu này" not in reply
    finally:
        main_module.ORCHESTRATOR = original


def test_chat_comparison_question_returns_direct_calacatta_vs_carrara_answer():
    original = main_module.ORCHESTRATOR
    main_module.ORCHESTRATOR = None
    try:
        client = TestClient(app)
        first = client.post(
            "/api/chat",
            json={
                "sessionId": "t-compare-context-1",
                "market": "VN",
                "message": "anh đang cân nhắc calacatta với carrara",
            },
        )
        assert first.status_code == 200

        second = client.post(
            "/api/chat",
            json={
                "sessionId": "t-compare-context-1",
                "market": "VN",
                "message": "Em so sánh giúp anh độ bền và khả năng chống ố của hai dòng này khác gì nhau không?",
            },
        )
        assert second.status_code == 200
        reply = second.json()["reply"].lower()
        assert "cả hai dòng đều có độ cứng 6-7 mohs" in reply
        assert "chống ố tuyệt đối" in reply
        assert "calacatta thường có vân to" in reply
        assert "carrara mang nét thanh lịch cổ điển" in reply
        assert "em hỗ trợ phần này trực tiếp" not in reply
        assert "ngoài mẫu này" not in reply
    finally:
        main_module.ORCHESTRATOR = original


def test_ensure_smart_followup_only_appends_after_quote_or_resolved_signal():
    normal_reply = "Dạ em gợi ý 2 mã phù hợp là LQ 914 và LQ 703."
    kept = _ensure_smart_followup(normal_reply, message="anh đang cân nhắc", language="vi").lower()
    assert "ngoài mẫu này" not in kept

    quote_reply = "Dạ em đã tính xong. Ước tính cơ bản: 3 tấm = ₫42,741,270."
    with_followup = _ensure_smart_followup(quote_reply, message="báo giá giúp anh", language="vi").lower()
    assert "ngoài mẫu này" in with_followup


def test_chat_lq914_west_tv_wall_12sqm_3cm_uses_three_slabs_and_positive_uv_heat_guidance():
    original = main_module.ORCHESTRATOR
    main_module.ORCHESTRATOR = None
    try:
        client = TestClient(app)
        res = client.post(
            "/api/chat",
            json={
                "sessionId": "t-lq914-west-tv-12sqm",
                "market": "VN",
                "message": "Anh cần mã LQ 914 loại 3cm cho vách tivi hướng Tây, diện tích 12 $m^2$",
            },
        )
        assert res.status_code == 200
        reply = res.json()["reply"].lower()
        assert "lq 914" in reply
        assert "ước tính 3cm +25% từ giá 2cm" in reply
        assert "3 tấm" in reply
        assert "ceil(" not in reply
        assert "x10.764" not in reply
        assert "bền màu uv" in reply
        assert "chịu nhiệt tốt" in reply
        assert "phù hợp" in reply
        assert "độ bền nhiệt tốt hơn" not in reply
        assert "tìm loại đá khác" not in reply
        assert reply.count("ngoài mẫu này") == 1
    finally:
        main_module.ORCHESTRATOR = original


def test_chat_vn_lq914_25sqm_3cm_quote_uses_six_slabs_without_formula():
    original = main_module.ORCHESTRATOR
    main_module.ORCHESTRATOR = None
    try:
        client = TestClient(app)
        res = client.post(
            "/api/chat",
            json={
                "sessionId": "t-lq914-25sqm-vn",
                "market": "VN",
                "message": "Anh cần báo giá LQ 914 loại 3cm cho 25 $m^2$, gửi thông số kỹ thuật, chiết khấu trên 10 tấm và vận chuyển. SĐT 0372704441",
            },
        )
        assert res.status_code == 200
        payload = res.json()
        reply = payload["reply"].lower()
        assert payload["language"] == "vi"
        assert "lq 914" in reply
        assert "đã nhận được số điện thoại" in reply
        assert "5 tấm cho phương án cơ bản" in reply
        assert "6 tấm khi tính trừ hao 5%" in reply
        assert "1 tấm" not in reply
        assert "ceil(" not in reply
        assert "x10.764" not in reply
        assert "công thức minh bạch" not in reply
        assert "độ cứng 6.0-7.0 mohs" in reply
        assert "[xem chi tiết sản phẩm tại đây]" in reply
        assert "ngoài mẫu này" in reply
        assert reply.count("ngoài mẫu này") == 1
    finally:
        main_module.ORCHESTRATOR = original


def test_chat_vn_market_english_quote_answers_in_english_without_formula():
    original = main_module.ORCHESTRATOR
    main_module.ORCHESTRATOR = None
    try:
        client = TestClient(app)
        res = client.post(
            "/api/chat",
            json={
                "sessionId": "t-lq914-25sqm-en",
                "market": "VN",
                "message": "Please quote LQ 914 3cm for 25 sqm with 5% allowance, technical specifications, bulk discount over 10 slabs, delivery handling. My phone is 0372704441",
            },
        )
        assert res.status_code == 200
        payload = res.json()
        reply = payload["reply"].lower()
        assert payload["market"] == "VN"
        assert payload["language"] == "en"
        assert "thanks, i have received your phone number" in reply
        assert "slabs needed" in reply
        assert "5 slabs for the base area" in reply
        assert "6 slabs with a 5% allowance" in reply
        assert "1 slab" not in reply
        assert "ceil(" not in reply
        assert "x10.764" not in reply
        assert "dạ" not in reply
        assert "anh/chị" not in reply
        assert "product link" in reply
        assert "view product details here" in reply
        assert "xem chi tiết sản phẩm tại đây" not in reply
    finally:
        main_module.ORCHESTRATOR = original
