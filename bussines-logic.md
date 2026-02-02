# Обновлённый план: Асинхронный скрипт анализа iGaming компаний

## Контекст задачи

Обработка ~1800 iGaming компаний через Claude API с web search. Двухэтапный анализ: квалификация (Pass/Fail) → детальный профиль (только для прошедших). Результаты в JSON/CSV.

---

## Архитектура проекта

```
igaming_analyzer/
├── main.py                 # Точка входа
├── config.py               # Конфигурация
├── processor.py            # Обработка одной компании
├── rate_limiter.py         # Rate limiter для web search
├── file_utils.py           # Атомарная запись, санитизация
├── aggregator.py           # Сборка итоговых файлов
├── prompts/
│   └── system_prompt.txt   # Системный промпт (для кеширования)
└── data/
    ├──input
    │   └── companies.csv       # Входной датасет
    ├── raw/                    # JSON по каждой компании
    │   ├── _index.json         # Маппинг имён → файлов
    │   └── _errors.json        # Ошибки обработки
    └── output/
        ├── qualified.json      # Только прошедшие квалификацию
        ├── qualified.csv       # CSV версия
        ├── disqualified.json   # Не прошедшие (FAIL)
        └── full_results.json   # Все результаты
```

---

## Ключевые особенности промпта

### Лимит Web Search
```
Промпт указывает: "Maximum 10 web searches total per company"
```
Это снижает общую нагрузку и стоимость.

### Двухэтапная логика
```
SECTION A: Qualification (Pass/Fail) — выполняется ВСЕГДА
SECTION B: Profile Data — ТОЛЬКО если overall_qualified = true

Экономия: компании с FAIL не требуют полного исследования (~3-5 searches vs 10)
```

### Входные данные
```
- company_name (required)
- website (optional)
- linkedin_url (optional)  
- additional_context (optional)
```

### Выходной JSON
Фиксированная структура из промпта с полями:
- `qualification` (legal_standing, game_portfolio, business_functions)
- `profile_data` (только если qualified)
- `research_notes`, `data_gaps`

---

## Структура входного CSV

```csv
company_name,website,linkedin_url,headquarters,known_products,notes
"Pragmatic Play","pragmaticplay.com","linkedin.com/company/pragmatic-play","Malta","Slots, Live Casino",""
"Unknown Studio","","","","",""
```

Минимум — только `company_name`. Остальные поля опциональны.

---

## Критические требования

### 1. Rate Limiting

```
Web Search: 30 RPM — главное ограничение
Макс. 10 searches/company → 18,000 searches всего (worst case)
Реалистично: ~5-7 searches/company (FAIL компании меньше)
```

Реализация:
- `AsyncSlidingWindowRateLimiter` с окном 60 сек
- Отслеживать `usage.server_tool_use.web_search_requests` после каждого вызова
- Динамически регулировать concurrency
- Начальный `semaphore(3)`, адаптировать до `semaphore(10)` если searches < 5/company

### 2. Определение прогресса

```
Источник правды: raw_output/_index.json + наличие файлов
```

```python
# При старте
index = load_json("raw_output/_index.json") or {}
processed = set(index.keys())
to_process = [c for c in companies if c["company_name"] not in processed]
```

### 3. Prompt Caching

Структура для эффективного кеширования:

```python
messages = [
    {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": SYSTEM_PROMPT,  # ~2500 токенов, статичный
                "cache_control": {"type": "ephemeral"}
            },
            {
                "type": "text",
                "text": format_company_input(company)  # ~50-100 токенов
            }
        ]
    }
]
```

Системный промпт (~2500 токенов) кешируется, динамическая часть минимальна.

### 4. Формирование запроса к компании

```python
def format_company_input(company: dict) -> str:
    parts = [f"## Company to Analyze\n\n**Company Name:** {company['company_name']}"]
    
    if company.get("website"):
        parts.append(f"**Website:** {company['website']}")
    if company.get("linkedin_url"):
        parts.append(f"**LinkedIn:** {company['linkedin_url']}")
    if company.get("additional_context"):
        parts.append(f"**Additional Context:** {company['additional_context']}")
    
    parts.append("\nPlease conduct the analysis and return the JSON output.")
    return "\n".join(parts)
```

