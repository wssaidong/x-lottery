#!/usr/bin/env python3
"""彩票数据分析 - 本地开发服务器

提供:
  - 静态文件服务 (HTML/JS/CSS/JSON)
  - /api/dlt?count=30  实时拉取大乐透数据
  - /api/ssq?count=30  实时拉取双色球数据

用法:
  python3 server.py              # 默认 8080 端口
  python3 server.py -p 3000      # 指定端口
"""

import argparse
import json
import os
import sys
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler

from refresh_data import FETCHERS, NAMES

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


class LotteryHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=SCRIPT_DIR, **kwargs)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path.startswith("/api/dlt") or parsed.path.startswith("/api/ssq"):
            self._handle_api(parsed)
        else:
            super().do_GET()

    def _handle_api(self, parsed: urllib.parse.ParseResult) -> None:
        game = "dlt" if parsed.path.startswith("/api/dlt") else "ssq"
        params = urllib.parse.parse_qs(parsed.query)
        count = int(params.get("count", ["30"])[0])
        name = NAMES[game]

        print(f"[API] 拉取 {name} 最近 {count} 期...")

        try:
            data = FETCHERS[game](count)
        except Exception as e:
            print(f"[API] 拉取失败: {e}")
            self._json_response({"error": str(e)}, status=500)
            return

        if not data:
            self._json_response({"error": "返回数据为空"}, status=500)
            return

        latest = data[-1]
        front_key = "红球" if game == "ssq" else "前区"
        back_key = "蓝球" if game == "ssq" else "后区"
        print(f"[API] 最新: 第{latest['期号']}期 ({latest['开奖日期']}) "
              f"{front_key} {latest[front_key]} + {back_key} {latest[back_key]}")

        self._json_response(data)

        os.makedirs(os.path.join(SCRIPT_DIR, "data"), exist_ok=True)
        from refresh_data import save
        save(data, game, count)

    def _json_response(self, body: object, status: int = 200) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="彩票数据分析服务器")
    parser.add_argument("-p", "--port", type=int, default=8080, help="端口号 (默认8080)")
    args = parser.parse_args()

    server = HTTPServer(("127.0.0.1", args.port), LotteryHandler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"🎱 彩票数据分析服务器已启动")
    print(f"   访问: {url}")
    print(f"   大乐透 API: {url}/api/dlt?count=30")
    print(f"   双色球 API: {url}/api/ssq?count=30")
    print(f"   Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        server.server_close()


if __name__ == "__main__":
    main()
