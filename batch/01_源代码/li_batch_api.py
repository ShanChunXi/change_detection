# -*- coding: utf-8 -*-
"""李昌辉“批量处理与自动化＋统计报告”模块的轻量 UI 接口。

本文件只负责参数校验、路径组织、进度事件转发和既有核心入口调度；
不复制变化检测、栅格统计、Excel、图表、Word 或 PDF 的业务逻辑。
"""

from __future__ import annotations

import csv
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Callable


ProgressCallback = Callable[[dict[str, Any]], None]

__all__ = [
    "run_batch_detection",
    "generate_statistics",
    "generate_report",
    "run_full_pipeline",
]

_MODULE_CACHE: dict[str, ModuleType] = {}
_BATCH_LOCK = threading.RLock()
_REPORT_LOCK = threading.RLock()


def _as_path(value: str | os.PathLike[str], *, name: str) -> Path:
    if value is None or not str(value).strip():
        raise ValueError(f"{name}不能为空")
    return Path(value).expanduser().resolve()


def _require_file(value: str | os.PathLike[str], *, name: str) -> Path:
    path = _as_path(value, name=name)
    if not path.is_file():
        raise FileNotFoundError(f"{name}不存在：{path}")
    return path


def _prepare_output_dir(value: str | os.PathLike[str]) -> Path:
    path = _as_path(value, name="output_dir")
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".li_batch_api_write_probe"
    try:
        probe.write_text("ok", encoding="ascii")
        probe.unlink()
    except OSError as exc:
        raise PermissionError(f"输出目录不可写：{path}") from exc
    return path


def _emit(
    callback: ProgressCallback | None,
    callback_errors: list[str],
    *,
    stage: str,
    task_id: str = "",
    current: int = 0,
    total: int = 0,
    status: str,
    progress_percent: float,
    message: str,
) -> None:
    if callback is None:
        return
    event = {
        "stage": str(stage),
        "task_id": str(task_id),
        "current": int(current),
        "total": int(total),
        "status": str(status),
        "progress_percent": float(max(0.0, min(100.0, progress_percent))),
        "message": str(message),
    }
    try:
        callback(event)
    except Exception as exc:  # UI回调异常不应破坏后台业务。
        callback_errors.append(f"{type(exc).__name__}: {exc}")


def _load_module(path: Path, module_key: str) -> ModuleType:
    cache_key = f"{module_key}:{path}"
    if cache_key in _MODULE_CACHE:
        return _MODULE_CACHE[cache_key]
    spec = importlib.util.spec_from_file_location(module_key, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模块：{path}")
    module = importlib.util.module_from_spec(spec)
    source_dir = str(path.parent)
    inserted = source_dir not in sys.path
    if inserted:
        sys.path.insert(0, source_dir)
    try:
        spec.loader.exec_module(module)
    finally:
        if inserted and sys.path and sys.path[0] == source_dir:
            sys.path.pop(0)
    _MODULE_CACHE[cache_key] = module
    return module


def _find_core_script(explicit: str | os.PathLike[str] | None, filename: str) -> Path:
    if explicit is not None:
        return _require_file(explicit, name=filename)
    here = Path(__file__).resolve().parent
    candidates = [here / filename]
    project_root = here.parents[1] if len(here.parents) >= 2 else here
    if filename in {"result_statistics.py", "report_generator.py"}:
        candidates.append(project_root / "06_统计汇总与报告" / "源代码" / filename)
    elif filename in {"batch_controller.py", "zhang_change_adapter.py"}:
        candidates.append(project_root / "04_批量处理程序" / "源代码" / filename)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"未找到核心文件{filename}；可通过对应的 *_script 参数显式传入")


def _read_status_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return [{key: value or "" for key, value in row.items()} for row in csv.DictReader(stream)]


