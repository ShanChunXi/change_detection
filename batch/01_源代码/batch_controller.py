# -*- coding: utf-8 -*-
"""李昌辉批量处理模块 v1：真实单次变化检测串行调度。"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from zhang_change_adapter import run_single_task


STATUS_FIELDS = [
    "task_id",
    "before_image",
    "after_image",
    "model_name",
    "gpu_id",
    "output_path",
    "status",
    "attempt",
    "retry_count",
    "attempt_history",
    "start_time",
    "end_time",
    "duration_seconds",
    "output_exists",
    "output_size_bytes",
    "error_type",
    "error_message",
]
REQUIRED_TASK_FIELDS = [
    "task_id",
    "before_image",
    "after_image",
    "model_name",
    "result_type",
    "gpu_id",
    "output_path",
    "max_retry",
    "enabled",
]
VALID_STATUSES = {"pending", "running", "success", "failed", "skipped"}
ERROR_MESSAGE_LIMIT = 1000


class ControlledTestFailure(RuntimeError):
    """仅在可靠性验收配置显式启用时使用的受控失败。"""


class ControlledInterruption(RuntimeError):
    """状态已落盘后用于模拟批次中断。"""


def append_history(current: object, event: str) -> str:
    history = str(current or "").strip()
    return f"{history};{event}" if history else event


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def is_enabled(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "是", "启用"}


def resolve_path(base_dir: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base_dir / path).resolve()


def load_config(config_path: Path) -> Dict[str, Any]:
    with config_path.open("r", encoding="utf-8-sig") as stream:
        config = json.load(stream)
    if not isinstance(config, dict):
        raise ValueError("batch_config.json 顶层必须是JSON对象")
    if not config.get("zhang_module_work_copy"):
        raise ValueError("配置缺少 zhang_module_work_copy")
    return config


def load_tasks(csv_path: Path) -> List[Dict[str, str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        missing = [
            field for field in REQUIRED_TASK_FIELDS if field not in (reader.fieldnames or [])
        ]
        if missing:
            raise ValueError(f"任务清单缺少字段：{', '.join(missing)}")
        tasks = list(reader)
    ids = [task.get("task_id", "").strip() for task in tasks]
    if any(not task_id for task_id in ids):
        raise ValueError("task_id不能为空")
    if len(ids) != len(set(ids)):
        raise ValueError("task_id不能重复")
    return tasks


def load_previous_status(path: Path) -> Dict[str, Dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    return {row.get("task_id", ""): row for row in rows if row.get("task_id")}


class DirectLogger:
    """不依赖全局logging状态的直接文件日志器，支持续跑追加。"""

    def __init__(self, log_path: Path, append: bool = False):
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        if not append:
            self.log_path.write_text("", encoding="utf-8")
        else:
            self.log_path.touch(exist_ok=True)

    def _emit(self, level: str, message: str, *args: object) -> None:
        rendered = message % args if args else message
        current = datetime.now().astimezone()
        stamp = current.strftime("%Y-%m-%d %H:%M:%S") + f",{current.microsecond // 1000:03d}"
        line = f"{stamp} | {level} | {rendered}"
        with self.log_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(line + "\n")
        print(line, flush=True)

    def info(self, message: str, *args: object) -> None:
        self._emit("INFO", message, *args)

    def error(self, message: str, *args: object) -> None:
        self._emit("ERROR", message, *args)


def build_logger(log_path: Path, append: bool = False) -> DirectLogger:
    return DirectLogger(log_path, append=append)


def validate_task(task: Dict[str, str]) -> None:
    """验证任务字段，after_image 允许为空（地物分类模式）。"""
    for field in REQUIRED_TASK_FIELDS:
        if field == "after_image":
            continue  # after_image 可以为空，用于地物分类
        if not task.get(field, "").strip():
            raise ValueError(f"{field}不能为空")
    int(task["gpu_id"])
    retry = int(task["max_retry"])
    if retry < 0:
        raise ValueError("max_retry不能小于0")


def output_info(output_path: str) -> Tuple[bool, int]:
    path = Path(output_path)
    exists = path.is_file()
    return exists, path.stat().st_size if exists else 0


def truncate_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}".replace("\r", " ").replace("\n", " ")
    return text[:ERROR_MESSAGE_LIMIT]


def pending_row(task: Dict[str, str]) -> Dict[str, Any]:
    return {
        "task_id": task["task_id"].strip(),
        "before_image": task["before_image"].strip(),
        "after_image": task["after_image"].strip(),
        "model_name": task["model_name"].strip(),
        "gpu_id": task["gpu_id"].strip(),
        "output_path": task["output_path"].strip(),
        "status": "pending",
        "attempt": 0,
        "retry_count": 0,
        "attempt_history": "",
        "start_time": "",
        "end_time": "",
        "duration_seconds": "0.000000",
        "output_exists": False,
        "output_size_bytes": 0,
        "error_type": "",
        "error_message": "",
    }


def write_status(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=STATUS_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(path)


def execute_task(
    task: Dict[str, str],
    config: Dict[str, Any],
    row: Dict[str, Any],
    all_rows: List[Dict[str, Any]],
    status_path: Path,
    logger: DirectLogger,
) -> None:
    """执行单项任务；每次状态和attempt变化后立即原子写入CSV。"""
    task_id = row["task_id"]
    started = datetime.now().astimezone()
    started_perf = time.perf_counter()
    previous_history = row.get("attempt_history", "")
    row.update(
        status="running",
        attempt=0,
        retry_count=0,
        attempt_history=previous_history,
        start_time=started.isoformat(timespec="seconds"),
        end_time="",
        duration_seconds="0.000000",
        error_type="",
        error_message="",
    )
    write_status(status_path, all_rows)
    max_retry = max(0, int(task["max_retry"]))
    logger.info(
        "任务开始 | task_id=%s | max_retry=%d | output=%s",
        task_id,
        max_retry,
        row["output_path"],
    )

    for attempt in range(1, max_retry + 2):
        row["attempt"] = attempt
        row["retry_count"] = max(0, attempt - 1)
        row["attempt_history"] = append_history(
            row.get("attempt_history"), f"attempt={attempt}:running"
        )
        write_status(status_path, all_rows)
        logger.info(
            "任务尝试 | task_id=%s | attempt=%d/%d",
            task_id,
            attempt,
            max_retry + 1,
        )
        try:
            validate_task(task)
            controlled_failures = int(task.get("test_failures_before_success", "0") or 0)
            if controlled_failures < 0:
                raise ValueError("test_failures_before_success不能小于0")
            if (
                bool(config.get("test_hooks_enabled", False))
                and attempt <= controlled_failures
            ):
                raise ControlledTestFailure(
                    f"可靠性验收受控失败：task_id={task_id}, attempt={attempt}, "
                    f"计划失败次数={controlled_failures}"
                )
            result = run_single_task(task, config)
            row["status"] = "success"
            row["output_exists"] = result["output_exists"]
            row["output_size_bytes"] = result["output_size_bytes"]
            row["error_type"] = ""
            row["error_message"] = ""
            row["attempt_history"] = append_history(
                row.get("attempt_history"), f"attempt={attempt}:success"
            )
            write_status(status_path, all_rows)
            logger.info(
                "任务成功 | task_id=%s | attempt=%d | retry_count=%d | output_size_bytes=%d",
                task_id,
                attempt,
                row["retry_count"],
                result["output_size_bytes"],
            )
            break
        except Exception as exc:
            exists, size = output_info(row["output_path"])
            row["output_exists"] = exists
            row["output_size_bytes"] = size
            row["error_type"] = type(exc).__name__
            row["error_message"] = truncate_error(exc)
            row["attempt_history"] = append_history(
                row.get("attempt_history"), f"attempt={attempt}:failed"
            )
            row["status"] = "running" if attempt <= max_retry else "failed"
            write_status(status_path, all_rows)
            logger.error(
                "任务异常 | task_id=%s | attempt=%d/%d | error_type=%s\n%s",
                task_id,
                attempt,
                max_retry + 1,
                type(exc).__name__,
                "".join(
                    traceback.format_exception(
                        type(exc), exc, exc.__traceback__, limit=100
                    )
                ),
            )
            if attempt <= max_retry:
                logger.info(
                    "准备重试 | task_id=%s | next_attempt=%d",
                    task_id,
                    attempt + 1,
                )
                continue

    ended = datetime.now().astimezone()
    row["end_time"] = ended.isoformat(timespec="seconds")
    row["duration_seconds"] = f"{time.perf_counter() - started_perf:.6f}"
    write_status(status_path, all_rows)
    logger.info(
        "任务结束 | task_id=%s | status=%s | attempt=%s | retry_count=%s | "
        "duration_seconds=%s",
        task_id,
        row["status"],
        row["attempt"],
        row["retry_count"],
        row["duration_seconds"],
    )


def write_summary(path: Path, summary: Dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2)


def run_batch(
    config_path: Path,
    tasks_path: Path | None = None,
    real_mode: bool = False,
    log_path: Path | None = None,
    status_path: Path | None = None,
    summary_path: Path | None = None,
    interrupt_after_successes: int | None = None,
) -> Tuple[int, Dict[str, Any]]:
    config_path = config_path.resolve()
    config = load_config(config_path)
    module_root = config_path.parent.parent
    task_csv = (
        tasks_path.resolve()
        if tasks_path
        else resolve_path(module_root, config.get("task_csv", "任务清单/batch_tasks_demo.csv"))
    )
    log_dir = resolve_path(module_root, config.get("log_dir", "运行日志"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = (log_path or log_dir / "batch_run.log").resolve()
    status_path = (status_path or log_dir / "batch_status.csv").resolve()
    summary_path = (summary_path or log_dir / "batch_summary.json").resolve()

    dry_run = bool(config.get("dry_run", False)) and not real_mode
    tasks = load_tasks(task_csv)
    previous = load_previous_status(status_path)
    append_log = log_path.is_file() and bool(previous)
    logger = build_logger(log_path, append=append_log)

    rows: List[Dict[str, Any]] = []
    for task in tasks:
        row = pending_row(task)
        prior = previous.get(row["task_id"])
        if prior:
            for field in STATUS_FIELDS:
                if field in prior and prior[field] not in (None, ""):
                    row[field] = prior[field]
        rows.append(row)

    # 在执行任何任务前先持久化全部pending/续跑状态。
    write_status(status_path, rows)
    started = datetime.now().astimezone()
    started_perf = time.perf_counter()
    session_successes = 0
    logger.info(
        "批处理启动 | mode=%s | tasks=%s | status=%s | serial=true | resume=%s",
        "dry-run" if dry_run else "real",
        task_csv,
        status_path,
        bool(previous),
    )

    for task, row in zip(tasks, rows):
        prior = previous.get(row["task_id"])
        output_exists, output_size = output_info(row["output_path"])
        if not is_enabled(task["enabled"]):
            timestamp = now_text()
            row.update(
                status="skipped",
                start_time=row.get("start_time") or timestamp,
                end_time=row.get("end_time") or timestamp,
                output_exists=output_exists,
                output_size_bytes=output_size,
                error_message="enabled=false",
                attempt_history=append_history(
                    row.get("attempt_history"), "resume:skipped_disabled"
                ),
            )
            write_status(status_path, rows)
            logger.info("任务跳过 | task_id=%s | reason=enabled=false", row["task_id"])
            continue
        if (
            prior
            and prior.get("status") in {"success", "skipped"}
            and output_exists
            and output_size > 0
        ):
            row.update(
                status="skipped",
                attempt=prior.get("attempt", row.get("attempt", 0)),
                retry_count=prior.get("retry_count", row.get("retry_count", 0)),
                start_time=prior.get("start_time", row.get("start_time", "")),
                end_time=prior.get("end_time", row.get("end_time", "")),
                duration_seconds=prior.get(
                    "duration_seconds", row.get("duration_seconds", "0.000000")
                ),
                output_exists=True,
                output_size_bytes=output_size,
                error_type="",
                error_message="已有有效结果，跳过；保留前次成功运行信息",
                attempt_history=append_history(
                    prior.get("attempt_history", ""), "resume:skipped_existing"
                ),
            )
            write_status(status_path, rows)
            logger.info(
                "任务续跑跳过 | task_id=%s | reason=已有有效成功输出 | "
                "output_size_bytes=%d",
                row["task_id"],
                output_size,
            )
            continue
        if dry_run:
            timestamp = now_text()
            row.update(
                status="skipped",
                start_time=row.get("start_time") or timestamp,
                end_time=row.get("end_time") or timestamp,
                output_exists=output_exists,
                output_size_bytes=output_size,
                error_message="dry-run仅校验，不生成真实推理成功状态",
                attempt_history=append_history(
                    row.get("attempt_history"), "dry-run:skipped"
                ),
            )
            write_status(status_path, rows)
            logger.info("任务dry-run跳过 | task_id=%s", row["task_id"])
            continue

        execute_task(task, config, row, rows, status_path, logger)
        if row["status"] == "success":
            session_successes += 1
        if (
            interrupt_after_successes is not None
            and interrupt_after_successes > 0
            and session_successes >= interrupt_after_successes
        ):
            write_status(status_path, rows)
            logger.error(
                "可靠性验收受控中断 | 已完成成功任务数=%d | last_task_id=%s | "
                "状态已持久化",
                session_successes,
                row["task_id"],
            )
            raise ControlledInterruption(
                f"状态已持久化，按测试要求在{session_successes}个任务成功后中断"
            )

    duration = time.perf_counter() - started_perf
    success = sum(row["status"] == "success" for row in rows)
    failed = sum(row["status"] == "failed" for row in rows)
    skipped = sum(row["status"] == "skipped" for row in rows)
    summary = {
        "run_mode": "dry-run" if dry_run else "real",
        "serial_execution": True,
        "resumed": bool(previous),
        "started_at": started.isoformat(timespec="seconds"),
        "ended_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "total_tasks": len(rows),
        "success_count": success,
        "failed_count": failed,
        "skipped_count": skipped,
        "total_duration_seconds": round(duration, 6),
        "task_csv": str(task_csv),
        "batch_run_log": str(log_path),
        "batch_status_csv": str(status_path),
        "summary_json": str(summary_path),
    }
    write_status(status_path, rows)
    write_summary(summary_path, summary)
    logger.info(
        "批处理完成 | total=%d | success=%d | failed=%d | skipped=%d | duration=%.6f",
        len(rows),
        success,
        failed,
        skipped,
        duration,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return (0 if failed == 0 else 1), summary


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="李昌辉批量处理模块 v1")
    parser.add_argument(
        "--config",
        type=Path,
        default=root / "配置文件" / "batch_config.json",
    )
    parser.add_argument("--tasks", type=Path)
    parser.add_argument("--real", action="store_true", help="覆盖配置中的dry_run并执行真实推理")
    parser.add_argument("--log-path", type=Path)
    parser.add_argument("--status-path", type=Path)
    parser.add_argument("--summary-path", type=Path)
    parser.add_argument(
        "--interrupt-after-successes",
        type=int,
        help="可靠性验收钩子：本次会话达到指定成功数后在状态落盘后中断",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        code, _ = run_batch(
            config_path=args.config,
            tasks_path=args.tasks,
            real_mode=args.real,
            log_path=args.log_path,
            status_path=args.status_path,
            summary_path=args.summary_path,
            interrupt_after_successes=args.interrupt_after_successes,
        )
        return code
    except ControlledInterruption as exc:
        print(f"[受控中断] {exc}", file=sys.stderr)
        return 75
    except Exception:
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())