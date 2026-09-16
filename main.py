import os
import re
import hashlib
from collections import defaultdict
from datetime import date

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="Garden Calendar AI Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-flash"

if not DEEPSEEK_API_KEY:
    raise RuntimeError("DEEPSEEK_API_KEY environment variable is not set")


# ========== МОДЕЛИ ==========

class AskRequest(BaseModel):
    query: str
    context: Optional[str] = ""
    device_id: Optional[str] = "unknown"


class AskPhotoRequest(BaseModel):
    image_base64: str
    context: Optional[str] = ""
    device_id: Optional[str] = "unknown"


class AiResponse(BaseModel):
    text: str


# ========== ФИЛЬТР ТЕМАТИКИ ==========

STOP_WORDS = [
    "код", "python", "kotlin", "javascript", "функци", "скрипт", "программ",
    "алгоритм", "баг", "ошибк", "compile", "api", "sql", "regex",
    "погод", "курс", "доллар", "акци", "крипт", "bitcoin", "новост",
    "президент", "политик", "войн", "религи", "гороскоп",
    "анекдот", "стих", "песн", "фильм", "игр", "мем", "шутк",
    "сочинени", "реферат", "переведи", "перевод", "формул", "уравнени",
    "рецепт", "диет", "похудет", "медицин", "врач", "лекарств",
]

GARDEN_MARKERS = [
    "растен", "сад", "огород", "куст", "дерев", "цвет", "лист", "плод",
    "урожай", "почв", "удобр", "полив", "семен", "рассад", "теплиц",
    "болезн", "вредител", "тля", "паутинн", "гриб", "плесен", "гнил",
    "смородин", "малин", "яблон", "вишн", "груш", "томат", "огурц",
    "картоф", "морков", "лук", "чеснок", "клубник", "земляник",
    "роз", "пион", "тюльпан", "гортензи", "фикус", "орхиде",
    "обрезк", "прививк", "пикировк", "мульч", "компост", "севооборот",
    "подкормк", "опрыскив", "борьб", "обработк", "профилактик",
]


def is_gardening_query(query: str) -> tuple[bool, str]:
    q = query.lower().strip()
    if len(q) < 2:
        return False, "Запрос слишком короткий"
    if len(q) > 500:
        return False, "Запрос слишком длинный (макс. 500 символов)"

    has_garden = any(m in q for m in GARDEN_MARKERS)
    has_stop = any(w in q for w in STOP_WORDS)

    if has_stop and not has_garden:
        return False, "Приложение отвечает только на вопросы о садоводстве и растениях"

    return True, ""


# ========== ЛИМИТЫ (в памяти, сбрасываются при засыпании Render) ==========

daily_usage: dict = defaultdict(lambda: {"date": None, "count": 0})
FREE_DAILY_LIMIT = 30  # подними/опусти под себя


def check_daily_limit(device_id: str) -> None:
    today = date.today().isoformat()
    rec = daily_usage[device_id]
    if rec["date"] != today:
        rec["date"] = today
        rec["count"] = 0
    if rec["count"] >= FREE_DAILY_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"Дневной лимит исчерпан ({FREE_DAILY_LIMIT} запросов). Попробуйте завтра."
        )
    rec["count"] += 1


# ========== КЭШ (в памяти) ==========

server_cache: dict = {}


def cache_key(query: str, context: str) -> str:
    return hashlib.sha256(f"{context}|{query}".lower().encode()).hexdigest()


# ========== DEEPSEEK ==========

async def call_deepseek(messages: list, max_tokens: int = 800) -> str:
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "temperature": 0.7,
        "max_tokens": max_tokens,
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"DeepSeek error {resp.status_code}: {resp.text}",
        )
    data = resp.json()
    return data["choices"][0]["message"]["content"]


# ========== ЭНДПОИНТЫ ==========

@app.get("/")
def health():
    return {"status": "ok", "service": "garden-calendar-ai"}


@app.post("/api/ask", response_model=AiResponse)
async def ask(req: AskRequest):
    # 1. Дневной лимит
    check_daily_limit(req.device_id)

    # 2. Фильтр тематики
    allowed, reason = is_gardening_query(req.query)
    if not allowed:
        raise HTTPException(status_code=400, detail=reason)

    # 3. Кэш
    key = cache_key(req.query, req.context)
    if key in server_cache:
        return AiResponse(text=server_cache[key])

    # 4. Запрос к DeepSeek
    system_prompt = (
        "Ты — эксперт-садовод. Отвечай кратко, по делу, на русском языке. "
        "Если вопрос не о садоводстве, растениях, болезнях или вредителях — "
        "вежливо откажись и предложи задать вопрос по теме."
    )
    user_content = req.query
    if req.context:
        user_content = f"Контекст (растение/тема): {req.context}\n\nВопрос: {req.query}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    text = await call_deepseek(messages)

    # 5. Сохраняем в кэш
    if len(server_cache) > 1000:
        server_cache.pop(next(iter(server_cache)))
    server_cache[key] = text

    return AiResponse(text=text)


@app.post("/api/ask-photo", response_model=AiResponse)
async def ask_photo(req: AskPhotoRequest):
    check_daily_limit(req.device_id)

    system_prompt = (
        "Ты — эксперт-садовод. Проанализируй фото растения. "
        "Определи проблему (болезнь, вредитель, дефицит питания) и дай рекомендации. "
        "Отвечай на русском языке, кратко."
    )
    user_content = [
        {"type": "text", "text": f"Контекст: {req.context or 'определи проблему'}. Что на фото и что делать?"},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{req.image_base64}"}},
    ]
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    text = await call_deepseek(messages, max_tokens=800)
    return AiResponse(text=text)