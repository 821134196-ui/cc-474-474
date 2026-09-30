"""python3 -m safety_shoes [--host H] [--port P] [--no-reseed]"""
from __future__ import annotations

import argparse

from .server import serve


def main():
    ap = argparse.ArgumentParser(description="工厂劳保鞋尺码发放与到期换领系统")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-reseed", action="store_true", help="保留现有数据，不重置演示数据")
    args = ap.parse_args()
    serve(args.host, args.port, reseed=not args.no_reseed)


if __name__ == "__main__":
    main()
