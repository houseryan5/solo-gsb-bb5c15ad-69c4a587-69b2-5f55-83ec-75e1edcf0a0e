FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_PORT=8000

WORKDIR /app

# 先单独复制依赖清单以利用层缓存；依赖安装在构建期完成，启动即就绪
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 再复制应用代码与启动入口（入口内完成导入/路由初始化自检后启动 uvicorn）
COPY app ./app
COPY docker-entrypoint.sh ./docker-entrypoint.sh
RUN chmod +x ./docker-entrypoint.sh

# 容器内以非 root 用户运行
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# 容器内监听端口，默认 8000，可用 APP_PORT 覆盖
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%d/health' % int(os.environ.get('APP_PORT', '8000')), timeout=3).status==200 else 1)"

ENTRYPOINT ["./docker-entrypoint.sh"]
