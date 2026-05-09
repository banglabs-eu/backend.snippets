FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ARG GIT_VERSION=unknown
ARG GIT_BRANCH=unknown
ARG GIT_SHA=unknown
ARG BUILT_AT=unknown
ENV APP_VERSION=$GIT_VERSION APP_BRANCH=$GIT_BRANCH APP_SHA=$GIT_SHA APP_BUILT_AT=$BUILT_AT

RUN adduser --disabled-password --gecos "" appuser
USER appuser
EXPOSE 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${BACKEND_PORT:-8000} --workers 4"]
