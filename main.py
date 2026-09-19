import os
import re
import time
import threading
import hashlib
from collections import defaultdict, deque
from datetime import date, datetime

import httpx
from fastapi import FastAPI, HTTPException, Request, Header
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


# ========== МЕТРИКИ (мониторинг) ==========

SERVER_STARTED_AT = time.time()

metrics = {
    "ask_total": 0,
    "ask_photo_total": 0,
    "rejected_total": 0,
    "cache_hits": 0,
    "errors_total": 0,
}

vision_model_stats: dict = {
    m: {"success": 0, "fail": 0, "last_used_ts": None, "last_error": None}
    for m in OPENROUTER_VISION_MODELS
}

error_log: deque = deque(maxlen=50)


def log_error(source: str, message: str, device_id: str = "unknown"):
    """Записывает ошибку в ring-buffer и увеличивает счётчик."""
    metrics["errors_total"] += 1
    error_log.append({
        "ts": datetime.utcnow().isoformat() + "Z",
        "source": source,
        "message": message[:500],
        "device_id": device_id,
    })


# ========== ПРОВЕРКА UPSTREAMS ==========

upstream_health: dict = {
    "deepseek": {
        "status": "unknown",
        "latency_ms": None,
        "checked_at": None,
        "error": None,
    },
    "openrouter": {
        "status": "unknown",
        "latency_ms": None,
        "checked_at": None,
        "error": None,
    },
}


def _ping_deepseek() -> None:
    """Синхронный пинг DeepSeek /models. Записывает результат в upstream_health."""
    start = time.time()
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                f"{DEEPSEEK_BASE_URL}/models",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            )
        latency = int((time.time() - start) * 1000)
        now_iso = datetime.utcnow().isoformat() + "Z"
        if resp.status_code == 200:
            upstream_health["deepseek"] = {
                "status": "ok",
                "latency_ms": latency,
                "checked_at": now_iso,
                "error": None,
            }
            print(f"Upstream DeepSeek: OK ({latency}ms)")
        else:
            upstream_health["deepseek"] = {
                "status": "error",
                "latency_ms": latency,
                "checked_at": now_iso,
                "error": f"HTTP {resp.status_code}",
            }
            print(f"Upstream DeepSeek: HTTP {resp.status_code}")
    except Exception as e:
        now_iso = datetime.utcnow().isoformat() + "Z"
        upstream_health["deepseek"] = {
            "status": "error",
            "latency_ms": None,
            "checked_at": now_iso,
            "error": str(e)[:200],
        }
        print(f"Upstream DeepSeek: exception {e}")


def _ping_openrouter() -> None:
    """Синхронный пинг OpenRouter /models. Записывает результат в upstream_health."""
    start = time.time()
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                f"{OPENROUTER_BASE_URL}/models",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            )
        latency = int((time.time() - start) * 1000)
        now_iso = datetime.utcnow().isoformat() + "Z"
        if resp.status_code == 200:
            upstream_health["openrouter"] = {
                "status": "ok",
                "latency_ms": latency,
                "checked_at": now_iso,
                "error": None,
            }
            print(f"Upstream OpenRouter: OK ({latency}ms)")
        else:
            upstream_health["openrouter"] = {
                "status": "error",
                "latency_ms": latency,
                "checked_at": now_iso,
                "error": f"HTTP {resp.status_code}",
            }
            print(f"Upstream OpenRouter: HTTP {resp.status_code}")
    except Exception as e:
        now_iso = datetime.utcnow().isoformat() + "Z"
        upstream_health["openrouter"] = {
            "status": "error",
            "latency_ms": None,
            "checked_at": now_iso,
            "error": str(e)[:200],
        }
        print(f"Upstream OpenRouter: exception {e}")


def check_upstreams_now() -> None:
    """Пингует оба upstream в параллельных потоках (быстрее, чем последовательно)."""
    t1 = threading.Thread(target=_ping_deepseek, daemon=True)
    t2 = threading.Thread(target=_ping_openrouter, daemon=True)
    t1.start()
    t2.start()
    t1.join(timeout=20)
    t2.join(timeout=20)


def _upstream_loop() -> None:
    """Фоновый цикл: пингует upstreams раз в 20 минут."""
    time.sleep(10)   # дать серверу подняться
    while True:
        try:
            check_upstreams_now()
        except Exception as e:
            print(f"Upstream loop error: {e}")
        time.sleep(20 * 60)


# ========== АДМИН-ДОСТУП ==========

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")

if not ADMIN_TOKEN:
    print("WARNING: ADMIN_TOKEN not set — /api/admin/* endpoints disabled")


