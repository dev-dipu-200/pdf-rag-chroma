from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.auth import get_optional_user, require_admin

router = APIRouter(tags=["chat"])
templates = Jinja2Templates(directory="templates")


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root_chat(request: Request, current_user=Depends(get_optional_user)):
    return templates.TemplateResponse(
        request=request,
        name="chat.html",
        context={"current_user": current_user},
    )


@router.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request, current_user=Depends(get_optional_user)):
    return templates.TemplateResponse(
        request=request,
        name="chat.html",
        context={"current_user": current_user},
    )


@router.get("/documents-page", response_class=HTMLResponse)
async def documents_page(request: Request, current_user=Depends(get_optional_user)):
    return templates.TemplateResponse(
        request=request,
        name="documents.html",
        context={"current_user": current_user},
    )
