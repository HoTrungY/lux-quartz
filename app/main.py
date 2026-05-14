from __future__ import annotations

import hashlib
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.catalog import CatalogContext, filter_products, find_variant, get_product_by_code, load_catalog, resolve_price_field
from app.openai_client import OpenAIOrchestrator
from app.quote import calculate_quote, sqm_to_sf
from app.rag import RAGRetriever
from app.session_store import SessionStore

BASE_DIR = Path(__file__).resolve().parents[1]

MARKET_CONFIGS: Dict[str, Dict[str, Any]] = {
    "US": {
        "csv_path": BASE_DIR / "Lux Quartz My.csv",
        "prompt_path": BASE_DIR / "prompts" / "lux_quartz_us_system.md",
        "chroma_dir": BASE_DIR / "data" / "chroma_us",
        "chroma_collection": "lux_quartz_us_products",
        "service_name": "lux-quartz-us-chatbot",
    },
    "VN": {
        "csv_path": BASE_DIR / "Lux Quartz VN.csv",
        "prompt_path": BASE_DIR / "prompts" / "lux_quartz_vn_system.md",
        "chroma_dir": BASE_DIR / "data" / "chroma_vn",
        "chroma_collection": "lux_quartz_vn_products",
        "service_name": "lux-quartz-vn-chatbot",
    },
}

load_dotenv(BASE_DIR / ".env")

