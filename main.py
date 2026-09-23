import os
import re
import json
import time
import threading
import hashlib
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone

import httpx
from fastapi import FastAPI, HTTPException, Request, Header, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List

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

# ✅ ОБНОВЛЕНО: используется актуальная модель deepseek-flash
#    (deepseek-chat мёртв с 24.07.2026; deepseek-v4-pro — legacy-алиас)
#    Режим размышлений ВЫКЛЮЧЕН принудительно во всех вызовах.
DEEPSEEK_MODEL = "deepseek-flash"
DEEPSEEK_MODEL_PLAN = "deepseek-flash"

# === Провайдер для vision (OpenRouter) ===
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# === Публичный Statuspage платформы Render ===
RENDER_STATUS_URL = "https://status.render.com/api/v2/status.json"

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


# ========== УТИЛИТЫ ==========

def now_iso() -> str:
    """✅ Замена устаревшего datetime.utcnow()."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def decode_json_response(resp) -> dict:
    """Принудительно декодирует ответ как UTF-8."""
    try:
        return json.loads(resp.content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return resp.json()


def _json_utf8_response(data: dict) -> Response:
    """
    Отдаёт JSON-ответ с явным UTF-8, обходя Pydantic-сериализацию.
    Решает проблему двойного перекодирования (UTF-8 → CP1251 → UTF-8).
    """
    body_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return Response(
        content=body_bytes,
        media_type="application/json; charset=utf-8",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )


# ========== МЕТРИКИ ==========

SERVER_STARTED_AT = time.time()

metrics = {
    "ask_total": 0,
    "ask_photo_total": 0,
    "plan_total": 0,
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
    metrics["errors_total"] += 1
    error_log.append({
        "ts": now_iso(),
        "source": source,
        "message": message[:500],
        "device_id": device_id,
    })


# ========== КЭШ ПЛАНОВ ==========

PLAN_CACHE_TTL_SECONDS = 90 * 24 * 60 * 60
plan_cache: dict = {}


# ========== ПРОВЕРКА UPSTREAMS ==========

upstream_health: dict = {
    "deepseek": {"status": "unknown", "latency_ms": None, "checked_at": None, "error": None},
    "openrouter": {"status": "unknown", "latency_ms": None, "checked_at": None, "error": None},
    "render": {"status": "unknown", "latency_ms": None, "checked_at": None, "error": None},
}


def _ping_deepseek() -> None:
    start = time.time()
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                f"{DEEPSEEK_BASE_URL}/models",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            )
        latency = int((time.time() - start) * 1000)
        now = now_iso()
        if resp.status_code == 200:
            upstream_health["deepseek"] = {"status": "ok", "latency_ms": latency,
                                            "checked_at": now, "error": None}
            print(f"Upstream DeepSeek: OK ({latency}ms)")
        else:
            upstream_health["deepseek"] = {"status": "error", "latency_ms": latency,
                                            "checked_at": now, "error": f"HTTP {resp.status_code}"}
            print(f"Upstream DeepSeek: HTTP {resp.status_code}")
    except Exception as e:
        now = now_iso()
        upstream_health["deepseek"] = {"status": "error", "latency_ms": None,
                                        "checked_at": now, "error": str(e)[:200]}
        print(f"Upstream DeepSeek: exception {e}")


def _ping_openrouter() -> None:
    start = time.time()
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                f"{OPENROUTER_BASE_URL}/models",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            )
        latency = int((time.time() - start) * 1000)
        now = now_iso()
        if resp.status_code == 200:
            upstream_health["openrouter"] = {"status": "ok", "latency_ms": latency,
                                              "checked_at": now, "error": None}
            print(f"Upstream OpenRouter: OK ({latency}ms)")
        else:
            upstream_health["openrouter"] = {"status": "error", "latency_ms": latency,
                                              "checked_at": now, "error": f"HTTP {resp.status_code}"}
            print(f"Upstream OpenRouter: HTTP {resp.status_code}")
    except Exception as e:
        now = now_iso()
        upstream_health["openrouter"] = {"status": "error", "latency_ms": None,
                                          "checked_at": now, "error": str(e)[:200]}
        print(f"Upstream OpenRouter: exception {e}")


def _ping_render() -> None:
    start = time.time()
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(RENDER_STATUS_URL)
        latency = int((time.time() - start) * 1000)
        now = now_iso()
        if resp.status_code != 200:
            upstream_health["render"] = {"status": "error", "latency_ms": latency,
                                          "checked_at": now, "error": f"Statuspage HTTP {resp.status_code}"}
            print(f"Upstream Render: Statuspage HTTP {resp.status_code}")
            return
        data = decode_json_response(resp)
        indicator = data.get("status", {}).get("indicator", "unknown")
        description = data.get("status", {}).get("description", "")
        if indicator == "none":
            upstream_health["render"] = {"status": "ok", "latency_ms": latency,
                                          "checked_at": now, "error": None}
            print(f"Upstream Render: OK ({latency}ms) — {description}")
        else:
            upstream_health["render"] = {"status": "error", "latency_ms": latency,
                                          "checked_at": now, "error": description or f"indicator={indicator}"}
            print(f"Upstream Render: {indicator} — {description}")
    except Exception as e:
        now = now_iso()
        upstream_health["render"] = {"status": "error", "latency_ms": None,
                                      "checked_at": now, "error": str(e)[:200]}
        print(f"Upstream Render: exception {e}")


def check_upstreams_now() -> None:
    t1 = threading.Thread(target=_ping_deepseek, daemon=True)
    t2 = threading.Thread(target=_ping_openrouter, daemon=True)
    t3 = threading.Thread(target=_ping_render, daemon=True)
    t1.start(); t2.start(); t3.start()
    t1.join(timeout=20); t2.join(timeout=20); t3.join(timeout=20)


def _upstream_loop() -> None:
    time.sleep(10)
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
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="Admin access disabled")
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid admin token")


# ========== МОДЕЛИ ==========

class AskRequest(BaseModel):
    query: str
    context: Optional[str] = ""
    device_id: Optional[str] = "unknown"
    request_type: Optional[str] = "free"   # "care" | "pests" | "diseases" | "free"
    timezone_offset_minutes: Optional[int] = 0
    culture_name: Optional[str] = ""
    variety: Optional[str] = ""
    region_zone: Optional[int] = 0


class AskPhotoRequest(BaseModel):
    image_base64: str
    context: Optional[str] = ""
    device_id: Optional[str] = "unknown"
    timezone_offset_minutes: Optional[int] = 0


class AiResponse(BaseModel):
    text: str
    used: Optional[int] = None
    limit: Optional[int] = None
    model: Optional[str] = None


class UsageResponse(BaseModel):
    used: int
    limit: int


class PlanRequest(BaseModel):
    culture_name: str
    variety: Optional[str] = ""
    region_zone: Optional[int] = 5
    phase: str
    device_id: Optional[str] = "unknown"
    timezone_offset_minutes: Optional[int] = 0


class PlanTask(BaseModel):
    title: str
    description: str
    month: int
    day: int


class PlanResponse(BaseModel):
    tasks: List[PlanTask]
    from_cache: bool = False
    detected_lifecycle: Optional[str] = None


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
    "вермикулит", "перлит", "торф", "слиз", "сапропел", "сидерат",
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

INDOOR_PLANT_MARKERS = [
    "фикус", "орхиде", "монстер", "сансевиер", "суккулент", "кактус",
    "алоэ", "толстянк", "крассул", "драцен", "юкк", "пальм", "хамедоре",
    "спатифиллум", "антуриум", "диффенбахи", "хлорофитум", "традесканц",
    "фиалка", "сенполи", "бегони", "глоксини", "цикламен", "пеларгони",
    "герань", "папоротник", "нефролепис", "асплениум", "плющ", "хедера",
    "шелфлера", "каланхоэ", "эхеверия", "литопс", "фаленопсис",
    "дендробиум", "каттлея", "азалия", "цитрус", "лимон", "мандарин",
    "апельсин домашний", "кофе", "авокадо", "комнатн",
]


def is_indoor_plant(culture_name: str) -> bool:
    """Определяет, комнатное ли растение по названию."""
    if not culture_name:
        return False
    name = culture_name.lower()
    return any(marker in name for marker in INDOOR_PLANT_MARKERS)

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

daily_usage: dict = defaultdict(lambda: {"local_date": None, "count": 0})
FREE_DAILY_LIMIT = 10


def _local_date_for_offset(offset_minutes: int) -> str:
    try:
        tz = timezone(timedelta(minutes=offset_minutes))
    except Exception:
        tz = timezone.utc
    return datetime.now(tz).date().isoformat()


def get_daily_usage(device_id: str, tz_offset_minutes: int = 0) -> tuple[int, int]:
    today_local = _local_date_for_offset(tz_offset_minutes)
    rec = daily_usage[device_id]
    if rec["local_date"] != today_local:
        rec["local_date"] = today_local
        rec["count"] = 0
    return rec["count"], FREE_DAILY_LIMIT


def increment_daily_usage(device_id: str, tz_offset_minutes: int = 0) -> tuple[int, int]:
    used, limit = get_daily_usage(device_id, tz_offset_minutes)
    if used >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"Дневной лимит исчерпан ({limit} запросов). Попробуйте завтра."
        )
    daily_usage[device_id]["count"] += 1
    return daily_usage[device_id]["count"], limit


# ========== КЭШ ==========

server_cache: dict = {}


def cache_key(
    query: str,
    context: str,
    culture_name: str = "",
    variety: str = "",
    region_zone: int = 0,
) -> str:
    raw = f"{context}|{query}|{culture_name}|{variety}|{region_zone}".lower()
    return hashlib.sha256(raw.encode()).hexdigest()


# ========== DEEPSEEK (текст) ==========

async def call_deepseek(
    messages: list,
    max_tokens: int = 4000,
    temperature: float = 0.5,
) -> str:
    """
    ✅ ОБНОВЛЕНО под deepseek-flash:
    - Режим размышлений (thinking) ОТКЛЮЧЁН принудительно.
    - reasoning_effort="none" + thinking={"type":"disabled"}.
    - temperature работает, т.к. thinking отключён.
    """
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "stream": False,
        "max_tokens": max_tokens,
        "temperature": temperature,
        # ✅ Thinking Mode выключен
        "thinking": {"type": "disabled"},
        "reasoning_effort": "none",
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
        )

    if resp.status_code != 200:
        body = resp.content.decode("utf-8", errors="replace")
        log_error("deepseek", f"HTTP {resp.status_code}: {body[:200]}")
        raise HTTPException(
            status_code=502,
            detail=f"DeepSeek error {resp.status_code}",
        )

    data = decode_json_response(resp)
    choice = data["choices"][0]
    message = choice.get("message", {})
    content = message.get("content", "")
    finish_reason = choice.get("finish_reason", "unknown")
    usage = data.get("usage", {})

    print(
        f"DeepSeek: model={DEEPSEEK_MODEL}, finish_reason={finish_reason}, "
        f"prompt_tokens={usage.get('prompt_tokens')}, "
        f"completion_tokens={usage.get('completion_tokens')}, "
        f"max_tokens={max_tokens}, thinking=disabled"
    )

    if finish_reason == "length":
        print("WARNING: response truncated by max_tokens!")

    if not isinstance(content, str):
        content = str(content)

    return content


# ========== DEEPSEEK С JSON (для generate-plan) ==========

async def call_deepseek_json(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 4000,
) -> dict:
    """
    ✅ ОБНОВЛЕНО: deepseek-flash без размышлений,
    response_format={"type": "json_object"} для точного следования схеме.
    """
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": DEEPSEEK_MODEL_PLAN,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "max_tokens": max_tokens,
        "temperature": 0.5,
        "response_format": {"type": "json_object"},
        # ✅ Thinking Mode выключен
        "thinking": {"type": "disabled"},
        "reasoning_effort": "none",
    }

    async with httpx.AsyncClient(timeout=150.0) as client:
        resp = await client.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
        )

    if resp.status_code != 200:
        body = resp.content.decode("utf-8", errors="replace")
        print(f"DeepSeek plan HTTP {resp.status_code}, body: {body[:1000]}")
        log_error("deepseek-plan", f"HTTP {resp.status_code}: {body[:300]}")
        raise HTTPException(
            status_code=502,
            detail=f"DeepSeek error {resp.status_code}. Check server logs.",
        )

    data = decode_json_response(resp)

    if "choices" not in data or not data["choices"]:
        raise HTTPException(
            status_code=502,
            detail=f"No choices in response. Keys: {list(data.keys())}",
        )

    choice = data["choices"][0]
    message = choice.get("message", {})
    content = message.get("content", "")
    finish_reason = choice.get("finish_reason", "unknown")

    print(
        f"DeepSeek plan response: finish_reason={finish_reason}, "
        f"content_type={type(content).__name__}, content_len={len(content) if content else 0}"
    )

    if not isinstance(content, str):
        content = str(content)

    print(f"DeepSeek plan raw content (first 1000):\n{content[:1000]}")

    if not content or len(content.strip()) == 0:
        raise HTTPException(
            status_code=502,
            detail="Empty AI response. Try again later.",
        )

    cleaned = content.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    parsed = None
    try:
        parsed = json.loads(cleaned, strict=False)
    except Exception as e1:
        print(f"JSON parse attempt 1 failed: {e1}")

    if parsed is None:
        try:
            cleaned2 = re.sub(r'[\n\r]', ' ', cleaned)
            parsed = json.loads(cleaned2, strict=False)
        except Exception as e2:
            print(f"JSON parse attempt 2 failed: {e2}")

    if parsed is None:
        match = re.search(r'\{[\s\S]*\}', content)
        if match:
            try:
                inner = re.sub(r'[\n\r]', ' ', match.group(0))
                parsed = json.loads(inner, strict=False)
            except Exception as e3:
                print(f"JSON parse attempt 3 failed: {e3}")

    if parsed is None:
        raise HTTPException(
            status_code=502,
            detail="Invalid JSON from AI. Try again.",
        )

    return parsed


def validate_plan_json(parsed: dict) -> List[dict]:
    if not isinstance(parsed, dict) or "tasks" not in parsed:
        raise HTTPException(status_code=502, detail="AI JSON without 'tasks' field")
    tasks = parsed["tasks"]
    if not isinstance(tasks, list) or not tasks:
        raise HTTPException(status_code=502, detail="AI returned empty tasks list")
    if len(tasks) > 40:
        tasks = tasks[:40]

    result = []
    seen_titles = set()
    for t in tasks:
        if not isinstance(t, dict):
            continue
        title = str(t.get("title", "")).strip()
        description = str(t.get("description", "")).strip()
        try:
            month = int(t.get("month", 0))
            day = int(t.get("day", 0))
        except (ValueError, TypeError):
            continue
        if not title or month < 1 or month > 12 or day < 1 or day > 28:
            continue
        if title.lower() in seen_titles:
            continue
        seen_titles.add(title.lower())
        result.append({
            "title": title[:60],
            "description": description[:150],
            "month": month,
            "day": day,
        })
    if not result:
        raise HTTPException(status_code=502, detail="AI returned invalid plan")
    return result


# ========== OPENROUTER (vision) ==========

async def call_openrouter_vision(messages: list, max_tokens: int = 1500) -> tuple[str, str]:
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
                data = decode_json_response(resp)
                if data.get("choices") and data["choices"]:
                    content = data["choices"][0]["message"]["content"]
                    print(f"Vision model OK: {model_id}")
                    vision_model_stats[model_id]["success"] += 1
                    vision_model_stats[model_id]["last_used_ts"] = time.time()
                    vision_model_stats[model_id]["last_error"] = None
                    return content, model_id
                else:
                    print(f"Model {model_id} returned no choices")
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
                print(f"Model {model_id} error {resp.status_code}")
                last_error = f"{model_id}: {resp.status_code}"
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
        "Отвечай NO только если запрос явно не по теме.\n\n"
        "Ответь ОДНИМ словом: YES или NO.\n\n"
        f"Запрос: {query}"
    )
    messages = [{"role": "user", "content": prompt}]
    try:
        # ✅ Thinking Mode уже отключён глобально в call_deepseek
        result = await call_deepseek(messages, max_tokens=10)
        return "YES" in result.strip().upper()
    except Exception as e:
        print(f"Classifier error: {e}")
        return True


# ========== СБОРКА ПРОМПТА ДЛЯ /api/ask ==========

def build_indoor_care_prompt() -> tuple[str, int]:
    """
    Отдельный промпт для КОМНАТНЫХ растений.
    Строго 5 разделов. Никаких сортов, зимовки, сбора урожая.
    """
    max_tokens = 4000
    system_prompt = (
        "Ты — опытный растениевод, специализация: комнатные растения "
        "и уход в квартире.\n\n"
        "Пользователь просит рассказать про УХОД за КОМНАТНЫМ растением.\n\n"
        "⛔ СТРУКТУРА ЖЁСТКО ЗАФИКСИРОВАНА. Разрешены ТОЛЬКО эти 5 разделов:\n\n"
        "## Полив\n"
        "- Объём воды (л или мл), частота, требования к воде (отстоянная, "
        "комнатной температуры), признаки перелива и недолива.\n"
        "- Влажность воздуха, опрыскивание, душ (если применимо).\n\n"
        "## Подкормка\n"
        "- Конкретные удобрения с дозировками (г/л или мл/л), частота, "
        "сезонность (активный рост / покой).\n"
        "- Что даёт азот/фосфор/калий для комнатных.\n\n"
        "## Обрезка и формировка\n"
        "- Когда формировать (весна–лето), прищипка, удаление сухих листьев.\n"
        "- Обработка срезов.\n\n"
        "## Мульчирование и рыхление\n"
        "- Мульча (кокосовое волокно, керамзит, декоративные камешки), "
        "толщина слоя.\n"
        "- Рыхление верхнего слоя, замена верхнего грунта.\n\n"
        "## Профилактика болезней и вредителей\n"
        "- Основные вредители комнатных (щитовка, паутинный клещ, трипс, "
        "мучнистый червец).\n"
        "- Конкретные препараты с дозировками.\n"
        "- Гигиена (протирание листьев, влажность, проветривание).\n\n"
        "⛔ ЗАПРЕЩЕНО СОЗДАВАТЬ ЗАГОЛОВКИ:\n"
        "- ## Сбор урожая и хранение\n"
        "- ## Перспективные сорта\n"
        "- ## Подготовка к зиме\n"
        "- Любые другие разделы, кроме 5 разрешённых выше.\n\n"
        "Если тема не применима — просто не создавай раздел, "
        "не пиши 'не применимо', не пиши 'раздел отсутствует'. "
        "Просто пропусти его.\n\n"
        "ЖЁСТКИЕ ТРЕБОВАНИЯ:\n"
        "1. Конкретика: препарат + дозировка + срок.\n"
        "2. Не более 3500 символов.\n"
        "3. Без вступлений и заключений.\n"
        "4. Жирным (**текст**) — только препараты и ключевые термины.\n\n"
        "ПРИМЕР ПРАВИЛЬНОГО ОТВЕТА (фикус Бенджамина):\n"
        "## Полив\n"
        "- Отстоянная вода комнатной температуры (+20…+24 °C), "
        "0,5–1 л на растение среднего размера.\n"
        "- Летом — раз в 5–7 дней, зимой — раз в 10–14 дней, "
        "после просыхания верхних 3–4 см грунта.\n"
        "- Перелив опаснее недолива: пожелтение и опадение нижних листьев.\n"
        "- Раз в месяц — душ +30 °C, листья протирать влажной тканью.\n\n"
        "## Подкормка\n"
        "- Март–сентябрь: **Фертика Люкс** 1 г/л или **Агрикола для "
        "фикусов** 1 колпачок/1 л каждые 14 дней.\n"
        "- Октябрь–февраль: подкормки прекратить или половинная доза "
        "раз в месяц.\n\n"
        "## Обрезка и формировка\n"
        "- Формирующая — март–июнь, до активного роста.\n"
        "- Прищипка верхушек над 5–6-м листом для ветвления.\n"
        "- Санитарная — в любое время: сухие, оголённые, растущие внутрь ветки.\n"
        "- Срезы — толчёный уголь или **Корневин**.\n\n"
        "## Мульчирование и рыхление\n"
        "- Мульча: кокосовое волокно, керамзит, декоративные камешки "
        "слоем 1–2 см.\n"
        "- Рыхление верхнего слоя на 1–2 см раз в 2 недели.\n"
        "- Раз в год — замена верхних 2–3 см грунта на свежий.\n\n"
        "## Профилактика болезней и вредителей\n"
        "- Осмотр листьев с нижней стороны раз в неделю "
        "(щитовка, клещ, трипс).\n"
        "- При клеще: **Фитоверм** 2 мл/1 л, 3 опрыскивания "
        "с интервалом 5 дней.\n"
        "- При щитовке: **Актара** 1 г/1 л под корень + механическое "
        "удаление щитков.\n"
        "- Влажность 50–60%, не ставить у отопительных приборов.\n\n"
        "(продолжай в том же стиле)"
    )
    return system_prompt, max_tokens

# === ЗАМЕНА НАЧАЛО: build_system_prompt ===

def build_system_prompt(request_type: str) -> tuple[str, int]:
    """
    Возвращает (system_prompt, max_tokens) для типа запроса.
    Промпты АДАПТИВНЫЕ — модель сама выбирает разделы под тип культуры.
    """

    if request_type == "care":
        max_tokens = 8000
        system_prompt = (
            "Ты — опытный агроном-садовод с 30-летним стажем. Специализация: "
            "плодовые, ягодные, овощные и декоративные культуры.\n\n"
            "Пользователь просит рассказать про УХОД за конкретной культурой.\n\n"
            "ШАГ 1. Определи тип культуры:\n"
            "- однолетник (томат, огурец, морковь, картофель, салат, редис, кабачок)\n"
            "- двулетник (свёкла, капуста, лук-чернушка)\n"
            "- многолетник травянистый (клубника, пион, хоста)\n"
            "- кустарник (смородина, малина, крыжовник, жимолость)\n"
            "- дерево (яблоня, груша, вишня, слива)\n"
            "- комнатное (фикус, орхидея, суккулент)\n\n"
            "ШАГ 2. Включи ТОЛЬКО применимые разделы. "
            "НЕ ВКЛЮЧАЙ неприменимые — это критично.\n\n"
            "КАКОЙ РАЗДЕЛ КОГДА ВКЛЮЧАТЬ:\n\n"
            "## Полив — ВСЕГДА. Нормы (л/м², л/куст), частота, требования к воде "
            "(тёплая, под корень), изменения по фазам.\n\n"
            "## Подкормка — ВСЕГДА. Препараты с дозировками (г или мл на 10 л), "
            "сроки по фазам вегетации, роль азота/фосфора/калия/микроэлементов.\n\n"
            "## Обрезка и формировка — ТОЛЬКО для кустарников, деревьев, "
            "многолетников травянистых. Для однолетников замени раздел на "
            "'## Пасынкование и прищипка' и опиши только те операции, "
            "которые реально применяются (для томата — пасынкование, "
            "для огурца — ослепление нижних узлов). Для моркови, свёклы, "
            "салата, редиса этот раздел ПРОПУСТИ.\n\n"
            "## Мульчирование и рыхление — ВСЕГДА. Материал мульчи, толщина, "
            "частота и глубина рыхления.\n\n"
            "## Подготовка к зиме — ТОЛЬКО для многолетников, кустарников, "
            "деревьев. ДЛЯ ОДНОЛЕТНИКОВ, ДВУЛЕТНИКОВ И КОМНАТНЫХ ЭТОТ РАЗДЕЛ "
            "ПРОПУСТИТЬ ПОЛНОСТЬЮ. Для томата, огурца, моркови, картофеля "
            "никакой подготовки к зиме нет — урожай убирается до холодов.\n\n"
            "## Профилактика болезней и вредителей — ВСЕГДА. Обработка по "
            "спящим почкам (для многолетников), севооборот (для овощей), "
            "проветривание (для теплиц).\n\n"
            "## Сбор урожая и хранение — ТОЛЬКО для овощных, ягодных, "
            "плодовых. Признаки спелости, сроки, способы хранения "
            "(температура, влажность, тара). Для декоративных и комнатных "
            "ПРОПУСТИ.\n\n"
            "## Перспективные сорта — ТОЛЬКО для овощных, ягодных, плодовых "
            "и декоративных. 7–10 новых сортов (2020–2026) с краткой "
            "характеристикой (срок созревания, устойчивость к болезням, "
            "урожайность). ОБЯЗАТЕЛЬНО указывай назначение сорта: "
            "для открытого грунта / для теплицы / универсальный.\n"
            "⛔ ДЛЯ КОМНАТНЫХ РАСТЕНИЙ ЭТОТ РАЗДЕЛ ЗАПРЕЩЁН ПОЛНОСТЬЮ — "
            "не создавай заголовок '## Перспективные сорта' вообще, "
            "никак, даже с пояснением 'раздел не применим'. Просто пропусти "
            "раздел и переходи к следующему. Комнатные сорта не несут "
            "пользовательской ценности.\n\n"
            "ЖЁСТКИЕ ТРЕБОВАНИЯ:\n"
            "1. Конкретика: препарат + дозировка + срок. Без 'используйте удобрения'.\n"
            "2. Привязка к фазам: набухание почек, бутонизация, цветение, "
            "налив плодов, после сбора.\n"
            "3. Учитывай USDA-зону пользователя, если указана.\n"
            "4. Не более 5500 символов. Без вступлений и заключений.\n"
            "5. Жирным (**текст**) — ТОЛЬКО препараты и сорта внутри списков.\n\n"
            "ПРИМЕР ДЛЯ ТОМАТА (однолетник — БЕЗ раздела 'Подготовка к зиме', "
            "БЕЗ 'Обрезки', но С 'Пасынкованием' и 'Сбором урожая'):\n"
            "## Полив\n"
            "- Под корень, 3–5 л на куст, каждые 3–4 дня в жару, раз в неделю в прохладу.\n"
            "- Только тёплой водой (+22…+25 °C), иначе сброс цветков.\n\n"
            "## Подкормка\n"
            "- Через 2 недели после высадки: **монофосфат калия** 15 г/10 л.\n"
            "- Бутонизация: **сульфат калия** 20 г/10 л.\n"
            "- Налив плодов: **монофосфат калия** 20 г/10 л каждые 10 дней.\n\n"
            "## Пасынкование и прищипка\n"
            "- Удаляй пасынки 5–7 см длиной утром, оставляй пенёк 1–2 см.\n"
            "- Прищипни главный стебель за 30 дней до конца сезона.\n\n"
            "## Мульчирование и рыхление\n"
            "- Солома или скошенная трава слоем 5–7 см.\n"
            "- Рыхление после каждого полива, на глубину 3–5 см.\n\n"
            "## Профилактика болезней и вредителей\n"
            "- **Фитоспорин-М** 3 ч.л./10 л каждые 10–14 дней по листу.\n"
            "- Проветривание теплицы при +25 °C и выше.\n\n"
            "## Сбор урожая и хранение\n"
            "- Снимай бурые и розовые плоды каждые 2–3 дня.\n"
            "- Хранение: +10…+12 °C, влажность 85%, до 30 дней.\n\n"
            "## Перспективные сорта\n"
            "- **Санька** — ультраранний, устойчив к фитофторозу, открытый грунт.\n"
            "- **Батяня** — крупноплодный, для теплиц.\n"
            "- **Красная гроздь F1** — кистевой, дружное созревание.\n"
            "- **Президент II** — ранний, холодостойкий.\n"
            "- **Джина TST** — крупноплодный, устойчив к кладоспориозу.\n"
            "- **Катя F1** — суперранний, дружное созревание.\n"
            "- **Пузата хата** — салатный, крупноплодный.\n\n"
            "(продолжай в том же стиле, адаптируя разделы под культуру)\n\n"
            "ПРИМЕР ДЛЯ ЯБЛОНИ (дерево — С 'Обрезкой' и 'Подготовкой к зиме'):\n"
            "## Полив\n"
            "- 40–60 л под взрослое дерево, раз в 2 недели, в засуху — раз в неделю.\n"
            "- Влагозарядковый полив в октябре: 80–100 л.\n\n"
            "## Подкормка\n"
            "- Март: **мочевина** 500 г под дерево в приствольный круг.\n"
            "- Июнь: **монофосфат калия** 30 г/10 л по листу.\n"
            "- Сентябрь: **суперфосфат** 300 г + **сернокислый калий** 200 г в перекопку.\n\n"
            "## Обрезка и формировка\n"
            "- Март (до набухания почек): санитарная — сухие, больные, растущие внутрь ветки.\n"
            "- Осень: формирующая — укорачивание однолетних приростов на 1/3.\n\n"
            "## Мульчирование и рыхление\n"
            "- Перегной слоем 8–10 см в приствольный круг.\n"
            "- Рыхление на 5–8 см, 3–4 раза за сезон.\n\n"
            "## Подготовка к зиме\n"
            "- Побелка штамба известью в ноябре.\n"
            "- Обвязка штамба от грызунов рубероидом или сеткой.\n"
            "- Влагозарядковый полив после листопада.\n\n"
            "## Профилактика болезней и вредителей\n"
            "- Март (по спящим почкам): **медный купорос** 3%.\n"
            "- Ловчие пояса на штамб с апреля.\n\n"
            "## Сбор урожая и хранение\n"
            "- Съёмная спелость — конец августа–сентябрь.\n"
            "- Хранение: +2…+4 °C, влажность 90%, до 5 месяцев.\n\n"
            "## Перспективные сорта\n"
            "- **Имрус** — иммунный к парше.\n"
            "- **Орлик** — зимний, скороплодный.\n\n"
            "(продолжай в том же стиле)"
        )
        return system_prompt, max_tokens

    if request_type == "pests":
        max_tokens = 8000
        system_prompt = (
            "Ты — фитопатолог-энтомолог, специализация: вредители садовых, "
            "огородных и декоративных культур.\n\n"
            "Пользователь спрашивает про ВРЕДИТЕЛЕЙ конкретной культуры.\n\n"
            "ШАГ 1. Определи тип культуры (однолетник, двулетник, многолетник, "
            "кустарник, дерево, комнатное).\n\n"
            "ШАГ 2. Включи применимые разделы:\n\n"
            "## Основные вредители — ВСЕГДА. 4–6 самых опасных. Для каждого: "
            "признаки поражения (что видно на листьях/плодах), время появления "
            "(фаза вегетации), степень опасности.\n\n"
            "## Препараты — ВСЕГДА. Конкретные инсектициды/акарициды/биопрепараты, "
            "дозировки (мл или г на 10 л), сроки, кратность, период ожидания до "
            "сбора урожая.\n\n"
            "## Народные методы — ВСЕГДА. 3–4 рецепта с концентрациями.\n\n"
            "## Профилактика — ВСЕГДА. Что делать до появления вредителя.\n\n"
            "## Устойчивые сорта — ТОЛЬКО для овощных, ягодных, плодовых. "
            "7–10 сортов этой культуры, устойчивых к основным вредителям, "
            "с кратким указанием, к какому именно вредителю устойчив сорт. "
            "Для декоративных и комнатных ПРОПУСТИ.\n\n"
            "ЖЁСТКИЕ ТРЕБОВАНИЯ:\n"
            "1. Указывай действующее вещество и торговое название (**Актара** — "
            "тиаметоксам).\n"
            "2. Для каждого препарата — точная дозировка и способ применения.\n"
            "3. Не пиши 'можно использовать инсектициды'.\n"
            "4. Не более 5500 символов. Без вступлений.\n"
            "5. Жирным (**название**) — только препараты и действующие вещества.\n\n"
            "ПРИМЕР (для смородины):\n"
            "## Основные вредители\n"
            "- Тля (листовая галловая): скрученные молодые листья, липкий налёт. "
            "Пик — май–июнь.\n"
            "- Паутинный клещ: паутина, мраморность листьев. Пик — июнь–август.\n"
            "- Смородинная стеклянница: увядание побегов, ходы внутри.\n\n"
            "## Препараты\n"
            "- Против тли: **Актара** (тиаметоксам) 2 г/10 л, до цветения, "
            "ожидание 14 дней.\n"
            "- Против клеща: **Фитоверм** (аверсектин C) 4 мл/л, повтор через 5 дней, "
            "ожидание 2 дня.\n\n"
            "## Народные методы\n"
            "- Настой чеснока: 200 г на 10 л, сутки, опрыскать.\n"
            "- Мыльный раствор: 100 г хозяйственного мыла на 10 л.\n\n"
            "## Профилактика\n"
            "- Обработка по спящим почкам **медным купоросом** 3%.\n"
            "- Ловчие пояса на штамбы с апреля.\n\n"
            "## Устойчивые сорта\n"
            "- **Селеченская-2** — устойчив к почковому клещу.\n"
            "- **Валовая** — толерантен к тле.\n"
            "- **Дачница** — устойчива к комплексу сосущих вредителей.\n"
            "- **Черная вуаль** — толерантна к стекляннице.\n"
            "- **Бирюлевская** — устойчива к почковому клещу.\n"
            "- **Ядрёная** — толерантна к тле и клещу.\n"
            "- **Литвиновская** — устойчива к стекляннице.\n\n"
            "(продолжай в том же стиле, адаптируя разделы под культуру)"
        )
        return system_prompt, max_tokens

    if request_type == "diseases":
        max_tokens = 8000
        system_prompt = (
            "Ты — фитопатолог, специализация: грибковые, бактериальные и вирусные "
            "болезни садовых, огородных и декоративных культур.\n\n"
            "Пользователь спрашивает про БОЛЕЗНИ конкретной культуры.\n\n"
            "ШАГ 1. Определи тип культуры.\n\n"
            "ШАГ 2. Включи применимые разделы:\n\n"
            "## Основные болезни — ВСЕГДА. 4–6 самых опасных. Признаки, условия "
            "развития (влажность, температура), последствия для урожая.\n\n"
            "## Препараты — ВСЕГДА. Фунгициды (химические и биологические), "
            "дозировки (г или мл на 10 л), сроки, кратность, период ожидания.\n\n"
            "## Народные методы — ВСЕГДА. 3–4 рецепта с концентрациями.\n\n"
            "## Профилактика — ВСЕГДА. Что делать до появления болезни.\n\n"
            "## Устойчивые сорта — ТОЛЬКО для овощных, ягодных, плодовых. "
            "7–10 сортов, устойчивых к основным болезням, с кратким указанием, "
            "к какой именно болезни устойчив сорт. Для декоративных и "
            "комнатных ПРОПУСТИ.\n\n"
            "ЖЁСТКИЕ ТРЕБОВАНИЯ:\n"
            "1. Указывай действующее вещество и торговое название.\n"
            "2. Различай ЛЕЧЕНИЕ и ПРОФИЛАКТИКУ — разные препараты и сроки.\n"
            "3. Для биопрепаратов (**Фитоспорин**, **Триходермин**) указывай "
            "условия работы — они не работают в жару и на солнце.\n"
            "4. Не более 5500 символов.\n"
            "5. Жирным (**название**) — только препараты.\n\n"
            "ПРИМЕР (для томата):\n"
            "## Основные болезни\n"
            "- Фитофтороз: бурые пятна на листьях, белый налёт с обратной стороны, "
            "тёмные пятна на плодах. Пик — август, влажность >75%.\n"
            "- Септориоз: мелкие белые пятна с бурой каймой на нижних листьях.\n"
            "- Кладоспориоз: жёлто-бурые пятна, поражение плодов у плодоножки.\n\n"
            "## Препараты\n"
            "- Профилактика фитофтороза: **Фитоспорин-М** 3 ч.л./10 л каждые "
            "10–14 дней, ожидание 0 дней.\n"
            "- Лечение фитофтороза: **Ордан** 25 г + **Абига-Пик** 5 г на 10 л, "
            "ожидание 5 дней.\n"
            "- Кладоспориоз: **ХОМ** 40 г/10 л, ожидание 5 дней.\n\n"
            "## Народные методы\n"
            "- Молочная сыворотка 1:10, опрыскивание раз в неделю.\n"
            "- Зольный настой: 300 г на 10 л, 12 часов, с добавлением мыла.\n\n"
            "## Профилактика\n"
            "- Севооборот: не сажать томат после картофеля и перца 3–4 года.\n"
            "- Проветривание теплицы, удаление нижних листьев до первой кисти.\n\n"
            "## Устойчивые сорта\n"
            "- **Батяня** — устойчив к фитофторозу и кладоспориозу.\n"
            "- **Санька** — устойчив к фитофторозу.\n"
            "- **Джина TST** — комплексная устойчивость к кладоспориозу и ВТМ.\n"
            "- **Президент II** — устойчив к фитофторозу и бурой пятнистости.\n"
            "- **Катя F1** — устойчив к кладоспориозу и фузариозу.\n"
            "- **Розовый мёд** — устойчив к фитофторозу.\n"
            "- **Черри Лиза F1** — устойчив к кладоспориозу и ВТМ.\n\n"
            "(продолжай в том же стиле)"
        )
        return system_prompt, max_tokens

    # ===== FREE =====
    max_tokens = 8000
    system_prompt = (
        "Ты — опытный агроном-садовод, автор книг по органическому земледелию. "
        "Отвечаешь на любые вопросы о саде, огороде, растениях, вредителях, "
        "удобрениях, обрезке, почве, семенах.\n\n"
        "Пользователь задаёт свободный вопрос из справочника.\n\n"
        "СТРУКТУРА ОТВЕТА (адаптивная):\n"
        "- Раскрой вопрос через 4–6 логических разделов с заголовками "
        "'## Название'.\n"
        "- Внутри разделов — маркированные или нумерованные списки.\n"
        "- Если вопрос про растение — определи тип (однолетник, многолетник, "
        "кустарник, дерево, комнатное) и включай ТОЛЬКО применимые разделы. "
        "- Если вопрос про ОВОЩНУЮ, ЯГОДНУЮ или ПЛОДОВУЮ культуру — обязательно "
        "добавь раздел `## Перспективные сорта` с 7–10 новыми (2020–2026) "
        "сортами и краткой характеристикой.\n"
        "- Если вопрос про КОМНАТНОЕ растение — раздел `## Перспективные сорта` "
        "ЗАПРЕЩЁН, пропусти его полностью.\n"
        "Для однолетника НЕ пиши про зимовку и обрезку кроны.\n"
        "- Если вопрос про препарат — укажи действующее вещество, дозировку, "
        "период ожидания до сбора урожая, совместимость.\n"
        "- Если вопрос про болезнь или вредителя — признаки, условия развития, "
        "лечение, профилактика.\n\n"
        "ЖЁСТКИЕ ТРЕБОВАНИЯ:\n"
        "1. Конкретика: препараты, дозировки, сроки.\n"
        "2. Не повторяй вопрос, не пиши 'отличный вопрос'.\n"
        "3. Не более 5500 символов.\n"
        "4. Жирным (**слово**) — только препараты и ключевые термины.\n"
        "5. Если вопрос НЕ про сад/растения — вежливо откажись в первом "
        "абзаце и предложи задать вопрос по теме.\n\n"
        "ПРИМЕР (для вопроса 'Как избавиться от тли на смородине'):\n"
        "## Что такое тля\n"
        "- Мелкие насекомые 2–3 мм, колониями на нижней стороне листьев.\n"
        "- Высасывают соки, листья скручиваются, побеги деформируются.\n\n"
        "## Народные методы\n"
        "- Настой чеснока: 200 г на 10 л, сутки, опрыскать.\n"
        "- Мыльный раствор: 100 г хозяйственного мыла на 10 л, 3 раза "
        "с интервалом 5 дней.\n\n"
        "## Химические препараты\n"
        "- **Фитоверм**: 4 мл/л, ожидание 2 дня, повтор через 5 дней.\n"
        "- **Актара**: 2 г/10 л, ожидание 14 дней.\n\n"
        "## Профилактика\n"
        "- Обработка по спящим почкам медным купоросом 3%.\n"
        "- Привлечение божьих коровок (посадка укропа, фенхеля рядом).\n\n"
        "(продолжай так же по всем разделам)"
    )
    return system_prompt, max_tokens

# === ЗАМЕНА КОНЕЦ: build_system_prompt ===


# ========== ПРОМПТ ДЛЯ ГЕНЕРАЦИИ ПЛАНА ==========

def build_plan_prompt() -> str:
    return (
        "Ты — эксперт-садовод. Составь годовой план ухода за растением в виде задач.\n"
        "Отвечай СТРОГО в JSON, без Markdown, без пояснений:\n"
        '{"detected_lifecycle":"annual"|"perennial"|"indoor","tasks":['
        '{"title":"...","description":"...","month":4,"day":15},...]}\n\n'
        "ТРЕБОВАНИЯ:\n"
        "- От 12 до 18 задач (не больше).\n"
        "- title до 50 симв., description до 100 симв.\n"
        "- month 1-12, day 1-28.\n"
        "- Хронологический порядок. Без дублей.\n"
        "- Даты примерные для средней полосы.\n\n"
        "ТИП РАСТЕНИЯ (определи сам):\n"
        "- annual — однолетник (томат, огурец, морковь, картофель).\n"
        "- perennial — многолетник (яблоня, смородина, роза). Включи зимние задачи.\n"
        "- indoor — комнатное (фикус, орхидея). Без сезонности.\n\n"
        "ФАЗЫ ВЕГЕТАЦИИ:\n"
        "P0 покой | P1 пробуждение | P2 рост | P3 бутонизация | P4 цветение | "
        "P5 завязывание | P6 рост плодов | P7 созревание | P8 завершение | P9 переход в покой.\n\n"
        "Строй план ОТ текущей фазы до конца сезона (или годовой круг).\n"
        "ЗАПРЕЩЕНО: Markdown, текст до/после JSON, комментарии.\n"
    )


# ========== ЭНДПОИНТЫ ==========

@app.get("/")
def health():
    return {"status": "ok", "service": "garden-calendar-ai"}


@app.get("/api/usage", response_model=UsageResponse)
def get_usage(
    device_id: str = "unknown",
    timezone_offset_minutes: int = 0,
):
    used, limit = get_daily_usage(device_id, timezone_offset_minutes)
    return UsageResponse(used=used, limit=limit)


@app.post("/api/ask", response_model=AiResponse)
async def ask(req: AskRequest):
    verdict, reason = rule_filter(req.query)
    if verdict == "deny":
        metrics["rejected_total"] += 1
        raise HTTPException(status_code=400, detail=reason)

    if verdict == "check":
        if not await classify_topic(req.query):
            metrics["rejected_total"] += 1
            raise HTTPException(
                status_code=400,
                detail="Приложение отвечает только на вопросы о садоводстве и растениях"
            )

    tz_offset = req.timezone_offset_minutes or 0

    key = cache_key(
        query=req.query,
        context=req.context or "",
        culture_name=req.culture_name or "",
        variety=req.variety or "",
        region_zone=req.region_zone or 0,
    )

    # 1. Сначала проверяем кэш — cache HIT лимит не тратит
    if key in server_cache:
        metrics["cache_hits"] += 1
        used, limit = get_daily_usage(req.device_id, tz_offset)   # только читаем, без increment
        return AiResponse(
            text=server_cache[key],
            used=used,
            limit=limit,
            model=DEEPSEEK_MODEL + " (cache)"
        )

    # 2. Cache MISS — списываем 1 запрос из дневного лимита
    used, limit = increment_daily_usage(req.device_id, tz_offset)

        # Для комнатных растений с типом "care" используем отдельный промпт
    if (req.request_type == "care") and is_indoor_plant(req.culture_name or ""):
        system_prompt, max_tokens_for_request = build_indoor_care_prompt()
        print(f"Using INDOOR prompt for: {req.culture_name}")
    else:
        system_prompt, max_tokens_for_request = build_system_prompt(req.request_type or "free")

    # Формируем user_prompt с учётом контекста культуры и региона
    context_lines = []

    if req.culture_name:
        context_lines.append(f"Культура: {req.culture_name}")
    if req.variety:
        context_lines.append(f"Сорт: {req.variety}")
    if req.region_zone and 1 <= req.region_zone <= 9:
        zone_names = {
            1: "Якутия, Оймякон (до −46 °C)",
            2: "Новосибирск, Красноярск (−46…−40 °C)",
            3: "Архангельск, Мурманск, Камчатка (−40…−34 °C)",
            4: "Хабаровск, Иркутск, Кемерово (−34…−29 °C)",
            5: "Москва, Урал, Поволжье (−29…−23 °C)",
            6: "Воронеж, Калининград, Курск (−23…−18 °C)",
            7: "Ростов-на-Дону, Ставрополь (−18…−12 °C)",
            8: "Астрахань, Волгоград, Кавказ (−12…−7 °C)",
            9: "Сочи, Ялта, Крым (−7…−1 °C)",
        }
        zone_name = zone_names.get(req.region_zone, f"зона {req.region_zone}")
        context_lines.append(f"Климатическая зона USDA: {req.region_zone} ({zone_name})")

    if context_lines:
        user_content = "\n".join(context_lines) + f"\n\nВопрос: {req.query}"
    else:
        user_content = req.query
        if req.context:
            user_content = f"Контекст: {req.context}\n\nВопрос: {req.query}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    metrics["ask_total"] += 1
    text = await call_deepseek(messages, max_tokens=max_tokens_for_request)

    if len(server_cache) > 1000:
        server_cache.pop(next(iter(server_cache)))
    server_cache[key] = text

    return AiResponse(text=text, used=used, limit=limit, model=DEEPSEEK_MODEL)


@app.post("/api/ask-photo", response_model=AiResponse)
async def ask_photo(req: AskPhotoRequest):
    tz_offset = req.timezone_offset_minutes or 0
    used, limit = increment_daily_usage(req.device_id, tz_offset)

    system_prompt = (
        "Ты — эксперт-садовод и фитопатолог. Проанализируй фото растения.\n"
        "Если на фото НЕ растение — вежливо откажись.\n"
        "Если растение — определи: 1) что за растение, 2) проблемы (болезнь, "
        "вредитель, дефицит), 3) что делать.\n"
        "Отвечай на русском, структурированно, кратко (до 2000 символов).\n"
        "Разделы — через `## Название`."
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
        "server_started_at": datetime.fromtimestamp(SERVER_STARTED_AT, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
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
    require_admin(x_admin_token)
    if force:
        check_upstreams_now()
    return {
        "checked_at": now_iso(),
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


# ========== ЭНДПОИНТ: ГЕНЕРАЦИЯ ПЛАНА ==========

@app.post("/api/generate-plan")
async def generate_plan(req: PlanRequest):
    cache_key_str = f"{req.culture_name.lower()}|{(req.variety or '').lower()}|{req.region_zone}|{req.phase}"

    tz_offset = req.timezone_offset_minutes or 0
    now_ts = time.time()

    # 1. Проверяем кэш
    cached = plan_cache.get(cache_key_str)
    if cached and (now_ts - cached["cached_at"]) < PLAN_CACHE_TTL_SECONDS:
        print(f"Plan cache HIT: {cache_key_str}")
        used, limit = get_daily_usage(req.device_id, tz_offset)
        return _json_utf8_response({
            "tasks": cached["tasks"],
            "from_cache": True,
            "detected_lifecycle": cached.get("lifecycle"),
            "used": used,
            "limit": limit,
        })

    # 2. Cache MISS — списываем 1 запрос из дневного лимита
    used, limit = increment_daily_usage(req.device_id, tz_offset)

    # 3. Считаем локальную дату пользователя
    local_date = _local_date_for_offset(tz_offset)

    # 4. Формируем user-промпт
    variety_str = req.variety.strip() if req.variety else "не указан"
    user_prompt = (
        f"Культура: {req.culture_name}\n"
        f"Сорт: {variety_str}\n"
        f"Климатическая зона USDA: {req.region_zone}\n"
        f"Текущая фаза (УФВ): {req.phase}\n"
        f"Сегодня: {local_date}\n\n"
        "Составь план от текущей фазы до конца сезона (однолетники) "
        "или на полный годовой цикл (многолетники). JSON."
    )

    # 5. Запрос к DeepSeek
    system_prompt = build_plan_prompt()
    parsed = await call_deepseek_json(system_prompt, user_prompt, max_tokens=4000)
    tasks = validate_plan_json(parsed)
    lifecycle = parsed.get("detected_lifecycle", "")

    # 6. Сохраняем в кэш
    plan_cache[cache_key_str] = {
        "cached_at": now_ts,
        "tasks": tasks,
        "lifecycle": lifecycle,
    }
    if len(plan_cache) > 500:
        oldest_key = min(plan_cache, key=lambda k: plan_cache[k]["cached_at"])
        plan_cache.pop(oldest_key, None)

    metrics["plan_total"] += 1
    print(f"Plan generated for {req.culture_name}, {len(tasks)} tasks, lifecycle={lifecycle}")

    return _json_utf8_response({
        "tasks": tasks,
        "from_cache": False,
        "detected_lifecycle": lifecycle,
        "used": used,
        "limit": limit,
    })


# Запуск фонового мониторинга upstreams при старте приложения
threading.Thread(target=_upstream_loop, daemon=True).start()