#!/usr/bin/env python3
"""
llamacpp-ollama-shim.py  -  Puente entre el agente de triaje y llama.cpp.

POR QUE EXISTE
--------------
`triage_agent.py` habla la API de Ollama en dos endpoints:
    POST /v1/chat/completions   (clasificar y redactar)   -> ya es formato OpenAI
    POST /api/embed             (embeddings semanticos)   -> formato nativo de Ollama
    GET  /api/version           (comprobacion de salud)

`llama.cpp` (llama-server) sirve /v1/chat/completions y /v1/embeddings (ambos OpenAI),
pero NO sirve /api/embed. Este shim escucha en el puerto que el agente espera
(por defecto 11434, el mismo de Ollama), enruta el chat a una instancia de llama-server
y traduce /api/embed a /v1/embeddings de otra instancia.

Asi el repo queda SIN MODIFICAR y `config.json` no cambia: sigue apuntando a
http://localhost:11434.

ARQUITECTURA
------------
    triage_agent.py
        |  :11434  (este shim)
        +--> /v1/chat/completions  ->  llama-server CHAT        :8080
        +--> /api/embed            ->  llama-server EMBEDDINGS  :8081   (/v1/embeddings)

USO
---
    # 1) servidor de chat (un modelo generativo)
    llama-server.exe -m C:\\ProgramData\\llm\\Qwen3-4B-Q4_K_M.gguf -c 8192 --port 8080

    # 2) servidor de embeddings (modelo BERT)
    llama-server.exe -m C:\\ProgramData\\llm\\bge-m3-Q8_0.gguf -c 8192 --port 8081 --embeddings

    # 3) este shim
    python llamacpp-ollama-shim.py

Sin dependencias externas: solo la libreria estandar de Python 3.8+.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CHAT_URL = "http://127.0.0.1:8080"
EMBED_URL = "http://127.0.0.1:8081"
# Nombre de modelo que anuncian los llama-server (se pasa con --alias). Si se deja vacio,
# el campo "model" del cliente se reenvia tal cual (comportamiento por defecto de Ollama).
# Se rellena solo si llama-server rechaza el nombre del agente con un 400.
MODEL_ALIAS = ""
# Timeouts hacia llama-server. DEBEN ser mayores que los del agente (config.json:
# llm_timeout / embed_timeout), porque si el shim se rinde antes devuelve un 502 y el
# agente lo ve como error aunque el modelo solo fuera lento. Medido: en esta VM (1 core)
# un correo real puede tardar 3-5 min; con 300 s el shim abortaba.
CHAT_TIMEOUT = 1800
EMBED_TIMEOUT = 600


def _post_json(base, path, payload, timeout=300):
    """POST a llama-server y devuelve (status, dict)."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base + path, data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"error": body[:500]}
    except Exception as e:
        return 502, {"error": f"{e.__class__.__name__}: {e}"}


def _get_json(base, path, timeout=15):
    try:
        with urllib.request.urlopen(base + path, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return 502, {"error": f"{e.__class__.__name__}: {e}"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # Log compacto a stderr; el agente captura stdout/stderr a su manera.
        sys.stderr.write("[shim] " + (fmt % args) + "\n")

    def _send(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---- salud: el agente / SETUP usan /api/version para comprobar que el cerebro vive ----
    def do_GET(self):
        if self.path.startswith("/api/version"):
            st, _ = _get_json(CHAT_URL, "/health")
            self._send(200, {"version": "llamacpp-shim-1.0", "chat_backend": "up" if st == 200 else "down"})
            return
        if self.path.startswith("/api/tags"):
            # Lista de modelos "instalados" (informativo).
            self._send(200, {"models": [{"name": "llama.cpp-chat"}, {"name": "llama.cpp-embed"}]})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception as e:
            self._send(400, {"error": f"JSON invalido: {e}"})
            return

        # ---- CHAT: ya es formato OpenAI -> se reenvia tal cual ----
        if self.path.startswith("/v1/chat/completions") or self.path.startswith("/api/chat"):
            # Solo si se configuro --model-alias se reescribe el nombre de modelo.
            if MODEL_ALIAS:
                payload["model"] = MODEL_ALIAS
            st, out = _post_json(CHAT_URL, "/v1/chat/completions", payload, timeout=CHAT_TIMEOUT)
            self._send(st, out)
            return

        # ---- EMBEDDINGS: Ollama -> OpenAI ----
        if self.path.startswith("/api/embed") or self.path.startswith("/api/embeddings"):
            # Ollama recibe {"model": ..., "input": "texto"} o {"prompt": "texto"}
            inp = payload.get("input")
            if inp is None:
                inp = payload.get("prompt", "")
            if isinstance(inp, str):
                inp = [inp]
            st, out = _post_json(EMBED_URL, "/v1/embeddings", {"input": inp}, timeout=EMBED_TIMEOUT)
            if st != 200:
                self._send(st, out)
                return
            # OpenAI devuelve {"data":[{"embedding":[...]}, ...]}
            # Ollama devuelve {"embeddings":[[...], ...]}
            vecs = [d.get("embedding", []) for d in out.get("data", [])]
            self._send(200, {"embeddings": vecs, "model": payload.get("model", "llama.cpp-embed")})
            return

        self._send(404, {"error": f"endpoint no soportado por el shim: {self.path}"})


def main():
    ap = argparse.ArgumentParser(description="Shim API Ollama -> llama.cpp")
    ap.add_argument("--host", default="127.0.0.1", help="interfaz de escucha (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=11434, help="puerto (default 11434, el de Ollama)")
    ap.add_argument("--chat", default="http://127.0.0.1:8080", help="base URL del llama-server de chat")
    ap.add_argument("--embed", default="http://127.0.0.1:8081", help="base URL del llama-server de embeddings")
    ap.add_argument("--model-alias", default="",
                    help="reescribe el campo model a este valor (solo si llama-server lo exige)")
    ap.add_argument("--chat-timeout", type=int, default=1800,
                    help="segundos de espera al backend de chat (debe superar el llm_timeout del agente)")
    ap.add_argument("--embed-timeout", type=int, default=600,
                    help="segundos de espera al backend de embeddings")
    ap.add_argument("--check", action="store_true", help="comprueba los backends y sale")
    args = ap.parse_args()

    global CHAT_URL, EMBED_URL, MODEL_ALIAS, CHAT_TIMEOUT, EMBED_TIMEOUT
    CHAT_URL, EMBED_URL = args.chat.rstrip("/"), args.embed.rstrip("/")
    MODEL_ALIAS = args.model_alias
    CHAT_TIMEOUT = args.chat_timeout
    EMBED_TIMEOUT = args.embed_timeout

    if args.check:
        cs, _ = _get_json(CHAT_URL, "/health")
        es, _ = _get_json(EMBED_URL, "/health")
        print(f"  chat  {CHAT_URL}/health -> {cs}")
        print(f"  embed {EMBED_URL}/health -> {es}")
        print("  " + ("TODO OK" if cs == 200 and es == 200 else "REVISAR: algun backend no responde"))
        return

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[shim] escuchando en http://{args.host}:{args.port}")
    print(f"[shim]   /v1/chat/completions -> {CHAT_URL}   (timeout {CHAT_TIMEOUT}s)")
    print(f"[shim]   /api/embed           -> {EMBED_URL}/v1/embeddings   (timeout {EMBED_TIMEOUT}s)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[shim] parado")


if __name__ == "__main__":
    main()
