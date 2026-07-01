"""
parser_pc.py — Парсер XML-выгрузки Informatica PowerCenter.
Извлекает метаданные для генерации PL/SQL скрипта регистрации.
"""

import re
import xml.etree.ElementTree as ET

# Таблицы-утилиты, которые не являются основной целевой сущностью
EXCLUDE_TARGETS = {'DUAL', 'LOG_PARAMPREVVALUE'}


def parse_powermart_xml(content: bytes) -> dict:
    """
    Парсит XML-выгрузку IPC (кодировка windows-1251).

    Возвращает словарь:
        folder_name       — имя папки (NOTSHARED)
        workflow_name     — имя воркфлоу
        fsd_description   — описание из тега WORKFLOW (очищенное)
        tgt_schema        — схема целевой таблицы (из Expression)
        target_name       — имя целевой таблицы
        src_schema        — схема источника (по умолчанию = folder_name)
        src_delta_table   — имя дельта-таблицы
        sessions          — список имён сессий в воркфлоу
        sources           — список имён SOURCE-объектов
        targets           — список имён TARGET-объектов
        table_type        — тип таблицы: SHIST | STRAN_AGG | HDIM | OTHER
    """

    # 1. Декодируем из windows-1251
    try:
        text = content.decode('windows-1251')
    except UnicodeDecodeError:
        text = content.decode('utf-8', errors='replace')

    # 2. Убираем DOCTYPE (ElementTree не умеет работать с внешними DTD)
    text = re.sub(r'<!DOCTYPE[^[>]*?(?:\[[^\]]*])?\s*>', '', text, flags=re.DOTALL)

    # 3. Меняем объявление кодировки на utf-8
    text = re.sub(r'(encoding=")[^"]*(")', r'\1utf-8\2', text, count=1)

    # 4. Парсим XML
    try:
        root = ET.fromstring(text.encode('utf-8'))
    except ET.ParseError as e:
        raise ValueError(f"Ошибка разбора XML: {e}")

    result = {
        'folder_name': '',
        'workflow_name': '',
        'fsd_description': '',
        'tgt_schema': '',
        'target_name': '',
        'src_schema': '',
        'src_delta_table': '',
        'sessions': [],
        'sources': [],
        'targets': [],
        'table_type': 'OTHER',
    }

    repo = root.find('REPOSITORY')
    if repo is None:
        raise ValueError("Элемент REPOSITORY не найден в XML")

    # ── Обходим все папки ──────────────────────────────────────────────────
    for folder in repo.findall('FOLDER'):
        folder_name = folder.get('NAME', '')
        is_shared   = folder.get('SHARED', '') == 'SHARED'

        # Собираем источники и цели из всех папок
        for src in folder.findall('SOURCE'):
            name = src.get('NAME', '')
            if name and name != 'DUAL' and name not in result['sources']:
                result['sources'].append(name)

        for tgt in folder.findall('TARGET'):
            name = tgt.get('NAME', '')
            if name and name not in result['targets']:
                result['targets'].append(name)

        # ── Основная (несвязанная) папка ───────────────────────────────────
        if not is_shared:
            result['folder_name'] = folder_name

            # WORKFLOW может быть прямым потомком FOLDER или вложен глубже
            workflow = folder.find('WORKFLOW') or folder.find('.//WORKFLOW')
            if workflow is not None:
                result['workflow_name'] = workflow.get('NAME', '')
                desc = workflow.get('DESCRIPTION', '')
                # Убираем переносы строк из описания
                clean = re.sub(r'[\r\n]+', ' ', desc).strip()
                result['fsd_description'] = clean

                # Имена сессий берём из TASKINSTANCE[@TASKTYPE='Session']
                for ti in workflow.findall('.//TASKINSTANCE'):
                    if ti.get('TASKTYPE') == 'Session':
                        # TASKNAME — ссылка на реальный SESSION-объект
                        name = ti.get('TASKNAME') or ti.get('NAME', '')
                        if name and name not in result['sessions']:
                            result['sessions'].append(name)

            # Если TASKINSTANCE не нашли — берём SESSION напрямую из папки
            if not result['sessions']:
                for session in folder.findall('.//SESSION'):
                    name = session.get('NAME', '')
                    if name and name not in result['sessions']:
                        result['sessions'].append(name)

        # ── Ищем схему из Expression-трансформации ─────────────────────────
        if not result['tgt_schema']:
            for transf in folder.findall('.//TRANSFORMATION'):
                if transf.get('TYPE') != 'Expression':
                    continue
                for field in transf.findall('TRANSFORMFIELD'):
                    fname = field.get('NAME', '')
                    if 'TARG_SCHEMA' in fname or 'TARGET_SCHEMA' in fname:
                        expr = field.get('EXPRESSION', '')
                        # Ищем 'SCHEMA_NAME' — одиночная кавычка + заглавный идентификатор
                        m = re.search(r"'([A-Z][A-Z0-9_]{2,})'", expr)
                        if m:
                            result['tgt_schema'] = m.group(1)

    # ── Основная целевая таблица ───────────────────────────────────────────
    main_targets = [t for t in result['targets'] if t not in EXCLUDE_TARGETS]
    if main_targets:
        result['target_name'] = main_targets[0]

    # ── Определяем тип таблицы по имени цели ──────────────────────────────
    tgt_upper = result['target_name'].upper()
    if any(s in tgt_upper for s in ('SHIST', 'BSHIST')):
        result['table_type'] = 'SHIST'
    elif any(s in tgt_upper for s in ('STRAN', 'SSTAT', '_AGG')):
        result['table_type'] = 'STRAN_AGG'
    elif 'HDIM' in tgt_upper:
        result['table_type'] = 'HDIM'

    # ── Значения по умолчанию ─────────────────────────────────────────────
    result['src_schema'] = result['folder_name']

    # Имя дельта-таблицы: первый SOURCE (не DUAL) + суффикс _DMDELTA
    non_dual = [s for s in result['sources'] if s != 'DUAL']
    if non_dual:
        result['src_delta_table'] = non_dual[0] + '_DMDELTA'

    return result
