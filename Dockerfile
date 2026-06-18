# KelpWorks ERP — runs anywhere Docker does (Render, Railway, Fly.io, a VPS).
FROM python:3.12-slim

WORKDIR /app
COPY . .

# The app uses ONLY the Python standard library — nothing to pip install.
# It listens on $PORT (hosts inject this) and binds 0.0.0.0 by default.
ENV PORT=8002
EXPOSE 8002

CMD ["python", "kelp_erp_server.py"]