def require_admin(x_admin_token: Optional[str] = Header(None)) -> None:
    """Проверяет X-Admin-Token. 401 при неверном или отсутствующем токене."""
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="Admin access disabled")
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid admin token")


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
    used: Optional[int] = None
    limit: Optional[int] = None
    model: Optional[str] = None


class UsageResponse(BaseModel):
    used: int
    limit: int


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
    "уход за", "выращиван", "посадк", "пересадк", "полив", "рыхлен",
    "прополк", "борьб с", "обработк", "защит растен",
    "голубик", "брусник", "ежевик", "крыжовник", "жимолост",
    "ирг", "облепих", "айв", "абрикос", "персик", "слив",
    "черешн", "виноград", "арбуз", "дын", "черноплодн", "рябин",
    "ягод", "фрукт", "овощ", "злак", "корнеплод",
    "сорт", "гибрид", "подвой", "привой",
    "корн", "стебел", "побег", "бутон", "завяз", "соцвети",
    "фотосинтез", "хлорофилл", "корнеплод", "клубн",
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
    "уход", "ухода", "уходу", "уходом",
    "ягод", "ягода", "ягоды", "ягодой",
    "плод", "плода", "плоды", "плодом",
}


def has_short_garden_term(query: str) -> bool:
    words = re.findall(r"[а-яёa-z]+", query.lower())
    return any(w in SHORT_GARDEN_WORDS for w in words)


def rule_filter(query: str) -> tuple[str, str]:
    q = query.lower().strip()

    SYSTEM_PREFIXES = (
        "расскажи про уход за культурой:",
        "какие вредители опасны для культуры:",
        "какие болезни бывают у культуры:",
    )
    if any(q.startswith(p) for p in SYSTEM_PREFIXES):
        return "allow", ""

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


def get_daily_usage(device_id: str) -> tuple[int, int]:
    """Возвращает (used, limit) БЕЗ инкремента."""
    today = date.today().isoformat()
    rec = daily_usage[device_id]
    if rec["date"] != today:
        rec["date"] = today
        rec["count"] = 0
    return rec["count"], FREE_DAILY_LIMIT


def increment_daily_usage(device_id: str) -> tuple[int, int]:
    """Инкрементирует счётчик и возвращает (used, limit). 429, если превышен."""
    used, limit = get_daily_usage(device_id)
    if used >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"Дневной лимит исчерпан ({limit} запросов). Попробуйте завтра."
        )
    daily_usage[device_id]["count"] += 1
    return daily_usage[device_id]["count"], limit


# ========== КЭШ ==========

server_cache: dict = {}


def cache_key(query: str, context: str) -> str:
    return hashlib.sha256(f"{context}|{query}".lower().encode()).hexdigest()


# ========== DEEPSEEK (текст) ==========

async def call_deepseek(messages: list, max_tokens: int = 4000) -> str:
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
    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
        )
    if resp.status_code != 200:
        log_error("deepseek", f"HTTP {resp.status_code}: {resp.text[:200]}")
        raise HTTPException(
            status_code=502,
            detail=f"DeepSeek error {resp.status_code}: {resp.text}",
        )
    data = resp.json()
    choice = data["choices"][0]
    finish_reason = choice.get("finish_reason", "unknown")
    usage = data.get("usage", {})
    print(
        f"DeepSeek: finish_reason={finish_reason}, "
        f"prompt_tokens={usage.get('prompt_tokens')}, "
        f"completion_tokens={usage.get('completion_tokens')}"
    )
    if finish_reason == "length":
        print("WARNING: response truncated by max_tokens!")
    return choice["message"]["content"]


# ========== OPENROUTER (vision) с автоматическим перебором моделей ==========

async def call_openrouter_vision(messages: list, max_tokens: int = 1500) -> tuple[str, str]:
    """Возвращает (text, model_id) — текст ответа и id модели, которая сработала."""
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
                    vision_model_stats[model_id]["success"] += 1
                    vision_model_stats[model_id]["last_used_ts"] = time.time()
                    vision_model_stats[model_id]["last_error"] = None
                    return content, model_id
                else:
                    print(f"Model {model_id} returned no choices: {data}")
                    last_error = f"{model_id}: no choices"
                    vision_model_stats[model_id]["fail"] += 1
                    vision_model_stats[model_id]["last_error"] = "no choices"
                    continue

            elif resp.status_code in (404, 402):
                print(f"Model {model_id} unavailable ({resp.status_code}), trying next...")
                last_error = f"{model_id}: {resp.status_code}"
                vision_model_stats[model_id]["fail"] += 1
                vision_model_stats[model_id]["last_error"] = f"HTTP {resp.status_code}"
                continue

            else:
                print(f"Model {model_id} error {resp.status_code}: {resp.text}")
                last_error = f"{model_id}: {resp.status_code} {resp.text[:200]}"
                vision_model_stats[model_id]["fail"] += 1
                vision_model_stats[model_id]["last_error"] = f"HTTP {resp.status_code}"
                log_error("openrouter", f"{model_id}: HTTP {resp.status_code}")
                continue

        except Exception as e:
            print(f"Exception on {model_id}: {e}")
            last_error = f"{model_id}: exception {e}"
            vision_model_stats[model_id]["fail"] += 1
            vision_model_stats[model_id]["last_error"] = str(e)[:200]
            log_error("openrouter", f"{model_id}: exception {e}")
            continue

    raise HTTPException(
        status_code=502,
        detail=f"All vision models failed. Last error: {last_error}"
    )


