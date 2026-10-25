# Ticketing API

Сервис продажи билетов на мероприятия с выбором конкретного места на карте зала: пользователь видит схему зала, выбирает места, они блокируются на 10 минут под оплату, после оплаты он получает билеты с QR-кодом. На входе контролёр сканирует QR — билет гасится, повторный проход невозможен.

**Стек:** FastAPI · SQLAlchemy 2.0 (async) · PostgreSQL 16 · Redis 7 · ARQ · Stripe (test mode) · Docker

---

## Что демонстрирует проект

Это не CRUD-приложение. Самое интересное здесь:

1. **Конкурентный захват мест без овербукинга.** 50 параллельных покупателей на одно место → ровно 1 побеждает. См. `tests/concurrency/test_seat_race.py`.
2. **Истечение брони без утечек.** Воркер на `FOR UPDATE SKIP LOCKED` + ленивая перепроверка при захвате. Места возвращаются в продажу, даже если воркер упал.
3. **Сквозная идемпотентность.** `Idempotency-Key` при создании заказа (Redis-лок + уникальный индекс в БД), дедупликация по `event.id` на вебхуках Stripe. Повторы безопасны by design.
4. **Оплата после истечения брони — штатная ветка.** Если hold истёк, пока пользователь был на странице Stripe, делаем автоматический рефанд вместо «оплачено, но билета нет».
5. **Транзакционный outbox.** События (`order.created`, `order.paid`, `order.expired`) пишутся в БД в той же транзакции, что и бизнес-изменения, и рассылаются отдельным воркером. Никакого dual-write.

---

## Запуск

```bash
cp .env.example .env
docker compose up -d postgres redis
alembic upgrade head
make dev       # http://localhost:8000/docs
make worker    # ARQ-воркер: истечение hold'ов + outbox
```

Или всё в контейнере:

```bash
docker build -t ticketing .
docker run --rm -p 8000:8000 --env-file .env ticketing
```

Тесты (нужны живые Postgres и Redis из `docker-compose`):

```bash
make test
```

---

## Архитектура

```
FastAPI (api)  ─────►  Postgres (источник истины)
   │                       ▲
   │                       │
   ├──► Redis (кэш +      ARQ-воркер (истечение hold'ов,
   │      идемпотентность)  рассылка outbox)
   │
   └──► Stripe (test mode)  ──►  /webhooks/stripe (HMAC)
```

- **Postgres** хранит состояния мест, заказы, брони, билеты и outbox.
- **Redis** — кэш карты зала (TTL 3 секунды) и лок идемпотентности по пользователю.
- **ARQ** крутит два крона: `expire_holds` (каждые 10 с) и `dispatch_outbox` (каждые 10 с).
- **Stripe** — внешний источник истины по платежам; вебхук — единственный надёжный сигнал.

---

## Ключевые решения (и почему именно так)

### `UPDATE ... WHERE status='available'` вместо `SELECT FOR UPDATE`

Захват места — это один атомарный UPDATE, который для всех, кроме победителя, не находит строк:

```sql
UPDATE showtime_seats
SET status='held', hold_id=..., hold_expires_at=..., order_id=...
WHERE showtime_id=:sid
  AND id = ANY(:seat_ids)
  AND (status='available' OR (status='held' AND hold_expires_at < now()))
RETURNING id, price_cents;
```

Если `rowcount < len(seat_ids)` — откат и `409` со списком занятых мест. Postgres сам сериализует конфликты на уровне строк; явный `SELECT FOR UPDATE` не нужен — он требует двух запросов и увеличивает шанс дедлока.

Полная реализация: `app/services/orders.py`.

### Идемпотентность на двух уровнях

`POST /orders` использует:

1. **Redis-лок** — быстрый путь, короткий TTL, ловит параллельные повторы за < 1 мс.
2. **`UNIQUE (user_id, idempotency_key)` в БД** — последний рубеж, работает даже при падении Redis.

Повтор с тем же ключом и тем же телом возвращает сохранённый `response_body` и тот же статус (`201`). Другое тело с тем же ключом → `422`.

### Деньги — в копейках, никогда не `float`

Каждая цена — `bigint` в копейках. `50000` = 500.00 ₽. Никакого `Decimal` в слое БД, никаких сюрпризов с округлением.

### Роль берётся из БД, а не из JWT

`get_current_user` перечитывает пользователя на каждый запрос и берёт `role` из Postgres. Компромисс: один индексный SELECT на запрос. Выгода: разжалованный админ теряет доступ сразу, а не через 15 минут. Для системы продажи билетов выбираем эту сторону.

### Outbox, а не fire-and-forget

Каждая мутация, у которой есть побочный эффект, пишет в `outbox` в той же транзакции:

