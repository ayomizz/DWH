#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
serve.py — локальный веб-сервис для генерации PL/SQL-обёртки.

Запуск:
    python serve.py
Откроется браузер на http://localhost:8000 с формой: вставляешь SQL,
заполняешь поля -> получаешь готовый скрипт (копировать / скачать).

Без зависимостей (только стандартная библиотека). Работает локально,
слушает 127.0.0.1 — наружу в сеть не выставляется.

Требует рядом файл gen_plsql_wrapper.py (вся логика генерации — там).
"""

import argparse
import html
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

# импортируем generate() из соседнего файла
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from gen_plsql_wrapper import generate, MAX_BYTES_DEFAULT
except ImportError:
    sys.exit("Не найден gen_plsql_wrapper.py — положи его в ту же папку, что и serve.py")


FORM_HTML = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<title>PL/SQL wrapper</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root{--bg:#0f1115;--card:#171a21;--ink:#e6e8ec;--mut:#9aa3af;--line:#2a2f3a;--acc:#3b82f6;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
  .wrap{max-width:880px;margin:0 auto;padding:28px 20px 60px}
  h1{font-size:20px;margin:0 0 4px}
  .sub{color:var(--mut);margin:0 0 22px;font-size:13px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px}
  label{display:block;font-size:13px;color:var(--mut);margin:14px 0 6px}
  input,textarea{width:100%;background:#0d0f14;color:var(--ink);border:1px solid var(--line);
    border-radius:8px;padding:10px 12px;font-family:ui-monospace,Consolas,monospace;font-size:13px}
  textarea{resize:vertical}
  .row{display:flex;gap:14px}.row>div{flex:1}
  .opt{color:var(--mut);font-weight:400}
  button{margin-top:20px;background:var(--acc);color:#fff;border:0;border-radius:8px;
    padding:11px 20px;font-size:14px;font-weight:600;cursor:pointer}
  button:hover{filter:brightness(1.08)}
  .hint{font-size:12px;color:var(--mut);margin-top:6px}
</style></head><body><div class="wrap">
  <h1>PL/SQL wrapper generator</h1>
  <p class="sub">Вставь тело SQL и параметры — получишь готовую DBMS_LOB-обёртку под DAG.</p>
  <form method="post" action="/generate" id="f"><div class="card">
    <label>SQL-скрипт *</label>
    <textarea name="sql" id="sql" rows="14" required placeholder="insert into ... with ... select ..."></textarea>
    <div class="row">
      <div><label>Номер задачи (v_patch_code) *</label><input name="jira" id="jira" required placeholder="PROJ-1234"></div>
      <div><label>DAG (v_wf) *</label><input name="dag" id="dag" required placeholder="MY_DAG_NAME"></div>
    </div>
    <div class="row">
      <div><label>v_ts (имя шага) *</label><input name="ts" id="ts" required placeholder="my_load_step"></div>
      <div><label>v_fd</label><input name="fd" id="fd" value="AIRFLOW"></div>
    </div>
    <div class="row">
      <div><label>Целевая таблица <span class="opt">(необяз. — добавит truncate)</span></label><input name="target_table" id="target_table" placeholder="STAGING.MY_TARGET_TABLE"></div>
      <div><label>Коммент-шага <span class="opt">(необяз.)</span></label><input name="step_comment" id="step_comment"></div>
    </div>
    <label>Хвост — вызов фреймворка <span class="opt">(необяз., вставится как есть; запомнится)</span></label>
    <textarea name="tail" id="tail" rows="4" placeholder="-- вызов процедуры, которая регистрирует v_SQL под DAG"></textarea>
    <label>Лимит байт на кусок</label><input name="max_bytes" id="max_bytes" value="__MAXB__">
    <div class="hint">VARCHAR2-литерал максимум 32767 байт; режется по строкам, с запасом.</div>
    <button type="submit">Сгенерировать</button>
  </div></form>
</div>
<script>
  // запоминаем «постоянные» поля между запусками (SQL не запоминаем — он меняется)
  var KEEP=["jira","dag","ts","fd","target_table","step_comment","tail","max_bytes"];
  KEEP.forEach(function(k){var v=localStorage.getItem("plsql_"+k);
    if(v!==null){var el=document.getElementById(k); if(el) el.value=v;}});
  document.getElementById("f").addEventListener("submit",function(){
    KEEP.forEach(function(k){var el=document.getElementById(k);
      if(el) localStorage.setItem("plsql_"+k, el.value);});
  });
</script>
</body></html>"""


