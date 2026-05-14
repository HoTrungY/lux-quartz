from __future__ import annotations

import csv
import html
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

ROOT = Path(r"c:/My Web Sites/clonelandingpage")
CSV_PATH = ROOT / "Lux Quartz My.csv"
OUTPUT_PATH = ROOT / "Lux Quartz My.csv"
EN_PRODUCT_DIR = ROOT / "luxquartzvietnam.com" / "en" / "product"
VN_PRODUCT_DIR = ROOT / "luxquartzvietnam.com" / "san-pham"

NEW_COLUMNS = [
    "Color_Group",
    "Pattern",
    "Application",
    "Finish",
    "Photo_URL",
    "In_Stock",
    "Weight_per_Slab",
    "Product_Description",
    "Description_Clean_Source",
]

DEFAULT_APPLICATION = "Kitchen Countertop, Bathroom Vanity, Wall Cladding"
DEFAULT_FINISH = "Polished"
DEFAULT_IN_STOCK = "Yes"

CODE_RE = re.compile(r"\b((?:LQ|LC)\s*-?\s*\d{3,4})\b", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
CTA_PATTERNS = [
    re.compile(r"\bcall us\b[^.?!]*[.?!]?", re.IGNORECASE),
    re.compile(r"\bcontact us\b[^.?!]*[.?!]?", re.IGNORECASE),
    re.compile(r"\bcall us at\b[^.?!]*[.?!]?", re.IGNORECASE),
    re.compile(r"\bfor pricing details\b[^.?!]*[.?!]?", re.IGNORECASE),
    re.compile(r"\bfor more information\b[^.?!]*[.?!]?", re.IGNORECASE),
    re.compile(r"\bavailable now\b[^.?!]*[.?!]?", re.IGNORECASE),
]


def normalize_code(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]", "", (value or "").upper())
    return cleaned


def normalize_text(value: str) -> str:
    value = html.unescape(value or "")
    value = TAG_RE.sub(" ", value)
    value = SPACE_RE.sub(" ", value).strip()
    return value


def csv_safe(value: str) -> str:
    return (value or "").replace("\r", " ").replace("\n", " ").strip()


def infer_color_group(color: str) -> str:
    c = (color or "").strip().lower()
    if "black" in c:
        return "Black"
    if "grey" in c or "gray" in c or "silver" in c:
        return "Grey"
    if "white" in c:
        return "White"
    return "Cream"


def infer_pattern(series: str, color: str, description: str) -> str:
    text = f"{series} {color} {description}".lower()

    if "mirror" in text or "sparkling" in text:
        return "Sparkling"
    if "big grain" in text:
        return "Big Grain"
    if "small grain" in text or "fine grain" in text:
        return "Fine Grain"
    if "medium grain" in text:
        return "Medium Grain"
    if "wavy" in text:
        return "Wavy Vein"
    if "vein" in text:
        if any(k in text for k in ["calacatta", "carrara", "gold", "blue", "grey"]):
            return "Big Vein"
        return "Small Vein"
    if "pure" in text or "ultra white" in text or "solid" in text:
        return "Solid"
    if "calacatta" in text or "carrara" in text:
        return "Big Vein"
    return "Solid"


def extract_first(patterns: Iterable[str], text: str) -> str:
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE | re.DOTALL):
            value = normalize_text(match.group(1))
            if value:
                return value
    return ""


def extract_code_candidates(text: str) -> List[str]:
    return [normalize_code(m) for m in CODE_RE.findall(text)]


def to_absolute_url(raw_url: str) -> str:
    cleaned = (raw_url or "").strip()
    if not cleaned:
        return ""
    if cleaned.startswith("//"):
        return f"https:{cleaned}"
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        return cleaned
    cleaned = cleaned.lstrip("./")
    return f"https://luxquartzvietnam.com/{cleaned}"


def extract_page_info(page_path: Path) -> Tuple[str, str, str]:
    text = page_path.read_text(encoding="utf-8", errors="ignore")

    description = extract_first(
        [
            r'<meta\s+name="description"\s+content="([^"]*)"',
            r"<meta\s+name='description'\s+content='([^']*)'",
            r'<meta\s+property="og:description"\s+content="([^"]*)"',
            r'<meta\s+name="twitter:description"\s+content="([^"]*)"',
        ],
        text,
    )

    photo = extract_first(
        [
            r'<meta\s+property="og:image"\s+content="([^"]*)"',
            r"<meta\s+property='og:image'\s+content='([^']*)'",
            r'<meta\s+name="twitter:image"\s+content="([^"]*)"',
            r"<meta\s+name='twitter:image'\s+content='([^']*)'",
        ],
        text,
    )

    title = extract_first([r"<title>(.*?)</title>"], text)

    return description, to_absolute_url(photo), title