### 5. Валидация ответа

```python
def validate_response(response: dict) -> tuple[bool, list[str]]:
    errors = []
    
    # Обязательные поля верхнего уровня
    required_top = ["company_name", "research_date", "qualification"]
    for field in required_top:
        if field not in response:
            errors.append(f"Missing required field: {field}")
    
    # Проверка qualification структуры
    qual = response.get("qualification", {})
    if "overall_qualified" not in qual:
        errors.append("Missing qualification.overall_qualified")
    
    # Если qualified=True, должен быть profile_data
    if qual.get("overall_qualified") and "profile_data" not in response:
        errors.append("Qualified company missing profile_data")
    
    return len(errors) == 0, errors
```

### 6. Retry с исправлением JSON

```python
# Если Claude вернул невалидный JSON
retry_prompt = """
Your previous response was not valid JSON. Please return ONLY the JSON object, 
no markdown code blocks, no explanations before or after.

Previous error: {error_message}

Return the complete JSON for: {company_name}
"""
```

---

## Конфигурация

```python
# config.py
from dataclasses import dataclass
from pathlib import Path

@dataclass
class Config:
    # API
    model: str = "claude-sonnet-4-5-20250514"
    max_tokens: int = 8192  # Большой output для полного JSON
    timeout: int = 180  # 3 мин, т.к. много web search
    
    # Rate Limiting
    web_search_rpm: int = 30
    initial_concurrency: int = 3
    max_concurrency: int = 10
    
    # Retry
    max_retries: int = 5
    base_delay: float = 2.0
    max_delay: float = 120.0
    
    # Paths
    input_file: Path = Path("data/companies.csv")
    raw_output_dir: Path = Path("raw_output")
    output_dir: Path = Path("output")
    system_prompt_file: Path = Path("prompts/system_prompt.txt")
    
    # Tools
    tools: list = None
    
    def __post_init__(self):
        self.tools = [{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 10  # Соответствует промпту
        }]
```

---

## Структура выходного JSON (на компанию)

```json
{
  "meta": {
    "processed_at": "2025-02-02T10:30:00Z",
    "model": "claude-sonnet-4-5-20250514",
    "processing_time_sec": 45.2,
    "usage": {
      "input_tokens": 3500,
      "output_tokens": 2100,
      "cache_read_tokens": 2400,
      "cache_creation_tokens": 0,
      "web_search_requests": 7
    }
  },
  "input": {
    "company_name": "Pragmatic Play",
    "website": "pragmaticplay.com",
    "linkedin_url": "linkedin.com/company/pragmatic-play"
  },
  "result": {
    // Полный JSON из ответа Claude согласно формату промпта
    "company_name": "Pragmatic Play",
    "website": "pragmaticplay.com",
    "linkedin_url": "...",
    "research_date": "2025-02-02",
    "qualification": {...},
    "profile_data": {...},
    "research_notes": "...",
    "data_gaps": [...]
  }
}
```

---

## Логика processor.py

