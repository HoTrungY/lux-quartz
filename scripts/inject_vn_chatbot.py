"""
inject_vn_chatbot.py
Thêm chatbot VN vào tất cả các trang tiếng Việt (không nằm trong /en/).
"""

import os
import re
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "luxquartzvietnam.com"
API_BASE = "http://localhost:8787"
VERSION = "20260514-vn-1"

# Snippet chatbot cần inject - dùng đường dẫn tương đối "../lib/"
# (trang chủ dùng "lib/" còn sub-pages dùng "../lib/")

CSS_TAG_SUBPAGE = f'<link rel="stylesheet" href="../lib/lux-chatbot-us.css?v={VERSION}" />'
JS_CONFIG_SUBPAGE = f"""<script>
      window.LUX_CHATBOT_VN_CONFIG = {{
        apiBase: "{API_BASE}",
        market: "VN"
      }};
    </script>"""
JS_TAG_SUBPAGE = f'<script defer src="../lib/lux-chatbot-vn.js?v={VERSION}"></script>'

# Đường dẫn trang chủ (index.html gốc) dùng "lib/" không có "../"
CSS_TAG_ROOT = f'<link rel="stylesheet" href="lib/lux-chatbot-us.css?v={VERSION}" />'
JS_CONFIG_ROOT = f"""<script>
      window.LUX_CHATBOT_VN_CONFIG = {{
        apiBase: "{API_BASE}",
        market: "VN"
      }};
    </script>"""
JS_TAG_ROOT = f'<script defer src="lib/lux-chatbot-vn.js?v={VERSION}"></script>'

# Marker để detect đã inject rồi (tránh inject 2 lần)
ALREADY_INJECTED_MARKER = "lux-chatbot-vn.js"

# Các thư mục cần bỏ qua (tiếng Anh hoặc không phải trang nội dung)
SKIP_DIRS = {
    "en",
    "lib",
    "wp-content",
    "wp-includes",
    "wp-json",
    "schema.org",
    ".git",
    "node_modules",
    "template",
    "signals",
}

# Regex tìm thẻ <head> để inject phía sau
HEAD_OPEN_PATTERN = re.compile(r"(<head[^>]*>)", re.IGNORECASE)


def inject_into_html(html: str, is_root: bool) -> tuple[str, bool]:
    """
    Inject chatbot CSS/JS vào <head>.
    Trả về (html_mới, đã_thay_đổi).
    """
    # Kiểm tra đã có chưa
    if ALREADY_INJECTED_MARKER in html:
        return html, False

    css_tag = CSS_TAG_ROOT if is_root else CSS_TAG_SUBPAGE
    js_config = JS_CONFIG_ROOT if is_root else JS_CONFIG_SUBPAGE
    js_tag = JS_TAG_ROOT if is_root else JS_TAG_SUBPAGE

    inject_block = f"\n    {css_tag}\n    {js_config}\n    {js_tag}"

    # Tìm </head> hoặc <head ...> để insert phía trước </head>
    head_close = re.search(r"(</head>)", html, re.IGNORECASE)
    if head_close:
        pos = head_close.start()
        new_html = html[:pos] + inject_block + "\n  " + html[pos:]
        return new_html, True

    # Fallback: inject sau thẻ <head>
    match = HEAD_OPEN_PATTERN.search(html)
    if match:
        pos = match.end()
        new_html = html[:pos] + inject_block + html[pos:]
        return new_html, True

    return html, False


def process_directory(base_dir: Path):
    injected = 0
    skipped_already = 0
    skipped_no_head = 0
    errors = []

    # Xử lý trang chủ index.html riêng
    root_index = base_dir / "index.html"
    if root_index.exists():
        html = root_index.read_text(encoding="utf-8", errors="replace")
        new_html, changed = inject_into_html(html, is_root=True)
        if changed:
            root_index.write_text(new_html, encoding="utf-8")
            print(f"  [OK] [ROOT] {root_index.relative_to(base_dir)}")
            injected += 1
        else:
            print(f"  [SKIP] [ROOT] Already injected: index.html")
            skipped_already += 1

    # Duyệt các thư mục con
    for item in sorted(base_dir.iterdir()):
        if not item.is_dir():
            continue
        dir_name = item.name

        # Bỏ qua các thư mục tiếng Anh và hệ thống
        if dir_name in SKIP_DIRS:
            print(f"  [SKIP] Skipping dir: {dir_name}/")
            continue

        # Tìm index.html trong mỗi thư mục
        target = item / "index.html"
        if not target.exists():
            continue

        try:
            html = target.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            errors.append(str(target.relative_to(base_dir)))
            print(f"  [ERR] Read error: {target.relative_to(base_dir)} - {e}")
            continue

        # Kiểm tra xem có phải trang VN không (có thể nhận biết qua lang="vi")
        # Bỏ qua nếu không có <head>
        if "<head" not in html.lower():
            skipped_no_head += 1
            continue

        new_html, changed = inject_into_html(html, is_root=False)
        if changed:
            try:
                target.write_text(new_html, encoding="utf-8")
                print(f"  [OK] {target.relative_to(base_dir)}")
                injected += 1
            except Exception as e:
                errors.append(str(target.relative_to(base_dir)))
                print(f"  [ERR] Write error: {target.relative_to(base_dir)} - {e}")
        else:
            skipped_already += 1

    print(f"\n{'='*55}")
    print(f"[OK]   Injected:      {injected} files")
    print(f"[SKIP] Already done:  {skipped_already} files")
    print(f"[WARN] No <head> tag: {skipped_no_head} files")
    if errors:
        print(f"[ERR]  Errors:        {len(errors)} files")
        for e in errors:
            print(f"   - {e}")
    print(f"{'='*55}")


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"[INFO] Base dir: {BASE}")
    print(f"[START] Injecting VN chatbot (v{VERSION}) into Vietnamese pages...\n")
    process_directory(BASE)
