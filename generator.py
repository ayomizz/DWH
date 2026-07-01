"""
generator.py — Генератор PL/SQL скрипта регистрации потока.
Воспроизводит шаблон UTL_MD_UPSERT по метаданным из XML + ввод пользователя.
"""


def _q(val: str) -> str:
    """Оборачивает значение в одинарные кавычки PL/SQL (с экранированием)."""
    return "'" + str(val).replace("'", "''") + "'"


def generate_plsql(p: dict) -> str:
    """
    Генерирует PL/SQL-скрипт регистрации потока в MD-таблицах.

    Ожидаемые ключи словаря p:
        folder_name, workflow_name, fsd_description
        tgt_schema, target_name
        src_schema, src_delta_table
        patch_code      — номер задачи JIRA
        reg_name        — имя регламента
        sessions        — list[str]  (имена сессий)
        source_workflows — list[dict] с ключами: folder_from, wf_from,
                          reg_from (опц.), eventget_id (опц., default=1)
        table_type      — SHIST | STRAN_AGG | HDIM | OTHER
    """
    folder     = p.get('folder_name', '')
    workflow   = p.get('workflow_name', '')
    fsd        = p.get('fsd_description', 'FSD:Название (FIT)')
    tgt_schema = p.get('tgt_schema', '')
    table      = p.get('target_name', '')
    src_schema = p.get('src_schema', folder)
    src_delta  = p.get('src_delta_table', 'NAME_DMDELTA')
    patch_code = p.get('patch_code', 'PROJECT-XXXXX')
    reg_name   = p.get('reg_name', 'WF_REG_YOUR_SCHEMA_REGULAR_DWH')
    sessions   = p.get('sessions', [])
    source_wfs = p.get('source_workflows', [])
    table_type = p.get('table_type', 'OTHER')
    dnm        = p.get('_dnmparam_schema', 'YOURSCHEMA')

    L = []

    # ─────────────────────────────────────────────
    # DECLARE
    # ─────────────────────────────────────────────
    L.append("declare")
    L.append("  a varchar(300);")
    L.append(f"  v_reg_name         varchar(300):= {_q(reg_name)};  -- Название регламента")
    L.append(f"  v_folder_name      varchar(300):= {_q(folder)};  -- Папка в IPC / На ГП стандартно AIRFLOW")
    L.append(f"  v_workflow_name    varchar(300):= {_q(workflow)};  -- Название потока")
    L.append(f"  v_tgt_schema_name  varchar(300):= {_q(tgt_schema)};  -- Схема конечной сущности")
    L.append(f"  v_table_name       varchar(300):= {_q(table)};  -- Конечная сущность")
    L.append(f"  v_patch_code       varchar(300):= {_q(patch_code)};  -- Номер задачи из JIRA")
    L.append("begin")
    L.append("")

    # ─────────────────────────────────────────────
    # 1. MD_WORKFLOWS — создание потока
    # ─────────────────────────────────────────────
    L.append("  -- Создание потока (MD_WORKFLOWS)")
    L.append(f"  a:= UTL_MD_UPSERT.UPSERT_WF(v_folder_name, v_workflow_name, {_q(fsd)}, v_patch_code);  -- В описании указать название FSD и вендора (FIT)")
    L.append("")

    # ─────────────────────────────────────────────
    # 2. MD_WORKFLOW2REG — привязка к регламенту
    # ─────────────────────────────────────────────
    L.append("  -- Привязка потока к регламенту (MD_WORKFLOW2REG). Указывать только регулярный регламент")
    L.append("  a:= UTL_MD_UPSERT.UPSERT_WF2REG(v_reg_name, v_folder_name, v_workflow_name, 'Y', v_patch_code);  --'Y' - поток включен, 'N' - выключен")
    L.append("")

    # ─────────────────────────────────────────────
    # 3. MD_WORKFLOW2TABLE — конечная таблица
    # ─────────────────────────────────────────────
    L.append("  -- В какую конечную таблицу заливает поток (MD_WORKFLOW2TABLE)")
    L.append("  a:= UTL_MD_UPSERT.UPSERT_WF2TABLE(v_folder_name, v_workflow_name, v_tgt_schema_name, v_table_name, v_patch_code);")
    L.append("")

    # ─────────────────────────────────────────────
    # 4. MD_ENTITY2TABLE — связка сущность-таблица
    # ─────────────────────────────────────────────
    L.append("  -- Создать связку таблица-сущность (MD_ENTITY2TABLE)")
    L.append("  a:= UTL_MD_UPSERT.UPSERT_E2T(v_table_name||'_'||v_tgt_schema_name, v_table_name, v_tgt_schema_name, v_patch_code);")
    L.append("")

    # ─────────────────────────────────────────────
    # 5. Подписки на потоки
    # ─────────────────────────────────────────────
    L.append("  -- Создаём подписки на потоки")

    if len(source_wfs) == 0:
        # Нет источников — оставляем закомментированный шаблон
        L.append(f"  -- TODO: заполните информацию о потоке-источнике")
        L.append(f"  -- a:= UTL_MD_UPSERT.upsert_WORKFLOWEVTYPES({_q(folder)}, v_workflow_name, 1, p_patch_code => v_patch_code);")
        L.append(f"  -- a:= UTL_MD_UPSERT.UPSERT_EVENTTYPESUBSCIBER(a, v_reg_name, v_folder_name, v_workflow_name, p_patch_code => v_patch_code);")

    elif len(source_wfs) == 1:
        # Один источник — inline
        sw = source_wfs[0]
        eid = int(sw.get('eventget_id', 1))
        L.append(f"  a:= UTL_MD_UPSERT.upsert_WORKFLOWEVTYPES({_q(sw.get('folder_from',''))}, {_q(sw.get('wf_from',''))}, {eid}, p_patch_code => v_patch_code);  -- Вся информация в строке относится к потоку-источнику")
        L.append(f"  a:= UTL_MD_UPSERT.UPSERT_EVENTTYPESUBSCIBER(a, v_reg_name, v_folder_name, v_workflow_name, p_patch_code => v_patch_code);")

    else:
        # Несколько источников — цикл FOR
        L.append("  for i in")
        L.append("  ( -- Перечисляем информацию о потоках-источниках")
        rows = []
        for sw in source_wfs:
            reg_f    = sw.get('reg_from', reg_name)
            folder_f = sw.get('folder_from', '')
            wf_f     = sw.get('wf_from', '')
            eid      = int(sw.get('eventget_id', 1))
            rows.append(
                f"    select {_q(reg_f)} as reg_from, "
                f"{_q(folder_f)} as folder_from, "
                f"{_q(wf_f)} as wf_from, "
                f"{eid} as eventget_id from dual"
            )
        L.append(" union all\n".join(rows))
        L.append("  )")
        L.append("  loop")
        L.append("    a:= UTL_MD_UPSERT.upsert_WORKFLOWEVTYPES(i.reg_from, i.folder_from, i.wf_from, i.eventget_id, p_patch_code => v_patch_code);")
        L.append("    a:= UTL_MD_UPSERT.UPSERT_EVENTTYPESUBSCIBER(a, v_reg_name, v_folder_name, v_workflow_name, p_patch_code => v_patch_code);")
        L.append("  end loop;")

    L.append("")

    # ─────────────────────────────────────────────
    # 6. Параметры — ОБЯЗАТЕЛЬНЫЕ СТАТИЧЕСКИЕ
    # ─────────────────────────────────────────────
    L.append("  -- Параметры")
    L.append("  -- Статические — ОБЯЗАТЕЛЬНЫЕ")

    def stat(param, value, comment=''):
        cmt = f"  -- {comment}" if comment else ""
        L.append(f"  a:= UTL_MD_UPSERT.upsert_STATPARAM(v_folder_name, v_workflow_name, 'GLOBAL', {_q(param)}, {_q(value)}, v_patch_code);{cmt}")

    stat('$$P_INITIAL_LOADING', '0')
    stat('$$P_RELOADING',       '0')
    stat('$$P_WF_CONTROL_FLG',  'Y',
         'Значение всегда Y (необходимо для избежания работы неактуальной процедуры CTL_NOTIFY_ENTITY_UPDATE)')
    stat('$$P_SRC_SCHEMA_NAME', src_schema,  'Название схемы, в которой находится дельта')
    stat('$$P_SRC_TABLE_NAME',  src_delta,   'Название дельты')

    # Для этих используем переменные PL/SQL (не литералы)
    L.append(f"  a:= UTL_MD_UPSERT.upsert_STATPARAM(v_folder_name, v_workflow_name, 'GLOBAL', '$$P_TGT_SCHEMA_NAME', v_tgt_schema_name, v_patch_code);")
    L.append(f"  a:= UTL_MD_UPSERT.upsert_STATPARAM(v_folder_name, v_workflow_name, 'GLOBAL', '$$P_TGT_TABLE_NAME', v_table_name, v_patch_code);")
    L.append(f"  a:= UTL_MD_UPSERT.upsert_STATPARAM(v_folder_name, v_workflow_name, 'GLOBAL', '$$P_ENTITY_NAME', v_table_name||'_'||v_tgt_schema_name, v_patch_code);")
    L.append(f"  a:= UTL_MD_UPSERT.upsert_STATPARAM(v_folder_name, v_workflow_name, 'GLOBAL', '$$P_TARGET_ENTITY_NAME', v_table_name||'_'||v_tgt_schema_name, v_patch_code);")
    L.append("")

    # ─────────────────────────────────────────────
    # 7. Параметры — ОБЯЗАТЕЛЬНЫЕ ДИНАМИЧЕСКИЕ
    # ─────────────────────────────────────────────
    L.append("  -- Динамические — ОБЯЗАТЕЛЬНЫЕ")

    def dyn(param, func, scope='GLOBAL', comment=''):
        cmt = f"  -- {comment}" if comment else ""
        L.append(f"  a:= UTL_MD_UPSERT.upsert_DYNAMPARAM({_q(param)}, {_q(func)}, v_folder_name, v_workflow_name, {_q(scope)}, v_patch_code);{cmt}")

    dyn('$$P_AS_OF_DAY',          f'DNMPARAM_{dnm}.P_AS_OF_DAY')
    dyn('$$P_DMSJOB',             f'DNMPARAM_{dnm}.P_DWSJOB')
    dyn('$$P_AS_OF_DAY_AND_TIME', f'DNMPARAM_{dnm}.P_AS_OF_DAY_AND_TIME')
    dyn('$$P_OPERATION_DAY',      f'DNMPARAM_{dnm}.P_OPERATION_DAY')

    if sessions:
        L.append("")
        L.append("  -- $PMSESSIONLOGFILE — задать для каждой сессии")
        for sess in sessions:
            dyn('$PMSESSIONLOGFILE', f'DNMPARAM_{dnm}.PMSESSIONLOGFILE', sess)

    L.append("")

    # ─────────────────────────────────────────────
    # 8. Условные параметры (по типу таблицы)
    # ─────────────────────────────────────────────
    if table_type == 'STRAN_AGG':
        L.append("  -- Таблица типа _STRAN / _SSTAT / _AGG (с value_day в ключе)")
        stat('$$P_TRUNCATE_TABLE', '0', '0 — не чистим, 1 — транкейтим, 2 — делаем delete')
        L.append("")
    elif table_type == 'SHIST':
        L.append("  -- Таблица типа _SHIST")
        stat('$$P_USE_DELETED_FLAG',  '1')
        stat('$$P_GROUP_HISTORY',     '1')
        stat('$$P_GATHER_TABLE_STATS','1')
        L.append("")

    L.append("end;")
    L.append("/")

    return '\n'.join(L)