DEFAULT_MARKET = (os.getenv("LUX_DEFAULT_MARKET", "US") or "US").upper()
BUFFER_RATIO = float(os.getenv("LUX_BUFFER_RATIO", "1.05"))
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
CHAT_MAX_TURNS = int(os.getenv("LUX_CHAT_MAX_TURNS", "10"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()


def _load_prompt(market: str) -> str:
    config = MARKET_CONFIGS.get(market, MARKET_CONFIGS["US"])
    prompt_path = config["prompt_path"]
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    if market == "VN":
        return "Bạn là trợ lý tư vấn Lux Quartz cho thị trường Việt Nam."
    return "You are a Lux Quartz US sales assistant."


def _load_catalog(market: str) -> CatalogContext:
    config = MARKET_CONFIGS.get(market, MARKET_CONFIGS["US"])
    return load_catalog(config["csv_path"], market=market)


def _build_market_runtime() -> tuple[Dict[str, CatalogContext], Dict[str, RAGRetriever], Dict[str, Optional[OpenAIOrchestrator]], Dict[str, str]]:
    catalogs: Dict[str, CatalogContext] = {}
    retrievers: Dict[str, RAGRetriever] = {}
    orchestrators: Dict[str, Optional[OpenAIOrchestrator]] = {}
    prompts: Dict[str, str] = {}

    for market, config in MARKET_CONFIGS.items():
        catalog = _load_catalog(market)
        catalogs[market] = catalog
        retrievers[market] = RAGRetriever(
            catalog=catalog,
            chroma_dir=config["chroma_dir"],
            collection_name=config["chroma_collection"],
        )
        prompt = _load_prompt(market)
        prompts[market] = prompt

        if OPENAI_API_KEY:
            try:
                orchestrators[market] = OpenAIOrchestrator(
                    api_key=OPENAI_API_KEY,
                    model=MODEL_NAME,
                    system_prompt=prompt,
                )
            except Exception:
                orchestrators[market] = None
        else:
            orchestrators[market] = None

    return catalogs, retrievers, orchestrators, prompts


CATALOGS, RAGS, ORCHESTRATORS, SYSTEM_PROMPTS = _build_market_runtime()
SESSION_STORE = SessionStore(max_turns=CHAT_MAX_TURNS)
CATALOG = CATALOGS.get(DEFAULT_MARKET, CATALOGS["US"])
RAG = RAGS.get(DEFAULT_MARKET, RAGS["US"])
ORCHESTRATOR = ORCHESTRATORS.get(DEFAULT_MARKET)

CODE_FINDER = re.compile(r"\b(?:LQ|LC)\s*-?\s*\d{3,4}\b", re.IGNORECASE)
TECH_SPEC_PATTERN = re.compile(
    r"\b(quality|durability|durable|hardness|mohs|absorption|water\s*absorption|flexural|mpa|chemical\s*resistance|c4|scratch|stain|heat\s*resistant|performance|technical\s*spec)\b",
    re.IGNORECASE,
)
PRODUCT_INTENT_PATTERN = re.compile(
    r"\b(quartz|stone|countertop|kitchen|bathroom|code|lq\s*-?\s*\d{3,4}|lc\s*-?\s*\d{3,4}|color|series|pattern|quote|price|pricing|budget|under\s*\$|sf|sq\s*ft|sqm|sq\s*m|m2|m²|slab|photo|sample|catalog|bao\s*gia|mau|ma\s*da|thach\s*anh|do\s*ben|độ\s*bền|chong\s*o|chống\s*ố|so\s*sanh|so\s*sánh|khac\s*gi|khác\s*gì|compare|durability|stain|ky\s*thuat|tu\s*van|tư\s*vấn|goi\s*y|gợi\s*ý|loai\s*da|loại\s*đá|dien\s*tich|diện\s*tích|phong\s*bep|phòng\s*bếp|nha\s*bep|nhà\s*bếp|vach\s*tivi|vách\s*tivi|huong\s*tay|hướng\s*tây|project|private\s*label|video\s*call|bundle|container|shipping|logistics|inspection|qc|kiem\s*hang|du\s*an|nhan\s*rieng|van\s*chuyen|calacatta|carrara|crack|cracks|breakage|broken|damage|damaged|warranty|claim|issue)\b",
    re.IGNORECASE,
)
CORRECTION_PATTERN = re.compile(
    r"\b(wrong|incorrect|mistake|recheck|re-calc|recalculate|recalculate|unit|nham|sai|tinh\s*lai|kiem\s*tra\s*lai)\b",
    re.IGNORECASE,
)
PROJECT_SERVICE_PATTERN = re.compile(
    r"\b(project|private\s*label|video\s*call|bundle|container|shipping|logistics|inspection|qc|du\s*an|nhan\s*rieng|kiem\s*hang|van\s*chuyen)\b",
    re.IGNORECASE,
)
LEAD_TIME_PATTERN = re.compile(
    r"\b(how\s*long|delivery\s*time|lead\s*time|when\s*will\s*i\s*receive|eta|transit\s*time|shipping\s*time|thời\s*gian\s*giao|bao\s*lâu\s*nhận\s*hàng)\b",
    re.IGNORECASE,
)
PAYMENT_TERMS_PATTERN = re.compile(
    r"\b(payment|payment\s*terms|terms\s*of\s*payment|tt\s*deposit|b/?l\s*copy|bill\s*of\s*lading|l/c|letter\s*of\s*credit|thanh\s*toán|điều\s*khoản\s*thanh\s*toán)\b",
    re.IGNORECASE,
)
LOADING_CAPACITY_PATTERN = re.compile(
    r"\b(moq|min(?:imum)?\s*order|20\s*ft|container|loading\s*capacity|tons?/container|slabs?|mix\s*color|số\s*lượng\s*tối\s*thiểu|quy\s*cách\s*đóng\s*container|trọng\s*lượng\s*container)\b",
    re.IGNORECASE,
)
CUSTOM_PATTERN_PATTERN = re.compile(
    r"\b(custom\s*pattern|match\s*sample|sample\s*match|oem|odm|custom\s*design|vein\s*pattern|color\s*depth|mẫu\s*đá\s*theo\s*yêu\s*cầu|match\s*mẫu|làm\s*mẫu\s*theo\s*yêu\s*cầu)\b",
    re.IGNORECASE,
)
COMPLAINT_PATTERN = re.compile(
    r"\b(crack|cracks|breakage|broken|damage|damaged|stain|defect|delamination|pitting|voids?)\b",
    re.IGNORECASE,
)
WARRANTY_INQUIRY_PATTERN = re.compile(
    r"\b(warranty|guarantee|after-sales|after sales|bảo\s*hành|hậu\s*mãi)\b",
    re.IGNORECASE,
)
WARRANTY_CONTACT_PATTERN = re.compile(
    r"\b(contact|reach|hotline|phone|whatsapp|zalo|email|liên\s*hệ|số\s*điện\s*thoại|sdt|đầu\s*mối\s*hỗ\s*trợ|kênh\s*hỗ\s*trợ)\b",
    re.IGNORECASE,
)
WARRANTY_CLAIM_PROCESS_PATTERN = re.compile(
    r"\b(how\s*to\s*claim|claim\s*process|file\s*a\s*claim|submit\s*a\s*claim|submit\s*(?:a\s*)?warranty\s*claim|warranty\s*claim\s*process|how\s*to\s*submit|request\s*warranty|quy\s*trình|cách\s*gửi|gửi\s*yêu\s*cầu|thủ\s*tục|hồ\s*sơ|khiếu\s*nại\s*bảo\s*hành)\b",
    re.IGNORECASE,
)
WARRANTY_SCOPE_PATTERN = re.compile(
    r"\b(policy|coverage|covered|what\s*is\s*covered|warranty\s*term|phạm\s*vi|chính\s*sách|điều\s*kiện\s*bảo\s*hành|bảo\s*hành\s*bao\s*lâu)\b",
    re.IGNORECASE,
)
WARRANTY_FULL_POLICY_PATTERN = re.compile(
    r"\b(chính\s*sách\s*bảo\s*hành|warranty\s*policy|warranty\s*overview|warranty\s*protection|company\s*warranty|company'?s\s*warranty|after-?sales\s*policy|quy\s*định\s*bảo\s*hành|bảo\s*hành\s*như\s*thế\s*nào|chế\s*độ\s*bảo\s*hành\s*của\s*công\s*ty|hiểu\s*chính\s*sách\s*bảo\s*hành\s*trước\s*khi\s*đặt\s*hàng|understand\s*your\s*warranty\s*before\s*ordering)\b",
    re.IGNORECASE,
)
WARRANTY_EXCLUSION_PATTERN = re.compile(
    r"\b(excluded|not\s*covered|exclusion|không\s*bảo\s*hành|không\s*được\s*bảo\s*hành|loại\s*trừ|không\s*áp\s*dụng|lắp\s*đặt\s*sai|va\s*đập|tác\s*động\s*mạnh|sử\s*dụng\s*sai|hóa\s*chất|tự\s*ý\s*sửa\s*đổi)\b",
    re.IGNORECASE,
)
WARRANTY_DOCUMENT_PATTERN = re.compile(
    r"\b(document|documents|paperwork|required\s*docs|hồ\s*sơ|giấy\s*tờ|chứng\s*từ|cần\s*gửi\s*gì|cần\s*những\s*gì|batch\s*number|hóa\s*đơn)\b",
    re.IGNORECASE,
)
WARRANTY_SHIPPING_PATTERN = re.compile(
    r"\b(shipping\s*damage|transit\s*damage|damage\s*on\s*arrival|lỗi\s*vận\s*chuyển|hư\s*hỏng\s*vận\s*chuyển|bể\s*vỡ\s*khi\s*nhận|nhận\s*hàng\s*bị\s*vỡ|24h)\b",
    re.IGNORECASE,
)
WARRANTY_AESTHETIC_PATTERN = re.compile(
    r"\b(aesthetic|surface\s*defect|color\s*mismatch|pinholes|pitting|sai\s*màu|rỗ\s*bề\s*mặt|lỗi\s*thẩm\s*mỹ|lỗi\s*bề\s*mặt)\b",
    re.IGNORECASE,
)
WARRANTY_REPLACEMENT_PATTERN = re.compile(
    r"\b(replacement|replace|1-?for-?1|1\s*đổi\s*1|đổi\s*mới|đổi\s*trả|đổi\s*ra\s*sao|đổi\s*như\s*thế\s*nào|xuất\s*kho\s*tấm\s*mới)\b",
    re.IGNORECASE,
)
WARRANTY_SLA_PATTERN = re.compile(
    r"\b(sla|response\s*time|timeline|how\s*long\s*to\s*respond|thời\s*gian\s*phản\s*hồi|bao\s*lâu\s*phản\s*hồi|phản\s*hồi\s*bao\s*lâu|thời\s*gian\s*xử\s*lý|thời\s*hạn\s*xử\s*lý)\b",
    re.IGNORECASE,
)
THERMAL_SHOCK_PATTERN = re.compile(
    r"\b(hot\s*pan|hot\s*pans|air\s*fryer|thermal\s*shock|heat\s*shock|đặt\s*nồi\s*nóng|nồi\s*nóng|sốc\s*nhiệt)\b",
    re.IGNORECASE,
)
RECEIVING_BREAKAGE_PATTERN = re.compile(
    r"\b(fob\s*houston|at\s*receipt|on\s*arrival|received\s*broken|broken\s*on\s*arrival|nhận\s*hàng\s*bị\s*bể|bể\s*vỡ\s*lúc\s*nhận|vỡ\s*do\s*vận\s*chuyển|a-?frame|crate|kiện\s*gỗ)\b",
    re.IGNORECASE,
)
POST_INSTALL_COLOR_PATTERN = re.compile(
    r"\b(installed|after\s*installation|already\s*installed|after\s*cut|wrong\s*color|color\s*mismatch|sai\s*màu|khác\s*màu|đã\s*lắp\s*đặt|đã\s*cắt)\b",
    re.IGNORECASE,
)
GREETING_PATTERN = re.compile(
    r"^(\s*)(hi|hello|hey|xin\s*chao|xin\s*chào|chao\s*ban|chào\s*bạn|chao\s*em|chào\s*em|chao\s*anh|chào\s*anh|chao\s*chi|chào\s*chị)([!.\s]*)$",
    re.IGNORECASE,
)
FENG_SHUI_PATTERN = re.compile(
    r"\b(feng\s*shui|phong\s*thuy|phong\s*thủy|mệnh|menh|ngũ\s*hành|ngu\s*hanh|hợp\s*mệnh|hop\s*menh)\b",
    re.IGNORECASE,
)
VI_HINT_PATTERN = re.compile(
    r"\b(xin\s*chao|xin\s*chào|chao|chào|bao\s*gia|báo\s*giá|gia|giá|mau|màu|ma\s*da|mã\s*đá|thach\s*anh|thạch\s*anh|phong\s*thuy|phong\s*thủy|tu\s*van|tư\s*vấn|bao\s*quan|bảo\s*quản|zalo|anh\s*chi|anh/chị|dien\s*tich|diện\s*tích|nha\s*bep|nhà\s*bếp|phong\s*bep|phòng\s*bếp|bep|bếp|bao\s*nhieu|bao\s*nhiêu|can\s*may\s*tam|cần\s*mấy\s*tấm|so\s*tam|số\s*tấm|thong\s*so|thông\s*số|do\s*cung|độ\s*cứng|hut\s*nuoc|hút\s*nước|chong\s*tham|chống\s*thấm|gan\s*bien|gần\s*biển|ngoai\s*troi|ngoài\s*trời|menh\s*thuy|mệnh\s*thủy)\b",
    re.IGNORECASE,
)
EN_HINT_PATTERN = re.compile(
    r"\b(hi|hello|hey|thanks|thank\s*you|quote|price|slab|countertop|kitchen|bathroom|whatsapp|photo|show|technical|spec)\b",
    re.IGNORECASE,
)
SPECIAL_CONTEXT_PATTERN = re.compile(
    r"\b(sa\s*mạc|sa\s*mac|gần\s*biển|gan\s*bien|ven\s*biển|ven\s*bien|bếp\s*ngoài\s*trời|bep\s*ngoai\s*troi|ngoài\s*trời|ngoai\s*troi|sa\s*mạc|coastal|near\s*sea|outdoor\s*kitchen|uv|nắng\s*gắt|nang\s*gat|hướng\s*tây|huong\s*tay|vách\s*tivi|vach\s*tivi)\b",
    re.IGNORECASE,
)
RESOLVED_SIGNAL_PATTERN = re.compile(
    r"\b(cảm\s*ơn|cam\s*on|ok|oke|đã\s*rõ|da\s*ro|xong|xong\s*rồi|được\s*rồi|duoc\s*roi|ổn\s*rồi|on\s*roi|done)\b",
    re.IGNORECASE,
)
PHONE_ONLY_CHARS_PATTERN = re.compile(r"^[\d\s().+-]+$")
AREA_WITH_UNIT_PATTERN = re.compile(
    r"(\d+(?:[\.,]\d+)?)\s*(?:\$\s*)?(m2|m²|m\^2|sqm|sq\s*m|sf|sq\s*ft)\s*\$?",
    re.IGNORECASE,
)
KITCHEN_SPACE_PATTERN = re.compile(r"\b(kitchen|countertop|bếp|bep|phòng\s*bếp|phong\s*bep|nhà\s*bếp|nha\s*bep)\b", re.IGNORECASE)
REPRESENTATIVE_SERIES_PATTERN = re.compile(r"\b(calacatta|carrara)\b", re.IGNORECASE)
COMPARISON_INTENT_PATTERN = re.compile(
    r"\b(so\s*sánh|khác\s*gì|độ\s*bền|do\s*ben|chống\s*ố|chong\s*o|stain|durability|difference|compare)\b",
    re.IGNORECASE,
)
HEAT_UV_CONTEXT_PATTERN = re.compile(
    r"\b(hướng\s*tây|huong\s*tay|vách\s*tivi|vach\s*tivi|nắng\s*gắt|nang\s*gat|uv|chịu\s*nhiệt|chiu\s*nhiet|bền\s*màu|ben\s*mau)\b",
    re.IGNORECASE,
)

FOLLOWUP_VI = "Ngoài mẫu này, Anh/Chị có muốn em tư vấn về màu hợp phong thủy hay cách bảo dưỡng mặt đá không ạ?"
LOCAL_PRODUCT_PREFIX = "http://127.0.0.1:5501/luxquartzvietnam.com/"
LOCAL_SITE_DIR = BASE_DIR / "luxquartzvietnam.com"
LOCAL_PRODUCT_FALLBACK_PATH = "bang-gia-da-thach-anh-nhan-tao-lux-moi-nhat/index.html"


class PageContext(BaseModel):
    url: Optional[str] = None
    title: Optional[str] = None


class LeadContext(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    whatsapp: Optional[str] = None
    email: Optional[str] = None


class ChatRequest(BaseModel):
    sessionId: str = Field(min_length=1)
    market: Optional[str] = None
    message: str = Field(min_length=1, max_length=2000)
    page: Optional[PageContext] = None
    lead: Optional[LeadContext] = None


app = FastAPI(title="Lux Quartz Multi-Market Chatbot", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "semantic_search_products",
            "description": "Find relevant products by semantic intent; do not use output prices for final quote.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_by_code",
            "description": "Get product details by exact or normalized code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "thickness_cm": {"type": "integer", "enum": [2, 3]},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "filter_products",
            "description": "Filter catalog by color, thickness, and max price using market-aware unit (US default SF, VN default Sqm).",
            "parameters": {
                "type": "object",
                "properties": {
                    "color": {"type": "string"},
                    "thickness_cm": {"type": "integer", "enum": [2, 3]},
                    "max_price": {"type": "number"},
                    "price_unit": {"type": "string", "enum": ["sf", "sqm", "slab"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_quote",
            "description": "Calculate slab count and total quote using the active market catalog; converts area_sqm to SF using x10.764 before slab math.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "thickness_cm": {"type": "integer", "enum": [2, 3]},
                    "area_sf": {"type": "number", "minimum": 0.01},
                    "area_sqm": {"type": "number", "minimum": 0.01},
                },
                "required": ["code", "thickness_cm"],
            },
        },
    },
]


def _normalize_market(market: Optional[str]) -> str:
    normalized = (market or DEFAULT_MARKET or "US").strip().upper()
    return normalized if normalized in MARKET_CONFIGS else "US"


def _get_catalog_for_market(market: Optional[str]) -> CatalogContext:
    resolved = _normalize_market(market)
    return CATALOGS.get(resolved, CATALOGS["US"])


def _get_rag_for_market(market: Optional[str]) -> RAGRetriever:
    resolved = _normalize_market(market)
    return RAGS.get(resolved, RAGS["US"])


def _get_orchestrator_for_market(market: Optional[str]) -> Optional[OpenAIOrchestrator]:
    if ORCHESTRATOR is None:
        return None
    resolved = _normalize_market(market)
    return ORCHESTRATORS.get(resolved)


def tool_semantic_search_products(query: str, limit: int = 5, market: str = DEFAULT_MARKET) -> Dict[str, Any]:
    resolved_market = _normalize_market(market)
    catalog = _get_catalog_for_market(resolved_market)
    rag = _get_rag_for_market(resolved_market)
    hits = rag.query(query, limit=limit)
    matches = []
    for hit in hits:
        product = get_product_by_code(catalog, hit.get("code") or "")
        if not product:
            continue
        matches.append(
            {
                "code": product.get("code"),
                "color": product.get("color"),
                "color_group": product.get("color_group"),
                "series": product.get("series"),
                "pattern": product.get("pattern"),
                "description": product.get("description"),
                "product_url": _localize_product_url(product.get("product_url"), language="en" if resolved_market == "US" else "vi"),
            }
        )
    return {"ok": True, "strategy": "semantic", "market": resolved_market, "matches": matches}


def tool_get_product_by_code(
    code: str,
    thickness_cm: Optional[int] = None,
    market: str = DEFAULT_MARKET,
) -> Dict[str, Any]:
    resolved_market = _normalize_market(market)
    catalog = _get_catalog_for_market(resolved_market)
    product = get_product_by_code(catalog, code)
    if not product:
        return {"ok": False, "error": "Code not found", "code": code}

    required_price_field = resolve_price_field(resolved_market, "slab")
    variant = find_variant(product, thickness_cm, required_price_field=required_price_field)
    return {
        "ok": True,
        "market": resolved_market,
        "product": {
            "code": product.get("code"),
            "color": product.get("color"),
            "color_group": product.get("color_group"),
            "series": product.get("series"),
            "pattern": product.get("pattern"),
            "description": product.get("description"),
            "product_url": _localize_product_url(product.get("product_url"), language="en" if resolved_market == "US" else "vi"),
            "variants": product.get("variants"),
            "selected_variant": variant,
        },
    }


def tool_filter_products(
    color: Optional[str] = None,
    thickness_cm: Optional[int] = None,
    max_price: Optional[float] = None,
    price_unit: Optional[str] = None,
    market: str = DEFAULT_MARKET,
    message_hint: Optional[str] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    resolved_market = _normalize_market(market)
    explicit_unit = _detect_explicit_price_unit(message_hint or "", resolved_market)
    resolved_price_field = resolve_price_field(resolved_market, price_unit or explicit_unit)
    catalog = _get_catalog_for_market(resolved_market)
    matches = filter_products(
        catalog,
        color=color,
        thickness_cm=thickness_cm,
        max_price=max_price,
        market=resolved_market,
        price_unit=price_unit or explicit_unit,
        limit=limit,
    )
    resolved_unit = (
        "sqm"
        if resolved_price_field.endswith("sqm_vnd") or resolved_price_field == "fob_sqm_usd"
        else "sf"
        if resolved_price_field.endswith("sf_vnd") or resolved_price_field == "fob_sf_usd"
        else "slab"
    )
    resolved_currency = "VND" if resolved_price_field.endswith("_vnd") else "USD"
    clean_matches = []
    for item in matches:
        clean_item = dict(item)
        clean_item.pop("photo_url", None)
        clean_item["product_url"] = _localize_product_url(clean_item.get("product_url"))
        clean_matches.append(clean_item)
    return {
        "ok": True,
        "market": resolved_market,
        "resolved_price_unit": resolved_unit,
        "resolved_currency": resolved_currency,
        "matches": clean_matches,
    }


def tool_calculate_quote(
    code: str,
    thickness_cm: int,
    area_sf: Optional[float] = None,
    area_sqm: Optional[float] = None,
    market: str = DEFAULT_MARKET,
) -> Dict[str, Any]:
    resolved_market = _normalize_market(market)
    catalog = _get_catalog_for_market(resolved_market)
    product = get_product_by_code(catalog, code)
    if not product:
        return {"ok": False, "error": "Code not found", "code": code}

    slab_price_field = resolve_price_field(resolved_market, "slab")
    variant = find_variant(product, thickness_cm, required_price_field=slab_price_field)
    if not variant:
        return {
            "ok": False,
            "error": "No slab price for requested thickness",
            "code": product.get("code"),
            "thickness_cm": thickness_cm,
        }

    slab_price = variant.get(slab_price_field)
    if slab_price is None:
        return {"ok": False, "error": "Missing slab price", "code": product.get("code")}

    if area_sf is None and area_sqm is None:
        return {"ok": False, "error": "area_sf or area_sqm is required"}

    source_area_unit = "sf"
    resolved_area_sf = area_sf
    if area_sqm is not None:
        resolved_area_sf = sqm_to_sf(area_sqm)
        source_area_unit = "sqm"

    slab_area_sf = 68.0 if source_area_unit == "sqm" else 55.11
    quote = calculate_quote(
        code=product.get("code"),
        thickness_cm=thickness_cm,
        area_sf=resolved_area_sf,
        fob_slab_usd=slab_price,
        buffer_ratio=BUFFER_RATIO,
        slab_area_sf=slab_area_sf,
    )

    currency = "VND" if slab_price_field.endswith("_vnd") else "USD"
    quote["market"] = resolved_market
    quote["currency"] = currency
    quote["price_unit"] = "slab"
    quote["price_per_slab"] = slab_price
    quote["price_per_slab_vnd"] = slab_price if currency == "VND" else None
    quote["price_per_slab_usd"] = slab_price if currency == "USD" else None

    base_quote = quote.get("base_quote") or {}
    buffer_quote = quote.get("buffer_quote") or {}
    base_total = base_quote.get("total_usd")
    buffer_total = buffer_quote.get("total_usd")
    base_quote["total"] = base_total
    buffer_quote["total"] = buffer_total
    if currency == "VND":
        base_quote["total_vnd"] = base_total
        buffer_quote["total_vnd"] = buffer_total
    else:
        base_quote["total_usd"] = base_total
        buffer_quote["total_usd"] = buffer_total
    quote["base_quote"] = base_quote
    quote["buffer_quote"] = buffer_quote

    quote["color"] = product.get("color")
    quote["series"] = product.get("series")
    quote["product_url"] = _localize_product_url(product.get("product_url"), language="en" if resolved_market == "US" else "vi")
    quote["source_area_unit"] = source_area_unit
    if area_sqm is not None:
        quote["area_sqm"] = round(area_sqm, 2)
        quote["conversion_factor"] = 10.764
    return {"ok": True, "market": resolved_market, "quote": quote}


def _build_structured(audit: List[Dict[str, Any]]) -> Dict[str, Any]:
    structured: Dict[str, Any] = {
        "intent": "general",
        "products": [],
        "base_quote": None,
        "buffer_quote": None,
        "needs_followup": True,
        "requested_contact": True,
    }

    for item in audit:
        tool = item.get("tool")
        result = item.get("result") or {}
        if tool == "calculate_quote" and result.get("ok") and result.get("quote"):
            quote = result["quote"]
            structured["intent"] = "quote"
            structured["products"] = [
                {
                    "code": quote.get("code"),
                    "color": quote.get("color"),
                    "series": quote.get("series"),
                    "product_url": _localize_product_url(quote.get("product_url")),
                    "thickness_cm": quote.get("thickness_cm"),
                    "area_sf": quote.get("area_sf"),
                }
            ]
            structured["base_quote"] = quote.get("base_quote")
            structured["buffer_quote"] = quote.get("buffer_quote")

        if tool in {"get_product_by_code", "filter_products", "semantic_search_products"}:
            payload_products = []
            if result.get("product"):
                payload_products = [result.get("product")]
            elif result.get("matches"):
                payload_products = result.get("matches")
            if payload_products and not structured["products"]:
                structured["products"] = [
                    {
                        "code": p.get("code"),
                        "color": p.get("color"),
                        "series": p.get("series"),
                        "product_url": _localize_product_url(p.get("product_url")),
                    }
                    for p in payload_products[:3]
                ]
                if structured["intent"] == "general":
                    structured["intent"] = "product_lookup"

    return structured


def _get_quote_from_audit(audit: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for item in audit:
        result = item.get("result") or {}
        if item.get("tool") == "calculate_quote" and result.get("ok") and result.get("quote"):
            return result.get("quote")
    return None


def _ensure_quote_transparency(reply: str, audit: List[Dict[str, Any]], language: str) -> str:
    return reply


def _ensure_area_slab_guidance(
    reply: str,
    message: str,
    history: List[Dict[str, Any]],
    language: str,
    audit: List[Dict[str, Any]],
) -> str:
    if _get_quote_from_audit(audit):
        return reply

    area, unit, from_history = _resolve_area_context(message, history)
    if area is None or unit is None:
        return reply

    normalized = (reply or "").lower()
    if any(token in normalized for token in ["ceil(", "slabs needed", "số tấm cần đặt", "5% allowance", "trừ hao 5%"]):
        return reply

    guidance = _area_slab_guidance(area, unit, language, from_history)
    return f"{(reply or '').rstrip()} {guidance}".strip()


def _detect_context(request: ChatRequest) -> Tuple[str, str]:
    page_url = (request.page.url if request.page and request.page.url else "") or ""
    market_hint = (request.market or "").strip().upper()

    url_lower = page_url.lower()
    message = request.message or ""
    message_lower = message.lower()

    market = market_hint or DEFAULT_MARKET
    language = "en" if market == "US" else "vi"

    if "/en/" in url_lower:
        market = "US"

    has_vi_signal = bool(VI_HINT_PATTERN.search(message_lower)) or bool(
        re.search(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", message_lower)
    )
    has_en_signal = bool(EN_HINT_PATTERN.search(message_lower))

    if has_vi_signal:
        language = "vi"
    elif has_en_signal:
        language = "en"

    return market, language


def _is_technical_query(message: str) -> bool:
    return bool(TECH_SPEC_PATTERN.search(message or ""))


def _is_product_related_query(message: str) -> bool:
    return bool(PRODUCT_INTENT_PATTERN.search(message or ""))


def _extract_area_hint(message: str) -> Tuple[Optional[float], Optional[str]]:
    text = (message or "").lower()
    match = AREA_WITH_UNIT_PATTERN.search(text)
    if not match:
        return None, None

    raw_value = (match.group(1) or "").replace(",", ".")
    try:
        area = float(raw_value)
    except ValueError:
        return None, None

    raw_unit = (match.group(2) or "").lower()
    if raw_unit in {"m2", "m²", "m^2", "sqm", "sq m", "sq   m"}:
        return area, "sqm"
    return area, "sf"


def _is_kitchen_area_consult_query(message: str) -> bool:
    area, _ = _extract_area_hint(message)
    if area is None:
        return False
    return bool(KITCHEN_SPACE_PATTERN.search(message or ""))


def _extract_latest_area_from_history(history: List[Dict[str, Any]]) -> Tuple[Optional[float], Optional[str]]:
    for item in reversed(history or []):
        if item.get("role") != "user":
            continue
        area, unit = _extract_area_hint(item.get("content") or "")
        if area is not None:
            return area, unit
    return None, None


def _resolve_area_context(message: str, history: List[Dict[str, Any]]) -> Tuple[Optional[float], Optional[str], bool]:
    area, unit = _extract_area_hint(message)
    if area is not None:
        return area, unit, False

    history_area, history_unit = _extract_latest_area_from_history(history)
    if history_area is not None:
        return history_area, history_unit, True

    return None, None, False


def _has_kitchen_context(message: str, history: List[Dict[str, Any]]) -> bool:
    if KITCHEN_SPACE_PATTERN.search(message or ""):
        return True
    for item in reversed(history or []):
        if item.get("role") != "user":
            continue
        if KITCHEN_SPACE_PATTERN.search(item.get("content") or ""):
            return True
    return False


def _format_area_text(area: Optional[float], unit: Optional[str], language: str) -> str:
    if area is None:
        return ""
    area_value = f"{area:.0f}" if float(area).is_integer() else f"{area:.1f}"
    if unit == "sqm":
        return f"{area_value} m²"
    return f"{area_value} SF"


def _estimate_slabs_from_area(area: float, unit: str) -> Tuple[float, float, int, int]:
    area_sf = sqm_to_sf(area) if unit == "sqm" else area
    buffered_sf = area_sf * BUFFER_RATIO

    slab_area_sf = 68.0 if unit == "sqm" else 55.11
    raw_base_slabs = area_sf / slab_area_sf
    raw_buffer_slabs = buffered_sf / slab_area_sf
    slabs_base = math.ceil(raw_base_slabs)
    slabs_buffer = math.ceil(raw_buffer_slabs)

    return area_sf, buffered_sf, slabs_base, slabs_buffer


def _area_slab_guidance(area: float, unit: str, language: str, from_history: bool = False) -> str:
    area_sf, buffered_sf, slabs_base, slabs_buffer = _estimate_slabs_from_area(area, unit)
    area_text = _format_area_text(area, unit, language)
    slab_area_sf = 68.0 if unit == "sqm" else 55.11
    raw_buffer_slabs = buffered_sf / slab_area_sf

    if language == "en":
        prefix = "Based on your previous area" if from_history else "For your area"
        line = f"{prefix} {area_text}, the cost-optimized plan is {slabs_buffer} slabs including 5% allowance (base area is {slabs_base} slabs)."
        if 2.8 < raw_buffer_slabs < 3.0:
            line = f"{line} Because the buffered result is above 2.8 slabs, you may consider +1 slab only for better vein continuity."
        return line

    prefix = "Dựa trên diện tích mình đã gửi trước đó" if from_history else "Với diện tích"
    line = f"{prefix} {area_text}, phương án tối ưu chi phí là {slabs_buffer} tấm đã gồm trừ hao 5% (diện tích cơ bản là {slabs_base} tấm)."
    if 2.8 < raw_buffer_slabs < 3.0:
        line = f"{line} Do kết quả trừ hao vượt 2,8 tấm, mình chỉ cân nhắc thêm 1 tấm khi cần ưu tiên ghép vân đồng bộ."
    return line


def _extract_representative_series(message: str) -> List[str]:
    hits: List[str] = []
    seen = set()
    for match in REPRESENTATIVE_SERIES_PATTERN.finditer((message or "").lower()):
        token = (match.group(1) or "").lower()
        if token and token not in seen:
            seen.add(token)
            hits.append(token)
    return hits


def _extract_thickness_hint(message: str) -> Optional[int]:
    match = re.search(r"\b(2|3)\s*cm\b", (message or "").lower())
    if not match:
        return None
    return int(match.group(1))


def _resolve_series_mentions(message: str, history: List[Dict[str, Any]]) -> List[str]:
    seen = set()
    mentions: List[str] = []

    def collect(text: str) -> None:
        for token in _extract_representative_series(text):
            if token in seen:
                continue
            seen.add(token)
            mentions.append(token)

    collect(message or "")
    for item in reversed(history or []):
        if item.get("role") != "user":
            continue
        collect(item.get("content") or "")
        if "calacatta" in seen and "carrara" in seen:
            break

    return mentions


def _is_calacatta_carrara_comparison_query(message: str, history: List[Dict[str, Any]]) -> bool:
    if not COMPARISON_INTENT_PATTERN.search(message or ""):
        return False
    mentions = _resolve_series_mentions(message, history)
    return "calacatta" in mentions and "carrara" in mentions


def _calacatta_carrara_comparison_reply(message: str, language: str, history: List[Dict[str, Any]]) -> Optional[str]:
    if not _is_calacatta_carrara_comparison_query(message, history):
        return None
    if language == "en":
        return (
            "Both lines share hardness around 6-7 Mohs and strong stain resistance thanks to water absorption below 0.05%. "
            "Calacatta usually has bolder luxury veins, while Carrara gives a cleaner classic look."
        )
    return (
        "Cả hai dòng đều có độ cứng 6-7 Mohs, chống ố tuyệt đối nhờ độ hút nước <0.05%. "
        "Tuy nhiên Calacatta thường có vân to sang trọng hơn, còn Carrara mang nét thanh lịch cổ điển."
    )


def _format_slab_price_for_reply(price_per_slab: Optional[float], market: str) -> Optional[str]:
    if price_per_slab is None:
        return None
    if market == "VN":
        return f"₫{price_per_slab:,.0f}/tấm"
    return f"${price_per_slab:,.2f}/slab"


def _format_total_price_for_reply(total: Optional[float], market: str) -> Optional[str]:
    if total is None:
        return None
    if market == "VN":
        return f"₫{total:,.0f}"
    return f"${total:,.2f}"


def _local_index_path_exists(path: str) -> bool:
    clean_path = (path or "").strip("/")
    if not clean_path:
        return False
    return (LOCAL_SITE_DIR / Path(clean_path)).is_file()


def _find_local_product_path_by_code(path: str, prefer_en: bool = False) -> Optional[str]:
    searchable = re.sub(r"[/_-]+", " ", path or "")
    code_match = CODE_FINDER.search(searchable)
    if not code_match:
        return None

    code_key = re.sub(r"\s+", "-", code_match.group(0).lower().strip())
    search_roots = ["en/product", "product", "san-pham"] if prefer_en else ["product", "san-pham", "en/product"]

    for root in search_roots:
        product_dir = LOCAL_SITE_DIR / root
        if not product_dir.exists():
            continue
        for index_path in product_dir.glob("*/index.html"):
            folder = index_path.parent.name.lower()
            if code_key in folder:
                return index_path.relative_to(LOCAL_SITE_DIR).as_posix()
    return None


def _normalize_local_index_path(path: str) -> str:
    clean_path = (path or "").strip("/")
    if not clean_path:
        return "index.html"
    if clean_path.endswith("index.html"):
        return clean_path
    return f"{clean_path.rstrip('/')}/index.html"


def _resolve_existing_local_path(path: str, prefer_en: bool = False) -> Optional[str]:
    clean_path = _normalize_local_index_path(path)

    if prefer_en:
        code_path = _find_local_product_path_by_code(clean_path, prefer_en=True)
        if code_path and _local_index_path_exists(code_path):
            return code_path

    if _local_index_path_exists(clean_path):
        return clean_path

    code_path = _find_local_product_path_by_code(clean_path, prefer_en=prefer_en)
    if code_path and _local_index_path_exists(code_path):
        return code_path

    return None


def _official_product_url_from_path(path: str, prefer_en: bool = False) -> str:
    clean_path = (path or "").strip("/")
    if clean_path.endswith("index.html"):
        clean_path = clean_path[: -len("index.html")].rstrip("/")

    if prefer_en:
        en_path = _find_local_product_path_by_code(clean_path, prefer_en=True)
        if en_path:
            en_clean = en_path[: -len("index.html")].rstrip("/") if en_path.endswith("index.html") else en_path.strip("/")
            return f"https://luxquartzvietnam.com/{en_clean}/"

    if not clean_path:
        return "https://luxquartzvietnam.com/"
    return f"https://luxquartzvietnam.com/{clean_path.rstrip('/')}/"


def _localize_product_url(raw_url: Optional[str], language: str = "vi") -> Optional[str]:
    url = (raw_url or "").strip().rstrip(".,);]")
    if not url:
        return None

    local_prefix = LOCAL_PRODUCT_PREFIX.rstrip("/") + "/"
    if url.startswith(local_prefix):
        path = url[len(local_prefix) :]
    else:
        match = re.match(r"https?://(?:www\.)?luxquartzvietnam\.com/?(.*)$", url, flags=re.IGNORECASE)
        if not match:
            if url.startswith("http://127.0.0.1:5501/") and url.endswith("index.html"):
                return url
            return None
        path = match.group(1)

    path = re.split(r"[?#]", path, maxsplit=1)[0].strip("/")
    prefer_en = language == "en"
    local_path = _resolve_existing_local_path(path, prefer_en=prefer_en)
    if local_path:
        return f"{local_prefix}{local_path}"
    return _official_product_url_from_path(path, prefer_en=prefer_en)


def _product_detail_label(language: str = "vi") -> str:
    if language == "en":
        return "View product details here"
    return "Xem chi tiết sản phẩm tại đây"


def _product_detail_link(product_url: Optional[str], language: str = "vi") -> str:
    local_url = _localize_product_url(product_url, language=language)
    if not local_url:
        return ""
    return f"[{_product_detail_label(language)}]({local_url})"


def _remove_image_markdown(text: str, language: str = "vi") -> str:
    def linked_image_repl(match: re.Match[str]) -> str:
        link = _product_detail_link(match.group(2), language)
        return link or ""

    cleaned = re.sub(
        r"\[!\[[^\]]*\]\((https?://[^)\s]+)\)\]\((https?://[^)\s]+)\)",
        linked_image_repl,
        text or "",
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"!\[[^\]]*\]\((https?://[^)\s]+)\)", "", cleaned, flags=re.IGNORECASE)
    return cleaned


def _localize_reply_links(text: str, language: str = "vi") -> str:
    def markdown_link_repl(match: re.Match[str]) -> str:
        local_url = _localize_product_url(match.group(2), language=language)
        if not local_url:
            return match.group(0)
        return f"[{_product_detail_label(language)}]({local_url})"

    def bare_url_repl(match: re.Match[str]) -> str:
        local_url = _localize_product_url(match.group(0), language=language)
        if not local_url:
            return match.group(0)
        return f"[{_product_detail_label(language)}]({local_url})"

    localized = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", markdown_link_repl, text or "", flags=re.IGNORECASE)
    localized = re.sub(r"(?<!\]\()https?://(?:www\.)?luxquartzvietnam\.com/[^\s)]+", bare_url_repl, localized, flags=re.IGNORECASE)
    return localized


def _extract_first_product_url_from_audit(audit: List[Dict[str, Any]], language: str) -> Optional[str]:
    for item in audit or []:
        result = item.get("result") or {}

        quote = result.get("quote") or {}
        quote_url = _localize_product_url(quote.get("product_url"), language=language)
        if quote_url:
            return quote_url

        product = result.get("product") or {}
        product_url = _localize_product_url(product.get("product_url"), language=language)
        if product_url:
            return product_url

        for match in result.get("matches") or []:
            match_url = _localize_product_url((match or {}).get("product_url"), language=language)
            if match_url:
                return match_url

    return None


def _ensure_clickable_product_detail_link(reply: str, audit: List[Dict[str, Any]], language: str) -> str:
    text = (reply or "").strip()
    if not text:
        return text

    label = _product_detail_label(language)
    bare_label_pattern = re.compile(rf"\[{re.escape(label)}\](?!\()")
    if not bare_label_pattern.search(text):
        return text

    product_url = _extract_first_product_url_from_audit(audit, language)
    if not product_url:
        code_match = CODE_FINDER.search(text)
        if code_match:
            market = "US" if language == "en" else "VN"
            product = get_product_by_code(_get_catalog_for_market(market), code_match.group(0))
            if product:
                product_url = _localize_product_url(product.get("product_url"), language=language)

    if not product_url:
        return text

    clickable = f"[{label}]({product_url})"
    return bare_label_pattern.sub(clickable, text)


def _extract_embedded_phone_contact(message: str) -> Optional[str]:
    for match in re.finditer(r"(?<!\d)(?:\+?84|0)[\d\s().+-]{8,16}\d(?!\d)", message or ""):
        digits = re.sub(r"\D", "", match.group(0))
        if digits.startswith("84") and len(digits) in {11, 12}:
            return "0" + digits[2:]
        if 9 <= len(digits) <= 11:
            return digits
    return None


def _has_bulk_discount_request(message: str) -> bool:
    return bool(re.search(r"\b(chiết\s*khấu|chiet\s*khau|discount|số\s*lượng\s*lớn|so\s*luong\s*lon|trên\s*10|over\s*10|bulk)\b", message or "", re.IGNORECASE))


def _has_transport_request(message: str) -> bool:
    return bool(re.search(r"\b(vận\s*chuyển|van\s*chuyen|lắp\s*đặt|lap\s*dat|delivery|shipping|handling|installation|transport)\b", message or "", re.IGNORECASE))


def _has_technical_detail_request(message: str) -> bool:
    return bool(re.search(r"\b(chứng\s*chỉ|chung\s*chi|kháng\s*khuẩn|khang\s*khuan|thông\s*số|thong\s*so|technical|specification|parameter|certificate)\b", message or "", re.IGNORECASE))


def _deterministic_code_quote_reply(message: str, language: str, market: str, history: List[Dict[str, Any]]) -> Optional[str]:
    code_match = CODE_FINDER.search(message or "")
    area, unit, from_history = _resolve_area_context(message, history)
    if not code_match or area is None or unit is None:
        return None

    quote_intent = re.search(r"\b(báo\s*giá|bao\s*gia|giá|chi\s*phí|chi\s*phi|tổng|tong|tấm|slab|quote|price|cost|total|allowance|trừ\s*hao|tru\s*hao)\b", message or "", re.IGNORECASE)
    if not quote_intent:
        return None

    resolved_market = _normalize_market(market)
    product = get_product_by_code(_get_catalog_for_market(resolved_market), code_match.group(0))
    if not product:
        return None

    slab_field = resolve_price_field(resolved_market, "slab")
    requested_thickness = _extract_thickness_hint(message) or 2
    variant = (
        find_variant(product, requested_thickness, required_price_field=slab_field)
        or find_variant(product, 2, required_price_field=slab_field)
        or find_variant(product, 3, required_price_field=slab_field)
        or find_variant(product, None, required_price_field=slab_field)
    )
    slab_price = variant.get(slab_field) if variant else None
    if slab_price is None:
        return None

    actual_thickness = variant.get("thickness_cm") if variant else None
    display_slab_price = slab_price
    estimate_note_vi = ""
    estimate_note_en = ""
    if requested_thickness == 3 and actual_thickness != 3:
        display_slab_price = round(slab_price * 1.25, 2)
        estimate_note_vi = " Bảng giá hiện có giá 2cm; với phương án 3cm, em tạm ước tính cao hơn 25% so với giá 2cm để Anh/Chị có con số sơ bộ."
        estimate_note_en = " The current catalog has a 2cm slab price; for 3cm, I am using a preliminary +25% estimate from the 2cm price."

    area_sf, _, slabs_base, slabs_buffer = _estimate_slabs_from_area(area, unit)
    total_base = round(display_slab_price * slabs_base, 2)
    total_buffer = round(display_slab_price * slabs_buffer, 2)
    price_text = _format_slab_price_for_reply(display_slab_price, resolved_market)
    if language == "en" and resolved_market == "VN":
        price_text = f"₫{display_slab_price:,.0f}/slab"
    total_base_text = _format_total_price_for_reply(total_base, resolved_market)
    total_buffer_text = _format_total_price_for_reply(total_buffer, resolved_market)
    area_text = _format_area_text(area, unit, language)
    product_link = _product_detail_link(product.get("product_url"), language)
    phone = _extract_embedded_phone_contact(message)

    if language == "en":
        lines = []
        if phone:
            lines.append("Thanks, I have received your phone number. A Lux Quartz specialist will contact you to support slab photos, volume pricing, and delivery options.")
        lines.append(
            f"For {product.get('code')} ({product.get('series')}), area {area_text}, requested thickness {requested_thickness}cm. "
            f"Estimated slab price: {price_text}.{estimate_note_en}"
        )
        lines.append(f"Slabs needed: {slabs_base} slabs for the base area, or {slabs_buffer} slabs with a 5% allowance.")
        lines.append(f"Estimated total: base {total_base_text}; with 5% allowance {total_buffer_text}.")
        if _has_technical_detail_request(message):
            lines.append("Technical parameters: hardness 6.0-7.0 Mohs, water absorption <0.05%, flexural strength 35.0-55.0 MPa, and chemical resistance C4.")
        if product_link:
            lines.append(f"Product link: {product_link}")
        if _has_bulk_discount_request(message):
            lines.append("For orders above 10 slabs, the sales team will confirm the best volume discount after checking stock and project schedule.")
        if _has_transport_request(message):
            lines.append("For delivery and handling, large slabs require site access coordination, elevator/loading checks, and an installation plan before confirmation.")
        return "\n\n".join(lines)

    lines = []
    if phone:
        lines.append("Dạ, em đã nhận được số điện thoại của mình rồi ạ. Chuyên viên Lux Quartz sẽ kết nối qua Zalo để hỗ trợ ảnh slab thực tế, giá số lượng và phương án vận chuyển cho Anh.")
    lines.append(
        f"Với mã {product.get('code')} ({product.get('series')}), diện tích {area_text}, độ dày Anh yêu cầu {requested_thickness}cm. "
        f"Giá theo tấm em dùng để tính sơ bộ là {price_text}.{estimate_note_vi}"
    )
    lines.append(f"Số tấm cần đặt: {slabs_base} tấm cho phương án cơ bản, hoặc {slabs_buffer} tấm khi tính trừ hao 5%.")
    lines.append(f"Tổng giá ước tính: phương án cơ bản {total_base_text}; phương án trừ hao 5% {total_buffer_text}.")
    if _has_technical_detail_request(message):
        lines.append("Thông số kỹ thuật tham khảo: độ cứng 6.0-7.0 Mohs, độ hút nước dưới 0,05%, độ bền uốn 35.0-55.0 MPa và khả năng kháng hóa chất mức C4.")
    if product_link:
        lines.append(f"Link sản phẩm: {product_link}")
    if _has_bulk_discount_request(message):
        lines.append("Với đơn trên 10 tấm, chuyên viên sẽ kiểm tra tồn kho, tiến độ dự án và báo mức chiết khấu số lượng tốt nhất cho Anh.")
    if _has_transport_request(message):
        lines.append("Về vận chuyển và lắp đặt, slab lớn cần kiểm tra lối vào, thang máy/thang hàng và phương án bốc xếp trước khi chốt lịch giao.")
    return "\n\n".join(lines)


def _representative_codes_reply(message: str, language: str, market: str, history: List[Dict[str, Any]]) -> Optional[str]:
    series_hits = _resolve_series_mentions(message, history)
    if not series_hits:
        return None

    catalog = _get_catalog_for_market(market)
    slab_field = resolve_price_field(market, "slab")
    mapping = {
        "calacatta": ["LQ 914", "LQ 912"],
        "carrara": ["LQ 703", "LQ 701"],
    }
    thickness_hint = _extract_thickness_hint(message) or 2

    representative_rows: List[Dict[str, Any]] = []
    for series in series_hits:
        for code in mapping.get(series, []):
            product = get_product_by_code(catalog, code)
            if not product:
                continue
            variant = (
                find_variant(product, thickness_hint, required_price_field=slab_field)
                or find_variant(product, 2, required_price_field=slab_field)
                or find_variant(product, 3, required_price_field=slab_field)
                or find_variant(product, None, required_price_field=slab_field)
            )
            slab_price = variant.get(slab_field) if variant else None
            if slab_price is None:
                continue
            actual_thickness = variant.get("thickness_cm") if variant else None
            display_slab_price = slab_price
            price_note = None
            if thickness_hint == 3 and actual_thickness != 3:
                display_slab_price = round(slab_price * 1.25, 2)
                price_note = "ước tính 3cm +25% từ giá 2cm"
            representative_rows.append(
                {
                    "series": series,
                    "code": product.get("code"),
                    "product_url": _localize_product_url(product.get("product_url"), language="en" if market == "US" else "vi"),
                    "slab_price": slab_price,
                    "display_slab_price": display_slab_price,
                    "price_note": price_note,
                }
            )
            break

    if not representative_rows:
        return None

    area, unit, from_history = _resolve_area_context(message, history)
    slabs_base: Optional[int] = None
    if area is not None and unit is not None:
        _, _, slabs_base, _ = _estimate_slabs_from_area(area, unit)

    if language == "en":
        compare_bits = []
        for item in representative_rows:
            label = "Calacatta" if item.get("series") == "calacatta" else "Carrara"
            price_text = _format_slab_price_for_reply(item.get("display_slab_price"), market)
            detail_link = _product_detail_link(item.get("product_url"), language)
            link_tail = f", {detail_link}" if detail_link else ""
            if item.get("price_note"):
                price_text = f"{price_text}, {item.get('price_note')}"
            if slabs_base is not None:
                total = round((item.get("display_slab_price") or 0) * slabs_base, 2)
                total_text = _format_total_price_for_reply(total, market)
                compare_bits.append(f"{label} {item.get('code')} ({price_text}, est. {slabs_base} slabs = {total_text}{link_tail})")
            else:
                compare_bits.append(f"{label} {item.get('code')} ({price_text}{link_tail})")
        reply = f"Representative codes for quick comparison: {'; '.join(compare_bits)}."
        if area is not None and unit is not None:
            reply = f"{reply} {_area_slab_guidance(area, unit, language, from_history)}"
        return reply

    compare_bits = []
    for item in representative_rows:
        label = "Calacatta" if item.get("series") == "calacatta" else "Carrara"
        price_text = _format_slab_price_for_reply(item.get("display_slab_price"), market)
        detail_link = _product_detail_link(item.get("product_url"), language)
        link_tail = f", {detail_link}" if detail_link else ""
        if slabs_base is not None:
            total = round((item.get("display_slab_price") or 0) * slabs_base, 2)
            total_text = _format_total_price_for_reply(total, market)
            compare_bits.append(f"{label} {item.get('code')} ({price_text}, ước tính {slabs_base} tấm = {total_text}{link_tail})")
        else:
            compare_bits.append(f"{label} {item.get('code')} ({price_text}{link_tail})")

    reply = f"Dạ em nhận diện đúng dòng mình đang quan tâm. Mã đại diện để so sánh nhanh: {'; '.join(compare_bits)}."
    if area is not None and unit is not None:
        reply = f"{reply} {_area_slab_guidance(area, unit, language, from_history)}"
    return reply


def _kitchen_area_consult_reply(
    message: str,
    language: str = "vi",
    market: str = DEFAULT_MARKET,
    history: Optional[List[Dict[str, Any]]] = None,
) -> str:
    area, unit, from_history = _resolve_area_context(message, history or [])
    resolved_market = _normalize_market(market)

    suggestions = tool_filter_products(market=resolved_market, thickness_cm=2, limit=3).get("matches") or []
    codes = [item.get("code") for item in suggestions if item.get("code")]
    if not codes:
        codes = ["LQ 504", "LQ 601", "LQ 701"]

    area_text = _format_area_text(area, unit, language) if area is not None else "diện tích mình gửi"
    code_text = ", ".join(codes[:3])

    slab_guidance = ""
    if area is not None and unit is not None:
        slab_guidance = _area_slab_guidance(area, unit, language, from_history)

    if language == "en":
        reply = (
            f"For a kitchen around {area_text}, I suggest these practical codes first: {code_text}. "
            "These options are commonly chosen for kitchen tops because they are durable and easy to match with cabinet tones."
        )
        if slab_guidance:
            reply = f"{reply} {slab_guidance}"
        return reply

    reply = (
        f"Dạ với khu bếp khoảng {area_text}, em gợi ý trước 3 mã dễ ứng dụng là {code_text}. "
        "Các mã này dùng mặt bếp ổn định, thẩm mỹ dễ phối và đang được chọn nhiều."
    )
    if slab_guidance:
        reply = f"{reply} {slab_guidance}"
    return reply


def _is_correction_request(message: str) -> bool:
    return bool(CORRECTION_PATTERN.search(message or ""))


def _is_project_service_query(message: str) -> bool:
    return bool(PROJECT_SERVICE_PATTERN.search(message or ""))


def _is_greeting_message(message: str) -> bool:
    return bool(GREETING_PATTERN.match((message or "").strip()))


def _greeting_reply(language: str = "vi") -> str:
    if language == "en":
        return (
            "Hello from Lux Quartz. May I have your name so I can address you properly? "
            "Then please share your product code or project area (SF/sqm), and I will prepare a quick quote for you."
        )
    return (
        "Dạ Lux Quartz xin chào Anh/Chị! Anh/Chị cho em xin tên để em tiện xưng hô ạ. "
        "Sau đó Anh/Chị gửi em mã đá hoặc diện tích (m²/SF), em báo giá nhanh ngay cho mình nhé."
    )


def _extract_phone_contact(message: str) -> Optional[str]:
    raw = (message or "").strip()
    if not raw:
        return None
    if not PHONE_ONLY_CHARS_PATTERN.fullmatch(raw):
        return None
    digits = re.sub(r"\D", "", raw)
    if 9 <= len(digits) <= 11:
        return digits
    return None


def _phone_capture_reply(phone: str, language: str = "vi") -> str:
    if language == "en":
        return (
            "Thanks, I have received your phone number. "
            "A Lux Quartz specialist will contact you via Zalo/WhatsApp to send real slab photos and the best available discount."
        )
    return (
        "Dạ em đã nhận được số điện thoại của mình rồi ạ. "
        "Chuyên viên Lux Quartz sẽ kết nối qua Zalo ngay để gửi ảnh thực tế và báo giá chiết khấu cho Anh/Chị."
    )


def _is_special_context_query(message: str) -> bool:
    return bool(SPECIAL_CONTEXT_PATTERN.search(message or ""))


def _consultative_context_reply(message: str, language: str = "vi") -> str:
    if language == "en":
        return (
            "For demanding installation contexts such as intense sun, west-facing areas, coastal salty breeze, or outdoor kitchens, Lux Quartz is suitable when installed correctly.\n\n"
            "- Quartz surface water absorption is below 0.05%, helping limit moisture, stains, and salt-air impact\n"
            "- Flexural strength is 35.0-55.0 MPa, helping the slab stay stable in daily use\n"
            "- Hardness is 6.0-7.0 Mohs, offering stronger scratch resistance than common marble\n"
            "- For strong sun or outdoor/coastal positions, proper installation detailing and regular neutral cleaning help maintain long-term surface stability\n\n"
            "Please share the installation area, total sqm or SF, and preferred 2cm or 3cm thickness so I can calculate slab count and quote total."
        )

    return (
        "Dạ với bối cảnh thi công đặc thù như nắng gắt, hướng Tây, gần biển hoặc khu bếp ngoài trời, Lux Quartz vẫn là lựa chọn phù hợp nếu thi công đúng kỹ thuật.\n\n"
        "- Bề mặt Quartz có độ hút nước dưới 0,05% nên hỗ trợ chống thấm, hạn chế bám bẩn và giảm ảnh hưởng từ hơi muối biển\n"
        "- Độ bền uốn 35.0-55.0 MPa giúp mặt đá ổn định khi dùng hằng ngày\n"
        "- Độ cứng 6.0-7.0 Mohs giúp chống trầy xước tốt hơn đá marble thông thường\n"
        "- Với vị trí nắng gắt hoặc ngoài trời, mình nên ưu tiên chi tiết che chắn, keo và kết cấu lắp đặt phù hợp để giữ bề mặt ổn định lâu dài\n\n"
        "Anh/Chị gửi giúp em khu vực lắp đặt, diện tích theo m² hoặc SF và độ dày 2cm hay 3cm, em sẽ quy đổi đúng chuẩn rồi tính số tấm và tổng báo giá ngay cho mình ạ."
    )


def _has_consultative_markers(reply: str) -> bool:
    normalized = (reply or "").lower()
    has_absorption = "0.05" in normalized or "0,05" in normalized
    return has_absorption and all(token in normalized for token in ["35.0", "mohs"])


def _strip_area_request_tail(reply: str) -> str:
    text = (reply or "")
    patterns = [
        r"Anh/Chị gửi giúp em khu vực lắp đặt, diện tích theo m² hoặc SF và độ dày 2cm hay 3cm, em sẽ quy đổi đúng chuẩn rồi tính số tấm và tổng báo giá ngay cho mình ạ\.?",
        r"Please share your area in SF and preferred thickness[^.?!]*[.?!]?",
    ]
    for pattern in patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    return text.strip()


def _is_feng_shui_query(message: str) -> bool:
    return bool(FENG_SHUI_PATTERN.search(message or ""))


def _extract_feng_shui_color_target(message: str) -> Optional[str]:
    text = (message or "").lower()
    if any(token in text for token in ["xanh lá", "xanh la", "green", "mệnh mộc", "menh moc"]):
        return "Green"
    if any(token in text for token in ["đen", "den", "black"]):
        return "Black"
    if any(token in text for token in ["xám", "xam", "grey", "gray", "ghi", "mệnh thủy", "menh thuy"]):
        return "Grey"
    if any(token in text for token in ["xanh dương", "xanh duong", "blue"]):
        return "Blue"
    return None


def _is_water_feng_shui_query(message: str) -> bool:
    return bool(re.search(r"\b(mệnh\s*thủy|menh\s*thuy|sinh\s*năm\s*1983|sinh\s*nam\s*1983|born\s*in\s*1983)\b", message or "", re.IGNORECASE))


def _water_feng_shui_project_reply(
    message: str,
    language: str = "vi",
    market: str = DEFAULT_MARKET,
    history: Optional[List[Dict[str, Any]]] = None,
) -> Optional[str]:
    if language == "en" or not _is_water_feng_shui_query(message):
        return None

    area, unit, from_history = _resolve_area_context(message, history or [])
    if area is None or unit is None:
        return None

    resolved_market = _normalize_market(market)
    product = get_product_by_code(_get_catalog_for_market(resolved_market), "LQ 914") or get_product_by_code(_get_catalog_for_market(resolved_market), "LQ 910")
    if not product:
        return None

    slab_field = resolve_price_field(resolved_market, "slab")
    requested_thickness = _extract_thickness_hint(message) or 2
    variant = (
        find_variant(product, requested_thickness, required_price_field=slab_field)
        or find_variant(product, 2, required_price_field=slab_field)
        or find_variant(product, None, required_price_field=slab_field)
    )
    slab_price = variant.get(slab_field) if variant else None
    if slab_price is None:
        return None

    area_text = _format_area_text(area, unit, language)
    _, _, slabs_base, slabs_buffer = _estimate_slabs_from_area(area, unit)
    price_text = _format_slab_price_for_reply(slab_price, resolved_market)
    total_base_text = _format_total_price_for_reply(round(slab_price * slabs_base, 2), resolved_market)
    total_buffer_text = _format_total_price_for_reply(round(slab_price * slabs_buffer, 2), resolved_market)
    product_link = _product_detail_link(product.get("product_url"), language)
    phone = _extract_embedded_phone_contact(message)

    lines = []
    if phone:
        lines.append("Dạ, em đã nhận được số điện thoại của Anh rồi ạ. Chuyên viên Lux Quartz sẽ kết nối qua Zalo để gửi ảnh slab thực tế, báo giá chiết khấu và phương án vận chuyển cho dự án.")

    lines.append(
        "Với biệt thự nghỉ dưỡng sát biển, khu bếp và quầy bar ngoài trời nắng gắt, Lux Quartz vẫn phù hợp nếu thi công đúng kỹ thuật. "
        "Bề mặt có độ hút nước dưới 0,05%, độ cứng 6.0-7.0 Mohs, độ bền uốn 35.0-55.0 MPa và khả năng kháng hóa chất mức C4, nên hỗ trợ hạn chế thấm ẩm, bám bẩn và ảnh hưởng từ hơi muối biển."
    )
    lines.append(
        f"Về phong thủy cho chủ nhà mệnh Thủy sinh năm 1983, em ưu tiên tông trắng/xám vì nhóm Kim sinh Thủy. "
        f"Mã em đề xuất để chốt phương án là {product.get('code')} ({product.get('series')}); có thể cân nhắc thêm LQ 910 nếu Anh muốn vân xám mềm hơn."
    )
    lines.append(
        f"Với diện tích {area_text}, độ dày {requested_thickness}cm, giá theo tấm em dùng để tính sơ bộ là {price_text}. "
        f"Số tấm cần đặt: {slabs_base} tấm cho phương án cơ bản, hoặc {slabs_buffer} tấm khi tính trừ hao 5%. "
        f"Tổng giá ước tính: phương án cơ bản {total_base_text}; phương án trừ hao 5% {total_buffer_text}."
    )
    if product_link:
        lines.append(f"Link chi tiết để gửi chủ đầu tư duyệt thông số: {product_link}")
    lines.append("Kết luận: với nhu cầu này em chọn tông trắng/xám như LQ 914 hoặc LQ 910 để vừa bền trong điều kiện gần biển, vừa hợp mệnh Thủy và dễ duyệt thẩm mỹ cho dự án nghỉ dưỡng.")
    return "\n\n".join(lines)


def _feng_shui_color_reply(message: str, language: str = "vi", market: str = DEFAULT_MARKET) -> Optional[str]:
    if not _is_feng_shui_query(message):
        return None

    requested = _extract_feng_shui_color_target(message)
    allowed_groups = ["Black", "Grey", "Blue"]

    if requested in {"Black", "Grey", "Blue"}:
        matches = tool_filter_products(color=requested, market=market, limit=3).get("matches") or []
        if matches:
            preview = ", ".join(item.get("code") for item in matches[:3] if item.get("code"))
            if language == "en":
                return (
                    f"For feng shui color preference, I only advise using verified catalog Color_Group values. "
                    f"For {requested} tone, current matching options include: {preview}. "
                    "If you share area in sqm, I will convert sqm to SF using x10.764 before slab calculation. "
                    "Please share thickness 2cm/3cm and your WhatsApp/Zalo for real slab photos."
                )
            return (
                f"Dạ với nhu cầu phong thủy, em chỉ tư vấn theo Color_Group có thật trong catalog. "
                f"Nhóm {requested} hiện có các mã phù hợp: {preview}. "
                "Nếu Anh/Chị gửi diện tích theo m², em sẽ đổi sang SF theo hệ số x10.764 trước khi tính số tấm slab. "
                "Anh/Chị cho em xin độ dày 2cm/3cm và Zalo/WhatsApp để em gửi ảnh slab thực tế nhé."
            )

    fallback_candidates: List[Dict[str, Any]] = []
    for group in allowed_groups:
        group_matches = tool_filter_products(color=group, market=market, limit=2).get("matches") or []
        fallback_candidates.extend(group_matches)

    unique_codes = []
    seen = set()
    for item in fallback_candidates:
        code = item.get("code")
        if not code or code in seen:
            continue
        seen.add(code)
        unique_codes.append(code)

    lq809 = get_product_by_code(_get_catalog_for_market(market), "LQ 809")
    lq809_note = ""
    if lq809 and lq809.get("code") not in seen:
        unique_codes.insert(0, lq809.get("code"))

    if language == "en":
        codes_text = ", ".join(unique_codes[:4]) if unique_codes else "Black/Grey options from current catalog"
        if lq809:
            lq809_note = " Example: LQ 809 (Calacatta Negro) is a strong Black-tone option."
        return (
            "Current catalog does not have a Green Color_Group, so I cannot suggest a non-existent green code. "
            f"I can recommend verified Black/Grey alternatives instead: {codes_text}.{lq809_note} "
            "If you provide area in sqm, I will convert sqm to SF with x10.764 before slab quote math. "
            "Share thickness and your WhatsApp/Zalo, I will send real photos and a precise quote."
        )

    codes_text = ", ".join(unique_codes[:4]) if unique_codes else "nhóm Black/Grey trong catalog hiện tại"
    if lq809:
        lq809_note = " Gợi ý nổi bật: LQ 809 (Calacatta Negro) thuộc tông Black rất phù hợp."
    return (
        "Dạ hiện catalog chưa có Color_Group xanh lá, nên em không thể gợi ý mã xanh lá không tồn tại. "
        f"Em đề xuất các mã tông Black/Grey có thật trong catalog: {codes_text}.{lq809_note} "
        "Nếu Anh/Chị gửi diện tích theo m², em sẽ đổi sang SF theo x10.764 trước khi tính slab và tổng giá. "
        "Anh/Chị cho em xin độ dày và Zalo/WhatsApp để em gửi ảnh thực tế ngay ạ."
    )


def _detect_explicit_price_unit(message: str, market: str) -> Optional[str]:
    text = (message or "").lower()
    if re.search(r"\b(sf|sq\s*ft|square\s*feet|\$/\s*sf|usd\s*/\s*sf)\b", text):
        return "sf"
    if re.search(r"\b(sqm|m2|m\^2|sq\s*m|\$/\s*sqm|usd\s*/\s*sqm)\b", text):
        return "sqm"
    if re.search(r"\b(slab|full\s*slab|\$/\s*slab|usd\s*/\s*slab)\b", text):
        return "slab"
    return "sf" if (market or "").upper() == "US" else None


def _is_brief_message(message: str) -> bool:
    words = [w for w in re.split(r"\s+", (message or "").strip()) if w]
    return len(words) <= 6


def _is_friendly_message(message: str) -> bool:
    text = (message or "").lower()
    friendly_tokens = [
        "hi",
        "hello",
        "hey",
        "thanks",
        "thank you",
        "bro",
        "chị ơi",
        "anh ơi",
        "ok",
        "oke",
        "nhé",
        "nha",
        "haha",
        "hihi",
        "❤️",
        ":)",
        ":-)",
    ]
    return any(token in text for token in friendly_tokens)


def _stable_variant_index(message: str, modulo: int) -> int:
    if modulo <= 0:
        return 0
    digest = hashlib.sha256((message or "").encode("utf-8", errors="ignore")).hexdigest()
    return int(digest[:8], 16) % modulo


def _clarify_non_product_reply(message: str = "", language: str = "vi") -> str:
    if language == "en":
        if _is_brief_message(message):
            return "I may have missed your intent. Please share code, color, quote per SF, or technical spec request and I will support immediately."
        if _is_friendly_message(message):
            return (
                "I can support this directly with Lux Quartz options. If you want, I can send best-selling vein patterns this month and shortlist by budget/SF right away."
            )
        variants = [
            "I may have misunderstood your request. Could you tell me your quartz need: product code, color tone, budget per SF, or project area?",
            "I can handle this from Lux Quartz project standards. Do you prefer white, grey, black, or cream tones first?",
            "To support you accurately, please share what matters most first: style, price/SF, or durability requirements.",
        ]
        selector = _stable_variant_index(message, len(variants))
        return variants[selector]

    if _is_brief_message(message):
        return "Dạ em hỗ trợ ngay được ạ. Anh/Chị cho em biết khu vực lắp đặt hoặc mã đá mình đang quan tâm để em tư vấn đúng nhu cầu nhé."
    if _is_friendly_message(message):
        return (
            "Dạ em hỗ trợ phần này trực tiếp cho mình luôn ạ. Nếu Anh/Chị thích, em gửi nhanh vài mẫu vân đẹp đang được chọn nhiều để mình tham khảo ngay nhé."
        )

    variants = [
        "Dạ để em tư vấn trúng nhu cầu hơn, Anh/Chị đang làm mới không gian hay cải tạo khu vực hiện có ạ?",
        "Anh/Chị chia sẻ giúp em khu vực lắp đặt và diện tích dự kiến, em sẽ chọn mẫu phù hợp rồi tính báo giá ngay cho mình ạ.",
        "Dạ em có thể đi nhanh theo nhu cầu thực tế của mình. Anh/Chị muốn ưu tiên độ bền, thẩm mỹ hay mức đầu tư trước ạ?",
        "Dạ em có thể gửi ngay vài mã phù hợp gu của mình. Anh/Chị thích vân mây nổi bật hay tông tinh gọn hiện đại hơn ạ?",
    ]
    selector = _stable_variant_index(message, len(variants))
    return variants[selector]


def _project_services_reply(language: str = "vi") -> str:
    if language == "en":
        return (
            "For large projects, Lux Quartz supports this service flow with full confidence.\n\n"
            "1. Private Label support based on your brand requirement.\n"
            "2. Video Call inspection before shipment confirmation.\n"
            "3. Bundle photo confirmation before container loading.\n"
            "4. Stock check confirmation by lot before booking shipment.\n\n"
            "Share your target volume and delivery schedule, and I will confirm the suitable flow immediately."
        )
    return (
        "Dạ, với dự án lớn Lux Quartz hỗ trợ đầy đủ và xử lý dứt khoát theo quy trình sau.\n\n"
        "1. Hỗ trợ làm nhãn riêng (Private Label) theo yêu cầu thương hiệu.\n"
        "2. Hỗ trợ kiểm tra hàng qua Video Call trước khi xác nhận xuất.\n"
        "3. Gửi ảnh Bundle trước khi đóng container để Anh/Chị duyệt.\n"
        "4. Kiểm kho theo lô và xác nhận tồn trước khi chốt lịch giao.\n\n"
        "Anh/Chị chia sẻ giúp em sản lượng và tiến độ giao để em chốt flow phù hợp ngay ạ."
    )


def _is_lead_time_query(message: str) -> bool:
    return bool(LEAD_TIME_PATTERN.search(message or ""))


def _is_payment_terms_query(message: str) -> bool:
    return bool(PAYMENT_TERMS_PATTERN.search(message or ""))


def _is_loading_capacity_query(message: str) -> bool:
    return bool(LOADING_CAPACITY_PATTERN.search(message or ""))


def _lead_time_reply(language: str = "vi") -> str:
    if language == "en":
        return (
            "Great question! For US export orders, the estimated lead time is typically 4 to 6 weeks from order confirmation.\n\n"
            "Here is the structure:\n"
            "- Production: scheduled at our Da Nang factory\n"
            "- Ocean Freight: shipment to US ports\n"
            "- Total Estimate: around 4-6 weeks from confirmation to delivery window\n\n"
            "Which US port are you shipping to?"
        )
    return (
        "Dạ thời gian giao hàng đi Mỹ hiện tại khoảng 4-6 tuần kể từ khi xác nhận đơn hàng.\n\n"
        "Cấu trúc tiến độ gồm:\n"
        "- Sản xuất tại nhà máy Đà Nẵng\n"
        "- Vận chuyển đường biển đến các cảng Hoa Kỳ\n"
        "- Tổng dự kiến: khoảng 4-6 tuần\n\n"
        "Anh/Chị đang ship về cảng nào tại Mỹ để em tối ưu phương án vận chuyển ạ?"
    )


def _payment_terms_reply(language: str = "vi") -> str:
    if language == "en":
        return (
            "Payment terms for US export are:\n"
            "- Option 1: 30% TT deposit, then balance payment after B/L copy is issued and before cargo release\n"
            "- Option 2: L/C (Letter of Credit)"
        )
    return (
        "Dạ điều khoản thanh toán cho thị trường Mỹ gồm:\n"
        "- Phương thức 1: Đặt cọc 30% TT, phần còn lại thanh toán sau khi có bản sao vận đơn (B/L copy) và trước khi giải phóng hàng\n"
        "- Phương thức 2: L/C (Thư tín dụng)"
    )


def _loading_capacity_reply(language: str = "vi") -> str:
    if language == "en":
        return (
            "20ft container loading plan is:\n"
            "- Weight: 21.5-27 tons/container\n"
            "- 2cm slabs: 70-104 slabs\n"
            "- 3cm slabs: 49-70 slabs\n"
            "- MOQ: 1 x 20ft container\n"
            "- Mix Color: up to 2 colors per container\n\n"
            "Which US port are you shipping to?"
        )
    return (
        "Dạ quy cách container 20ft hiện tại là:\n"
        "- Trọng lượng: 21.5-27 tấn/container\n"
        "- Loại 2cm: 70-104 tấm\n"
        "- Loại 3cm: 49-70 tấm\n"
        "- MOQ: 1 container 20ft\n"
        "- Mix color: tối đa 2 mã màu/container\n\n"
        "Anh/Chị đang ship về cảng nào tại Mỹ để em tối ưu phương án vận chuyển ạ?"
    )


def _is_custom_pattern_query(message: str) -> bool:
    return bool(CUSTOM_PATTERN_PATTERN.search(message or ""))


def _custom_pattern_reply(language: str = "vi", market: str = DEFAULT_MARKET) -> str:
    resolved_market = _normalize_market(market)
    if language == "en":
        return (
            "Absolutely — we provide custom stone pattern design and matching. Our R&D team in Da Nang specializes in high-end custom pattern matching (OEM) "
            f"for the {resolved_market} market.\n\n"
            "We offer a low-cost sampling process that is 100% refundable upon your first container order.\n\n"
            "Could you please share your WhatsApp/Email? Our specialist will contact you directly to discuss the design details.\n\n"
            "Alternatively, you can reach us immediately via:\n"
            "WhatsApp/Phone: 0982073500\n"
            "Email: cs@luxquartzvietnam.com"
        )
    return (
        "Dạ bên em có dịch vụ thiết kế và match custom pattern cho dự án của mình ạ. "
        "Đội R&D tại Đà Nẵng chuyên làm hàng mẫu theo yêu cầu (OEM).\n\n"
        "Anh/Chị cho em xin SĐT/Zalo/Email để chuyên viên kỹ thuật liên hệ trực tiếp trao đổi chi tiết thiết kế nhé.\n\n"
        "Hoặc mình liên hệ ngay qua:\n"
        "Hotline/Zalo: 0982073500\n"
        "Email: cs@luxquartzvietnam.com\n"
        "Địa chỉ: Nhà máy Lux Quartz, Đà Nẵng"
    )


def _is_complaint_query(message: str) -> bool:
    return bool(COMPLAINT_PATTERN.search(message or ""))


def _is_warranty_inquiry_query(message: str) -> bool:
    text = message or ""
    return bool(WARRANTY_INQUIRY_PATTERN.search(text)) and not _is_complaint_query(text)


def _has_recent_warranty_context(history: Optional[List[Dict[str, Any]]] = None, max_messages: int = 16) -> bool:
    if not history:
        return False

    recent = history[-max_messages:]
    for item in recent:
        content = (item.get("content") or "") if isinstance(item, dict) else ""
        if any(
            pattern.search(content)
            for pattern in [
                WARRANTY_INQUIRY_PATTERN,
                WARRANTY_CONTACT_PATTERN,
                WARRANTY_CLAIM_PROCESS_PATTERN,
                WARRANTY_SCOPE_PATTERN,
                WARRANTY_EXCLUSION_PATTERN,
                WARRANTY_DOCUMENT_PATTERN,
                WARRANTY_SHIPPING_PATTERN,
                WARRANTY_AESTHETIC_PATTERN,
                WARRANTY_REPLACEMENT_PATTERN,
                WARRANTY_SLA_PATTERN,
            ]
        ):
            return True
    return False


def _detect_warranty_intent(message: str, history: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
    text = message or ""
    has_warranty = bool(WARRANTY_INQUIRY_PATTERN.search(text))
    has_recent_context = _has_recent_warranty_context(history)

    if WARRANTY_SLA_PATTERN.search(text) and (has_warranty or has_recent_context):
        return "sla"
    if WARRANTY_DOCUMENT_PATTERN.search(text) and (has_warranty or has_recent_context):
        return "claim_documents"
    if WARRANTY_SHIPPING_PATTERN.search(text) and (has_warranty or has_recent_context):
        return "shipping_damage"
    if WARRANTY_AESTHETIC_PATTERN.search(text) and (has_warranty or has_recent_context):
        return "aesthetic_defect"
    if WARRANTY_REPLACEMENT_PATTERN.search(text) and (has_warranty or has_recent_context):
        return "replacement"
    if WARRANTY_EXCLUSION_PATTERN.search(text) and (has_warranty or has_recent_context):
        return "exclusions"
    if WARRANTY_CLAIM_PROCESS_PATTERN.search(text) and (has_warranty or has_recent_context):
        return "claim_process"
    if WARRANTY_CONTACT_PATTERN.search(text) and (has_warranty or has_recent_context):
        return "contact"
    if WARRANTY_FULL_POLICY_PATTERN.search(text):
        return "full_policy"
    if has_warranty or WARRANTY_SCOPE_PATTERN.search(text):
        return "coverage"
    return None


def _is_thermal_shock_complaint(message: str) -> bool:
    return bool(THERMAL_SHOCK_PATTERN.search(message or ""))


def _is_receiving_breakage_complaint(message: str) -> bool:
    return bool(RECEIVING_BREAKAGE_PATTERN.search(message or ""))


def _is_post_install_color_complaint(message: str) -> bool:
    text = message or ""
    has_post_install = bool(POST_INSTALL_COLOR_PATTERN.search(text))
    has_color_issue = bool(re.search(r"\b(color|colour|màu|sai\s*màu|khác\s*màu|mismatch)\b", text, re.IGNORECASE))
    return has_post_install and has_color_issue


def _warranty_coverage_reply(language: str = "vi", market: str = DEFAULT_MARKET) -> str:
    resolved_market = _normalize_market(market)
    if resolved_market != "VN":
        if language == "en":
            return "For US export, warranty coverage is a 12-year limited warranty for structural manufacturing defects."
        return "Dạ với thị trường US, phạm vi bảo hành là 12 năm giới hạn cho lỗi kết cấu do sản xuất."

    if language == "en":
        return (
            "For Vietnam market, warranty coverage is 12 years for structural manufacturing defects, including slab-core manufacturing faults and delamination. "
            "Surface/aesthetic defects are reviewed only when reported before cutting or fabrication."
        )
    return (
        "Dạ phạm vi bảo hành tại thị trường Việt Nam là 12 năm cho các lỗi kết cấu do nhà sản xuất, bao gồm lỗi phôi và tách lớp. "
        "Các lỗi thẩm mỹ/bề mặt chỉ được tiếp nhận khi báo trước khi cắt hoặc thi công ạ."
    )


def _warranty_exclusions_reply(language: str = "vi", market: str = DEFAULT_MARKET) -> str:
    if language == "en":
        return (
            "Warranty exclusions include installation errors, impact damage, misuse, chemical damage from improper cleaners, and unauthorized modification after delivery."
        )
    return (
        "Dạ các trường hợp loại trừ bảo hành gồm lỗi thi công/lắp đặt, hư hỏng do va đập, sử dụng sai cách, hư hại do hóa chất không phù hợp và tự ý chỉnh sửa sau khi bàn giao ạ."
    )


def _warranty_claim_documents_reply(language: str = "vi", market: str = DEFAULT_MARKET) -> str:
    if language == "en":
        return "Required claim documents are defect video/photos, slab backside Batch Number image, and original purchase invoice."
    return "Dạ hồ sơ yêu cầu bảo hành gồm video/ảnh lỗi, ảnh Batch Number mặt sau tấm đá và hóa đơn mua hàng gốc ạ."


def _warranty_claim_process_reply(language: str = "vi", market: str = DEFAULT_MARKET) -> str:
    resolved_market = _normalize_market(market)
    if resolved_market != "VN":
        if language == "en":
            return "For US claims, please submit defect photos/videos, Batch Number, and invoice; our team will verify and respond with the resolution path."
        return "Dạ với yêu cầu bảo hành thị trường US, Anh/Chị gửi ảnh/video lỗi, Batch Number và hóa đơn để bên em xác minh và phản hồi phương án xử lý ạ."

    if language == "en":
        return (
            "Vietnam claim process: submit defect video/photos, slab backside Batch Number, and invoice via Zalo 0833904255. "
            "After verification of structural manufacturing defects, we proceed with replacement support under nationwide remote handling."
        )
    return (
        "Dạ quy trình gửi yêu cầu bảo hành tại Việt Nam: Anh/Chị gửi video/ảnh lỗi, Batch Number mặt sau tấm đá và hóa đơn qua Zalo 0833904255. "
        "Sau khi xác minh lỗi kết cấu do nhà sản xuất, bên em triển khai hỗ trợ đổi mới theo quy trình chung toàn quốc ạ."
    )


def _warranty_contact_support_reply(language: str = "vi", market: str = DEFAULT_MARKET) -> str:
    if language == "en":
        return "You can contact warranty support via Hotline/Zalo/WhatsApp: 0833904255 or Email: cs@luxquartzvietnam.com."
    return "Dạ kênh hỗ trợ bảo hành gồm Hotline/Zalo: 0833904255 và Email: cs@luxquartzvietnam.com ạ."


def _warranty_shipping_damage_reply(language: str = "vi", market: str = DEFAULT_MARKET) -> str:
    resolved_market = _normalize_market(market)
    if resolved_market != "VN":
        if language == "en":
            return "For US export, shipping damage claims require immediate evidence review and handling under shipment terms and claim documentation."
        return "Dạ với thị trường US, lỗi vận chuyển được xử lý theo điều kiện giao hàng và hồ sơ khiếu nại kèm bằng chứng ạ."

    if language == "en":
        return "For Vietnam market, shipping damage must be reported within 24 hours from delivery receipt with supporting photos/videos and invoice."
    return "Dạ với thị trường Việt Nam, lỗi vận chuyển cần được báo trong vòng 24 giờ kể từ khi nhận hàng, kèm ảnh/video và hóa đơn để xử lý nhanh ạ."


def _warranty_aesthetic_defect_reply(language: str = "vi", market: str = DEFAULT_MARKET) -> str:
    if language == "en":
        return "Surface/aesthetic defects must be reported before cutting or fabrication; after installation, claims are reviewed case-by-case based on evidence."
    return "Dạ lỗi thẩm mỹ/bề mặt cần được báo trước khi cắt hoặc thi công; nếu đã lắp đặt, hồ sơ sẽ được xem xét theo từng trường hợp dựa trên bằng chứng thực tế ạ."


def _warranty_replacement_policy_reply(language: str = "vi", market: str = DEFAULT_MARKET) -> str:
    resolved_market = _normalize_market(market)
    if resolved_market != "VN":
        if language == "en":
            return "For US market, replacement is proposed after verification and follows the approved export resolution path."
        return "Dạ với thị trường US, phương án đổi tấm sẽ được đề xuất sau xác minh và theo lộ trình xử lý đã duyệt ạ."

    if language == "en":
        return "For Vietnam market, once structural manufacturing defects are confirmed, we apply 1-for-1 replacement and dispatch to the nearest cargo depot/warehouse."
    return "Dạ tại thị trường Việt Nam, khi xác minh lỗi kết cấu do nhà sản xuất, bên em áp dụng chính sách 1 đổi 1 và chuyển tấm mới về chành xe/kho gần nhất ạ."


def _warranty_sla_reply(language: str = "vi", market: str = DEFAULT_MARKET) -> str:
    if language == "en":
        return "Service timeline: initial response within 24 hours, and full claim review in 3–5 business days after receiving complete documents."
    return "Dạ thời gian phản hồi tiêu chuẩn là phản hồi ban đầu trong 24 giờ và thẩm định hồ sơ trong 3-5 ngày làm việc sau khi nhận đủ chứng từ ạ."


def _warranty_full_policy_reply(language: str = "vi", market: str = DEFAULT_MARKET) -> str:
    resolved_market = _normalize_market(market)
    if resolved_market != "VN":
        if language == "en":
            return (
                "We maintain a clear after-sales and warranty process so customers can order with confidence. Our US export warranty structure is designed to support long-term project reliability.\n\n"
                "Warranty term: Lux Quartz provides a 12-Year Limited Warranty from the delivery date.\n\n"
                "Coverage scope: this warranty applies to structural manufacturing defects and material/production-related issues verified through technical review.\n\n"
                "Surface and aesthetic condition policy: any surface, color, or aesthetic concern must be reported before fabrication or installation.\n\n"
                "Shipping damage policy: shipping or packaging claims must be reported within 30 days from receipt at the US port or warehouse, with supporting evidence.\n\n"
                "Exclusions: the warranty does not cover installation/fabrication damage, misuse, impact or external-force damage, chemical exposure from improper cleaners, or unauthorized modifications after delivery.\n\n"
                "Liability limitation: Lux Quartz covers slab value only. Local labor, removal, reinstallation, and refabrication costs are excluded.\n\n"
                "Required claim documents: overall and close-up photos/videos, slab backside Batch Number, and original purchase invoice.\n\n"
                "Claim process: Step 1 submit the complete evidence dossier; Step 2 technical review and root-cause verification; Step 3 resolution proposal after verification under shipment terms.\n\n"
                "Response timeline: initial response within 24 hours, and full review within 3–5 business days after complete documents are received.\n\n"
                "Support channels: Hotline/Zalo/WhatsApp 0833904255 | Email cs@luxquartzvietnam.com."
            )
        return (
            "Dạ để Anh/Chị yên tâm khi đặt hàng thị trường US, Lux Quartz luôn áp dụng quy trình hậu mãi và bảo hành rõ ràng, theo dõi xuyên suốt sau giao hàng ạ.\n\n"
            "Thời hạn bảo hành là 12 năm giới hạn tính từ ngày giao hàng. Phạm vi bảo hành áp dụng cho lỗi kết cấu do sản xuất và các vấn đề liên quan đến vật liệu/quy trình sản xuất sau khi được thẩm định kỹ thuật.\n\n"
            "Về lỗi thẩm mỹ/bề mặt, Anh/Chị cần báo trước khi cắt hoặc lắp đặt. Với lỗi vận chuyển/đóng gói, thời hạn tiếp nhận là trong vòng 30 ngày kể từ khi nhận hàng tại cảng hoặc kho tại Mỹ, kèm bằng chứng đầy đủ.\n\n"
            "Các trường hợp không thuộc bảo hành gồm: lỗi do lắp đặt/thi công, sử dụng sai mục đích, va đập/tác động ngoại lực, hư hại do hóa chất không phù hợp và tự ý chỉnh sửa sau bàn giao.\n\n"
            "Giới hạn trách nhiệm: Lux Quartz chỉ bảo hành giá trị tấm đá; chi phí nhân công địa phương, tháo dỡ, lắp đặt lại và gia công lại sẽ không nằm trong phạm vi chi trả.\n\n"
            "Hồ sơ yêu cầu gồm ảnh/video tổng thể và cận cảnh lỗi, ảnh Batch Number mặt sau tấm đá và hóa đơn mua hàng. Quy trình xử lý gồm 3 bước: gửi hồ sơ, thẩm định nguyên nhân, và đề xuất phương án xử lý sau xác minh.\n\n"
            "SLA phản hồi: phản hồi ban đầu trong 24 giờ, thẩm định đầy đủ trong 3-5 ngày làm việc sau khi nhận đủ chứng từ. Kênh hỗ trợ: Hotline/Zalo/WhatsApp 0833904255 | Email cs@luxquartzvietnam.com ạ."
        )

    if language == "en":
        return (
            "Vietnam warranty policy summary: coverage is 15 years for structural manufacturing defects. Exclusions include installation/fabrication errors, misuse, impact damage, chemical exposure, and unauthorized modification.\n\n"
            "Claim documents include defect photos/videos, slab backside Batch Number, and original invoice. Claim process is submission via Zalo 0833904255, technical verification, then replacement support if manufacturer defect is confirmed.\n\n"
            "Shipping damage must be reported within 24 hours of receipt. Surface/aesthetic defects must be reported before fabrication or installation. Replacement policy is 1-for-1 after verified manufacturer defect, with delivery to nearest cargo depot/warehouse.\n\n"
            "Service timeline: initial response within 24 hours and claim review within 3–5 business days after complete documents. Support channels: Hotline/Zalo 0833904255, Email cs@luxquartzvietnam.com."
        )
    return (
        "Dạ để Anh/Chị yên tâm hơn khi đặt hàng, bên em có chính sách bảo hành và quy trình hỗ trợ khá rõ ràng, theo dõi xuyên suốt trong quá trình sử dụng ạ.\n\n"
        "Về phạm vi bảo hành, Lux Quartz Việt Nam bảo hành 12 năm cho lỗi kết cấu do nhà sản xuất. Các trường hợp không áp dụng bảo hành gồm lỗi thi công/lắp đặt, sử dụng sai cách, va đập, hư hại do hóa chất không phù hợp và tự ý chỉnh sửa sau bàn giao.\n\n"
        "Về hồ sơ, Anh/Chị cần gửi video/ảnh khu vực lỗi, ảnh Batch Number mặt sau tấm đá và hóa đơn gốc. Quy trình xử lý là tiếp nhận hồ sơ qua Zalo 0833904255, sau đó bên em xác minh nguyên nhân; nếu xác minh đúng lỗi do nhà sản xuất, bên em sẽ áp dụng chính sách đổi mới theo quy định (1 đổi 1) và chuyển tấm mới về chành xe/kho gần nhất.\n\n"
        "Về quy định tiếp nhận lỗi, lỗi vận chuyển cần báo trong 24 giờ từ khi nhận hàng; lỗi thẩm mỹ/bề mặt cần báo trước khi cắt hoặc thi công. Thời gian phản hồi tiêu chuẩn là phản hồi ban đầu trong 24 giờ và thẩm định hồ sơ trong 3-5 ngày làm việc sau khi nhận đủ chứng từ.\n\n"
        "Kênh hỗ trợ bảo hành: Hotline/Zalo 0833904255 và Email cs@luxquartzvietnam.com."
    )


def _warranty_inquiry_reply(language: str = "vi", market: str = DEFAULT_MARKET) -> str:
    return _warranty_coverage_reply(language=language, market=market)


def _warranty_context_reply(
    message: str,
    language: str = "vi",
    market: str = DEFAULT_MARKET,
    history: Optional[List[Dict[str, Any]]] = None,
) -> Optional[str]:
    intent = _detect_warranty_intent(message, history)
    if intent == "contact":
        return _warranty_contact_support_reply(language=language, market=market)
    if intent == "claim_process":
        return _warranty_claim_process_reply(language=language, market=market)
    if intent == "claim_documents":
        return _warranty_claim_documents_reply(language=language, market=market)
    if intent == "exclusions":
        return _warranty_exclusions_reply(language=language, market=market)
    if intent == "shipping_damage":
        return _warranty_shipping_damage_reply(language=language, market=market)
    if intent == "aesthetic_defect":
        return _warranty_aesthetic_defect_reply(language=language, market=market)
    if intent == "replacement":
        return _warranty_replacement_policy_reply(language=language, market=market)
    if intent == "sla":
        return _warranty_sla_reply(language=language, market=market)
    if intent == "full_policy":
        return _warranty_full_policy_reply(language=language, market=market)
    if intent == "coverage":
        return _warranty_coverage_reply(language=language, market=market)
    return None


def _complaint_warranty_reply(message: str, language: str = "vi", market: str = DEFAULT_MARKET) -> str:
    resolved_market = _normalize_market(market)

    if resolved_market == "VN":
        if language == "en":
            return (
                "We are sorry for this issue. For Vietnam market, Lux Quartz applies a 12-year structural warranty for factory defects.\n\n"
                "Please share video evidence, slab backside Batch Number, and invoice via Zalo 0833904255 within 7 days from first notice.\n"
                "For transit damage, please report within 24 hours. For aesthetic defects, report before cutting/fabrication.\n\n"
                "We apply one nationwide process for all locations in Vietnam. After verification of manufacturer fault, we apply 1-for-1 replacement and ship to your nearest cargo depot."
            )
        return (
            "Dạ em rất tiếc về sự cố này. Với thị trường Việt Nam, Lux Quartz áp dụng bảo hành 12 năm cho lỗi kết cấu do nhà sản xuất.\n\n"
            "Anh/Chị gửi giúp em video chi tiết, ảnh Batch Number mặt sau tấm đá và hóa đơn qua Zalo 0833904255 trong tối đa 07 ngày từ lúc báo lỗi.\n"
            "Lưu ý thời gian vàng: lỗi vận chuyển báo trong 24h; lỗi thẩm mỹ cần báo trước khi cắt hoặc thi công.\n\n"
            "Bên em áp dụng một quy trình xử lý chung trên toàn quốc, không phân biệt tỉnh/thành. Sau khi xác định lỗi do nhà sản xuất, bên em áp dụng 1 đổi 1 và chuyển tấm mới về chành xe hoặc kho gần nhất của mình ạ."
        )

    if _is_receiving_breakage_complaint(message):
        if language == "en":
            return (
                "We are sorry for this issue. For transit damage under FOB Danang terms, responsibility transfers at port loading, so we first verify whether crate/A-frame packing was faulty.\n\n"
                "Please share:\n"
                "- Full photos/videos of the A-frame/crate before and after unloading\n"
                "- Close-up damage photos on slabs and edges\n"
                "- Batch Number and original invoice\n\n"
                "Resolution path:\n"
                "- If packing was non-compliant, Lux Quartz will arrange replacement slabs\n"
                "- If damage occurred during sea transit/handling, we will guide you to proceed with your insurance provider"
            )
        return (
            "Dạ em rất tiếc về sự cố này. Với điều kiện FOB Đà Nẵng, trách nhiệm được chuyển giao tại thời điểm xếp hàng lên tàu, nên bên em sẽ ưu tiên xác minh kiện gỗ/A-frame có lỗi đóng gói hay không.\n\n"
            "Anh/Chị vui lòng gửi:\n"
            "- Ảnh/video toàn bộ kiện A-frame trước và sau khi dỡ hàng\n"
            "- Ảnh cận cảnh vị trí bể vỡ trên tấm đá/cạnh đá\n"
            "- Batch Number và hóa đơn gốc\n\n"
            "Hướng xử lý:\n"
            "- Nếu lỗi đóng gói không đạt chuẩn, Lux Quartz sẽ bố trí gửi bù\n"
            "- Nếu hư hỏng phát sinh trong vận chuyển biển/bốc dỡ, bên em sẽ hướng dẫn làm việc với bảo hiểm"
        )

    if _is_thermal_shock_complaint(message):
        if language == "en":
            return (
                "We are sorry for this issue. Lux Quartz is heat-resistant, but not heat-proof. Based on your description (hot pans/air fryer heat), this is a thermal shock misuse case, not a manufacturing defect under the free 12-year structural warranty.\n\n"
                "Please share clear photos/videos of the affected area and the original invoice. We will provide remote technical support so your local fabricator can assess and repair the surface."
            )
        return (
            "Dạ em rất tiếc về sự cố này. Lux Quartz có khả năng chịu nhiệt, nhưng không phải chống nhiệt tuyệt đối. Theo mô tả (nhiệt từ nồi nóng/air fryer), đây là trường hợp sốc nhiệt do sử dụng, không phải lỗi sản xuất thuộc bảo hành miễn phí 12 năm.\n\n"
            "Anh/Chị gửi giúp em ảnh/video rõ khu vực lỗi và hóa đơn gốc, bên em sẽ hỗ trợ kỹ thuật từ xa để thợ địa phương đánh giá và xử lý bề mặt."
        )

    if _is_post_install_color_complaint(message):
        if language == "en":
            return (
                "We are sorry for this issue. Since the slab has already been installed/fabricated, this is a difficult case under our Inspect before Fabrication rule.\n\n"
                "Please still share high-resolution overall and close-up photos, Batch Number from the slab backside, and the original invoice. We will review the evidence and support the best possible resolution."
            )
        return (
            "Dạ em rất tiếc về vấn đề màu sắc. Vì đá đã lắp đặt/gia công, đây là trường hợp khó hỗ trợ theo quy tắc Inspect before Fabrication (kiểm tra trước khi cắt).\n\n"
            "Tuy vậy, Anh/Chị vẫn gửi giúp em ảnh toàn cảnh/cận cảnh độ phân giải cao, Batch Number mặt sau và hóa đơn gốc để bên em kiểm tra và hỗ trợ mức tốt nhất có thể."
        )

    if language == "en":
        return (
            "We are sorry for this issue. Lux Quartz provides a 12-year Limited Warranty for structural defects.\n\n"
            "To verify and activate support, please share:\n"
            "- High-resolution photos/videos of the damage\n"
            "- Batch Number printed on the back of the slab\n"
            "- Original invoice\n\n"
            "For the US export market, because the factory is in Da Nang, we resolve claims in this order:\n"
            "- Credit Note: discount/credit applied to your next order\n"
            "- Replacement: free replacement slab shipped in the next US-bound container\n"
            "- Remote Support: video call with your local fabricator for practical repair guidance\n\n"
            "Important scope: Lux Quartz warranty covers slab value only; local US labor costs (removal, refabrication, reinstallation) are handled by the customer or customer insurance."
        )
    return (
        "Dạ em rất tiếc về sự cố này. Lux Quartz có bảo hành giới hạn 12 năm cho lỗi kết cấu.\n\n"
        "Để xác minh và kích hoạt hỗ trợ, Anh/Chị vui lòng gửi:\n"
        "- Ảnh/video độ phân giải cao khu vực lỗi\n"
        "- Batch Number in ở mặt sau tấm đá\n"
        "- Hóa đơn mua hàng gốc\n\n"
        "Với thị trường xuất khẩu Mỹ, do nhà máy ở Đà Nẵng, bên em xử lý theo thứ tự:\n"
        "- Credit Note: trừ/giảm giá vào đơn hàng kế tiếp\n"
        "- Replacement: gửi bù tấm đá miễn phí trong container đi Mỹ kế tiếp\n"
        "- Remote Support: video call hướng dẫn thợ địa phương xử lý thực tế\n\n"
        "Phạm vi trách nhiệm: Lux Quartz bảo hành giá trị tấm đá (slab value), không bao gồm chi phí nhân công tháo dỡ, gia công lại, lắp đặt tại Mỹ."
    )


def _correction_recovery_reply(language: str = "vi") -> str:
    if language == "en":
        return (
            "You are absolutely right to flag that, and I’m sorry for the confusion.\n\n"
            "Please share the exact code, thickness, area, and pricing unit (SF or slab), and I will recalculate immediately with transparent steps in the same message."
        )
    return (
        "Dạ Anh/Chị nói đúng, em xin lỗi vì nhầm lẫn vừa rồi.\n\n"
        "Anh/Chị gửi lại giúp em mã đá, độ dày, diện tích và đơn vị giá (SF hay slab), em sẽ tính lại ngay trong cùng tin nhắn với công thức minh bạch ạ."
    )


def _technical_handoff_reply(language: str = "vi") -> str:
    if language == "en":
        return "I will log this deep technical point and get a specialist-confirmed answer back to you via WhatsApp/Zalo right away."
    return "Dạ, vấn đề này em sẽ ghi nhận để xin ý kiến chuyên gia kỹ thuật và phản hồi Anh/Chị ngay qua Zalo nhé."


def _technical_specs_reply(message: str, language: str = "vi") -> str:
    code_match = CODE_FINDER.search(message or "")
    code_text = f" for code {code_match.group(0).upper()}" if code_match and language == "en" else ""
    if code_match and language != "en":
        code_text = f" cho mã {code_match.group(0).upper()}"

    if language == "en":
        return (
            f"For quality and durability{code_text}, Lux Quartz technical benchmarks are: hardness 6.0-7.0 Mohs (about 2x marble), "
            "water absorption <0.05%, flexural strength 35.0-55.0 MPa, and chemical resistance level C4. "
            "In practical daily kitchen use, that means strong resistance to routine prep wear when used properly. "
            "For premium island countertops and high-traffic kitchens, I professionally recommend 3cm thickness when design allows, "
            "while 2cm is a cost-optimized option for lighter-use areas. "
            "Would you like me to recommend 2-3 matching codes and send real slab photos to your WhatsApp/Zalo?"
        )

    return (
        f"Dạ về chất lượng và độ bền{code_text}, Lux Quartz có thông số kỹ thuật: độ cứng 6.0-7.0 Mohs (khoảng gấp 2 lần marble), "
        "độ hút nước <0.05%, độ bền uốn 35.0-55.0 MPa và kháng hóa chất mức C4. "
        "Trong thực tế dùng bếp hằng ngày, bề mặt chịu lực và chống trầy khá tốt khi sơ chế nấu nướng đúng cách. "
        "Với đảo bếp cao cấp và khu vực sử dụng nhiều, em khuyến nghị chuyên môn chọn độ dày 3cm nếu thiết kế cho phép; "
        "2cm là phương án tối ưu chi phí cho khu vực tải nhẹ hơn. "
        "Anh/Chị có muốn em gợi ý 2-3 mã phù hợp và gửi ảnh slab thực tế qua Zalo/WhatsApp không ạ?"
    )


def _has_required_technical_markers(text: str) -> bool:
    normalized = (text or "").lower()
    has_mohs = "6.0-7.0" in normalized
    has_absorption = "0.05" in normalized or "0,05" in normalized
    has_flexural = "35.0-55.0" in normalized
    has_c4 = "c4" in normalized
    return has_mohs and has_absorption and has_flexural and has_c4


def _ensure_non_product_guard(
    reply: str,
    message: str,
    language: str,
    market: str = DEFAULT_MARKET,
    history: Optional[List[Dict[str, Any]]] = None,
    from_ai: bool = False,
) -> str:
    # Greeting và complaint luôn dùng hardcode (chuẩn nhất)
    if _is_greeting_message(message):
        return _greeting_reply(language)
    if _is_complaint_query(message):
        return _complaint_warranty_reply(message, language, market=market)

    # FIX 3 (nâng cấp): Nếu OpenAI đã trả lời đủ nội dung (>50 ký tự),
    # ưu tiên reply đó — không cho hardcode warranty/lead_time/... override
    if from_ai and reply and len(reply.strip()) > 50:
        return reply

    # Fallback hardcode khi không có OpenAI
    warranty_reply = _warranty_context_reply(message, language=language, market=market, history=history)
    if warranty_reply:
        return warranty_reply

    if _is_lead_time_query(message):
        return _lead_time_reply(language)
    if _is_payment_terms_query(message):
        return _payment_terms_reply(language)
    if _is_loading_capacity_query(message):
        return _loading_capacity_reply(language)
    if _is_custom_pattern_query(message):
        return _custom_pattern_reply(language, market=market)
    if _is_product_related_query(message):
        return reply
    return _clarify_non_product_reply(message=message, language=language)


def _ensure_technical_evidence(reply: str, message: str, language: str) -> str:
    if _is_complaint_query(message):
        return reply
    if not _is_technical_query(message):
        return reply
    if COMPARISON_INTENT_PATTERN.search(message or ""):
        return reply
    if _has_required_technical_markers(reply):
        return reply

    supplemental = _technical_specs_reply(message, language)
    if language == "en":
        return (reply or "").rstrip() + " " + supplemental
    return (reply or "").rstrip() + " " + supplemental


def _ensure_consultative_response(reply: str, message: str, language: str) -> str:
    if not _is_special_context_query(message):
        return reply
    if _has_consultative_markers(reply):
        return reply

    area, unit = _extract_area_hint(message)
    if area is not None and unit is not None:
        return _strip_area_request_tail(reply)

    normalized = (reply or "").lower()
    if CODE_FINDER.search(message or "") and any(token in normalized for token in ["giá", "price", "₫", "$", "/tấm", "/slab"]):
        return f"{reply.rstrip()} {_strip_area_request_tail(_consultative_context_reply(message, language))}".strip()
    return _consultative_context_reply(message, language)


def _heat_uv_context_reply(language: str = "vi") -> str:
    if language == "en":
        return (
            "For a west-facing TV wall or strong-sun location, Lux Quartz is still suitable when installed correctly. "
            "Lux Quartz has good UV color stability and heat resistance for this application, so the customer does not need to switch materials only because the wall faces west."
        )
    return (
        "Với vách tivi hướng Tây hoặc khu vực có nắng gắt, Lux Quartz vẫn phù hợp khi thi công đúng kỹ thuật. "
        "Đá Lux Quartz bền màu UV và chịu nhiệt tốt cho ứng dụng vách tivi, nên mình không cần chuyển sang vật liệu khác chỉ vì hướng Tây."
    )


def _ensure_heat_uv_consultation(reply: str, message: str, language: str) -> str:
    if not HEAT_UV_CONTEXT_PATTERN.search(message or ""):
        return reply

    normalized = (reply or "").lower()
    negative_advice = any(
        token in normalized
        for token in [
            "cân nhắc thêm về việc sử dụng",
            "độ bền nhiệt tốt hơn",
            "tìm loại đá khác",
            "switch materials",
            "more heat-resistant material",
        ]
    )
    has_positive_uv_heat = "bền màu uv" in normalized and "chịu nhiệt tốt" in normalized
    if has_positive_uv_heat and not negative_advice:
        return reply

    compact_reply = _strip_area_request_tail(reply)
    addition = _heat_uv_context_reply(language)
    if not compact_reply:
        return addition
    return f"{compact_reply} {addition}".strip()


def _sanitize_chat_style(reply: str, language: str = "vi") -> str:
    text = _localize_reply_links(_remove_image_markdown(reply or "", language), language)
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = text.replace("**", "")
    text = text.replace("__", "")
    text = re.sub(r"(?<!\S)\*(?!\s)", "", text)
    text = re.sub(r"(?<!\s)\*(?!\S)", "", text)
    text = re.sub(r"(^|\n)\s*\*\s+", r"\1", text)
    text = re.sub(r"(^|\n)\s*\d+\.\s+", r"\1", text)
    # FIX 2: Bỏ dòng regex xóa label dấu ':' vì nó phá format như 'Hotline/Zalo: 0833904255'
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _ensure_language_consistency(reply: str, message: str, language: str, market: str = DEFAULT_MARKET) -> str:
    text = (reply or "").strip()
    if language == "en":
        if re.search(r"\b(Dạ|Anh/Chị|độ\s+hút\s+nước|số\s+tấm|tổng\s+giá|báo\s+giá)\b", text, re.IGNORECASE):
            return _fallback_reply(message, language="en", market=market)
        return text

    replacements = [
        r"For quality and durability, Lux Quartz technical benchmarks are:[\s\S]*?(?:WhatsApp/Zalo\??|$)",
        r"Technical parameters: hardness 6\.0-7\.0 Mohs, water absorption <0\.05%, flexural strength 35\.0-55\.0 MPa, and chemical resistance C4\.?",
        r"In practical daily kitchen use,[^.?!]*[.?!]?",
        r"For premium island countertops[^.?!]*[.?!]?",
        r"Would you like me to recommend 2-3 matching codes and send real slab photos to your WhatsApp/Zalo\??",
        r"Would you like me to send real slab photos\??",
        r"Please share your WhatsApp\.?",
        r"Please share your WhatsApp/Zalo[^.?!]*[.?!]?",
        r"Share your WhatsApp/Zalo[^.?!]*[.?!]?",
    ]
    for pattern in replacements:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE | re.DOTALL)

    if re.search(r"\b(would\s+you\s+like|please\s+share|share\s+your\s+whatsapp)\b", text, re.IGNORECASE):
        return _fallback_reply(message, language="vi", market=market)

    return text.strip()


def _smart_followup_question(message: str, language: str) -> str:
    if language == "en":
        options = [
            "Would you like me to shortlist 2-3 matching codes for your project?",
            "Would you like a quick side-by-side option for 2cm vs 3cm next?",
            "Would you like me to prepare the next step with exact quantity and total?",
        ]
        return options[_stable_variant_index(message or "", len(options))]

    options = [
        "Anh/Chị muốn em gợi ý nhanh 2-3 mã phù hợp nhất với nhu cầu hiện tại không ạ?",
        "Anh/Chị có muốn em so sánh nhanh phương án 2cm và 3cm cho mình ngay không ạ?",
        "Anh/Chị muốn em chốt luôn bước tiếp theo theo đúng diện tích của mình không ạ?",
    ]
    return options[_stable_variant_index(message or "", len(options))]


def _should_append_followup(reply: str, message: str, language: str) -> bool:
    normalized_reply = (reply or "").lower()
    normalized_message = (message or "").lower()

    if any(token in normalized_reply for token in ["cho em xin", "vui lòng cho em", "please share", "chia sẻ giúp em"]):
        return False

    if _is_complaint_query(message):
        return False

    if _detect_warranty_intent(message):
        return False

    if _is_custom_pattern_query(message):
        return False

    if any(token in normalized_reply for token in ["hotline", "zalo", "email", "warranty", "bảo hành"]):
        return False

    if RESOLVED_SIGNAL_PATTERN.search(normalized_message):
        return False

    has_quote_result = any(
        token in normalized_reply
        for token in ["ước tính cơ bản", "estimated base total", "tổng giá", "tổng tiền", "total", "giá theo tấm", "slab price"]
    )
    return has_quote_result


def _ensure_smart_followup(reply: str, message: str, language: str) -> str:
    text = (reply or "").strip()
    if not text:
        return text

    if not _should_append_followup(text, message, language):
        return text

    followup = _smart_followup_question(message, language)
    if followup.lower() in text.lower():
        return text
    return f"{text}\n\n{followup}".strip()


def _fallback_reply(
    message: str,
    language: str = "vi",
    market: str = DEFAULT_MARKET,
    history: Optional[List[Dict[str, Any]]] = None,
) -> str:
    resolved_market = _normalize_market(market)
    session_history = history or []
    area, unit, from_history = _resolve_area_context(message, session_history)

    deterministic_reply = _deterministic_code_quote_reply(message, language, resolved_market, session_history)
    if deterministic_reply:
        return deterministic_reply

    water_feng_shui_reply = _water_feng_shui_project_reply(message, language, market=resolved_market, history=session_history)
    if water_feng_shui_reply:
        return water_feng_shui_reply

    comparison_reply = _calacatta_carrara_comparison_reply(message, language, session_history)
    if comparison_reply:
        if area is not None and unit is not None and "ceil(" not in comparison_reply.lower():
            return f"{comparison_reply} {_area_slab_guidance(area, unit, language, from_history)}"
        return comparison_reply

    representative_reply = _representative_codes_reply(message, language, resolved_market, session_history)
    if representative_reply:
        return representative_reply

    feng_shui_reply = _feng_shui_color_reply(message, language, market=resolved_market)
    if feng_shui_reply:
        if area is not None and unit is not None and "ceil(" not in feng_shui_reply.lower():
            return f"{feng_shui_reply} {_area_slab_guidance(area, unit, language, from_history)}"
        return feng_shui_reply

    if _is_special_context_query(message) and not CODE_FINDER.search(message or ""):
        reply = _consultative_context_reply(message, language)
        if area is not None and unit is not None:
            compact_reply = _strip_area_request_tail(reply)
            return f"{compact_reply} {_area_slab_guidance(area, unit, language, from_history)}"
        return reply

    if _is_complaint_query(message):
        return _complaint_warranty_reply(message, language, market=resolved_market)

    warranty_reply = _warranty_context_reply(message, language=language, market=resolved_market, history=session_history)
    if warranty_reply:
        return warranty_reply

    if _is_lead_time_query(message):
        return _lead_time_reply(language)

    if _is_payment_terms_query(message):
        return _payment_terms_reply(language)

    if _is_loading_capacity_query(message):
        return _loading_capacity_reply(language)

    if _is_custom_pattern_query(message):
        return _custom_pattern_reply(language, market=resolved_market)

    if _is_correction_request(message):
        return _correction_recovery_reply(language)

    if _is_project_service_query(message):
        return _project_services_reply(language)

    if _is_technical_query(message):
        if re.search(r"\b(xrd|sem|astm\s*c880|micro\s*fracture|petrography|advanced\s*lab)\b", (message or ""), re.IGNORECASE):
            return _technical_handoff_reply(language)
        return _technical_specs_reply(message, language)

    if not _is_product_related_query(message):
        if area is not None and unit is not None:
            if language == "en":
                return (
                    f"{_area_slab_guidance(area, unit, language, from_history)} "
                    "I can continue with exact pricing as soon as you share one code and thickness 2cm or 3cm."
                )
            return (
                f"{_area_slab_guidance(area, unit, language, from_history)} "
                "Anh/Chị gửi thêm giúp em 1 mã cụ thể và độ dày 2cm hoặc 3cm để em chốt tổng giá ngay ạ."
            )
        return _clarify_non_product_reply(message=message, language=language)

    if _is_kitchen_area_consult_query(message) or (area is not None and _has_kitchen_context(message, session_history)):
        return _kitchen_area_consult_reply(message, language=language, market=resolved_market, history=session_history)

    code_match = CODE_FINDER.search(message or "")
    if code_match:
        code = code_match.group(0)
        product = get_product_by_code(_get_catalog_for_market(resolved_market), code)
        if product:
            slab_field = resolve_price_field(resolved_market, "slab")
            requested_thickness = _extract_thickness_hint(message) or 2
            variant = (
                find_variant(product, requested_thickness, required_price_field=slab_field)
                or find_variant(product, 2, required_price_field=slab_field)
                or find_variant(product, 3, required_price_field=slab_field)
                or find_variant(product, None, required_price_field=slab_field)
            )
            slab_price = variant.get(slab_field) if variant else None
            if slab_price is not None:
                actual_thickness = variant.get("thickness_cm") if variant else None
                display_slab_price = slab_price
                price_note = None
                if requested_thickness == 3 and actual_thickness != 3:
                    display_slab_price = round(slab_price * 1.25, 2)
                    price_note = "ước tính 3cm +25% từ giá 2cm"
                price_text = _format_slab_price_for_reply(display_slab_price, resolved_market)
                note_tail = f" ({price_note})" if price_note else ""
                detail_link = _product_detail_link(product.get("product_url"), language)
                link_tail = f" {detail_link}" if detail_link else ""
                if language == "en":
                    reply = f"For code {product.get('code')} ({product.get('series')}), current slab price starts from {price_text}{note_tail}.{link_tail}"
                    if area is not None and unit is not None:
                        _, _, slabs_base, _ = _estimate_slabs_from_area(area, unit)
                        total_text = _format_total_price_for_reply(round(display_slab_price * slabs_base, 2), resolved_market)
                        return f"{reply} Estimated base total: {slabs_base} slabs = {total_text}. {_area_slab_guidance(area, unit, language, from_history)}"
                    return (
                        f"{reply} Please share your project area in SF and preferred thickness (2cm or 3cm) so I can calculate the exact total. "
                        "Would you like me to send real slab photos for this code? Please share your WhatsApp/Zalo for quick support."
                    )

                reply = f"Dạ Anh/Chị, mã {product.get('code')} ({product.get('series')}) hiện có giá theo tấm từ {price_text}{note_tail}.{link_tail}"
                if area is not None and unit is not None:
                    _, _, slabs_base, _ = _estimate_slabs_from_area(area, unit)
                    total_text = _format_total_price_for_reply(round(display_slab_price * slabs_base, 2), resolved_market)
                    return f"{reply} Ước tính cơ bản: {slabs_base} tấm = {total_text}. {_area_slab_guidance(area, unit, language, from_history)}"
                unit_hint = "m² hoặc SF" if resolved_market == "VN" else "SF"
                return (
                    f"{reply} Anh/Chị cho em xin diện tích ({unit_hint}) và độ dày 2cm hay 3cm để em tính tổng chính xác ngay ạ. "
                    "Anh/Chị có muốn em gửi ảnh thực tế của mã này không? Anh/Chị cho em xin Zalo/WhatsApp nhé."
                )

    if area is not None and unit is not None:
        matches = tool_filter_products(market=resolved_market, thickness_cm=2, limit=2).get("matches") or []
        suggested_codes = [item.get("code") for item in matches if item.get("code")]
        code_hint = ", ".join(suggested_codes[:2]) if suggested_codes else "LQ 504, LQ 703"
        if language == "en":
            return (
                f"{_area_slab_guidance(area, unit, language, from_history)} "
                f"If you want, I can continue with representative codes {code_hint} and calculate the exact total for 2cm and 3cm."
            )
        return (
            f"{_area_slab_guidance(area, unit, language, from_history)} "
            f"Nếu mình muốn em đi tiếp ngay, em có thể chốt theo các mã đại diện {code_hint} rồi so sánh luôn 2cm và 3cm cho mình ạ."
        )

    if language == "en":
        return (
            "Please share product code or preferred color, area, and thickness (2cm/3cm) so I can prepare an accurate quote. "
            "Would you like me to send real slab photos as well?"
        )

    if resolved_market == "VN":
        return (
            "Dạ Anh/Chị vui lòng cho em mã đá hoặc màu mong muốn, diện tích theo m² hoặc SF và độ dày 2cm/3cm để em báo giá chuẩn VN ngay ạ. "
            "Anh/Chị có muốn em gửi ảnh thực tế mẫu phù hợp không?"
        )

    return (
        "Dạ Anh/Chị vui lòng cho em mã đá hoặc màu mong muốn, diện tích theo SF và độ dày 2cm/3cm để em báo giá chuẩn US ngay ạ. "
        "Anh/Chị có muốn em gửi ảnh thực tế mẫu phù hợp không?"
    )


@app.get("/api/health")
def health() -> Dict[str, Any]:
    default_market = _normalize_market(DEFAULT_MARKET)
    return {
        "ok": True,
        "service": MARKET_CONFIGS[default_market]["service_name"],
        "default_market": default_market,
        "enabled_markets": sorted(list(MARKET_CONFIGS.keys())),
        "catalog_count_by_market": {market: len(catalog.products) for market, catalog in CATALOGS.items()},
        "openai_enabled_by_market": {market: bool(ORCHESTRATORS.get(market)) for market in MARKET_CONFIGS.keys()},
    }


@app.post("/api/chat")
def chat(request: ChatRequest) -> Dict[str, Any]:
    market, language = _detect_context(request)
    market = _normalize_market(market)
    if market not in MARKET_CONFIGS:
        raise HTTPException(status_code=400, detail=f"Unsupported market: {market}")

    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    session_key = SessionStore.make_key(market, request.sessionId)
    active_orchestrator = _get_orchestrator_for_market(market)

    captured_phone = _extract_phone_contact(message)
    if captured_phone:
        reply = _phone_capture_reply(captured_phone, language)
        SESSION_STORE.append_turn(session_key, message, reply)
        structured = _build_structured([])
        structured["intent"] = "lead_capture"
        structured["needs_followup"] = False
        structured["requested_contact"] = False
        return {
            "reply": reply,
            "market": market,
            "language": language,
            "structured": structured,
        }

    history = SESSION_STORE.get_history(session_key)
    # FIX 1: Tính deterministic trước nhưng chỉ dùng làm fallback khi OpenAI trả rỗng
    deterministic_reply = _deterministic_code_quote_reply(message, language, market, history)
    if not deterministic_reply:
        deterministic_reply = _water_feng_shui_project_reply(message, language, market=market, history=history)

    context_prefix = (
        f"Context: market={market}, preferred_language={language}, "
        f"page_url={(request.page.url if request.page else '') or ''}, "
        f"page_title={(request.page.title if request.page else '') or ''}."
    )

    audit: List[Dict[str, Any]] = []
    from_ai = False

    if active_orchestrator is not None:
        # FIX 1: OpenAI luôn được gọi trước khi dùng deterministic
        def _semantic_handler(**kwargs: Any) -> Dict[str, Any]:
            payload = dict(kwargs or {})
            payload.setdefault("market", market)
            return tool_semantic_search_products(**payload)

        def _get_code_handler(**kwargs: Any) -> Dict[str, Any]:
            payload = dict(kwargs or {})
            payload.setdefault("market", market)
            return tool_get_product_by_code(**payload)

        def _filter_handler(**kwargs: Any) -> Dict[str, Any]:
            payload = dict(kwargs or {})
            payload.setdefault("market", market)
            payload.setdefault("message_hint", message)
            return tool_filter_products(**payload)

        def _quote_handler(**kwargs: Any) -> Dict[str, Any]:
            payload = dict(kwargs or {})
            payload.setdefault("market", market)
            return tool_calculate_quote(**payload)

        handlers = {
            "semantic_search_products": _semantic_handler,
            "get_product_by_code": _get_code_handler,
            "filter_products": _filter_handler,
            "calculate_quote": _quote_handler,
        }
        fresh_system_prompt = _load_prompt(market)
        out = active_orchestrator.chat_with_tools(
            user_message=f"{context_prefix}\n\nUser message: {message}",
            history=history,
            tools=TOOLS,
            handlers=handlers,
            system_prompt_override=fresh_system_prompt,
        )
        reply = (out.get("reply") or "").strip()
        audit = out.get("audit") or []
        from_ai = bool(reply)
        # Chỉ dùng deterministic/fallback khi OpenAI trả về rỗng
        if not reply:
            reply = deterministic_reply or _fallback_reply(message, language=language, market=market, history=history)
    elif deterministic_reply:
        reply = deterministic_reply
    else:
        reply = _fallback_reply(message, language=language, market=market, history=history)

    if not reply:
        reply = _fallback_reply(message, language=language, market=market, history=history)

    # FIX 3: Truyền from_ai để guard không override reply tốt của OpenAI
    reply = _ensure_non_product_guard(reply, message, language, market=market, history=history, from_ai=from_ai)
    reply = _ensure_technical_evidence(reply, message, language)
    reply = _ensure_consultative_response(reply, message, language)
    reply = _ensure_heat_uv_consultation(reply, message, language)
    reply = _ensure_quote_transparency(reply, audit, language)
    reply = _ensure_area_slab_guidance(reply, message, history, language, audit)
    reply = _ensure_language_consistency(reply, message, language, market=market)
    reply = _ensure_clickable_product_detail_link(reply, audit, language)
    reply = _sanitize_chat_style(reply, language)
    reply = _ensure_smart_followup(reply, message, language)
    reply = _sanitize_chat_style(reply, language)

    SESSION_STORE.append_turn(session_key, message, reply)
    structured = _build_structured(audit)

    return {
        "reply": reply,
        "market": market,
        "language": language,
        "structured": structured,
    }
