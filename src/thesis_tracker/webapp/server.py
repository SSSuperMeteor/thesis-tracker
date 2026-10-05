"""The loopback HTTP server and its single-slot analysis worker.

Security model (all of it tested in ``tests/test_webapp_security.py``):

* every start generates a random access token, printed inside the URL; every
  request — including the read-only ones — must present it, by cookie or header;
* the ``Host`` header must name this loopback server and this port, which blocks
  DNS-rebinding attempts;
* a present ``Origin`` must be this same origin, and no CORS header is ever sent;
* endpoints that change state or cost money accept ``POST`` only;
* every response carries ``Content-Security-Policy: default-src 'self'``;
* no endpoint returns an environment value, and no log line contains the token.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from thesis_tracker.decision.agent import DeepSeekClient
from thesis_tracker.decision.core import DEFAULT_ARCHIVE
from thesis_tracker.decision.evidence import HORIZON_LABELS
from thesis_tracker.financial.pit_store import DEFAULT_FACT_DB
from thesis_tracker.prices import DEFAULT_DB as DEFAULT_PRICE_DB
from thesis_tracker.webapp import service
from thesis_tracker.webapp.data import tickers
from thesis_tracker.webapp.jobs import DEFAULT_JOB_DB, KIND_ANALYZE, JobStore

STATIC_DIR = Path(__file__).resolve().parent / "static"
# Endpoints that change state or spend money never answer a GET.
POST_ONLY_PATHS = frozenset({"/api/analyze"})
COOKIE_NAME = "dsh_token"
ALLOWED_HOSTS = ("127.0.0.1", "localhost")
MAX_BODY_BYTES = 64 * 1024
CSP = ("default-src 'self'; img-src 'self' data:; base-uri 'none'; "
       "form-action 'none'; frame-ancestors 'none'; object-src 'none'")
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
                 ".ico": "image/x-icon"}
# Injected by tests.  Production uses the real DeepSeek client, which reads the
# API key from the environment and never exposes it to the browser.
CLIENT_FACTORY = DeepSeekClient


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "thesis-tracker-webapp"
    sys_version = ""

    # -- plumbing ------------------------------------------------------------

    def log_message(self, fmt, *args):  # noqa: A002 - stdlib signature
        """Keep the query string (and therefore any token) out of the logs."""

    def do_GET(self):  # noqa: N802 - stdlib naming
        self._handle("GET")

    def do_HEAD(self):  # noqa: N802
        self._handle("GET")

    def do_POST(self):  # noqa: N802
        self._handle("POST")

    def _reject_method(self):
        self._json(405, {"error": "这个接口不接受该请求方法。"})

    do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = _reject_method

    def handle_one_request(self) -> None:
        """Never leave a request unanswered: every path writes exactly one response."""
        self._responded = False
        try:
            super().handle_one_request()
        except (ConnectionError, TimeoutError, OSError):
            self.close_connection = True
            return
        if getattr(self, "_responded", False) or self.close_connection is True:
            return
        if not self.raw_requestline:
            return
        self._json(500, {"error": "服务器内部错误。"})

    def _send(self, status: int, body: bytes, content_type: str,
              extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self._responded = True
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload, extra: dict | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self._send(status, body, "application/json; charset=utf-8", extra)

    def _html(self, status: int, text: str) -> None:
        self._send(status, text.encode(), "text/html; charset=utf-8")

    # -- request handling ----------------------------------------------------

    def _handle(self, method: str) -> None:
        app = self.server.app  # type: ignore[attr-defined]
        parsed = urlparse(self.path)
        if not self._host_allowed(app):
            self._json(403, {"error": "Host 请求头不是本机的这个端口，请求已拒绝。"})
            return
        if not self._origin_allowed(app):
            self._json(403, {"error": "Origin 请求头不是本站，请求已拒绝。"})
            return
        if method == "POST":
            body, error = self._read_json_body()
            if error is not None:
                self._json(*error)
                return
            if not self._token_valid(app):
                self._json(401, {"error": "缺少或错误的访问令牌；请用启动时打印的网址打开。"})
                return
            self._route_post(parsed, body)
            return
        if parsed.path == "/":
            self._serve_root(app, parsed)
            return
        if not self._token_valid(app):
            self._json(401, {"error": "缺少或错误的访问令牌；请用启动时打印的网址打开。"})
            return
        if parsed.path.startswith("/api/"):
            self._route_get(parsed)
        else:
            self._serve_static(parsed.path)

    def _host_port(self) -> int:
        return self.server.server_address[1]

    def _host_allowed(self, app) -> bool:
        header = self.headers.get("Host")
        if not header:
            return False
        name, _, port = header.rpartition(":")
        if not name or not port.isdigit():
            return False
        return name.lower() in ALLOWED_HOSTS and int(port) == self._host_port()

    def _origin_allowed(self, app) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        parsed = urlparse(origin)
        if parsed.scheme not in {"http", "https"}:
            return False
        try:
            port = parsed.port
        except ValueError:
            return False
        if parsed.hostname is None or parsed.hostname.lower() not in ALLOWED_HOSTS:
            return False
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            return False
        return (port or (443 if parsed.scheme == "https" else 80)) == self._host_port()

    def _token_valid(self, app) -> bool:
        supplied = _token_from_cookie(self.headers.get("Cookie"))
        if supplied is None:
            header = self.headers.get("Authorization") or ""
            if header.startswith("Bearer "):
                supplied = header[len("Bearer "):]
        if supplied is None:
            supplied = self.headers.get("X-DSH-Token")
        return supplied is not None and secrets.compare_digest(supplied, app.token)

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None, (400, {"error": "Content-Length 无效。"})
        if length > MAX_BODY_BYTES:
            return None, (413, {"error": "请求体超过上限。"})
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}, None
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, (400, {"error": "请求体不是有效的 JSON。"})
        if not isinstance(payload, dict):
            return None, (400, {"error": "请求体必须是 JSON 对象。"})
        return payload, None

    # -- routes --------------------------------------------------------------

    def _route_get(self, parsed) -> None:
        app = self.server.app  # type: ignore[attr-defined]
        path = parsed.path
        query = parse_qs(parsed.query)
        reference = date.today().isoformat()
        if path in POST_ONLY_PATHS:
            self._json(405, {"error": "这个接口只接受 POST。"})
            return
        try:
            if path == "/api/overview":
                self._json(200, service.overview(
                    fact_db=app.fact_db, price_db=app.price_db, card_db=app.card_db,
                    reference_date=reference))
                return
            if path == "/api/companies":
                self._json(200, {"tickers": _known_tickers(app)})
                return
            if path.startswith("/api/companies/"):
                ticker = unquote(path[len("/api/companies/"):]).upper()
                if ticker not in _known_tickers(app):
                    self._json(404, {"error": "数据库里没有这家公司。"})
                    return
                as_of = _first(query, "as_of") or reference
                self._json(200, service.company_page(
                    fact_db=app.fact_db, price_db=app.price_db, card_db=app.card_db,
                    ticker=ticker, as_of=as_of, reference_date=reference))
                return
            if path == "/api/cards":
                horizon = _first(query, "horizon")
                self._json(200, {"cards": service.card_list(
                    card_db=app.card_db, ticker=_first(query, "ticker"), horizon=horizon)})
                return
            if path.startswith("/api/cards/"):
                card_id = unquote(path[len("/api/cards/"):])
                self._json(200, service.card_detail(card_db=app.card_db, card_id=card_id))
                return
            if path == "/api/usage":
                self._json(200, service.usage_average(app.card_db))
                return
            if path == "/api/jobs":
                self._json(200, {"jobs": app.store.list()})
                return
            if path.startswith("/api/jobs/"):
                job = app.store.get(unquote(path[len("/api/jobs/"):]))
                if job is None:
                    self._json(404, {"error": "没有这个任务。"})
                    return
                self._json(200, job)
                return
        except KeyError:
            self._json(404, {"error": "找不到请求的对象。"})
            return
        except FileNotFoundError:
            self._json(503, {"error": "本地数据库不存在，请先在终端运行采集命令。"})
            return
        except sqlite3.Error:
            self._json(500, {"error": "本地数据库不可读取。"})
            return
        except ValueError:
            self._json(400, {"error": "请求参数无效。"})
            return
        self._json(404, {"error": "没有这个接口。"})

    def _route_post(self, parsed, body: dict) -> None:
        app = self.server.app  # type: ignore[attr-defined]
        if parsed.path != "/api/analyze":
            self._json(404, {"error": "没有这个接口。"})
            return
        if body.get("confirm") is not True:
            self._json(400, {"error": "创建分析任务需要确认；请在请求中带 confirm=true。"})
            return
        ticker = str(body.get("ticker") or "").upper()
        horizon = body.get("horizon") or "mid"
        as_of = body.get("as_of") or date.today().isoformat()
        if ticker not in _known_tickers(app):
            self._json(400, {"error": "数据库里没有这家公司，无法分析。"})
            return
        if horizon not in HORIZON_LABELS:
            self._json(400, {"error": "周期只能是 short、mid 或 long。"})
            return
        try:
            date.fromisoformat(str(as_of))
        except ValueError:
            self._json(400, {"error": "as_of 必须是 YYYY-MM-DD 日期。"})
            return
        job = app.store.create(KIND_ANALYZE, {"ticker": ticker, "horizon": horizon,
                                              "as_of": str(as_of)})
        self._json(200, {"job": job})

    # -- static --------------------------------------------------------------

    def _serve_root(self, app, parsed) -> None:
        supplied = _first(parse_qs(parsed.query), "token")
        if supplied is not None and secrets.compare_digest(supplied, app.token):
            self._send(302, b"", "text/plain; charset=utf-8", extra={
                "Location": "/",
                "Set-Cookie": f"{COOKIE_NAME}={app.token}; Path=/; HttpOnly; SameSite=Strict",
            })
            return
        if self._token_valid(app):
            self._send_file(STATIC_DIR / "index.html")
            return
        self._html(401, "<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">"
                        "<title>需要访问令牌</title>"
                        "<p>请用启动时终端里打印的那个网址打开本页（网址里带访问令牌）。</p>")

    def _serve_static(self, path: str) -> None:
        relative = unquote(path).lstrip("/")
        if not relative or ".." in Path(relative).parts:
            self._json(404, {"error": "没有这个文件。"})
            return
        target = STATIC_DIR / relative
        try:
            target = target.resolve()
            target.relative_to(STATIC_DIR.resolve())
        except (OSError, ValueError):
            self._json(404, {"error": "没有这个文件。"})
            return
        if not target.is_file():
            self._json(404, {"error": "没有这个文件。"})
            return
        self._send_file(target)

    def _send_file(self, target: Path) -> None:
        try:
            body = target.read_bytes()
        except OSError:
            self._json(500, {"error": "静态文件不可读取。"})
            return
        self._send(200, body, CONTENT_TYPES.get(target.suffix, "application/octet-stream"))


def _token_from_cookie(header: str | None) -> str | None:
    if not header:
        return None
    for part in header.split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE_NAME and value:
            return value
    return None


def _first(query: dict, key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _known_tickers(app) -> list[str]:
    """Companies the fact database actually holds; never a hardcoded list."""
    if app._ticker_cache is None:
        app._ticker_cache = tickers(app.fact_db)
    return app._ticker_cache


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class AnalysisWorker:
    """One background thread, one running analysis at a time, the rest queued."""

    def __init__(self, store: JobStore, *, archive_path, fact_db, price_db) -> None:
        self.store = store
        self.archive_path = Path(archive_path)
        self.fact_db = Path(fact_db)
        self.price_db = Path(price_db)
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()
        self._stopping = False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="webapp-worker", daemon=True)
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self, timeout: float = 5.0) -> None:
        self._stopping = True
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _loop(self) -> None:
        while not self._stopping:
            job = self.store.claim_next()
            if job is None:
                self._wake.wait(0.5)
                self._wake.clear()
                continue
            self._run(job)

    def _run(self, job: dict) -> None:
        def progress(event: dict) -> None:
            self.store.append_progress(job["job_id"], event)

        try:
            result = self._analyze(job, progress)
        except Exception as exc:
            # Log nothing but the exception type: SDK errors can carry headers.
            self.store.finish(job["job_id"], status="failed",
                              error=f"分析失败（{type(exc).__name__}）。")
            return
        if result.get("status") == "passed":
            self.store.finish(job["job_id"], status="succeeded", result={
                "card_id": result["card_id"], "ticker": result["card"]["ticker"],
                "as_of": result["card"]["as_of"], "horizon": result["card"]["horizon"],
                "analysis_id": result["analysis_id"], "stats": _safe_stats(result["stats"]),
            })
            return
        self.store.finish(job["job_id"], status="failed",
                          error=f"未通过校验：{result.get('reason') or 'unknown'}",
                          result={"violations": result.get("violations") or [],
                                  "analysis_id": result["analysis_id"]})

    def _analyze(self, job: dict, progress) -> dict:
        from thesis_tracker.decision.agent import run_analysis

        parameters = job["parameters"]
        return run_analysis(parameters["ticker"], parameters["as_of"],
                            horizon=parameters["horizon"],
                            client=CLIENT_FACTORY(), archive_path=self.archive_path,
                            price_db=self.price_db, fact_db=self.fact_db,
                            progress=progress)


def _safe_stats(stats: dict) -> dict:
    """Only the counters the job page shows; never message bodies."""
    return {key: stats[key] for key in ("rounds", "tool_calls", "revisions",
                                        "input_tokens", "output_tokens",
                                        "cache_hit_tokens")
            if key in stats}


class WebApp:
    """A started server plus its worker; ``stop`` shuts both down."""

    def __init__(self, *, fact_db=DEFAULT_FACT_DB, price_db=DEFAULT_PRICE_DB,
                 card_db=DEFAULT_ARCHIVE, job_db=DEFAULT_JOB_DB, host: str = "127.0.0.1",
                 port: int = 8000, token: str | None = None,
                 start_worker: bool = True) -> None:
        self.fact_db = Path(fact_db)
        self.price_db = Path(price_db)
        self.card_db = Path(card_db)
        self.job_db = Path(job_db)
        self.host = host
        self.token = token or secrets.token_urlsafe(32)
        self.store = JobStore(self.job_db)
        self._ticker_cache: list[str] | None = None
        self._httpd = _Server((host, port), _Handler)
        self._httpd.app = self  # type: ignore[attr-defined]
        self._worker = (AnalysisWorker(self.store, archive_path=self.card_db,
                                       fact_db=self.fact_db,
                                       price_db=self.price_db)
                        if start_worker else None)
        self.port = self._httpd.server_address[1]
        self.interrupted_jobs: list[str] = []

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/?token={self.token}"

    def start(self) -> None:
        # A restart cannot know whether an interrupted model call completed, so
        # jobs left running are reported as interrupted rather than retried.
        self.interrupted_jobs = self.store.mark_interrupted()
        thread = threading.Thread(target=self._httpd.serve_forever, name="webapp-http",
                                  daemon=True)
        thread.start()
        self._thread = thread
        if self._worker is not None:
            self._worker.start()

    def stop(self) -> None:
        if self._worker is not None:
            self._worker.stop()
        self._httpd.shutdown()
        self._httpd.server_close()
        thread = getattr(self, "_thread", None)
        if thread is not None:
            thread.join(timeout=5)


def format_startup(app: WebApp) -> str:
    """The one line the user needs; the only place the token is ever printed."""
    lines = [f"本机网页已启动：{app.url}",
             f"只在 127.0.0.1 上监听，端口 {app.port}；关掉这个终端即停止。",
             "网址里的访问令牌等同密码，不要分享；日志和接口都不会再打印它。"]
    if app.interrupted_jobs:
        lines.append(f"上次运行中的 {len(app.interrupted_jobs)} 个任务已标记为中断。")
    return "\n".join(lines)


__all__ = ["AnalysisWorker", "WebApp", "format_startup"]
