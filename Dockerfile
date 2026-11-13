FROM python:3.15-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project
COPY src ./src
RUN uv sync --locked

RUN useradd --system --no-create-home placescout
USER placescout
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "placescout.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
