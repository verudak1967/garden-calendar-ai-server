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
    # Программирование
    "код", "python", "kotlin", "javascript", "скрипт", "программ",
    "алгоритм", "баг", "ошибк", "compile", "sql", "regex", "html", "css",
    # Общие знания и новости
    "погод", "курс", "доллар", "акци", "крипт", "bitcoin", "новост",
    "президент", "политик", "войн", "религи", "гороскоп", "биограф",
    # Развлечения
    "анекдот", "стих", "песн", "фильм", "игр", "мем", "шутк", "прикол",
    # Учёба / общие задачи
    "сочинени", "реферат", "переведи", "перевод", "формул", "уравнени",
    "доклад", "эссе", "тест по",
    # Личное / медицина
    "рецепт", "диет", "похудет", "медицин", "врач", "лекарств", "таблет",
    "симптом", "болезн человека",
    # Финансы / бизнес
    "налог", "ипотек", "кредит", "инвест", "бизнес-план",
]

GARDEN_MARKERS = [
    "растен", "сад", "огород", "куст", "дерев", "цвет", "лист", "плод",
    "урожай", "почв", "удобр", "полив", "семен", "рассад", "теплиц",
    "болезн растен", "вредител", "тля", "паутинн", "гриб", "плесен", "гнил",
    "смородин", "малин", "яблон", "вишн", "груш", "томат", "огурц",
    "картоф", "морков", "лук", "чеснок", "клубник", "земляник", "кабач",
    "перец", "баклажан", "редис", "свекл", "капуст", "тыкв",
    "роз", "пион", "тюльпан", "гортензи", "фикус", "орхиде", "лили",
    "обрезк", "прививк", "пикировк", "мульч", "компост", "севооборот",
    "подкормк", "опрыскив", "обработк", "профилактик", "рыхлени",
    "грядк", "парник", "орошени", "дренаж", "черенк", "сажен",
    "цветени", "плодоношени", "вегетаци", "фитофтор", "мучнист",
]


def rule_filter(query: str) -> tuple[str, str]:
    """
    Возвращает (verdict, reason):
    - "allow"   — точно по теме, пропускаем без LLM
    - "deny"    — точно не по теме, отклоняем сразу
    - "check"   — пограничный, нужен LLM-классификатор
    """
    q = query.lower().strip()

    if len(q) < 3:
        return "deny", "Запрос слишком короткий"
    if len(q) > 500:
        return "deny", "Запрос слишком длинный (макс. 500 символов)"

    has_garden = any(m in q for m in GARDEN_MARKERS)
    has_stop = any(w in q for w in STOP_WORDS)

    # Стоп-слово без садовых маркеров — точно мусор
    if has_stop and not has_garden:
        return "deny", "Приложение отвечает только на вопросы о садоводстве и растениях"

    # Явные садовые слова без стоп-слов — точно по теме
    if has_garden and not has_stop:
        return "allow", ""

    # Смешанный запрос (и сад, и стоп) — подозрительно, отправляем на LLM
    if has_stop and has_garden:
        return "check", ""

    # Ничего не нашли — короткий запрос пропускаем, длинный проверяем
    if not has_garden and not has_stop:
        if len(q) < 15:
            return "check", ""
        return "check", ""

    return "check", ""


# ========== LLM-КЛАССИФИКАТОР ==========

async def classify_topic(query: str) -> bool:
    """
    Отправляет запрос на проверку темы в DeepSeek.
    Возвращает True, если запрос ЧИСТО садовый.
    """
    prompt = (
        "Определи, относится ли запрос к теме садоводства, огородничества, "
        "комнатных растений, болезней растений или вредителей.\n"
        "Ответь ОДНИМ словом: YES или NO.\n"
        "Если запрос смешанный (например, «напиши код для полива на Python») — ответь NO.\n"
        "Если запрос — вопрос не по теме (погода, программирование, стихи, "
        "медицина, новости и т.д.) — ответь NO.\n\n"
        f"Запрос: {query}"
    )
    messages = [{"role": "user", "content": prompt}]
    try:
        result = await call_deepseek(messages, max_tokens=5)
        return "YES" in result.strip().upper()
    except Exception as e:
        print(f"Classifier error: {e}")
        # При сбое классификатора — не блокируем пользователя
        return True


# ========== ЛИМИТЫ ==========

daily_usage: dict = defaultdict(lambda: {"date": None, "count": 0})
FREE_DAILY_LIMIT = 30


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


# ========== КЭШ ==========

server_cache: dict = {}


def cache_key(query: str, context: str) -> str:
    return hashlib.sha256(f"{context}|{query}".lower().encode()).hexdigest()


# ========== DEEPSEEK ==========

async def call_deepseek(messages: list, max_tokens: int = 2500) -> str:
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
    finish_reason = data["choices"][0].get("finish_reason", "unknown")
    if finish_reason == "length":
        print(f"WARNING: response truncated by max_tokens")
    return data["choices"][0]["message"]["content"]


# ========== ЭНДПОИНТЫ ==========

@app.get("/")
def health():
    return {"status": "ok", "service": "garden-calendar-ai"}


@app.post("/api/ask", response_model=AiResponse)
async def ask(req: AskRequest):
    # 1. Дневной лимит
    check_daily_limit(req.device_id)

    # 2. Rule-based фильтр
    verdict, reason = rule_filter(req.query)
    if verdict == "deny":
        raise HTTPException(status_code=400, detail=reason)

    # 3. LLM-классификатор для пограничных случаев
    if verdict == "check":
        if not await classify_topic(req.query):
            raise HTTPException(
                status_code=400,
                detail="Приложение отвечает только на вопросы о садоводстве и растениях"
            )

    # 4. Кэш
    key = cache_key(req.query, req.context)
    if key in server_cache:
        return AiResponse(text=server_cache[key])

    # 5. Основной запрос
    system_prompt = (
        "Ты — эксперт-садовод. Отвечай кратко, по делу, на русском языке. "
        "Тема: садоводство, огородничество, комнатные растения, болезни растений, "
        "вредители, удобрения, обрезка, полив, урожай.\n"
        "ВАЖНО: если вопрос хотя бы частично не по теме (программирование, "
        "стихи, рецепты, медицина, погода, новости и т.д.) — вежливо откажись "
        "и предложи задать вопрос по садоводческой теме. "
        "Не отвечай на нецелевые части смешанного запроса."
    )
    user_content = req.query
    if req.context:
        user_content = f"Контекст (растение/тема): {req.context}\n\nВопрос: {req.query}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    text = await call_deepseek(messages, max_tokens=2500)

    # 6. Сохраняем в кэш
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