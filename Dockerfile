FROM python:3.12-slim
WORKDIR /app
COPY app.py engine.py index.html ./
RUN useradd --system --uid 10001 --no-create-home app && chown -R app:app /app
ENV BIND_HOST=0.0.0.0
ENV ALLOW_PRIVATE_TARGETS=0
EXPOSE 8088
USER app
CMD ["python", "app.py"]
