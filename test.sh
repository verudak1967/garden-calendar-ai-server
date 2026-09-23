#!/bin/bash
# test.sh — тестирование AI-сервера. Запуск: ./test.sh [ask|photo|plan|usage|all]

BASE_URL="https://garden-calendar-ai-server.onrender.com"
DEVICE_ID="cli-test-$(date +%s)"

# Цвета для вывода
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Проверка наличия python
if ! command -v python &> /dev/null; then
    echo -e "${RED}✗ Python не найден. Установи Python и повтори.${NC}"
    exit 1
fi

# Функция: создаёт JSON с кириллицей через Python (гарантированный UTF-8)
make_json() {
    local file="$1"
    local payload="$2"
    python -c "
import json, sys
payload = json.loads('''$payload''')
with open('$file', 'w', encoding='utf-8') as f:
    json.dump(payload, f, ensure_ascii=False)
"
}

# Функция: отправляет запрос и красиво выводит ответ
send_request() {
    local method="$1"   # POST или GET
    local path="$2"     # /api/ask и т.д.
    local body_file="$3"  # файл с телом, или пусто для GET
    local desc="$4"

    echo ""
    echo -e "${YELLOW}═══════════════════════════════════════════${NC}"
    echo -e "${YELLOW}→ $desc${NC}"
    echo -e "${YELLOW}  $method $BASE_URL$path${NC}"
    echo -e "${YELLOW}═══════════════════════════════════════════${NC}"

    if [ -n "$body_file" ]; then
        HTTP_CODE=$(curl -s -o response.json -w "%{http_code}" \
            -X "$method" "$BASE_URL$path" \
            -H "Content-Type: application/json; charset=utf-8" \
            -d @"$body_file")
    else
        HTTP_CODE=$(curl -s -o response.json -w "%{http_code}" \
            -X "$method" "$BASE_URL$path")
    fi

    if [ "$HTTP_CODE" = "200" ]; then
        echo -e "${GREEN}✓ HTTP $HTTP_CODE${NC}"
    else
        echo -e "${RED}✗ HTTP $HTTP_CODE${NC}"
    fi

    # Красивый вывод через python
    python -c "
import json
try:
    with open('response.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
except Exception:
    with open('response.json', 'r', encoding='utf-8', errors='replace') as f:
        print(f.read()[:2000])
    raise SystemExit

if isinstance(data, dict) and 'text' in data:
    print('--- TEXT ---')
    print(data['text'])
    print()
    print('--- META ---')
    meta = {k: v for k, v in data.items() if k != 'text'}
    print(json.dumps(meta, ensure_ascii=False, indent=2))
elif isinstance(data, dict) and 'tasks' in data:
    print('--- TASKS ---')
    for t in data['tasks']:
        print(f\"{t.get('month',0):02d}-{t.get('day',0):02d}  {t.get('title','')}\")
        print(f\"           {t.get('description','')}\")
    print()
    print('--- META ---')
    meta = {k: v for k, v in data.items() if k != 'tasks'}
    print(json.dumps(meta, ensure_ascii=False, indent=2))
else:
    print(json.dumps(data, ensure_ascii=False, indent=2))
"
}

# === Тест 1: /api/ask (текст) ===
test_ask() {
    make_json request.json '{"query":"Как избавиться от тли на смородине?","request_type":"pests","device_id":"'"$DEVICE_ID"'","timezone_offset_minutes":180,"culture_name":"Смородина","region_zone":5}'
    send_request POST /api/ask request.json "Текстовый запрос (pests)"
}

# === Тест 2: /api/ask (уход за однолетником) ===
test_ask_care() {
    make_json request.json '{"query":"Расскажи про уход за культурой: Томат","request_type":"care","device_id":"'"$DEVICE_ID"'","timezone_offset_minutes":180,"culture_name":"Томат","region_zone":5}'
    send_request POST /api/ask request.json "Уход за томатом (должен быть без 'зимовки')"
}

# === Тест 3: /api/ask-photo ===
test_photo() {
    # Минимальное валидное JPEG-изображение (1x1 пиксель)
    make_json request.json '{"image_base64":"/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q==","context":"болезни смородины","device_id":"'"$DEVICE_ID"'","timezone_offset_minutes":180}'
    send_request POST /api/ask-photo request.json "Анализ фото (vision через OpenRouter)"
}

# === Тест 4: /api/generate-plan ===
test_plan() {
    make_json request.json '{"culture_name":"Томат","variety":"Санька","region_zone":5,"phase":"P2","device_id":"'"$DEVICE_ID"'","timezone_offset_minutes":180}'
    send_request POST /api/generate-plan request.json "Генерация плана для томата"
}

# === Тест 5: /api/usage ===
test_usage() {
    send_request GET "/api/usage?device_id=$DEVICE_ID&timezone_offset_minutes=180" "" "Счётчик лимитов"
}

# === Тест: яблоня (дерево — должны быть обрезка и подготовка к зиме) ===
test_ask_apple() {
    make_json request.json '{"query":"Расскажи про уход за культурой: Яблоня","request_type":"care","device_id":"'"$DEVICE_ID"'","timezone_offset_minutes":180,"culture_name":"Яблоня","variety":"Антоновка","region_zone":5}'
    send_request POST /api/ask request.json "Уход за яблоней (дерево — нужны обрезка и зимовка)"
}

# === Тест: фикус (комнатное — без зимовки, без сортов) ===
test_ask_ficus() {
    make_json request.json '{"query":"Расскажи про уход за культурой: Фикус Бенджамина","request_type":"care","device_id":"'"$DEVICE_ID"'","timezone_offset_minutes":180,"culture_name":"Фикус Бенджамина","region_zone":5}'
    send_request POST /api/ask request.json "Уход за фикусом (комнатное — БЕЗ зимовки и сортов)"
}

# === Тест: огурец, болезни (проверяем diseases + однолетник) ===
test_ask_cucumber() {
    make_json request.json '{"query":"Какие болезни бывают у культуры: Огурец? Как лечить?","request_type":"diseases","device_id":"'"$DEVICE_ID"'","timezone_offset_minutes":180,"culture_name":"Огурец","region_zone":5}'
    send_request POST /api/ask request.json "Болезни огурца (однолетник, diseases)"
}

# === Тест: томат, болезни (проверяем diseases + устойчивые сорта) ===
test_ask_tomato_diseases() {
    make_json request.json '{"query":"Какие болезни бывают у культуры: Томат? Как лечить?","request_type":"diseases","device_id":"'"$DEVICE_ID"'","timezone_offset_minutes":180,"culture_name":"Томат","region_zone":5}'
    send_request POST /api/ask request.json "Болезни томата (diseases)"
}

# === Точка входа ===
case "${1:-all}" in
    ask)      test_ask ;;
    care)     test_ask_care ;;
    photo)    test_photo ;;
    plan)     test_plan ;;
    usage)    test_usage ;;
    # НОВЫЕ тесты для проверки адаптивности промптов:
    apple)    test_ask_apple ;;
    ficus)    test_ask_ficus ;;
    cucumber) test_ask_cucumber ;;
    tomato-dis) test_ask_tomato_diseases ;;
    all)
        test_ask
        test_ask_care
        test_plan
        test_usage
        ;;
    adaptive)
        echo ">>> Проверка адаптивности промптов (4 типа культур) <<<"
        test_ask_care           # томат — однолетник
        test_ask_apple          # яблоня — дерево
        test_ask_ficus          # фикус — комнатное
        test_ask_cucumber       # огурец — болезни
        ;;
    *)
        echo "Использование: $0 [ask|care|photo|plan|usage|apple|ficus|cucumber|tomato-dis|adaptive|all]"
        exit 1
        ;;
esac

# Уборка
rm -f request.json response.json

echo ""
echo -e "${GREEN}✓ Готово.${NC}"