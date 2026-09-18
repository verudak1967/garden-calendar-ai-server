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

# === Провайдер для текста (DeepSeek) ===
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-flash"

# === Провайдер для vision (OpenRouter) ===
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Список vision-моделей для автоматического перебора.
# Если первая вернёт 404/402 — пробуем следующую.
OPENROUTER_VISION_MODELS = [
    "inclusionai/ling-3.0-flash-vl:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "nex-agi/nex-n2.5-pro:free",
    "nex-agi/nex-n2.5-mini:free",
    "dots-studio/dots-3-note-preview:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "thinkingmachines/inkling-small:free",
    "qwen/qwen2.5-vl-32b-instruct:free",
    "openrouter/free",
]

if not DEEPSEEK_API_KEY:
    raise RuntimeError("DEEPSEEK_API_KEY environment variable is not set")
if not OPENROUTER_API_KEY:
    raise RuntimeError("OPENROUTER_API_KEY environment variable is not set")


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


# ========== СТОП-СЛОВА ==========

STOP_WORDS = [
    "код", "python", "kotlin", "javascript", "скрипт", "программ",
    "алгоритм", "баг", "ошибк", "compile", "sql", "regex", "html", "css",
    "погод", "курс", "доллар", "акци", "крипт", "bitcoin", "новост",
    "президент", "политик", "войн", "религи", "гороскоп", "биограф",
    "анекдот", "стих", "песн", "фильм", "игр", "мем", "шутк", "прикол",
    "сочинени", "реферат", "переведи", "перевод", "формул", "уравнени",
    "доклад", "эссе",
    "рецепт", "диет", "похудет", "медицин", "врач", "таблет",
    "симптом человек",
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
    "микроэлемент", "купорос", "доломит", "известков", "хелат", "гумат",
    "нитроаммофос", "суперфосфат", "калимагнези", "монофосфат",
    "аммиачн", "селитр", "карбамид", "зольн", "перегной", "биогумус",
    "вермикулит", "перлит", "торф", "сапропел", "сидерат",
    "борн", "азофоск", "нитрофоск", "флоровит", "агрикол", "фертик",
    "эпин", "циркон", "корневин", "гетероауксин", "фитоспорин",
    "триходермин", "боверия", "метаризиум", "актар", "фитоверм",
    "топаз", "фундазол", "бордоск", "ридомил", "квадрис", "строби",
    "абига", "фуфанон", "инта-вир", "биотлин", "актеллик",
    "бордосск", "медный купорос", "железный купорос",
    "кислотност", "раскислен", "кашпо", "субстрат", "грунт для",
    "аэраци", "влагоемк",
    "пасынкован", "дождеван", "капельн", "прореживан", "прищипк",
    "дезинфекц", "обеззараж", "стратификац", "скарификац",
    "черенкован", "окучиван", "пикирова",
    "секатор", "сучкорез", "культиватор", "плоскорез", "мотыг",
    "опрыскивател", "садовый инвентар", "бордюрн",
]


SHORT_GARDEN_WORDS = {
    "бор", "бора", "бором", "боре", "бору", "боры", "борн",
    "борная", "борной", "борную", "борным",
    "цинк", "цинка", "цинком", "цинке", "цинку", "цинков",
    "цинковый", "цинковая", "цинковой",
    "медь", "меди", "медью", "медн", "медный", "медная", "медной",
    "медную", "медьсодержащ",
    "сера", "серы", "серой", "серу", "серн", "серный", "серная",
    "серной", "серную",
    "гипс", "гипса", "гипсом", "гипсе", "гипсов", "гипсовый",
    "калий", "калия", "калием", "калии", "калийн", "калийный",
    "фосфор", "фосфора", "фосфором", "фосфорн", "фосфорный",
    "азот", "азота", "азотом", "азотн", "азотный",
    "зола", "золы", "золой", "золу", "зольн",
    "известь", "извести", "известью", "известк",
    "хом", "хома", "хомом", "искра", "искры", "искрой", "искру",
}


def has_short_garden_term(query: str) -> bool:
    words = re.findall(r"[а-яёa-z]+", query.lower())
    return any(w in SHORT_GARDEN_WORDS for w in words)


def rule_filter(query: str) -> tuple[str, str]:
    q = query.lower().strip()

    if len(q) < 3:
        return "deny", "Запрос слишком короткий"
    if len(q) > 500:
        return "deny", "Запрос слишком длинный (макс. 500 символов)"

    if has_short_garden_term(q):
        return "allow", ""

    has_garden = any(m in q for m in GARDEN_MARKERS)
    has_stop = any(w in q for w in STOP_WORDS)

    if has_stop and not has_garden:
        return "deny", "Приложение отвечает только на вопросы о садоводстве и растениях"

    if has_garden and not has_stop:
        return "allow", ""

    return "check", ""


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


# ========== DEEPSEEK (текст) ==========

async def call_deepseek(messages: list, max_tokens: int = 2500) -> str:
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": DEEPSEEK_MODEL,
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


# ========== OPENROUTER (vision) с автоматическим перебором моделей ==========