```python
async def process_company(
    company: dict,
    client: AsyncAnthropic,
    rate_limiter: RateLimiter,
    semaphore: asyncio.Semaphore,
    config: Config,
    system_prompt: str
) -> ProcessingResult:
    
    async with semaphore:
        start_time = time.time()
        
        # 1. Формирование сообщения
        user_content = format_company_input(company)
        
        messages = [{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"}
                },
                {
                    "type": "text",
                    "text": user_content
                }
            ]
        }]
        
        # 2. API вызов с retry
        response = await call_api_with_retry(
            client, messages, config
        )
        
        # 3. Извлечь usage и обновить rate limiter
        usage = response.usage
        web_searches = usage.server_tool_use.get("web_search_requests", 0)
        await rate_limiter.consume(web_searches)
        
        # 4. Парсинг JSON из ответа
        result_json = extract_json_from_response(response)
        
        # 5. Валидация
        is_valid, errors = validate_response(result_json)
        if not is_valid:
            # Retry с просьбой исправить
            result_json = await retry_for_valid_json(
                client, company, errors, config
            )
        
        # 6. Формирование результата
        processing_time = time.time() - start_time
        
        return ProcessingResult(
            success=True,
            company_name=company["company_name"],
            meta={
                "processed_at": datetime.utcnow().isoformat(),
                "model": config.model,
                "processing_time_sec": round(processing_time, 2),
                "usage": {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_tokens": usage.cache_read_input_tokens,
                    "web_search_requests": web_searches
                }
            },
            input=company,
            result=result_json
        )
```

---

## Логика aggregator.py

```python
def aggregate_results(config: Config):
    raw_dir = config.raw_output_dir
    output_dir = config.output_dir
    
    all_results = []
    qualified = []
    disqualified = []
    
    # 1. Загрузить все JSON
    for json_file in raw_dir.glob("*.json"):
        if json_file.name.startswith("_"):
            continue  # Skip _index.json, _errors.json
        
        data = load_json(json_file)
        all_results.append(data)
        
        # Классификация
        is_qualified = data.get("result", {}).get("qualification", {}).get("overall_qualified", False)
        
        if is_qualified:
            qualified.append(data)
        else:
            disqualified.append(data)
    
    # 2. Сохранить JSON файлы
    save_json(output_dir / "full_results.json", all_results)
    save_json(output_dir / "qualified.json", qualified)
    save_json(output_dir / "disqualified.json", disqualified)
    
    # 3. Генерация CSV для qualified
    csv_rows = flatten_for_csv(qualified)
    save_csv(output_dir / "qualified.csv", csv_rows)
    
    # 4. Статистика
    return {
        "total": len(all_results),
        "qualified": len(qualified),
        "disqualified": len(disqualified),
        "qualification_rate": f"{len(qualified)/len(all_results)*100:.1f}%"
    }
```

### Flatten для CSV

```python
def flatten_for_csv(results: list[dict]) -> list[dict]:
    """Преобразует nested JSON в плоскую структуру для CSV"""
    rows = []
    for r in results:
        result = r.get("result", {})
        qual = result.get("qualification", {})
        profile = result.get("profile_data", {})
        
        row = {
            # Meta
            "processed_at": r.get("meta", {}).get("processed_at"),
            "web_searches_used": r.get("meta", {}).get("usage", {}).get("web_search_requests"),
            
            # Basic info
            "company_name": result.get("company_name"),
            "website": result.get("website"),
            "linkedin_url": result.get("linkedin_url"),
            "research_date": result.get("research_date"),
            
            # Qualification
            "legal_status": qual.get("legal_standing", {}).get("status"),
            "legal_details": qual.get("legal_standing", {}).get("details"),
            "portfolio_status": qual.get("game_portfolio", {}).get("status"),
            "game_types": ", ".join(qual.get("game_portfolio", {}).get("game_types_found", [])),
            "has_development": qual.get("business_functions", {}).get("development", {}).get("present"),
            "has_publishing": qual.get("business_functions", {}).get("publishing_marketing", {}).get("present"),
            "has_live_ops": qual.get("business_functions", {}).get("live_operations", {}).get("present"),
            
            # Profile
            "total_games": profile.get("portfolio_size", {}).get("total_games"),
            "games_last_2_years": profile.get("release_frequency", {}).get("games_last_2_years"),
            "employee_count": profile.get("company_size", {}).get("employee_count"),
            "revenue_usd": profile.get("revenue", {}).get("amount"),
            "works_with_external_studios": profile.get("external_partnerships", {}).get("works_with_external_studios"),
            "eu_based_studios": profile.get("external_partnerships", {}).get("eu_based_studios"),
            "has_external_funding": profile.get("funding", {}).get("has_external_funding"),
            "public_company": profile.get("funding", {}).get("public_company"),
            "has_art_team": profile.get("in_house_creative", {}).get("has_art_team"),
            "art_team_size": profile.get("in_house_creative", {}).get("team_size_estimate"),
            
            # Notes
            "research_notes": result.get("research_notes"),
            "data_gaps": ", ".join(result.get("data_gaps", []))
        }
        rows.append(row)
    
    return rows
```

