# AGENTS.md — обязательные инструкции для AI coding agent

Ты работаешь над проектом UZEX Data Agent. До написания кода полностью прочитай `UZEX_AI_AGENT_ARCHITECTURE.md` и исходный `README.md` из архива `uzex_spot_data.zip`.

## Приоритет правил

1. `UZEX_AI_AGENT_ARCHITECTURE.md` — обязательная техническая спецификация.
2. Исходный README и загруженные CSV — источник фактической структуры и бизнес-правил.
3. Код проекта — реализация этих правил.
4. Предположения запрещены. Если поле или смысл не описаны, сначала исследуй исходные файлы; если подтвердить нельзя — не реализуй догадку.

## Запрещено

- Не помещай LLM API key в React, HTML, клиентский JavaScript, Vite `VITE_*` env или репозиторий.
- Не давай LLM прямой доступ к PostgreSQL, файлам, Python, shell, интернету или произвольным tools.
- Не исполняй SQL, который вернула LLM.
- Не конкатенируй пользовательский текст в SQL.
- Не используй `float` для денежных данных; используй `decimal.Decimal` и PostgreSQL `NUMERIC`.
- Не заменяй NULL на 0.
- Не объединяй сделки по названию товара вместо contract number.
- Не объединяй UZS и USD без метода конвертации из README.
- Не складывай физические объёмы в разных единицах.
- Не трактуй warehouse region как company region.
- Не подменяй отсутствие данных догадками.
- Не хардкодь контрольные значения в production formatter.
- Не заявляй о «100% точности» без результатов тестирования.
- Не удаляй непригодные/неиспользуемые строки молча.

## Обязательная цепочка запроса

`question -> language/script detection -> LLM strict JSON QueryPlan -> Pydantic validation -> entity resolution -> allowlist/policy validation -> parameterized SQL handler -> PostgreSQL read-only -> result/evidence validation -> deterministic RU/UZ formatter -> response`.

Если один шаг не прошёл проверку, останови исполнение и верни корректный статус: `NOT_FOUND`, `NEEDS_CLARIFICATION`, `UNSUPPORTED` или `UNVERIFIED`. Не продолжай «на авось».

## Обязательные инженерные практики

- Python typing и Pydantic для всех внешних/внутренних контрактов.
- SQLAlchemy Core или параметризованный SQL; никаких произвольных SQL строк из LLM.
- Alembic migrations.
- pytest для каждого handler и каждого business rule.
- Секреты через environment/Secret Manager.
- Runtime DB role — только SELECT.
- Structured logs с request_id; никаких secrets в логах.
- Импорт через staging, validation, затем атомарный переключатель dataset version.
- Все ответы FOUND сопровождай evidence.

## Порядок выполнения

1. Обследуй ZIP, README и CSV заголовки; сформируй data dictionary.
2. Реализуй importer и integrity checks.
3. Реализуй PostgreSQL schema/migrations.
4. Напиши и протестируй SQL handlers без LLM.
5. Добавь strict QueryPlan и LLM adapter.
6. Добавь entity resolver, orchestrator и safe states.
7. Добавь frontend.
8. Добавь Docker/deploy/health checks.
9. Запусти все тесты и подготовь test report.

Не перескакивай через этапы. На каждом этапе создай рабочую реализацию, тесты и кратко сообщи результаты. Не заменяй отсутствующие тесты объяснениями.

## Критерии приёмки

- Import: 500 000 deals и все справочники загружены без нарушения целостности.
- Golden counts по годам совпадают с architecture spec.
- Все денежные вычисления точные и используют Decimal/NUMERIC.
- Никаких выдуманных фактов в тестах отсутствующих данных.
- Неоднозначная сущность не разрешается автоматически.
- Ошибка LLM/DB не маскируется под NOT_FOUND.
- Frontend не раскрывает API key.
- Веб-сервис доступен по публичной ссылке без регистрации.

Если реализация вступает в конфликт с этой спецификацией, приостанови эту часть, укажи конфликт и не выбирай самовольно более свободный вариант.
