"""Builders for Bot API 10.3 rich messages (tables, quotes, in-text buttons).

Buttons are InputRichBlockButtons / RichMessageButton inside the message body,
NOT InlineKeyboardMarkup under the message.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TypeVar

T = TypeVar("T")

from aiogram.types import (
    InputRichBlockBlockQuotation,
    InputRichBlockButtons,
    InputRichBlockDetails,
    InputRichBlockDivider,
    InputRichBlockFooter,
    InputRichBlockList,
    InputRichBlockListItem,
    InputRichBlockParagraph,
    InputRichBlockPreformatted,
    InputRichBlockSectionHeading,
    InputRichBlockTable,
    InputRichMessage,
    RichBlockTableCell,
    RichMessageButton,
    RichTextCode,
    RichTextSpoiler,
)

RichBlock = (
    InputRichBlockSectionHeading
    | InputRichBlockParagraph
    | InputRichBlockPreformatted
    | InputRichBlockFooter
    | InputRichBlockDivider
    | InputRichBlockBlockQuotation
    | InputRichBlockTable
    | InputRichBlockButtons
    | InputRichBlockList
    | InputRichBlockDetails
)


def heading(text: str, size: int = 3) -> InputRichBlockSectionHeading:
    return InputRichBlockSectionHeading(text=text, size=size)


def code(text: str) -> RichTextCode:
    return RichTextCode(text=text)


def _coerce_rich_text(text: object) -> object:
    if isinstance(text, dict) and text.get("type") == "code":
        return RichTextCode(text=str(text.get("text", "")))
    if isinstance(text, list):
        return [_coerce_rich_text(part) for part in text]
    return text


def paragraph(text: object) -> InputRichBlockParagraph:
    return InputRichBlockParagraph(text=_coerce_rich_text(text))


def quote(text: str) -> InputRichBlockBlockQuotation:
    return InputRichBlockBlockQuotation(blocks=[InputRichBlockParagraph(text=text)])


def pre(text: str, language: str | None = None) -> InputRichBlockPreformatted:
    return InputRichBlockPreformatted(text=text, language=language)


def divider() -> InputRichBlockDivider:
    return InputRichBlockDivider()


def footer(text: str) -> InputRichBlockFooter:
    return InputRichBlockFooter(text=text)


def spoiler(text: str) -> RichTextSpoiler:
    return RichTextSpoiler(text=text)


def details(title: str, body: str) -> InputRichBlockDetails:
    return InputRichBlockDetails(
        summary=title,
        blocks=[InputRichBlockParagraph(text=body)],
    )


def bullet_list(items: Sequence[str]) -> InputRichBlockList:
    return InputRichBlockList(
        items=[
            InputRichBlockListItem(blocks=[InputRichBlockParagraph(text=item)])
            for item in items
        ]
    )


def table(
    headers: Sequence[str],
    rows: Sequence[Sequence[object]],
    *,
    caption: str | None = None,
) -> InputRichBlockTable:
    header_row = [
        RichBlockTableCell(text=str(h), is_header=True, align="left", valign="middle")
        for h in headers
    ]
    body = [
        [
            RichBlockTableCell(text=str(cell), align="left", valign="middle")
            for cell in row
        ]
        for row in rows
    ]
    return InputRichBlockTable(
        cells=[header_row, *body],
        is_bordered=True,
        is_striped=True,
        is_compact=True,
        caption=caption,
    )


def button_row(
    items: Sequence[tuple[str, str] | tuple[str, str, str]],
    *,
    style: str | None = None,
    align: str = "left",
) -> InputRichBlockButtons:
    """In-message buttons. Item is (label, callback_data) or (label, callback_data, style)."""
    buttons: list[RichMessageButton] = []
    for item in items[:8]:
        label, data, *rest = item
        payload = data.encode("utf-8")
        if len(payload) > 64:
            raise ValueError(f"callback_data exceeds 64 bytes: {data!r}")
        btn_style = rest[0] if rest else style
        if btn_style:
            buttons.append(RichMessageButton(text=label, callback_data=data, style=btn_style))
        else:
            buttons.append(RichMessageButton(text=label, callback_data=data))
    return InputRichBlockButtons(buttons=buttons, align=align)


def button_grid(
    items: Sequence[tuple[str, str] | tuple[str, str, str]],
    *,
    per_row: int = 4,
    style: str | None = None,
) -> list[InputRichBlockButtons]:
    return [button_row(chunk, style=style) for chunk in chunks(items, per_row)]


def link_row(items: Sequence[tuple[str, str]], *, align: str = "left") -> InputRichBlockButtons:
    buttons = [RichMessageButton(text=label, url=url) for label, url in items[:8]]
    return InputRichBlockButtons(buttons=buttons, align=align)


def copy_row(label: str, text: str) -> InputRichBlockButtons:
    from aiogram.types import CopyTextButton

    return InputRichBlockButtons(
        buttons=[RichMessageButton(text=label, copy_text=CopyTextButton(text=text))]
    )


def rich(*blocks: RichBlock | None) -> InputRichMessage:
    cleaned = [block for block in blocks if block is not None]
    return InputRichMessage(blocks=cleaned)


def chunks(items: Sequence[T], size: int) -> Iterable[Sequence[T]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]
