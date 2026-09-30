"""启动入口：python -m shoe_system [--host 127.0.0.1] [--port 8000] [--reseed]

一条本地启动命令：./run.sh
首次启动或 --reseed 时自动建库并写入演示数据（缺码/调岗/提前换领样例）。
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .db import connect, init_db
from .reminders import ReminderWorker
from .seed import seed
from .server import serve


def main():
    ap = argparse.ArgumentParser(description="工厂劳保鞋发放与到期换领系统")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--db", default="data/shoes.db")
    ap.add_argument("--photos", default="data/photos")
    ap.add_argument("--reseed", action="store_true", help="重置并重新写入演示数据")
    args = ap.parse_args()

    db = Path(args.db)
    if args.reseed or not db.exists():
        seed(args.db, args.photos, reset=args.reseed or not db.exists())
    else:
        conn = connect(args.db)
        init_db(conn)
        conn.close()

    worker = ReminderWorker(lambda: connect(args.db), interval_sec=60)
    worker.start()
    worker.tick()  # 启动即生成一次到期/候补提醒

    serve(args.db, args.photos, args.host, args.port)


if __name__ == "__main__":
    main()
