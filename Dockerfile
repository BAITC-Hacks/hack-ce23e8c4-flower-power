FROM ghcr.io/astral-sh/uv:0.9.2 AS uv
FROM python:3.12-slim-bookworm

COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY career_quest ./career_quest
RUN uv sync --locked --no-dev
COPY app.py ./
COPY data/employees.json data/events.json data/skills.json data/activity_history.csv ./data/
RUN groupadd --system careerquest && useradd --system --gid careerquest --home-dir /app careerquest
USER careerquest

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=2)"]
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--browser.gatherUsageStats=false"]
