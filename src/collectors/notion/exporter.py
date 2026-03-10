"""Notion 블록을 Markdown으로 변환한다."""

from pathlib import Path
from urllib.parse import urlparse


def rich_text_to_md(rich_texts: list[dict]) -> str:
    """Notion rich_text 배열을 Markdown 문자열로 변환한다."""
    parts = []
    for rt in rich_texts:
        text = rt.get("plain_text", "")
        annotations = rt.get("annotations", {})

        if annotations.get("code"):
            text = f"`{text}`"
        if annotations.get("bold"):
            text = f"**{text}**"
        if annotations.get("italic"):
            text = f"*{text}*"
        if annotations.get("strikethrough"):
            text = f"~~{text}~~"

        href = rt.get("href")
        if href:
            text = f"[{text}]({href})"

        parts.append(text)
    return "".join(parts)


def block_to_md(block: dict, indent: int = 0) -> str:
    """단일 Notion 블록을 Markdown 문자열로 변환한다."""
    block_type = block.get("type", "")
    data = block.get(block_type, {})
    prefix = "  " * indent
    lines = []

    if block_type == "paragraph":
        lines.append(f"{prefix}{rich_text_to_md(data.get('rich_text', []))}")

    elif block_type.startswith("heading_"):
        level = int(block_type[-1])
        lines.append(f"{'#' * level} {rich_text_to_md(data.get('rich_text', []))}")

    elif block_type == "bulleted_list_item":
        lines.append(f"{prefix}- {rich_text_to_md(data.get('rich_text', []))}")

    elif block_type == "numbered_list_item":
        lines.append(f"{prefix}1. {rich_text_to_md(data.get('rich_text', []))}")

    elif block_type == "to_do":
        checked = "x" if data.get("checked") else " "
        lines.append(f"{prefix}- [{checked}] {rich_text_to_md(data.get('rich_text', []))}")

    elif block_type == "toggle":
        lines.append(f"{prefix}<details>")
        lines.append(f"{prefix}<summary>{rich_text_to_md(data.get('rich_text', []))}</summary>")

    elif block_type == "code":
        lang = data.get("language", "")
        lines.append(f"{prefix}```{lang}")
        lines.append(f"{prefix}{rich_text_to_md(data.get('rich_text', []))}")
        lines.append(f"{prefix}```")

    elif block_type == "quote":
        lines.append(f"{prefix}> {rich_text_to_md(data.get('rich_text', []))}")

    elif block_type == "callout":
        icon = (data.get("icon") or {}).get("emoji", "")
        lines.append(f"{prefix}> {icon} {rich_text_to_md(data.get('rich_text', []))}")

    elif block_type == "divider":
        lines.append(f"{prefix}---")

    elif block_type == "table":
        pass  # 테이블 행은 children에서 처리

    elif block_type == "table_row":
        cells = data.get("cells", [])
        row = " | ".join(rich_text_to_md(cell) for cell in cells)
        lines.append(f"{prefix}| {row} |")

    elif block_type == "image":
        image_data = data.get("file", data.get("external", {}))
        url = image_data.get("url", "")
        caption = rich_text_to_md(data.get("caption", []))
        lines.append(f"{prefix}![{caption}]({url})")
        extracted = block.get("_extracted_text", "")
        if extracted:
            img_name = caption or Path(urlparse(url).path).name or "image"
            lines.append(f"<!-- @source_type:file:{img_name} -->")
            lines.append("")
            for eline in extracted.split("\n"):
                lines.append(f"{prefix}> {eline}")
            lines.append("")
            lines.append("<!-- @source_type:document -->")

    elif block_type in ("file", "pdf"):
        file_data = data.get("file", data.get("external", {}))
        url = file_data.get("url", "")
        caption = rich_text_to_md(data.get("caption", []))
        url_name = Path(urlparse(url).path).name if url else ""
        name = caption or url_name or ("PDF 문서" if block_type == "pdf" else "첨부파일")
        icon = "📄" if block_type == "pdf" else "📎"
        lines.append(f"{prefix}{icon} [{name}]({url})")
        extracted = block.get("_extracted_text", "")
        if extracted:
            lines.append(f"<!-- @source_type:file:{name} -->")
            lines.append("")
            lines.append(extracted)
            lines.append("")
            lines.append("<!-- @source_type:document -->")

    elif block_type == "bookmark":
        url = data.get("url", "")
        caption = rich_text_to_md(data.get("caption", []))
        lines.append(f"{prefix}[{caption or url}]({url})")

    elif block_type == "child_page":
        title = data.get("title", "")
        lines.append(f"{prefix}## {title}")

    elif block_type == "child_database":
        title = data.get("title", "")
        lines.append(f"{prefix}## [DB] {title}")

    # 하위 블록 재귀 처리
    children = block.get("children", [])
    if children:
        for child in children:
            lines.append(block_to_md(child, indent + 1))
        if block_type == "toggle":
            lines.append(f"{prefix}</details>")

    return "\n".join(lines)


def blocks_to_markdown(blocks: list[dict]) -> str:
    """블록 목록을 하나의 Markdown 문서로 변환한다."""
    parts = []
    prev_type = None

    for block in blocks:
        block_type = block.get("type", "")
        # 테이블 헤더 구분선 삽입
        if block_type == "table_row" and prev_type == "table_row" and len(parts) == 1:
            cells = block.get("table_row", {}).get("cells", [])
            separator = "| " + " | ".join("---" for _ in cells) + " |"
            parts.insert(1, separator)

        parts.append(block_to_md(block))
        prev_type = block_type

    return "\n\n".join(parts)


def comments_to_markdown(comments: list[dict]) -> str:
    """Notion 댓글 목록을 Markdown으로 변환한다."""
    if not comments:
        return ""

    lines = ["<!-- @source_type:comment -->", "## Comments", ""]
    for comment in comments:
        created = comment.get("created_time", "")[:10]
        author = comment.get("created_by", {}).get("id", "unknown")
        body = rich_text_to_md(comment.get("rich_text", []))
        lines.append(f"- **{created}** (user:{author[:8]}): {body}")

    return "\n".join(lines)


def get_page_title(page: dict) -> str:
    """페이지 객체에서 제목을 추출한다."""
    props = page.get("properties", {})

    # title 타입 속성 찾기
    for prop in props.values():
        if prop.get("type") == "title":
            title_parts = prop.get("title", [])
            return rich_text_to_md(title_parts) if title_parts else "Untitled"

    return "Untitled"
