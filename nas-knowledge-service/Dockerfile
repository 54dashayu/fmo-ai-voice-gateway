FROM python:3.12-alpine
WORKDIR /app
COPY service.py gallery.py /app/
RUN addgroup -S fmo && adduser -S -G fmo -u 10001 fmo
RUN chmod 0444 /app/service.py /app/gallery.py
USER fmo
EXPOSE 8787
CMD ["python3", "/app/service.py"]

