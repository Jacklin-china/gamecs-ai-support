"""Start the local development services without Docker or Redis (Windows)."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

from sqlalchemy import text

from app.config import settings
from app.db import engine


BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent


def listening(port: int) -> bool:
    with socket.socket() as connection:
        connection.settimeout(0.25)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def start(name: str, command: list[str], cwd: Path, port: int, env=None) -> None:
    if listening(port):
        raise RuntimeError(f"{name}: 端口 {port} 已被占用，未启动。请确认占用程序后重试。")
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    for _ in range(50):
        if process.poll() is not None:
            raise RuntimeError(f"{name} 启动失败，请检查配置与依赖。")
        if listening(port):
            print(f"{name}: 已启动，端口 {port}。")
            return
        time.sleep(0.1)
    raise RuntimeError(f"{name} 未能在规定时间内启动，请检查端口和配置。")


def frontend_port(preferred: int, reserved: set[int]) -> int:
    for port in (preferred, *range(5175, 5200)):
        if port not in reserved and not listening(port):
            return port
    raise RuntimeError("没有可用的前端端口（5173–5199）。")


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("此启动脚本针对 Windows；其他系统请参照 README 手动启动。")
    cfg = settings()
    if not cfg.local_mode:
        raise SystemExit("请先在 backend/.env 中设置 LOCAL_MODE=true。")
    try:
        from alembic.config import Config
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory
        alembic = Config(str(BACKEND / "alembic.ini"))
        alembic.set_main_option("script_location", str(BACKEND / "alembic"))
        head = ScriptDirectory.from_config(alembic).get_current_head()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            current = MigrationContext.configure(connection).get_current_revision()
    except Exception:
        raise SystemExit("无法连接新系统的 MySQL，请检查服务和 backend/.env。") from None
    if current != head:
        raise SystemExit("数据库版本不是最新版本；请先备份、验证迁移，再执行 alembic upgrade head。")
    node = shutil.which("node")
    if not node:
        raise SystemExit("未找到 Node.js，请安装依赖后重试。")
    for app in ("player", "admin"):
        if not (ROOT / "frontend" / app / "node_modules" / "vite" / "bin" / "vite.js").is_file():
            raise SystemExit(f"{app} 前端缺少依赖，请在其目录运行 npm ci。")

    minio = BACKEND / ".local" / "minio" / "minio.exe"
    if cfg.minio_endpoint in ("127.0.0.1:19000", "localhost:19000") and not cfg.minio_secure:
        if not minio.is_file():
            print("未找到本地 MinIO，附件功能暂不可用。")
        elif not cfg.minio_access_key or not cfg.minio_secret_key:
            print("MinIO 凭据未配置，附件功能暂不可用。")
        else:
            storage = BACKEND / ".local" / "minio" / "storage"
            storage.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            env["MINIO_ROOT_USER"] = cfg.minio_access_key
            env["MINIO_ROOT_PASSWORD"] = cfg.minio_secret_key
            start("附件服务", [str(minio), "server", str(storage), "--address", "127.0.0.1:19000", "--console-address", "127.0.0.1:19001"], BACKEND, 19000, env)
    else:
        print("附件服务使用配置中的外部地址；请确认该服务已启动。")

    start("后端", [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "18765"], BACKEND, 18765)
    chosen = {}
    reserved = {5174}
    for app, label, preferred in (("player", "玩家端", 5173), ("admin", "管理端", 5174)):
        if app == "admin":
            reserved.discard(5174)
        port = frontend_port(preferred, reserved)
        reserved.add(port)
        chosen[app] = port
        directory = ROOT / "frontend" / app
        start(label, [node, str(directory / "node_modules" / "vite" / "bin" / "vite.js"), "--host", "127.0.0.1", "--port", str(port), "--strictPort"], directory, port)
    print(f"玩家端：http://127.0.0.1:{chosen['player']}/  管理端：http://127.0.0.1:{chosen['admin']}/")


if __name__ == "__main__":
    main()