RESULT_HTML = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Результат</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root{--bg:#0f1115;--card:#171a21;--ink:#e6e8ec;--mut:#9aa3af;--line:#2a2f3a;--acc:#3b82f6;--ok:#22c55e;--warn:#f59e0b;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
  .wrap{max-width:980px;margin:0 auto;padding:28px 20px 60px}
  h1{font-size:19px;margin:0 0 14px}
  a.back{color:var(--mut);text-decoration:none;font-size:13px}
  .stats{display:flex;gap:18px;flex-wrap:wrap;margin:0 0 14px;font-size:13px;color:var(--mut)}
  .stats b{color:var(--ink)}
  .warn{color:var(--warn)}
  textarea{width:100%;height:60vh;background:#0d0f14;color:var(--ink);border:1px solid var(--line);
    border-radius:10px;padding:14px;font-family:ui-monospace,Consolas,monospace;font-size:13px;white-space:pre}
  .bar{display:flex;gap:10px;margin:14px 0}
  button{background:var(--acc);color:#fff;border:0;border-radius:8px;padding:10px 18px;font-size:14px;font-weight:600;cursor:pointer}
  button.ghost{background:#222733}
</style></head><body><div class="wrap">
  <a class="back" href="/">← назад к форме</a>
  <h1>Готово</h1>
  <div class="stats">
    <span>кусков APPEND: <b>__CHUNKS__</b></span>
    <span>макс. кусок: <b>__MAXBYTES__</b> байт</span>
    <span>всего SQL: <b>__TOTAL__</b> байт</span>
    __WARN__
  </div>
  <div class="bar">
    <button onclick="copy()">Скопировать</button>
    <button class="ghost" onclick="download()">Скачать .sql</button>
  </div>
  <textarea id="out" readonly>__PLSQL__</textarea>
</div>
<script>
  function copy(){navigator.clipboard.writeText(document.getElementById("out").value)
    .then(function(){alert("Скопировано");});}
  function download(){
    var blob=new Blob([document.getElementById("out").value],{type:"text/plain"});
    var a=document.createElement("a");a.href=URL.createObjectURL(blob);
    a.download=__FILENAME__;document.body.appendChild(a);a.click();a.remove();}
</script>
</body></html>"""


def render_result(plsql, stats, filename):
    warn = ""
    if stats["warnings"]:
        warn = '<span class="warn">⚠ ' + " · ".join(html.escape(w) for w in stats["warnings"]) + "</span>"
    return (RESULT_HTML
            .replace("__CHUNKS__", str(stats["chunks"]))
            .replace("__MAXBYTES__", str(stats["max_chunk_bytes"]))
            .replace("__TOTAL__", str(stats["total_bytes"]))
            .replace("__WARN__", warn)
            .replace("__PLSQL__", html.escape(plsql))
            .replace("__FILENAME__", json.dumps(filename)))


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, code=200):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(FORM_HTML.replace("__MAXB__", str(MAX_BYTES_DEFAULT)))
        else:
            self._send("<h1>404</h1><a href='/'>на форму</a>", 404)

    def do_POST(self):
        if self.path != "/generate":
            self._send("<h1>404</h1>", 404)
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8")
        f = parse_qs(raw, keep_blank_values=True)

        def g(k, default=""):
            return f.get(k, [default])[0]

        try:
            sql = g("sql")
            jira, dag, ts = g("jira"), g("dag"), g("ts")
            if not (sql.strip() and jira and dag and ts):
                raise ValueError("Заполни обязательные поля: SQL, jira, DAG, v_ts.")
            try:
                max_bytes = int(g("max_bytes", str(MAX_BYTES_DEFAULT)))
            except ValueError:
                max_bytes = MAX_BYTES_DEFAULT
            plsql, stats = generate(
                sql_text=sql, jira=jira, dag=dag, v_ts=ts,
                v_fd=g("fd", "AIRFLOW") or "AIRFLOW",
                tail=g("tail") or None,
                target_table=g("target_table") or None,
                step_comment=g("step_comment") or None,
                max_bytes=max_bytes)
            self._send(render_result(plsql, stats, f"{ts}_dag.sql"))
        except Exception as e:
            self._send(f"<div style='font:15px system-ui;color:#eee;background:#0f1115;"
                       f"padding:24px'><h1>Ошибка</h1><p>{html.escape(str(e))}</p>"
                       f"<a style='color:#9aa3af' href='/'>← назад</a></div>", 400)

    def log_message(self, *a):  # тише в консоли
        pass


def main(argv=None):
    p = argparse.ArgumentParser(description="Локальный веб-сервис для PL/SQL-обёртки.")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-browser", action="store_true", help="не открывать браузер автоматически")
    args = p.parse_args(argv)

    url = f"http://localhost:{args.port}"
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Сервис запущен: {url}  (Ctrl+C для остановки)")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
        srv.shutdown()


if __name__ == "__main__":
    main()