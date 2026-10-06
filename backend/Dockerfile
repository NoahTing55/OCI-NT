# ============ 第一阶段：构建前端 ============
# 单端口部署：前端不再独立跑 dev server，build 产物由 FastAPI 直接托管
FROM node:20-alpine AS frontend-builder

WORKDIR /web

COPY frontend/package.json ./
RUN npm install

COPY frontend/ ./
# 产物输出到 /web/dist（vite 默认 outDir，可在 vite.config.js 确认）
RUN npm run build

# ============ 第二阶段：后端运行环境 ============
FROM python:3.11-slim

WORKDIR /code

# 注意：build 上下文是项目根目录（docker-compose 里 context: .），所以路径带 backend/ 前缀
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
# 数据迁移文件一并拷入，容器内可执行 alembic upgrade head
COPY backend/alembic.ini ./alembic.ini
COPY backend/alembic ./alembic

# 把第一阶段的前端构建产物拷进来，FastAPI 在 /app/static 下直接托管
COPY --from=frontend-builder /web/dist /app/static

EXPOSE 8035

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8035"]
