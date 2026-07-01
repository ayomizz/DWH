"""
run.py — Генератор PL/SQL скрипта регистрации потока IPC.

Использование:
  1. Положи XML-файл из Informatica в папку input/
  2. Заполни блок НАСТРОЙКА ниже
  3. Запусти: python run.py
  4. Забери файл OUTPUT_*.sql из папки output/
"""

import os
import glob

from parser_pc import parse_powermart_xml
from generator import generate_plsql

# ══════════════════════════════════════════════════════════════════
#  НАСТРОЙКА — заполни перед запуском
# ══════════════════════════════════════════════════════════════════

# Имя XML-файла внутри папки input/ (или оставь "" — возьмётся первый .xml)
XML_FILE = ""

# Обязательно
PATCH_CODE = "PROJECT-XXXXX"               # ← номер задачи JIRA

# Меняй при необходимости (остальное подтянется из XML)
REG_NAME        = "WF_REG_YOUR_SCHEMA_REGULAR_DWH"
DNMPARAM_SCHEMA = "YOURSCHEMA"             # ← часть после DNMPARAM_

# Потоки-источники для подписок (добавляй строки если нужно)
SOURCE_WORKFLOWS = [
    # {"folder_from": "YOUR_FOLDER", "wf_from": "WF_SOURCE_NAME", "eventget_id": 1},
]

# Тип таблицы: OTHER | SHIST | STRAN_AGG | HDIM
# Если оставить "" — определится автоматически из имени целевой таблицы
TABLE_TYPE = ""

# ══════════════════════════════════════════════════════════════════
#  ЗАПУСК — ниже не трогать
# ══════════════════════════════════════════════════════════════════

INPUT_DIR  = "input"
OUTPUT_DIR = "output"

def main():
    # Создаём папки если нет
    os.makedirs(INPUT_DIR,  exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Ищем XML-файл
    if XML_FILE:
        xml_path = os.path.join(INPUT_DIR, XML_FILE)
    else:
        candidates = glob.glob(os.path.join(INPUT_DIR, "*.xml")) + \
                     glob.glob(os.path.join(INPUT_DIR, "*.XML"))
        if not candidates:
            print(f"[ОШИБКА] XML-файлы не найдены в папке {INPUT_DIR}/")
            print(f"         Положи файл в {INPUT_DIR}/ или укажи имя в XML_FILE.")
            return
        xml_path = candidates[0]
        print(f"[XML]  Найден файл: {xml_path}")

    if not os.path.exists(xml_path):
        print(f"[ОШИБКА] Файл не найден: {xml_path}")
        return

    # Парсим XML
    print("[...] Парсим XML...")
    with open(xml_path, "rb") as f:
        content = f.read()

    try:
        data = parse_powermart_xml(content)
    except Exception as e:
        print(f"[ОШИБКА] Не удалось разобрать XML: {e}")
        return

    # Выводим что нашли
    print()
    print("  Папка:          ", data["folder_name"]             or "(не найдено)")
    print("  Воркфлоу:       ", data["workflow_name"]           or "(не найдено)")
    print("  Целевая таблица:", data["target_name"]             or "(не найдено)")
    print("  Схема цели:     ", data["tgt_schema"]              or "(не найдено)")
    print("  Дельта:         ", data["src_delta_table"]         or "(не найдено)")
    print("  Сессии:         ", ", ".join(data["sessions"])     or "(нет)")
    print("  Тип таблицы:    ", data["table_type"])
    print()

    # Собираем параметры
    params = {
        **data,
        "patch_code":       PATCH_CODE,
        "reg_name":         REG_NAME,
        "source_workflows": SOURCE_WORKFLOWS,
        "table_type":       TABLE_TYPE or data["table_type"],
        "_dnmparam_schema": DNMPARAM_SCHEMA,
    }

    # Генерируем SQL
    print("[...] Генерируем PL/SQL...")
    try:
        sql = generate_plsql(params)
    except Exception as e:
        print(f"[ОШИБКА] Генерация не удалась: {e}")
        return

    # Сохраняем в output/
    wf_name  = data["workflow_name"] or "OUTPUT"
    out_path = os.path.join(OUTPUT_DIR, f"OUTPUT_{wf_name}.sql")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(sql)

    print(f"[OK]   Скрипт сохранён: {out_path}")
    print()

if __name__ == "__main__":
    main()