async def call_openrouter_vision(messages: list, max_tokens: int = 1500) -> str:
    """
    Перебирает модели из OPENROUTER_VISION_MODELS, пока одна не сработает.
    При ошибке 404 (модель удалена) или 402 (недоступна) — идёт к следующей.
    """
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://garden-calendar.app",
        "X-Title": "Garden Calendar",
    }

    last_error = "no models tried"

    for model_id in OPENROUTER_VISION_MODELS:
        try:
            payload = {
                "model": model_id,
                "messages": messages,
                "max_tokens": max_tokens,
            }
            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.post(
                    f"{OPENROUTER_BASE_URL}/chat/completions",
                    headers=headers,
                    json=payload,
                )

            if resp.status_code == 200:
                data = resp.json()
                if data.get("choices") and data["choices"]:
                    content = data["choices"][0]["message"]["content"]
                    print(f"Vision model OK: {model_id}")
                    return content
                else:
                    print(f"Model {model_id} returned no choices: {data}")
                    last_error = f"{model_id}: no choices"
                    continue

            elif resp.status_code in (404, 402):
                print(f"Model {model_id} unavailable ({resp.status_code}), trying next...")
                last_error = f"{model_id}: {resp.status_code}"
                continue

            else:
                print(f"Model {model_id} error {resp.status_code}: {resp.text}")
                last_error = f"{model_id}: {resp.status_code} {resp.text[:200]}"
                continue

        except Exception as e:
            print(f"Exception on {model_id}: {e}")
            last_error = f"{model_id}: exception {e}"
            continue

    raise HTTPException(
        status_code=502,
        detail=f"All vision models failed. Last error: {last_error}"
    )


# ========== LLM-КЛАССИФИКАТОР ==========

async def classify_topic(query: str) -> bool:
    prompt = (
        "Определи, относится ли запрос к теме садоводства, огородничества, "
        "комнатных растений, болезней растений или вредителей.\n\n"
        "ВАЖНО: садоводы используют специальные термины, которые могут "
        "выглядеть как химия, геология или общие слова, но относятся "
        "к садоводству (бор, борная кислота, цинк, медь, доломитовая мука, "
        "зола, известь, гипс, сера, калий, фосфор, азот, хелат, гумат, "
        "суперфосфат, Эпин, Циркон, Корневин, Фитоспорин, Триходермин, "
        "Актара, Фитоверм, Топаз, Фундазол, бордоская смесь, ХОМ, "
        "пикировка, черенкование, прививка, мульчирование, окучивание, "
        "пасынкование, дождевание, капельный полив, прищипка, "
        "стратификация, скарификация, секатор, сучкорез, культиватор, "
        "плоскорез, мотыга, опрыскиватель, кислотность, раскисление, "
        "дренаж, торф, перлит, вермикулит, субстрат).\n\n"
        "Ответь ОДНИМ словом: YES или NO.\n"
        "Если запрос смешанный (например, «напиши код для полива на Python») — ответь NO.\n"
        "Если запрос не о садоводстве (погода, программирование, стихи, "
        "медицина, новости) — ответь NO.\n\n"
        f"Запрос: {query}"
    )
    messages = [{"role": "user", "content": prompt}]
    try:
        result = await call_deepseek(messages, max_tokens=5)
        return "YES" in result.strip().upper()
    except Exception as e:
        print(f"Classifier error: {e}")
        return True


# ========== ЭНДПОИНТЫ ==========

@app.get("/")
def health():
    return {"status": "ok", "service": "garden-calendar-ai"}


@app.post("/api/ask", response_model=AiResponse)
async def ask(req: AskRequest):
    check_daily_limit(req.device_id)

    verdict, reason = rule_filter(req.query)
    if verdict == "deny":
        raise HTTPException(status_code=400, detail=reason)

    if verdict == "check":
        if not await classify_topic(req.query):
            raise HTTPException(
                status_code=400,
                detail="Приложение отвечает только на вопросы о садоводстве и растениях"
            )

    key = cache_key(req.query, req.context)
    if key in server_cache:
        return AiResponse(text=server_cache[key])

    system_prompt = (
        "Ты — эксперт-садовод. Отвечай кратко, по делу, на русском языке. "
        "Тема: садоводство, огородничество, комнатные растения, болезни растений, "
        "вредители, удобрения, обрезка, полив, урожай.\n"
        "ВАЖНО: если вопрос хотя бы частично не по теме — вежливо откажись."
    )
    user_content = req.query
    if req.context:
        user_content = f"Контекст (растение/тема): {req.context}\n\nВопрос: {req.query}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    text = await call_deepseek(messages, max_tokens=2500)

    if len(server_cache) > 1000:
        server_cache.pop(next(iter(server_cache)))
    server_cache[key] = text

    return AiResponse(text=text)


@app.post("/api/ask-photo", response_model=AiResponse)
async def ask_photo(req: AskPhotoRequest):
    """Анализ фото растения через OpenRouter с автоматическим перебором vision-моделей."""
    check_daily_limit(req.device_id)

    system_prompt = (
        "Ты — эксперт-садовод и фитопатолог. Проанализируй фото растения. "
        "Определи: 1) что это за растение (если возможно), "
        "2) какие проблемы видны (болезнь, вредитель, дефицит питания, "
        "механические повреждения), "
        "3) что делать — конкретные шаги и препараты. "
        "Отвечай на русском языке, структурированно, кратко."
    )
    user_text = req.context or "Определи, что на фото, и что делать."
    user_content = [
        {"type": "text", "text": user_text},
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{req.image_base64}"},
        },
    ]
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    text = await call_openrouter_vision(messages, max_tokens=2000)
    return AiResponse(text=text)