---

## Обновлённая оценка ресурсов

### Сценарии по web search

| Сценарий | Searches/company | Всего | Время | Стоимость search |
|----------|------------------|-------|-------|------------------|
| Все FAIL на Section A | ~4 | 7,200 | ~4 часа | $72 |
| 50% qualified | ~7 | 12,600 | ~7 часов | $126 |
| Все qualified (worst) | ~10 | 18,000 | ~10 часов | $180 |

### Полная стоимость (Sonnet 4.5, 50% qualified)

| Компонент | Расчёт | Стоимость |
|-----------|--------|-----------|
| Web Search | 12,600 × $0.01 | $126 |
| Input tokens (с кешем) | ~90M × $0.30/MTok (cache read) | $27 |
| Output tokens | ~3.5M × $15/MTok | $52 |
| **ИТОГО** | | **~$205** |

*Cache read pricing: $0.30/MTok vs $3.00/MTok full price — экономия 90%*

---

## Мониторинг и логирование

```python
# Структура лога
{
    "timestamp": "2025-02-02T10:30:00Z",
    "event": "company_processed",
    "company": "Pragmatic Play",
    "qualified": true,
    "web_searches": 8,
    "processing_time_sec": 42.5,
    "progress": "150/1800 (8.3%)",
    "rate": {
        "current_rpm": 24,
        "avg_searches_per_company": 6.2
    },
    "estimates": {
        "remaining_time_min": 320,
        "total_cost_usd": 185.50
    }
}
```

### Прогресс-бар

```
Processing iGaming Companies
━━━━━━━━━━╸━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  25% │ 450/1800
Qualified: 180 (40%) │ Web searches: 2,850 │ Est. cost: $52
Rate: 28 RPM │ ETA: 4h 15m
```

---

## CLI интерфейс

```bash
# Полная обработка
python main.py

# Продолжить после сбоя (автоматически)
python main.py  # Определит прогресс из raw_output/

# Только агрегация
python main.py --aggregate-only

# Обработать конкретные компании
python main.py --companies "Pragmatic Play,Evolution Gaming"

# Тестовый прогон (5 компаний)
python main.py --test-run

# Dry run (проверка без API)
python main.py --dry-run

# Изменить concurrency
python main.py --concurrency 5
```

---

## Чеклист перед запуском

- [ ] API ключ с доступом к web search tool
- [ ] Входной CSV с корректной структурой
- [ ] Системный промпт в `prompts/system_prompt.txt`
- [ ] Директории `raw_output/` и `output/` созданы
- [ ] Тест на 5-10 компаниях: `python main.py --test-run`
- [ ] Проверить формат JSON ответов
- [ ] Оценить реальный avg searches/company
- [ ] Убедиться что rate limiter работает корректно

---

## Особые кейсы

### Компания без website и LinkedIn

```python
# Только имя — Claude будет искать сам
{
    "company_name": "Mystery Games Ltd",
    "website": "",
    "linkedin_url": ""
}
```
Ожидаемо больше web searches (~8-10).

### Компания с очевидным FAIL

```python
# Если в additional_context указано что это не iGaming
{
    "company_name": "Tech Consulting Inc",
    "additional_context": "IT consulting, no gaming products"
}
```
Быстрый FAIL на Section A, минимум searches (~2-3).

### Дубликаты названий

```python
# Два "Game Studio" в датасете
# Решение: включать website в hash для уникальности
filename = f"{sanitize(name)}_{hash(name + website)[:8]}.json"
```

