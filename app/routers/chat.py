from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["chat"])
templates = Jinja2Templates(directory="templates")


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def root_chat(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="chat.html",
        context={},
    )


@router.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="chat.html",
        context={},
    )
