FROM python:3.11-slim

WORKDIR /app

COPY backend/requirements.txt backend/requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
# Only needed so the "test an owlplanner upgrade" endpoint can run the real
# suite inside its own scratch virtualenv -- not used by the running app.
COPY backend/tests ./tests

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
