# Payment Service

Асинхронный приём платежей. `POST` сохраняет платёж, ключ идемпотентности и событие outbox в одной транзакции PostgreSQL и отвечает `202 Accepted`. Relay в процессе API публикует событие в RabbitMQ, consumer проводит платёж через эмуляцию шлюза и отправляет webhook.

`202` значит, что платёж записан. Итог списания — в `GET /api/v1/payments/{id}`, поля `status` и `webhook_delivered_at`.

Публикация в брокер и webhook — at-least-once. Повтор одного `payment_id` не проводит его второй раз: обработка держит lease, решение шлюза для идентификатора стабильно, webhook клиенту стоит принимать идемпотентно по `payment_id`.

## Запуск

Нужны Docker и Docker Compose. Скопируйте `.env.example` в `.env`: пароли, `API_KEY` и `WEBHOOK_SECRET` читаются оттуда. `env_file` передаёт файл в API, consumer и миграции. Логин Postgres и RabbitMQ подставляются из тех же переменных. Внутри сети Compose адреса указывают на сервисы `postgres` и `rabbitmq`. Значения `DATABASE_URL` и `RABBITMQ_URL` в `.env` остаются с `localhost` — ими пользуются тесты и запуск на хосте.

```bash
copy .env.example .env
docker compose up --build
```

Поднимаются PostgreSQL 16, RabbitMQ 3.13, миграции, API и consumer.

- API: http://localhost:8000
- OpenAPI: http://localhost:8000/docs
- RabbitMQ Management: http://localhost:15672 (`RABBITMQ_USER` / `RABBITMQ_PASSWORD` из `.env`)

Ключ API — `API_KEY` из `.env`. В примере это `dev-api-key`. В PowerShell вызывайте `curl.exe`.

```powershell
curl.exe -s -D - -X POST http://localhost:8000/api/v1/payments `
  -H "Content-Type: application/json" `
  -H "X-API-Key: dev-api-key" `
  -H "Idempotency-Key: 4f3c2a1b-9d8e-4c7b-a6f5-111122223333" `
  -d "{\"amount\":\"1500.50\",\"currency\":\"RUB\",\"description\":\"Order 1001\",\"metadata\":{\"order_id\":\"1001\"},\"webhook_url\":\"https://example.com/webhooks/payments\"}"
```

Ответ `202`: `{"payment_id":"...","status":"pending","created_at":"..."}`.

Шлюз ждёт 2–5 секунд и проводит около 90% платежей. Адрес из примера webhook не примет: статус станет `succeeded` или `failed`, а `webhook_delivered_at` останется пустым.

Статус: `curl.exe -s http://localhost:8000/api/v1/payments/<payment_id> -H "X-API-Key: dev-api-key"`.

`docker compose down` останавливает сервисы. Данные Postgres остаются в volume `postgres_data`.

## API

Префикс `/api/v1`. Методы платежей требуют `X-API-Key`.

`POST /payments` требует `Idempotency-Key`: 1–200 печатных ASCII-символов. Тело:

| Поле | Правило |
| --- | --- |
| `amount` | строка, больше нуля, не больше двух знаков после запятой |
| `currency` | `RUB`, `USD`, `EUR` |
| `description` | 1–500 символов |
| `metadata` | объект, до 16 КиБ |
| `webhook_url` | публичный `https` |

Тот же ключ и то же тело возвращают сохранённый `202` и тот же `payment_id`. Другое тело с тем же ключом — `409`.

`GET /payments/{payment_id}` отдаёт карточку платежа.

| Код | Когда |
| --- | --- |
| 202 | платёж принят |
| 401 | неверный ключ |
| 404 | платежа нет |
| 409 | ключ идемпотентности занят другим телом или первый запрос ещё не завершён |
| 422 | тело или ключ не прошли проверку |

Статусы: `pending`, `processing`, `succeeded`, `failed`. Успешный шлюз заполняет `provider_reference`, отказ — `failure_reason`. `webhook_delivered_at` появляется отдельно, после `2xx` от клиента.

## Webhook

`POST` на `webhook_url`, редиректы не выполняются. Подпись — hex HMAC-SHA256 сырого тела в заголовке `X-Signature`, секрет `WEBHOOK_SECRET`. В теле ключи отсортированы и нет пробелов: `payment_id`, `status`, `amount`, `currency`, `processed_at`, `failure_reason`. Сверять подпись нужно по исходным байтам.

Ответ вне `2xx` приводит к повтору с увеличивающейся паузой. Перед отправкой адрес проверяется снова, чтобы webhook не ушёл во внутреннюю сеть.

## Архитектура

Слои clean/DDD:

- `src/domain` — entities, enums, exceptions
- `src/application` — dto, use-case services, validators, protocols
- `src/infrastructure` — ORM, repositories, gateway/webhook, RabbitMQ, workers
- `src/presentation` — FastAPI routes, deps, `main:app`

## Обработка

Ошибка шлюза, таймаут или недоставка webhook возвращают сообщение в `payments.retry.N`. У очереди свой TTL, затем оно снова попадает в `payments.new`. После трёх попыток (`CONSUMER_MAX_ATTEMPTS`) сообщение уходит в `payments.dlq`, а платёж без решения шлюза становится `failed`. Если платёж ещё держит другой worker, повтор не тратит попытку.

Шлюз локальный: пауза 2–5 с, ~90% успех и стабильный исход по хешу `payment_id`. Поведенческие константы — в `src/infrastructure/**/constants.py`; в `.env` только секреты и адреса.

## Тесты

Python 3.12. `tests/unit` не нуждается в базе. `tests/integration` и `tests/e2e` ходят в Postgres из compose на `localhost:5432`. Тесты создают базу `payment_test` и не трогают `payment`.

```bash
pip install -e ".[dev]"
pytest
pytest -m unit
pytest -m integration
pytest -m e2e
ruff check src tests alembic
mypy
```
