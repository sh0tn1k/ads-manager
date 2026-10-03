from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from ads_api.client import AdsClient
from ads_api.errors import AdsApiError, AdsTransportError
from ads_api.models import UploadFile
from bot.handlers.menu import show_error
from bot.rich import copy_row, heading, paragraph, quote
from bot.states import UploadMediaSG
from bot.ui import Event, ack, reply_rich, selected_account_id

router = Router(name="media")


@router.callback_query(F.data == "md:photo")
async def upload_photo_start(event: Event, state: FSMContext) -> None:
    await ack(event)
    await state.set_state(UploadMediaSG.file)
    await state.update_data(media_kind="photo")
    await reply_rich(event, heading("Фото объявления"), paragraph("Пришлите изображение."), wizard=True)


@router.callback_query(F.data == "md:video")
async def upload_video_start(event: Event, state: FSMContext) -> None:
    await ack(event)
    await state.set_state(UploadMediaSG.file)
    await state.update_data(media_kind="video")
    await reply_rich(event, heading("Видео объявления"), paragraph("Пришлите mp4."), wizard=True)


@router.callback_query(F.data == "md:site")
async def upload_site_start(event: Event, state: FSMContext) -> None:
    await ack(event)
    await state.set_state(UploadMediaSG.file)
    await state.update_data(media_kind="website")
    await reply_rich(event, heading("Фото сайта"), paragraph("Пришлите изображение."), wizard=True)


@router.message(UploadMediaSG.file, F.photo | F.video | F.document)
async def upload_file(message: Message, state: FSMContext, ads: AdsClient) -> None:
    kind = (await state.get_data()).get("media_kind")
    file_id = None
    filename = "upload.bin"
    if message.photo:
        file_id = message.photo[-1].file_id
        filename = "ad.jpg"
    elif message.video:
        file_id = message.video.file_id
        filename = message.video.file_name or "ad.mp4"
    elif message.document:
        file_id = message.document.file_id
        filename = message.document.file_name or "file.bin"
    if not file_id:
        await reply_rich(message, quote("Нужен файл."), wizard=True)
        return
    tg_file = await message.bot.get_file(file_id)
    buf = await message.bot.download(tg_file)
    raw = buf.read() if buf else b""
    upload = UploadFile(raw, filename=filename)
    account_id = await selected_account_id(state, ads)
    try:
        if kind == "video":
            result = await ads.upload_ad_video(upload, account_id=account_id)
            ident, url = result.video_id, result.video_url
        elif kind == "website":
            result = await ads.upload_website_photo(upload, account_id=account_id)
            ident, url = result.photo_id, result.photo_url
        else:
            result = await ads.upload_ad_photo(upload, account_id=account_id)
            ident, url = result.photo_id, result.photo_url
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(message, exc)
        return
    await state.set_state(None)
    await reply_rich(
        message,
        heading("Загружено"),
        paragraph(["ID: ", {"type": "code", "text": ident}]),
        paragraph(url),
        copy_row("Скопировать ID", ident),
        back="media",
    )
