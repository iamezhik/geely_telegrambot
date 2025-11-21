# Используем более свежую версию Python
FROM python:3.11-slim

# Переменные среды для оптимизации Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Создаем системного пользователя (безопасность)
RUN groupadd -r botuser && useradd -r -g botuser botuser

WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Установка часового пояса
ENV TZ=Europe/Moscow

# Сначала копируем requirements для кэширования слоя
COPY requirements.txt .
RUN pip install -r requirements.txt

# Копируем код и меняем владельца
COPY . .
RUN chown -R botuser:botuser /app

# Переключаемся на пользователя
USER botuser

# Healthcheck для мониторинга
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD pgrep python || exit 1

CMD ["python", "checker_bot.py"]