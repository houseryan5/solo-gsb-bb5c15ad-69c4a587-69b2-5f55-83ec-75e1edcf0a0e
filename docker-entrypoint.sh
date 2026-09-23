#!/bin/sh
# 容器启动入口：依赖安装已在镜像构建阶段完成；此处做一次性初始化自检，
# 确保应用模块可导入、全部路由（含 /api/v1/stoichiometry/yield）已注册，
# 随后再启动 uvicorn，使“依赖及初始化随启动完成”，自检失败容器直接退出。
set -eu

APP_PORT="${APP_PORT:-8000}"

echo "[entrypoint] 初始化：校验应用导入与路由注册..."
python - <<'PY'
from app.main import app

required = {
    "/health",
    "/api/v1/balance",
    "/api/v1/balance/variants",
    "/api/v1/analyze",
    "/api/v1/stoichiometry/batch",
    "/api/v1/stoichiometry/yield",
    "/api/v1/stoichiometry/plan",
    "/api/v1/stoichiometry/sequence",
    "/api/v1/inventory/allocate",
}
routes = {getattr(route, "path", None) for route in app.routes}
missing = sorted(path for path in required if path not in routes)
if missing:
    raise SystemExit(f"初始化失败：缺少路由 {missing}")
print(f"[entrypoint] 初始化完成：已注册 {len(required)} 个核心路由")
PY

echo "[entrypoint] 启动 uvicorn，监听 0.0.0.0:${APP_PORT}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${APP_PORT}"