def collect_page_index() -> Dict[str, Dict[str, str]]:
    by_code: Dict[str, Dict[str, str]] = {}

    def set_if_missing(path: Path, priority: str) -> None:
        description, photo_url, title = extract_page_info(path)
        source_text = f"{title} {path.as_posix()}"
        codes = set(extract_code_candidates(source_text))
        if not codes:
            return

        payload = {
            "description": description,
            "photo_url": photo_url,
            "source": path.as_posix(),
            "priority": priority,
        }

        for code in codes:
            existing = by_code.get(code)
            if existing:
                # always keep EN source when already available
                if existing.get("priority") == "EN":
                    continue
                # VN fallback only replaces when the current entry lacks description
                if priority == "VN":
                    if existing.get("description"):
                        continue
                    if not description and existing.get("photo_url"):
                        continue
            by_code[code] = payload

    # pass 1: index EN pages first
    for page in sorted(EN_PRODUCT_DIR.glob("**/index.html")):
        set_if_missing(page, "EN")

    # pass 2: VN fallback for codes without usable EN data
    for page in sorted(VN_PRODUCT_DIR.glob("**/index.html")):
        set_if_missing(page, "VN")

    return by_code


def clean_description(description: str) -> str:
    text = normalize_text(description)
    if not text:
        return ""

    for pattern in CTA_PATTERNS:
        text = pattern.sub(" ", text)

    text = PHONE_RE.sub(" ", text)
    text = SPACE_RE.sub(" ", text).strip(" .,-;:")

    if text and text[-1] not in ".!?":
        text += "."

    return text


def build_description(row: Dict[str, str], page_description: str, color_group: str, pattern: str) -> Tuple[str, str]:
    cleaned_page_desc = clean_description(page_description)
    if cleaned_page_desc:
        return cleaned_page_desc, "PAGE"

    code = (row.get("Code") or "").strip()
    series = (row.get("Series") or "").strip() or "Lux Quartz"
    color = (row.get("color") or "").strip() or color_group
    fob_slab = (row.get('FOB/ slab (160 x 320cm/ 63 x 126")') or "").strip()

    price_part = f" priced at {fob_slab} per slab" if fob_slab else ""
    fallback = (
        f"Product {code} is a {color_group} {pattern.lower()} quartz in the {series} series, "
        f"with {color} tone{price_part}, suitable for kitchen and vanity applications."
    )
    return fallback, "FALLBACK"


def read_rows() -> Tuple[List[str], List[Dict[str, str]]]:
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    return fieldnames, rows


def write_rows(fieldnames: List[str], rows: List[Dict[str, str]]) -> None:
    with OUTPUT_PATH.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: csv_safe(v) for k, v in row.items()})


def main() -> None:
    fieldnames, rows = read_rows()

    # bỏ cột rỗng cuối file nếu có
    fieldnames = [h for h in fieldnames if (h or "").strip()]

    for col in NEW_COLUMNS:
        if col not in fieldnames:
            fieldnames.append(col)

    page_index = collect_page_index()

    page_derived_codes = set()
    fallback_codes = set()

    for row in rows:
        code = (row.get("Code") or "").strip()
        code_norm = normalize_code(code)

        page_info = page_index.get(code_norm, {})
        page_description = (page_info.get("description") or "").strip()
        page_photo = (page_info.get("photo_url") or "").strip()
        source_priority = (page_info.get("priority") or "").upper()

        color_group = infer_color_group(row.get("color", ""))
        pattern = infer_pattern(row.get("Series", ""), row.get("color", ""), page_description)

        row.pop("", None)

        row["Color_Group"] = color_group
        row["Pattern"] = pattern
        row["Application"] = row.get("Application") or DEFAULT_APPLICATION
        row["Finish"] = row.get("Finish") or DEFAULT_FINISH
        row["Photo_URL"] = page_photo
        row["In_Stock"] = row.get("In_Stock") or DEFAULT_IN_STOCK
        row["Weight_per_Slab"] = row.get("Weight_per_Slab") or ""

        description, desc_source = build_description(row, page_description, color_group, pattern)
        row["Product_Description"] = description

        if desc_source == "PAGE":
            row["Description_Clean_Source"] = source_priority if source_priority in {"EN", "VN"} else "PAGE"
            page_derived_codes.add(code_norm)
        else:
            row["Description_Clean_Source"] = "FALLBACK"
            fallback_codes.add(code_norm)

    write_rows(fieldnames, rows)

    unique_codes = {normalize_code(r.get("Code", "")) for r in rows}
    page_derived_codes = page_derived_codes & unique_codes
    fallback_codes = fallback_codes & unique_codes

    print(f"Updated CSV: {OUTPUT_PATH}")
    print(f"Rows: {len(rows)}")
    print(f"Unique codes: {len(unique_codes)}")
    print(f"Codes with page-derived description: {len(page_derived_codes)}")
    print(f"Codes with fallback description: {len(fallback_codes)}")
    if fallback_codes:
        preview = sorted(fallback_codes)[:20]
        print("Fallback description codes (first 20):", ", ".join(preview))


if __name__ == "__main__":
    main()
