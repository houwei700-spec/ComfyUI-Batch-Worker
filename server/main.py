import os
import sys
import socket
import urllib.parse
from http.server import ThreadingHTTPServer as BaseThreadingHTTPServer, BaseHTTPRequestHandler
import webbrowser
from server.config import PORT, WEB_DIR, log_msg
from server.routes.static_handler import serve_static_file
from server.routes.api_router import handle_get, handle_post
from server.services.thumbnail_service import start_thumbnail_prebuild_worker
from server.services.cluster_monitor import start_cluster_monitor
from server.workers.batch_dispatcher import start_dispatcher_daemon
from server.services.path_service import resolve_under
from server.services.task_service import recover_interrupted_tasks


class ThreadingHTTPServer(BaseThreadingHTTPServer):
    # Windows SO_REUSEADDR can let an old checkout share the production port.
    allow_reuse_address = False

    def server_bind(self):
        if os.name == 'nt':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

class ModularHubHandler(BaseHTTPRequestHandler):
    def address_string(self):
        # 极速优化：重写此方法直接返回客户端 IP，彻底禁用 Windows 反向 DNS 查找导致的 500ms~2s 延迟停顿
        return self.client_address[0]

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query_params = urllib.parse.parse_qs(parsed.query)

        # 1. 尝试处理 API GET 请求
        if handle_get(self, path, query_params):
            return

        # 2. 静态页面入口
        if path in ["/", "/index.html"]:
            serve_static_file(self, os.path.join(WEB_DIR, "index.html"))
            return

        # 3. 静态资源托管 /css/*, /js/*, /assets/*
        clean_rel = path.lstrip("/")
        target_path = resolve_under(WEB_DIR, urllib.parse.unquote(clean_rel))
        if target_path and os.path.isfile(target_path):
            serve_static_file(self, target_path)
            return

        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"404 Not Found")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query_params = urllib.parse.parse_qs(parsed.query)

        # 尝试处理 API POST 请求
        if handle_post(self, path, query_params):
            return

        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"404 Not Found")

    def log_message(self, format, *args):
        # 静默默认请求日志，消除 I/O 锁竞争
        pass

def main():
    # 先占用监听端口，避免重复启动时先拉起第二套生产线程。
    server = ThreadingHTTPServer(("127.0.0.1", PORT), ModularHubHandler)
    recover_interrupted_tasks()
    # 启动后台守护任务：异步探活与缩略图极速预热
    start_cluster_monitor()
    start_thumbnail_prebuild_worker()
    start_dispatcher_daemon()

    # 升级为支持真正多线程高并发的 ThreadingHTTPServer
    url = f"http://127.0.0.1:{PORT}"
    log_msg("=" * 60)
    log_msg(f"🚀 ComfyUI 视频批量生产中枢 (高并发极速版) 已就绪: {url}")
    log_msg("=" * 60)
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log_msg("[*] 正在退出生产中枢服务...")
        server.server_close()

if __name__ == "__main__":
    main()