# ========== LLM-КЛАССИФИКАТОР ==========

async def classify_topic(query: str) -> bool:
    prompt = (
        "Ты — фильтр для приложения «Садовый календарь». "
        "Приложение принимает ЛЮБЫЕ запросы про: садоводство, огородничество, "
        "комнатные и садовые растения, ягоды, фрукты, овощи, деревья, кустарники, "
        "болезни и вредителей растений, уход за растениями, удобрения и подкормки, "
        "почву, семена, рассаду, теплицы, обрезку, полив, посадку.\n\n"
        "Отвечай YES, если запрос хотя бы косвенно про растения или сад.\n"
        "Отвечай NO только если запрос явно не по теме (программирование, "
        "погода, политика, стихи, анекдоты, медицина человека, рецепты еды, "
        "финансы, отношения).\n\n"
        "Примеры YES: «уход за голубикой», «как поливать томаты», "
        "«борная кислота для смородины», «мои кусты желтеют», "
        "«чем подкормить яблоню», «рассада вытянулась».\n"
        "Примеры NO: «напиши код на Python», «погода на завтра», "
        "«что приготовить на ужин», «как похудеть».\n\n"
        "Ответь ОДНИМ словом: YES или NO.\n\n"
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


@app.get("/api/usage", response_model=UsageResponse)
def get_usage(device_id: str = "unknown"):
    """Возвращает текущее использование дневного лимита для устройства."""
    used, limit = get_daily_usage(device_id)
    return UsageResponse(used=used, limit=limit)


@app.post("/api/ask", response_model=AiResponse)
async def ask(req: AskRequest):
    # 1. Rule-фильтр (без обращения к AI)
    verdict, reason = rule_filter(req.query)
    if verdict == "deny":
        metrics["rejected_total"] += 1
        raise HTTPException(status_code=400, detail=reason)

    # 2. LLM-классификатор для пограничных случаев
    if verdict == "check":
        if not await classify_topic(req.query):
            metrics["rejected_total"] += 1
            raise HTTPException(
                status_code=400,
                detail="Приложение отвечает только на вопросы о садоводстве и растениях"
            )

    # 3. Инкремент счётчика только после успешных фильтров
    used, limit = increment_daily_usage(req.device_id)

    # 4. Кэш
    key = cache_key(req.query, req.context)
    if key in server_cache:
        metrics["cache_hits"] += 1
        return AiResponse(text=server_cache[key], used=used, limit=limit)

    # 5. Основной запрос
    system_prompt = (
        "Ты — эксперт-садовод. Отвечай на русском языке, структурированно.\n\n"
        "ОБЯЗАТЕЛЬНЫЙ ФОРМАТ ОТВЕТА:\n"
        "1. Каждый смысловой раздел начинай с markdown-заголовка второго уровня — "
        "два символа решётки и пробел: `## Название раздела`.\n"
        "   ПРАВИЛЬНО: `## Химия`, `## Народные средства`, `## Профилактика`.\n"
        "   НЕПРАВИЛЬНО: `**Химия**`, `**Народные средства**`, `4. Химия` — "
        "жирный для заголовков НЕ ИСПОЛЬЗУЙ.\n"
        "2. Внутри каждого раздела — маркированный список через `- ` или "
        "нумерованный через `1. `.\n"
        "3. Жирным (`**термин**`) выделяй ТОЛЬКО названия препаратов и "
        "ключевые термины ВНУТРИ текста, а не заголовки разделов.\n\n"
        "ПРИМЕР ПРАВИЛЬНОГО ОТВЕТА:\n"
        "```\n"
        "## Народные средства\n"
        "- Чеснок: настой 200 г на 10 л воды\n"
        "- Табак: 300 г на 10 л воды\n\n"
        "## Химия\n"
        "1. **Актара** — 4 г на 10 л воды\n"
        "2. **Фитоверм** — 4 мл на 1 л воды\n\n"
        "## Профилактика\n"
        "- Осенняя перекопка приствольного круга\n"
        "```\n\n"
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
    metrics["ask_total"] += 1
    text = await call_deepseek(messages, max_tokens=4000)

    if len(server_cache) > 1000:
        server_cache.pop(next(iter(server_cache)))
    server_cache[key] = text

    return AiResponse(text=text, used=used, limit=limit)


@app.post("/api/ask-photo", response_model=AiResponse)
async def ask_photo(req: AskPhotoRequest):
    """Анализ фото растения через OpenRouter с автоматическим перебором vision-моделей."""
    used, limit = increment_daily_usage(req.device_id)

    system_prompt = (
        "Ты — эксперт-садовод и фитопатолог. Твоя задача — анализировать "
        "фотографии ЖИВЫХ РАСТЕНИЙ: их листьев, стеблей, корней, плодов, цветков, "
        "а также признаки болезней, вредителей и дефицита питания.\n\n"
        "ЕСЛИ на фото НЕТ растения (или его части) — вежливо откажись "
        "от анализа и объясни, что приложение работает только с растениями. "
        "Не предлагай использовать не-растительные объекты (шины, бутылки, "
        "вёдра, ящики, строительные материалы, инструменты, людей, животных, "
        "еду, предметы быта) в саду — это НЕ твоя задача.\n\n"
        "Если на фото растение — определи:\n"
        "1) что это за растение (если возможно);\n"
        "2) какие проблемы видны (болезнь, вредитель, дефицит питания, "
        "механические повреждения);\n"
        "3) что делать — конкретные шаги и препараты.\n\n"
        "Отвечай на русском языке, структурированно, кратко.\n\n"
        "ФОРМАТ ОТВЕТА (обязательно):\n"
        "- Разделы обозначай через `## Название раздела`.\n"
        "- Внутри разделов — маркированные или нумерованные списки.\n"
        "- Названия препаратов и ключевые термины выделяй жирным `**термин**`.\n\n"
        "Пример правильного отказа: «На фото — автомобильные шины, это не растение. "
        "Приложение анализирует только растения и их проблемы. "
        "Пришлите фото листа, стебля или плода растения.»"
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
    metrics["ask_photo_total"] += 1
    text, model_id = await call_openrouter_vision(messages, max_tokens=2500)
    return AiResponse(text=text, used=used, limit=limit, model=model_id)


# ========== АДМИН-ЭНДПОИНТЫ ==========

@app.get("/api/admin/stats")
def admin_stats(_: None = None, x_admin_token: Optional[str] = Header(None)):
    """
    Возвращает метрики сервера: uptime, счётчики, статистику моделей, ошибки.
    Требует заголовок X-Admin-Token.
    """
    require_admin(x_admin_token)

    now = time.time()
    uptime_sec = int(now - SERVER_STARTED_AT)

    model_stats_list = []
    for model_id, stats in vision_model_stats.items():
        last_used_ago = None
        if stats["last_used_ts"] is not None:
            last_used_ago = int(now - stats["last_used_ts"])
        model_stats_list.append({
            "model": model_id,
            "success": stats["success"],
            "fail": stats["fail"],
            "last_used_seconds_ago": last_used_ago,
            "last_error": stats["last_error"],
        })

    model_stats_list.sort(
        key=lambda x: (-x["success"], x["last_used_seconds_ago"] or 999999)
    )

    return {
        "uptime_seconds": uptime_sec,
        "uptime_human": _format_uptime(uptime_sec),
        "server_started_at": datetime.utcfromtimestamp(SERVER_STARTED_AT).isoformat() + "Z",
        "counters": dict(metrics),
        "active_devices_24h": len([d for d, r in daily_usage.items() if r.get("count", 0) > 0]),
        "upstreams": dict(upstream_health),
        "vision_models": model_stats_list,
        "recent_errors": list(error_log)[::-1],
    }


@app.get("/api/admin/health")
def admin_health(
    force: bool = False,
    x_admin_token: Optional[str] = Header(None),
):
    """
    Возвращает статус upstreams (DeepSeek + OpenRouter).
    С ?force=true — запускает свежую проверку перед ответом.
    """
    require_admin(x_admin_token)

    if force:
        check_upstreams_now()

    return {
        "checked_at": datetime.utcnow().isoformat() + "Z",
        "upstreams": dict(upstream_health),
    }


def _format_uptime(seconds: int) -> str:
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    parts = []
    if days > 0:
        parts.append(f"{days}д")
    if hours > 0 or days > 0:
        parts.append(f"{hours}ч")
    parts.append(f"{minutes}м")
    return " ".join(parts)


# Запуск фонового мониторинга upstreams при старте приложения
threading.Thread(target=_upstream_loop, daemon=True).start()