def _result_location(rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    paths = [Path(row["output_path"]).expanduser() for row in rows if row.get("output_path", "").strip()]
    if not paths:
        return "", []
    parents = [str(path.resolve().parent) for path in paths]
    try:
        common = os.path.commonpath(parents)
    except ValueError:
        common = ""
    return common, [str(path.resolve()) for path in paths]


def run_batch_detection(
    task_list_path: str | os.PathLike[str],
    config_path: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    progress_callback: ProgressCallback | None = None,
    *,
    real_mode: bool = False,
    batch_controller_script: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """调用现有批量控制器，返回批次汇总及状态表、日志和结果路径。"""
    task_path = _require_file(task_list_path, name="task_list_path")
    config = _require_file(config_path, name="config_path")
    target = _prepare_output_dir(output_dir)
    controller_path = _find_core_script(batch_controller_script, "batch_controller.py")
    callback_errors: list[str] = []
    status_path = target / "batch_status.csv"
    log_path = target / "batch_run.log"
    summary_path = target / "batch_summary.json"

    _emit(
        progress_callback,
        callback_errors,
        stage="batch_detection",
        status="started",
        progress_percent=0,
        message="批量变化检测已启动",
    )

    with _BATCH_LOCK:
        controller = _load_module(controller_path, "_li_batch_controller_core")
        original_write_status = controller.write_status
        last_state: dict[str, tuple[str, str, str]] = {}

        def write_status_with_progress(path: Path, rows: list[dict[str, Any]]) -> None:
            original_write_status(path, rows)
            total = len(rows)
            completed = sum(str(row.get("status", "")) in {"success", "failed", "skipped"} for row in rows)
            for index, row in enumerate(rows):
                task_id = str(row.get("task_id", ""))
                state = (
                    str(row.get("status", "")),
                    str(row.get("attempt", "")),
                    str(row.get("end_time", "")),
                )
                if last_state.get(task_id) == state:
                    continue
                last_state[task_id] = state
                progress = (completed / total * 100.0) if total else 100.0
                _emit(
                    progress_callback,
                    callback_errors,
                    stage="batch_detection",
                    task_id=task_id,
                    current=index + 1,
                    total=total,
                    status=state[0] or "pending",
                    progress_percent=progress,
                    message=f"任务{task_id or index + 1}：{state[0] or 'pending'}",
                )

        controller.write_status = write_status_with_progress
        try:
            exit_code, summary = controller.run_batch(
                config_path=config,
                tasks_path=task_path,
                real_mode=bool(real_mode),
                log_path=log_path,
                status_path=status_path,
                summary_path=summary_path,
            )
        finally:
            controller.write_status = original_write_status

    rows = _read_status_rows(status_path)
    result_dir, result_paths = _result_location(rows)
    failed_count = int(summary.get("failed_count", 0))
    final_status = "success" if failed_count == 0 else "completed_with_errors"
    result: dict[str, Any] = {
        **summary,
        "stage": "batch_detection",
        "success": failed_count == 0,
        "completed": True,
        "status": final_status,
        "exit_code": int(exit_code),
        "output_dir": str(target),
        "status_table_path": str(status_path),
        "log_path": str(log_path),
        "summary_path": str(summary_path),
        "result_dir": result_dir,
        "result_paths": result_paths,
        "task_results": rows,
        "callback_errors": callback_errors,
    }
    _emit(
        progress_callback,
        callback_errors,
        stage="batch_detection",
        current=len(rows),
        total=len(rows),
        status=final_status,
        progress_percent=100,
        message=f"批量检测完成：成功{summary.get('success_count', 0)}，失败{failed_count}，跳过{summary.get('skipped_count', 0)}",
    )
    return result


def _resolve_executable(
    explicit: str | os.PathLike[str] | None,
    *,
    name: str,
    env_name: str,
    search_names: tuple[str, ...],
    default_to_current_python: bool = False,
) -> Path:
    value = explicit or os.environ.get(env_name)
    if value:
        return _require_file(value, name=name)
    if default_to_current_python:
        return _require_file(sys.executable, name=name)
    for command in search_names:
        found = shutil.which(command)
        if found:
            return Path(found).resolve()
    raise FileNotFoundError(f"未找到{name}；请传入参数或设置环境变量{env_name}")


def _run_process_lines(
    command: list[str],
    *,
    cwd: Path,
    callback: ProgressCallback | None,
    callback_errors: list[str],
    stage: str,
) -> tuple[int, str]:
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    lines: list[str] = []
    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.rstrip("\r\n")
        lines.append(line)
        if line:
            _emit(
                callback,
                callback_errors,
                stage=stage,
                status="running",
                progress_percent=50,
                message=line[-500:],
            )
    return process.wait(), "\n".join(lines)


def _last_json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    for match in re.finditer(r"\{", text):
        try:
            value, relative_end = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append((match.start() + relative_end, -match.start(), value))
    if not candidates:
        raise RuntimeError("核心脚本未输出可解析的JSON结果")
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def generate_statistics(
    status_table_path: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    progress_callback: ProgressCallback | None = None,
    *,
    result_root: str | os.PathLike[str] | None = None,
    python_executable: str | os.PathLike[str] | None = None,
    node_executable: str | os.PathLike[str] | None = None,
    node_modules_dir: str | os.PathLike[str] | None = None,
    statistics_script: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """调用纯Python统计脚本，生成统计Excel、明细CSV、日志和验收记录。

    ``node_executable``与``node_modules_dir``仅为兼容V1.0调用保留，V1.1不再使用。
    """
    status_path = _require_file(status_table_path, name="status_table_path")
    target = _prepare_output_dir(output_dir)
    script = _find_core_script(statistics_script, "result_statistics.py")
    python_path = _resolve_executable(
        python_executable,
        name="python_executable",
        env_name="LI_BATCH_PYTHON",
        search_names=(),
        default_to_current_python=True,
    )
    work_dir = target / "_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    excel_path = target / "批量变化检测统计汇总.xlsx"
    detail_csv_path = target / "批量变化检测统计明细.csv"
    log_path = target / "变化统计运行.log"
    acceptance_path = target / "变化统计与Excel验收报告.md"
    resolved_result_root = _as_path(result_root, name="result_root") if result_root else status_path.parent
    callback_errors: list[str] = []
    _emit(
        progress_callback,
        callback_errors,
        stage="statistics",
        status="started",
        progress_percent=0,
        message="统计与Excel生成已启动",
    )

    command = [
        str(python_path),
        str(script),
        "--status-csv",
        str(status_path),
        "--result-root",
        str(resolved_result_root),
        "--xlsx",
        str(excel_path),
        "--detail-csv",
        str(detail_csv_path),
        "--log",
        str(log_path),
        "--report",
        str(acceptance_path),
        "--work-dir",
        str(work_dir),
        "--overwrite",
    ]
    exit_code, output = _run_process_lines(
        command,
        cwd=script.parent,
        callback=progress_callback,
        callback_errors=callback_errors,
        stage="statistics",
    )
    if exit_code != 0:
        tail = "\n".join(output.splitlines()[-20:])
        raise RuntimeError(f"统计脚本执行失败，退出代码{exit_code}；日志：{log_path}\n{tail}")
    core_result = _last_json_object(output)
    result = {
        "stage": "statistics",
        "success": True,
        "status": "success",
        "exit_code": int(exit_code),
        "summary": core_result.get("overview", {}),
        "excel_qa": core_result.get("excel_qa", {}),
        "excel_path": str(excel_path),
        "detail_csv_path": str(detail_csv_path),
        "log_path": str(log_path),
        "acceptance_report_path": str(acceptance_path),
        "output_dir": str(target),
        "work_dir": str(work_dir),
        "warnings": (
            ["node_executable和node_modules_dir在V1.1中已停用并被忽略。"]
            if node_executable is not None or node_modules_dir is not None
            else []
        ),
        "callback_errors": callback_errors,
    }
    summary = result["summary"]
    _emit(
        progress_callback,
        callback_errors,
        stage="statistics",
        current=int(summary.get("total_tasks", 0)),
        total=int(summary.get("total_tasks", 0)),
        status="success",
        progress_percent=100,
        message=f"统计完成：可统计结果{summary.get('statistic_result_count', 0)}个",
    )
    return result


def _safe_filename(title: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title.strip())
    value = value.rstrip(" .")
    return value[:80] or "批量变化检测统计报告"


def _generate_report_in_process(job: dict[str, Any]) -> dict[str, Any]:
    report_script = _require_file(job["report_script"], name="report_generator.py")
    report_module = _load_module(report_script, "_li_report_generator_core")
    output_dir = Path(job["output_dir"])
    title = str(job["report_title"])
    stem = _safe_filename(title)
    chart_dir = output_dir / "图表"
    docx_path = output_dir / f"{stem}.docx"
    pdf_path = output_dir / f"{stem}.pdf"
    log_path = output_dir / "图表与报告生成.log"
    acceptance_path = output_dir / "图表与报告生成验收报告.md"
    work_dir = output_dir / "_work"
    args = SimpleNamespace(
        excel=Path(job["excel_path"]),
        stats_csv=Path(job["stats_csv_path"]),
        reliability_csv=Path(job["reliability_status_path"]),
        reliability_evidence=Path(job["reliability_evidence_path"]),
        project_python=(Path(job["project_python"]) if job.get("project_python") else Path(sys.executable)),
        bundled_python=None,
        poppler=None,
        work_dir=work_dir,
        chart_dir=chart_dir,
        docx=docx_path,
        pdf=pdf_path,
        log=log_path,
        acceptance=acceptance_path,
        internal_stage=None,
        payload=None,
        stage_result=None,
        finalize_visual_qa=None,
        visual_qa_notes="",
        report_title=title,
    )
    with _REPORT_LOCK:
        exit_code = int(report_module.orchestrate(args))
    manifest_path = work_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    generated = docx_path.is_file()
    pdf_generated = bool(
        manifest.get("conversion", {}).get("status") == "success"
        and pdf_path.is_file()
    )
    automatic_passed = bool(manifest.get("automatic_passed", False))
    status = (
        "success"
        if generated and automatic_passed and pdf_generated
        else (
            "success_without_pdf"
            if generated and automatic_passed
            else ("generated_with_qa_warnings" if generated else "failed")
        )
    )
    chart_results = manifest.get("chart_results", {})
    chart_paths = [str(value.get("path")) for value in chart_results.values() if isinstance(value, dict) and value.get("path")]
    warnings_list = list(manifest.get("warnings", []))
    errors_list = list(manifest.get("errors", []))
    return {
        "stage": "report",
        "success": generated and automatic_passed,
        "generated": generated,
        "status": status,
        "exit_code": exit_code,
        "report_title": title,
        "charts_dir": str(chart_dir),
        "chart_dir": str(chart_dir),
        "chart_paths": chart_paths,
        "word_path": str(docx_path),
        "pdf_path": str(pdf_path) if pdf_generated else None,
        "pdf_generated": pdf_generated,
        "warnings": warnings_list,
        "errors": errors_list,
        "log_path": str(log_path),
        "acceptance_report_path": str(acceptance_path),
        "manifest_path": str(manifest_path),
        "automatic_passed": automatic_passed,
        "word_page_count": manifest.get("conversion", {}).get("word_page_count"),
        "pdf_page_count": (
            manifest.get("pdf_qa", {}).get("page_count") if pdf_generated else None
        ),
        "output_dir": str(output_dir),
    }


def generate_report(
    excel_path: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    report_title: str = "批量变化检测统计报告",
    progress_callback: ProgressCallback | None = None,
    *,
    stats_csv_path: str | os.PathLike[str] | None = None,
    orchestrator_python: str | os.PathLike[str] | None = None,
    project_python: str | os.PathLike[str] | None = None,
    poppler_path: str | os.PathLike[str] | None = None,
    reliability_status_path: str | os.PathLike[str] | None = None,
    reliability_evidence_path: str | os.PathLike[str] | None = None,
    report_script: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """使用普通Python库生成图表和Word，并尽力通过本机Word生成PDF。

    ``orchestrator_python``、``project_python``与``poppler_path``仅为兼容
    V1.0调用保留；报告生成不再依赖这些外部运行时路径。
    """
    excel = _require_file(excel_path, name="excel_path")
    target = _prepare_output_dir(output_dir)
    title = str(report_title).strip()
    if not title:
        raise ValueError("report_title不能为空")
    script = _find_core_script(report_script, "report_generator.py")
    inferred_csv = excel.with_name("批量变化检测统计明细.csv")
    stats_csv = _require_file(stats_csv_path or inferred_csv, name="stats_csv_path")
    optional_dir = target / "_optional_inputs"
    reliability_status = Path(reliability_status_path).resolve() if reliability_status_path else optional_dir / "reliability_status.csv"
    reliability_evidence = Path(reliability_evidence_path).resolve() if reliability_evidence_path else optional_dir / "reliability_evidence.json"
    job = {
        "excel_path": str(excel),
        "stats_csv_path": str(stats_csv),
        "output_dir": str(target),
        "report_title": title,
        "project_python": str(project_python) if project_python else "",
        "reliability_status_path": str(reliability_status),
        "reliability_evidence_path": str(reliability_evidence),
        "report_script": str(script),
    }
    callback_errors: list[str] = []
    _emit(
        progress_callback,
        callback_errors,
        stage="report",
        status="started",
        progress_percent=0,
        message="图表、Word与PDF生成已启动",
    )
    result = _generate_report_in_process(job)
    legacy_paths = [orchestrator_python, project_python, poppler_path]
    if any(value is not None for value in legacy_paths):
        result.setdefault("warnings", []).append(
            "orchestrator_python、project_python和poppler_path在V1.1报告生成中已停用并被忽略。"
        )
    result["callback_errors"] = callback_errors
    _emit(
        progress_callback,
        callback_errors,
        stage="report",
        current=1,
        total=1,
        status=str(result.get("status", "completed")),
        progress_percent=100,
        message=f"报告生成结束：{result.get('status', 'completed')}",
    )
    return result


def run_full_pipeline(
    task_list_path: str | os.PathLike[str],
    config_path: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    progress_callback: ProgressCallback | None = None,
    *,
    report_title: str = "批量变化检测统计报告",
    real_mode: bool = False,
    python_executable: str | os.PathLike[str] | None = None,
    node_executable: str | os.PathLike[str] | None = None,
    node_modules_dir: str | os.PathLike[str] | None = None,
    orchestrator_python: str | os.PathLike[str] | None = None,
    project_python: str | os.PathLike[str] | None = None,
    poppler_path: str | os.PathLike[str] | None = None,
    batch_controller_script: str | os.PathLike[str] | None = None,
    statistics_script: str | os.PathLike[str] | None = None,
    report_script: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """按“批量检测→统计→报告”顺序调用三个既有核心阶段。"""
    target = _prepare_output_dir(output_dir)
    callback_errors: list[str] = []
    _emit(
        progress_callback,
        callback_errors,
        stage="pipeline",
        status="started",
        progress_percent=0,
        message="完整流水线已启动",
    )
    batch = run_batch_detection(
        task_list_path,
        config_path,
        target / "01_批量检测",
        progress_callback,
        real_mode=real_mode,
        batch_controller_script=batch_controller_script,
    )
    statistics = generate_statistics(
        batch["status_table_path"],
        target / "02_统计",
        progress_callback,
        result_root=batch.get("result_dir") or Path(batch["status_table_path"]).parent,
        python_executable=python_executable,
        node_executable=node_executable,
        node_modules_dir=node_modules_dir,
        statistics_script=statistics_script,
    )
    report = generate_report(
        statistics["excel_path"],
        target / "03_报告",
        report_title,
        progress_callback,
        stats_csv_path=statistics["detail_csv_path"],
        orchestrator_python=orchestrator_python or python_executable,
        project_python=project_python,
        poppler_path=poppler_path,
        report_script=report_script,
    )
    pipeline_success = bool(batch.get("success") and statistics.get("success") and report.get("success"))
    result = {
        "stage": "pipeline",
        "success": pipeline_success,
        "completed": True,
        "status": "success" if pipeline_success else "completed_with_errors",
        "output_dir": str(target),
        "batch_detection": batch,
        "statistics": statistics,
        "report": report,
        "callback_errors": callback_errors,
    }
    _emit(
        progress_callback,
        callback_errors,
        stage="pipeline",
        current=3,
        total=3,
        status=result["status"],
        progress_percent=100,
        message=f"完整流水线结束：{result['status']}",
    )
    return result
