from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app, tool_calculate_quote



def test_health_includes_vn_market():
    client = TestClient(app)
    res = client.get("/api/health")
    assert res.status_code == 200
    payload = res.json()
    assert "VN" in payload["enabled_markets"]
    assert "US" in payload["enabled_markets"]
    assert payload["catalog_count_by_market"]["VN"] > 0


def test_chat_vn_market_returns_reply_without_rejection():
    original = main_module.ORCHESTRATOR
    main_module.ORCHESTRATOR = None
    try:
        client = TestClient(app)
        res = client.post(
            "/api/chat",
            json={
                "sessionId": "vn-chat-1",
                "market": "VN",
                "message": "báo giá mã LQ 504",
            },
        )
        assert res.status_code == 200
        payload = res.json()
        assert payload["market"] == "VN"
        assert payload["language"] == "vi"
        assert payload["reply"] != ""
    finally:
        main_module.ORCHESTRATOR = original


def test_tool_calculate_quote_vn_uses_vnd_currency():
    out = tool_calculate_quote(code="LQ 504", thickness_cm=2, area_sqm=12, market="VN")
    assert out["ok"] is True
    quote = out["quote"]
    assert quote["market"] == "VN"
    assert quote["currency"] == "VND"
    assert quote["price_per_slab_vnd"] is not None
    assert quote["base_quote"]["total_vnd"] is not None
