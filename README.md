# CheckStock — где сейчас товар

Веб-прототип учёта остатков для менеджера: видно, какой товар физически лежит на каждом ФФ,
сколько доступно и зарезервировано под FBS, что едет между ФФ, на склад маркетплейса (FBO)
и к клиенту (FBS).

**Стек:** Python 3.12 · FastAPI · SQLAlchemy 2.0 · PostgreSQL 16 · UI — один HTML-файл на ванильном JS (без сборки).

---

## 1. Требования

Выберите один из вариантов:

| Вариант | Что нужно установить |
|---|---|
| **A. Docker (рекомендуется)** | Docker Desktop (Windows/macOS) или Docker Engine + Compose v2 (Linux) |
| **B. Без Docker, быстро** | Python 3.10+ (лучше 3.12). БД — SQLite, ничего поднимать не нужно |
| **C. Без Docker, целевой стек** | Python 3.10+ и PostgreSQL 14+ (или только БД из Docker) |

Все команды выполняются **из корня проекта** — из папки, где лежат `docker-compose.yml`,
`requirements.txt` и каталог `app/`.

---

## 2. Запуск

### Вариант A — Docker (одна команда)

```bash
docker compose up --build
```

Откройте **http://localhost:8000**.

Что происходит автоматически при старте:
- поднимается PostgreSQL 16 и дожидается healthcheck;
- создаётся схема БД;
- засеваются тестовые данные из задания (один магазин, ФФ «Север» и «Юг»):

| Артикул | Товар | Север | Юг |
|---|---|---:|---:|
| SKU-101 | Органайзер | 10 | 6 |
| SKU-102 | Контейнер | 5 | 0 |
| SKU-103 | Корзина | 0 | 4 |

Полезные адреса:
- **http://localhost:8000** — интерфейс;
- **http://localhost:8000/docs** — Swagger (все эндпоинты можно дёргать прямо там);
- **http://localhost:8000/api/reconciliation** — сверка остатков с журналом в JSON.

Остановка и сброс к чистому стенду:

```bash
docker compose down          # данные удаляются — так задумано для воспроизводимой проверки
docker compose up            # снова чистый стенд с теми же тестовыми данными
```

### Вариант B — без Docker, быстрый запуск на SQLite

Подходит, чтобы посмотреть проект без развёрнутой БД.

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

DATABASE_URL=sqlite:///./checkstock.db uvicorn app.main:app --reload --port 8000
```

**Windows (PowerShell):**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

$env:DATABASE_URL = "sqlite:///./checkstock.db"
uvicorn app.main:app --reload --port 8000
```

**Windows (cmd):**
```bat
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt

set DATABASE_URL=sqlite:///./checkstock.db
uvicorn app.main:app --reload --port 8000
```

> **Важно:** без переменной `DATABASE_URL` приложение подключается к PostgreSQL на
> `localhost:5432` и без запущенной БД не стартует. Для SQLite переменная обязательна.
> Сброс данных — удалить файл `checkstock.db`.

### Вариант C — без Docker-приложения, на PostgreSQL

**C1. Только БД из compose, приложение — локально:**
```bash
docker compose up -d db        # дождитесь состояния healthy (несколько секунд)

source .venv/bin/activate      # venv из варианта B
pip install -r requirements.txt

DATABASE_URL=postgresql+psycopg2://checkstock:checkstock@localhost:5432/checkstock \
  uvicorn app.main:app --reload --port 8000
```
В PowerShell сначала `$env:DATABASE_URL = "postgresql+psycopg2://checkstock:checkstock@localhost:5432/checkstock"`, затем `uvicorn ...`.

**C2. Собственный PostgreSQL:** создайте роль и БД, затем запустите приложение с тем же URL.
```sql
CREATE USER checkstock WITH PASSWORD 'checkstock';
CREATE DATABASE checkstock OWNER checkstock;
```

### Переменные окружения

| Переменная | Значение по умолчанию | Назначение |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg2://checkstock:checkstock@localhost:5432/checkstock` | Строка подключения SQLAlchemy. Поддерживаются `postgresql+psycopg2://…` и `sqlite:///…` |
| `TEST_DATABASE_URL` | `sqlite://` (в памяти) | БД для автотестов. Позволяет прогнать тот же набор на PostgreSQL |

Образец строки подключения — в файле `.# app_testovoe
