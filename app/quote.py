from __future__ import annotations

import math
from typing import Any, Dict


SLAB_AREA_SF = 55.11
SQM_TO_SF = 10.764


def sqm_to_sf(area_sqm: float) -> float:
    return area_sqm * SQM_TO_SF


def calculate_quote(
    *,
    code: str,
    thickness_cm: int,
    area_sf: float,
    fob_slab_usd: float,
    buffer_ratio: float = 1.05,
    slab_area_sf: float = SLAB_AREA_SF,
) -> Dict[str, Any]:
    if area_sf <= 0:
        raise ValueError("area_sf must be greater than 0")
    if fob_slab_usd <= 0:
        raise ValueError("fob_slab_usd must be greater than 0")

    if slab_area_sf <= 0:
        raise ValueError("slab_area_sf must be greater than 0")

    raw_base_slabs = area_sf / slab_area_sf
    slabs_base = math.ceil(raw_base_slabs)
    total_base = round(slabs_base * fob_slab_usd, 2)

    buffered_area = area_sf * buffer_ratio
    raw_buffer_slabs = buffered_area / slab_area_sf
    slabs_buffer = math.ceil(raw_buffer_slabs)
    total_buffer = round(slabs_buffer * fob_slab_usd, 2)

    pattern_suggestion_slabs = None
    if 2.8 < raw_buffer_slabs < 3.0:
        pattern_suggestion_slabs = slabs_buffer + 1

    payload = {
        "code": code,
        "thickness_cm": thickness_cm,
        "area_sf": round(area_sf, 2),
        "slab_area_sf": slab_area_sf,
        "fob_slab_usd": round(fob_slab_usd, 2),
        "base_quote": {
            "slabs": slabs_base,
            "total_usd": total_base,
            "formula": f"ceil({area_sf:.2f} / {slab_area_sf:.2f}) * {fob_slab_usd:.2f}",
        },
        "buffer_quote": {
            "buffer_ratio": buffer_ratio,
            "buffered_area_sf": round(buffered_area, 2),
            "slabs": slabs_buffer,
            "total_usd": total_buffer,
            "formula": f"ceil(({area_sf:.2f} * {buffer_ratio:.2f}) / {slab_area_sf:.2f}) * {fob_slab_usd:.2f}",
            "raw_slabs": round(raw_buffer_slabs, 2),
        },
    }
    if pattern_suggestion_slabs is not None:
        payload["pattern_suggestion"] = {
            "slabs": pattern_suggestion_slabs,
            "reason": "buffered raw slab count is above 2.8, so one extra slab may help vein matching",
        }
    return payload
