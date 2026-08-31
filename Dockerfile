FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_HOST=0.0.0.0 \
    APP_PORT=8080

WORKDIR /app

# Copy the application before installation so the wheel actually contains Cogen.
COPY pyproject.toml README.md ./
COPY app ./app
COPY web ./web
RUN pip install --no-cache-dir "."

# The browse_page_for_details agent tool needs a real Chromium — install it and its OS-level deps
# (fonts, codecs, etc.) while still root, into a path the later non-root user can read.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install --with-deps chromium

# Fail the image build if Python resolves some dependency's top-level `app`
# instead of Cogen, or if our health route is absent.
RUN python - <<'PY'
import app
import app.main

module_path = str(app.main.__file__)
routes = {getattr(route, "path", None) for route in app.main.app.routes}
print("COGEN_APP_MODULE=", module_path)
print("COGEN_ROUTES=", sorted(path for path in routes if path))
assert module_path.startswith("/app/app/"), module_path
assert "/healthz" in routes, routes
assert "/readyz" in routes, routes
PY

RUN useradd -r -u 10001 appuser && chown -R appuser:appuser /app /ms-playwright
USER appuser

EXPOSE 8080
CMD ["sh", "-c", "python -m uvicorn app.main:app --host ${APP_HOST:-0.0.0.0} --port ${PORT:-${APP_PORT:-8080}}"]
