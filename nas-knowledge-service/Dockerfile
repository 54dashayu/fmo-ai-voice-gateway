FROM python:3.12-alpine
WORKDIR /app
COPY service.py /app/service.py
RUN addgroup -S fmo && adduser -S -G fmo -u 10001 fmo
USER fmo
EXPOSE 8787
CMD ["python3", "/app/service.py"]

