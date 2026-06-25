#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_plsql_wrapper.py

Оборачивает произвольный SQL-скрипт в PL/SQL-генератор формата
    DECLARE ... DBMS_LOB.CREATETEMPORARY(v_SQL, TRUE);
            DBMS_LOB.APPEND(v_SQL, q'~ ... ~'); ...
    END;
который регистрирует текст SQL как шаг под DAG (Airflow).

Что делает за тебя:
  * режет SQL на куски строго < лимита VARCHAR2 (32767 байт), по границам строк;
  * считает длину в БАЙТАХ (UTF-8) — важно, т.к. кириллица в комментариях 2 байта;
  * сам подбирает q-разделитель (~ { [ ( < ...), если в куске встречается
    последовательность, которая закрыла бы литерал (напр. ~');
  * подставляет v_fd / v_wf (даг) / v_ts / v_patch_code (номер задачи);
  * по желанию добавляет PRE-SQL (truncate) и /*коммент-шага*/;
  * хвост-вызов фреймворка берёт из файла (--tail) и вставляет как есть.

Пример:
    python gen_plsql_wrapper.py \
        --sql        body.sql \
        --jira       PROJ-1234 \
        --dag        MY_DAG_NAME \
        --ts         my_load_step \
        --out        out.sql
        [--tail      tail_call.sql]
        [--target-table STAGING.MY_TARGET_TABLE]
        [--step-comment "my_mapping_step"]
"""

import argparse
import sys

# Лимит литерала VARCHAR2 в PL/SQL = 32767 байт. Берём запас.
MAX_BYTES_DEFAULT = 30000

# Кандидаты q-разделителей: (открывающий, закрывающий).
# Литерал q'<O> ... <C>' завершается последовательностью <C>'.
DELIMITERS = [
    ("~", "~"), ("{", "}"), ("[", "]"), ("(", ")"),
    ("<", ">"), ("!", "!"), ("|", "|"), ("#", "#"),
]


def byte_len(s: str, encoding: str = "utf-8") -> int:
    return len(s.encode(encoding))


def split_bytes(s: str, max_bytes: int) -> list:
    """Режет ОЧЕНЬ длинную строку (длиннее лимита) по границам символов."""
    out, cur, cur_b = [], [], 0
    for ch in s:
        cb = byte_len(ch)
        if cur and cur_b + cb > max_bytes:
            out.append("".join(cur))
            cur, cur_b = [ch], cb
        else:
            cur.append(ch)
            cur_b += cb
    if cur:
        out.append("".join(cur))
    return out


def to_units(sql: str, max_bytes: int) -> list:
    """SQL -> список 'юнитов' (строк), каждый гарантированно <= лимита."""
    units = []
    for line in sql.splitlines(keepends=True):
        if byte_len(line) <= max_bytes:
            units.append(line)
        else:
            units.extend(split_bytes(line, max_bytes))
    return units


def pack(units: list, max_bytes: int) -> list:
    """Жадно упаковывает юниты в куски <= лимита, не разрывая строки."""
    chunks, cur, cur_b = [], [], 0
    for u in units:
        ub = byte_len(u)
        if cur and cur_b + ub > max_bytes:
            chunks.append("".join(cur))
            cur, cur_b = [u], ub
        else:
            cur.append(u)
            cur_b += ub
    if cur:
        chunks.append("".join(cur))
    return chunks


def choose_delimiter(chunk: str):
    """Возвращает (open, close), для которого закрывающая последовательность
    <close>' НЕ встречается в куске. Иначе литерал закрылся бы раньше времени."""
    for o, c in DELIMITERS:
        if (c + "'") not in chunk:
            return o, c
    raise ValueError(
        "Не нашёл безопасный q-разделитель: кусок содержит все варианты "
        "закрывающих последовательностей. Уменьши --max-bytes или поправь SQL."
    )


def build_append(chunk: str) -> str:
    o, c = choose_delimiter(chunk)
    body = chunk.rstrip("\n")
    return f"  DBMS_LOB.APPEND(v_SQL, q'{o}\n{body}\n{c}');"


def sql_str_literal(val: str) -> str:
    """Безопасный строковый литерал для VARCHAR2 (удвоение одинарных кавычек)."""
    return "'" + val.replace("'", "''") + "'"


DEFAULT_TAIL = """  -- TODO: ХВОСТ. Вставь сюда вызов процедуры фреймворка, которая
  --       регистрирует/запускает v_SQL под DAG, используя уже объявленные
  --       v_fd, v_wf, v_ts, v_patch_code, v_SQL.
  --       (передай файл через --tail, чтобы он подставлялся автоматически)"""


def generate(sql_text, jira, dag, v_ts, v_fd="AIRFLOW",
             tail=None, target_table=None, step_comment=None,
             max_bytes=MAX_BYTES_DEFAULT):
    warnings = []
    if len(jira) > 25:
        warnings.append(f"v_patch_code '{jira}' длиннее 25 символов (varchar2(25)).")
    if len(dag) > 255:
        warnings.append(f"v_wf (даг) длиннее 255 символов.")
    if len(v_ts) > 255:
        warnings.append(f"v_ts длиннее 255 символов.")

    # Префикс: коммент-шага и PRE-SQL truncate (всё попадёт в первый кусок).
    prefix = ""
    if step_comment:
        prefix += f"/*{step_comment}*/\n"
    if target_table:
        prefix += (f"--PRE SQL for table {target_table}\n"
                   f"truncate table {target_table}; --$$P_SRC_TABLE_NAME\n\n")
    body_sql = prefix + sql_text

    chunks = pack(to_units(body_sql, max_bytes), max_bytes)
    appends = "\n".join(build_append(c) for c in chunks)
    tail_block = tail.rstrip("\n") if tail else DEFAULT_TAIL

    plsql = f"""DECLARE
  v_fd         varchar2(255) := {sql_str_literal(v_fd)};
  v_wf         varchar2(255) := {sql_str_literal(dag)};
  v_ts         varchar2(255) := {sql_str_literal(v_ts)};
  v_patch_code varchar2(25)  := {sql_str_literal(jira)};
  v_SQL        clob;
BEGIN
  DBMS_LOB.CREATETEMPORARY(v_SQL, TRUE);

{appends}

{tail_block}
END;
"""

    stats = {
        "chunks": len(chunks),
        "max_chunk_bytes": max(byte_len(c) for c in chunks) if chunks else 0,
        "total_bytes": byte_len(body_sql),
        "warnings": warnings,
    }
    return plsql, stats


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Оборачивает SQL в PL/SQL-генератор (DBMS_LOB) для DAG-загрузки.")
    p.add_argument("--sql", required=True, help="путь к файлу с телом SQL")
    p.add_argument("--jira", required=True, help="номер задачи -> v_patch_code")
    p.add_argument("--dag", required=True, help="имя дага -> v_wf")
    p.add_argument("--ts", required=True, help="имя шага -> v_ts")
    p.add_argument("--out", help="куда писать (по умолчанию <ts>_dag.sql)")
    p.add_argument("--fd", default="AIRFLOW", help="v_fd (по умолчанию AIRFLOW)")
    p.add_argument("--tail", help="файл с хвостом-вызовом фреймворка (вставится как есть)")
    p.add_argument("--target-table", help="если задано -> добавит PRE-SQL truncate")
    p.add_argument("--step-comment", help="если задано -> добавит /*коммент*/ в начало")
    p.add_argument("--max-bytes", type=int, default=MAX_BYTES_DEFAULT,
                   help=f"лимит байт на кусок (по умолчанию {MAX_BYTES_DEFAULT})")
    args = p.parse_args(argv)

    with open(args.sql, encoding="utf-8") as f:
        sql_text = f.read()
    tail = None
    if args.tail:
        with open(args.tail, encoding="utf-8") as f:
            tail = f.read()

    plsql, stats = generate(
        sql_text=sql_text, jira=args.jira, dag=args.dag, v_ts=args.ts,
        v_fd=args.fd, tail=tail, target_table=args.target_table,
        step_comment=args.step_comment, max_bytes=args.max_bytes)

    out_path = args.out or f"{args.ts}_dag.sql"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(plsql)

    print(f"OK -> {out_path}")
    print(f"  кусков APPEND: {stats['chunks']}")
    print(f"  макс. кусок:   {stats['max_chunk_bytes']} байт (лимит {args.max_bytes})")
    print(f"  всего SQL:     {stats['total_bytes']} байт")
    if not tail:
        print("  ВНИМАНИЕ: хвост не задан (--tail) — в файле стоит TODO-заглушка.")
    for w in stats["warnings"]:
        print(f"  ВНИМАНИЕ: {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())