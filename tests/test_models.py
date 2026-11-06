import io
import json
import threading
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from inspect_cj import models
from inspect_cj.models import ModelError, ResponsesModel, ScriptedModel, parse_object


def test_model_json_rejects_duplicates_nonfinite_and_nonobject():
    for text in (
        '{"x":1,"x":2}',
        '{"x":NaN}',
        '{"x":1e309}',
        '{"x":-1e309}',
        "[]",
        "```json\n{}\n```",
    ):
        with pytest.raises(ModelError):
            parse_object(text)


def test_scripted_transport_enforces_roles_and_exhaustion():
    model = ScriptedModel([{"role": "inspector", "response": {"actions": []}}])
    assert model.complete("inspector", {}) == {"actions": []}
    with pytest.raises(ModelError, match="exhausted"):
        model.complete("coder", {})


def route_responses_worker(monkeypatch, server):
    build_opener = urllib.request.build_opener

    class LocalHTTPS(urllib.request.HTTPSHandler):
        def https_open(self, request):
            assert request.host == "api.openai.com"
            return self.do_open(
                lambda _host, **kwargs: HTTPConnection("127.0.0.1", server.server_port, **kwargs),
                request,
            )

    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        lambda *handlers: build_opener(urllib.request.ProxyHandler({}), *handlers, LocalHTTPS),
    )

    def run_worker(_args, *, input_text, **_kwargs):
        output = io.StringIO()
        monkeypatch.setattr(models.sys, "stdin", io.StringIO(input_text))
        code = 0
        with redirect_stdout(output), redirect_stderr(output):
            try:
                models.responses_worker()
            except SystemExit as error:
                code = error.code
        return SimpleNamespace(returncode=code, output=output.getvalue(), timed_out=False)

    monkeypatch.setattr(models, "run_command", run_worker)


def test_responses_transport_serializes_schema_and_extracts_text(tmp_path, monkeypatch):
    observed = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            observed["path"] = self.path
            observed["authorization"] = self.headers["Authorization"]
            observed["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "status": "completed",
                        "output": [
                            {
                                "type": "message",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": '{"source":"main() { 0 }","summary":"fix"}',
                                    }
                                ],
                            }
                        ],
                    }
                ).encode()
            )

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("OPENAI_API_KEY", "local-test-secret")
    try:
        route_responses_worker(monkeypatch, server)
        model = ResponsesModel("test-model")
        reply = model.complete("coder", {"requirement": "zero"}, timeout=5)
        assert observed["path"] == "/v1/responses"
        assert reply["source"] == "main() { 0 }"
        assert observed["authorization"] == "Bearer local-test-secret"
        assert observed["body"]["text"]["format"]["type"] == "json_schema"
        assert observed["body"]["model"] == "test-model"
        assert observed["body"]["store"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_responses_transport_does_not_forward_credentials_through_redirect(monkeypatch):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append("POST")
            self.send_response(302)
            self.send_header("Location", "/credential-sink")
            self.end_headers()

        def do_GET(self):
            requests.append("GET")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("OPENAI_API_KEY", "redirect-test-secret")
    try:
        route_responses_worker(monkeypatch, server)
        model = ResponsesModel("test")
        with pytest.raises(ModelError):
            model.complete("coder", {}, timeout=5)
        assert requests == ["POST"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_responses_transport_rejects_endpoint_override():
    with pytest.raises(TypeError):
        ResponsesModel("test", endpoint="http://localhost/v1/responses")