```python
order.status = 'paid'
outbox_svc.emit(session, 'order.paid', {...})
await session.commit()
```

Диспетчер (`app/workers/tasks/outbox_dispatcher.py`) читает outbox, рассылает события и помечает их опубликованными. Падение между бизнес-записью и рассылкой означает ретрай, а не потерю. Замените `log.info` на вызов вебхука — паттерн масштабируется.

---

## Грабли, через которые мы прошли

Это баги, которые были в проекте и которые мы починили. Они стоят того, чтобы их помнить:

- **`session.rollback()` экспирирует ORM-объекты.** Каждый роут, который делает rollback, должен захватить `user.id`, `order.total_cents` и т. п. до rollback. Иначе ленивая подгрузка после rollback → `MissingGreenlet`.
- **`async with session.begin()` — анти-паттерн в SQLAlchemy 2.0.** Autobegin стартует транзакцию на первом `execute()`. Вызывайте `commit()` / `rollback()` явно.
- **Кэш карты зала нужно инвалидировать на каждое изменение.** Мы инвалидируем в `POST /orders`, `POST /orders/{id}/cancel` и в воркере `expire_holds`. Забыть любой из них → «свободные» места ещё 3 секунды.
- **Никогда не передавайте ORM-объект через `asyncio.to_thread` или `session.rollback()`.** Только `UUID`, `int`, `str`. Это граница слоёв.

---

## Эндпоинты (кратко)

| Метод | Путь | Авторизация | Назначение |
|---|---|---|---|
| POST | `/auth/register` `/login` `/refresh` `/logout` | публично | JWT + Argon2 |
| GET | `/me` | user | профиль |
| POST | `/admin/venues` `/admin/venues/{id}/halls` `/admin/halls/{id}/seats:bulk` | admin | каталог |
| POST | `/admin/events` `/admin/events/{id}/showtimes` | admin | мероприятия + материализация `showtime_seats` |
| GET | `/showtimes/{id}` `/showtimes/{id}/seats` | публично | карта зала, кэш 3 с |
| POST | `/orders` | user + `Idempotency-Key` | атомарный захват мест |
| POST | `/orders/{id}/pay` | user | создаёт Stripe PaymentIntent |
| POST | `/orders/{id}/cancel` | user | освобождает места |
| POST | `/webhooks/stripe` | HMAC | дедуп по `event.id`, выпускает билеты |
| GET | `/me/tickets` `/tickets/{id}` | user | билеты с HMAC-подписью QR |
| POST | `/check-in` | staff | атомарное гашение билета |
| GET | `/metrics` | — | Prometheus |

Полная спецификация OpenAPI: `http://localhost:8000/docs`.

---

## Наблюдаемость

- **`GET /metrics`** — Prometheus. `http_requests_total{method,path_template,status}`, `http_request_duration_seconds{method,path_template}`.
- **Структурные логи** — одна JSON-строка на запрос с `request_id`, `method`, `path`, `status`, `duration_ms`. `x-request-id` возвращается в заголовке ответа и пробрасывается через вебхуки.
- **`/healthz`** — liveness-проба.

---

## Тесты

```
tests/
  test_auth.py            # 13
  test_admin.py           # 6
  test_showtimes_public.py# 4
  test_orders.py          # 10
  test_orders_expiry.py   # 1
  test_payments.py        # 5
  test_tickets.py         # 5
  concurrency/
    test_seat_race.py     # 1 — главный
```

Запустить race-тест отдельно:

```bash
pytest -q -s tests/concurrency/test_seat_race.py -v
# ожидаемый вывод: ok=1 conflict=49
```

---

## Осознанные компромиссы

- **Один регион, один аккаунт Stripe.** Никакой мультивалютности или резервного провайдера.
- **Нет UI для возвратов.** Рефанды делаются автоматически при оплате после истечения hold'а; ручной возврат потребовал бы `POST /admin/orders/{id}/refund`.
- **Кэш — best-effort.** 3 секунды устаревания карты зала; путь захвата всегда авторитетен.
- **Пока нет rate limiting.** Redis уже на месте; middleware с token bucket — небольшая добавка.

---

## Деплой

`Dockerfile` multi-stage, приложение запускается от non-root пользователя. Деплой на Fly.io:

```bash
fly launch --no-deploy
fly postgres create --name ticketing-db
fly postgres attach ticketing-db
fly secrets set REDIS_URL=... JWT_SECRET=... STRIPE_SECRET_KEY=... STRIPE_WEBHOOK_SECRET=...
fly deploy
```

Миграции — отдельным шагом:

```bash
fly ssh console -C "alembic upgrade head"
```

ARQ-воркер можно запустить второй процессной группой на Fly (`[processes] worker = "arq app.workers.settings.WorkerSettings"`).

