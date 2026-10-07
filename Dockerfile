FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir '.[all]'

RUN useradd --create-home --uid 10001 dbtagent
USER dbtagent

ENTRYPOINT ["dbt-agents-mcp"]
CMD ["--transport", "stdio"]
