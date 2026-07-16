#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 简单的本地服务：提供「A股创52周新低」页面 + 实时刷新接口。
# 用法：python3 server.py  （默认 http://127.0.0.1:8765）
# 刷新按钮会请求 /api/refresh，由本服务实时调用 westock-tool 拉取最新数据后返回 JSON。
import json, os
from http.server import SimpleHTTPRequestHandler, HTTPServer
import generate_52week_low as gen

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8765

class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=HERE, **kw)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/refresh":
            self.handle_refresh()
            return
        if path in ("/", "/index.html"):
            self.serve_report()
            return
        # 其余路径走静态文件（SimpleHTTPRequestHandler 默认行为）
        super().do_GET()

    def serve_report(self):
        try:
            with open(gen.OUT, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            msg = str(e).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

    def handle_refresh(self):
        try:
            rows, stats = gen.build_dataset()
            gen.write_html(rows, stats)  # 同步刷新静态文件，便于 file:// 直接打开时也最新
            body = json.dumps({"rows": rows, "stats": stats}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            msg = json.dumps({"error": str(e)}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

    def log_message(self, fmt, *args):
        pass  # 静默日志

if __name__ == "__main__":
    print(f"serving on http://127.0.0.1:{PORT}  (Ctrl+C 退出)")
    HTTPServer(("127.0.0.1", PORT), H).serve_forever()
