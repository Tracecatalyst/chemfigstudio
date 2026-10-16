"""chemfigstudio.batch — 批量出图：一个 CSV → 多张图。

CSV 列（大小写不敏感，顺序随意）：

    name,smiles,delta_h[,kind][,n_carbons]
    甲醇,CO,-22.7
    乙醇,CCO,-36.1

* ``name`` 可省；``smiles`` 必填
* ``delta_h`` 可省（留空则不画 ΔH 行）
* ``kind`` 可省，缺省用 ``--kind``（默认 ``alcohol``）
* 以 ``#`` 开头的行与空行跳过（方便写注释）

失败策略：**单行失败不中断**——好的照常出图，坏的记进报告（含行号与原因）。
"""

from __future__ import annotations

import csv
import io
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import api, i18n
from .errors import RenderError
from .settings import VALID_FORMATS

__all__ = ["BatchItem", "BatchReport", "read_csv", "read_csv_text", "run_batch",
           "target_paths", "watch"]

REQUIRED_COLUMN = "smiles"


@dataclass
class BatchItem:
    """CSV 里的一行。"""

    line: int
    name: str = ""
    smiles: str = ""
    delta_h: str = ""
    kind: str = ""
    n_carbons: Optional[int] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"line": self.line, "name": self.name, "smiles": self.smiles,
                "delta_h": self.delta_h, "kind": self.kind, "n_carbons": self.n_carbons}


@dataclass
class BatchReport:
    """批量结果：成功/失败逐条，便于 CLI 与 GUI 呈现。"""

    total: int = 0
    items: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def succeeded(self) -> List[Dict[str, Any]]:
        return [item for item in self.items if item.get("outputs")]

    @property
    def failed(self) -> List[Dict[str, Any]]:
        return [item for item in self.items if item.get("error")]

    @property
    def warnings(self) -> List[str]:
        out: List[str] = []
        for item in self.items:
            for warning in item.get("warnings") or []:
                out.append("%s: %s" % (item.get("name") or item.get("line"), warning))
        return out

    def as_dict(self) -> Dict[str, Any]:
        return {"total": self.total, "succeeded": len(self.succeeded),
                "failed": len(self.failed), "warnings": self.warnings,
                "items": list(self.items)}


def _pick(row: Dict[str, str], *names: str) -> str:
    for name in names:
        if name in row and row[name] is not None:
            return str(row[name]).strip()
    return ""


def read_csv(path) -> List[BatchItem]:
    """读 CSV 文件（按 UTF-8/UTF-8-BOM 解码，兼容 Excel 导出的 BOM）。"""
    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise RenderError(i18n.t("batch.err.not_found", path=target)) from exc
    except UnicodeDecodeError as exc:
        raise RenderError(i18n.t("batch.err.encoding", path=target)) from exc
    return read_csv_text(raw, str(target))


def read_csv_text(text: str, source: str = "CSV") -> List[BatchItem]:
    """从字符串解析（GUI 直接把浏览器读到的 CSV 文本发过来，免 multipart）。"""
    lines = [line for line in str(text).splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    if not lines:
        raise RenderError(i18n.t("batch.err.empty", path=source))

    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    header = {str(name).strip().lower(): name for name in (reader.fieldnames or [])}
    if REQUIRED_COLUMN not in header:
        raise RenderError(i18n.t("batch.err.missing_column", column=REQUIRED_COLUMN,
                                 columns=", ".join(sorted(header)) or "-"))

    items: List[BatchItem] = []
    for offset, row in enumerate(reader, start=2):        # 第 1 行是表头
        cleaned = {(str(k).strip().lower() if k else ""): v for k, v in row.items()}
        item = BatchItem(
            line=offset,
            name=_pick(cleaned, "name", "名称"),
            smiles=_pick(cleaned, "smiles"),
            delta_h=_pick(cleaned, "delta_h", "dh", "δh", "焓变"),
            kind=_pick(cleaned, "kind", "类型"),
        )
        carbons = _pick(cleaned, "n_carbons", "carbons", "碳数")
        if carbons:
            try:
                item.n_carbons = int(float(carbons))
            except ValueError:
                item.n_carbons = None
        items.append(item)
    if not items:
        raise RenderError(i18n.t("batch.err.empty", path=source))
    return items


def target_paths(items: Sequence[BatchItem], outdir, *, kind: str = "alcohol",
                 formats: Sequence[str] = ("png",),
                 filename_template: str = "{index:02d}_{name}") -> List[Path]:
    """算出将要写出的文件（**不落盘**，供 GUI 先问"是否覆盖"）。

    ``kind`` 必须与 :func:`run_batch` 用**同一个**默认值、同一套 fallback：两边一旦
    不一致（曾经这里写死 ``"figure"``、那边用 ``kind``），覆盖确认检查的就是另一批
    文件名，会**静默覆盖**已有文件 —— 这是本项目唯一的数据丢失级缺陷。
    """
    directory = Path(outdir).expanduser()
    wanted = tuple(str(f).lower().lstrip(".") for f in formats) or ("png",)
    out: List[Path] = []
    for index, item in enumerate(items, start=1):
        stem = filename_template.format(index=index, name=item.name or item.kind or kind,
                                        smiles=item.smiles)
        safe = "".join(ch if ch not in '/\\:' else "-" for ch in stem).strip() or str(index)
        out.append(directory / (safe + "." + wanted[0]))
    return out


def run_batch(items: Sequence[BatchItem], outdir, *, kind: str = "alcohol",
              formats: Sequence[str] = ("png",), dpi: int = 300,
              filename_template: str = "{index:02d}_{name}",
              on_progress: Optional[Callable[[int, int, BatchItem], None]] = None) -> BatchReport:
    """逐行出图；单行失败不影响其它行。"""
    directory = Path(outdir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    wanted = tuple(str(f).lower().lstrip(".") for f in formats) or ("png",)
    for fmt in wanted:
        if fmt not in VALID_FORMATS:
            raise RenderError(i18n.t("export.err.bad_format", format=fmt))

    report = BatchReport(total=len(items))
    for index, item in enumerate(items, start=1):
        if on_progress:
            on_progress(index, len(items), item)
        use_kind = item.kind or kind
        stem = filename_template.format(index=index, name=item.name or use_kind,
                                        smiles=item.smiles)
        safe = "".join(ch if ch not in '/\\:' else "-" for ch in stem).strip() or str(index)
        params: Dict[str, Any] = {"smiles": item.smiles}
        if item.delta_h:
            params["delta_h"] = item.delta_h
        if item.name:
            params["name"] = item.name
        if item.n_carbons is not None:
            params["n_carbons"] = item.n_carbons
        record: Dict[str, Any] = {"line": item.line, "name": item.name or safe,
                                  "kind": use_kind, "smiles": item.smiles}
        try:
            result = api.render(use_kind, out=str(directory / (safe + "." + wanted[0])),
                                params=params, formats=wanted, dpi=dpi)
        except RenderError as exc:
            record["error"] = str(exc)
        else:
            record["outputs"] = dict(result.outputs)
            record["path"] = str(result.path)
            record["size"] = list(result.size)
            record["warnings"] = list(result.warnings)
        report.items.append(record)
    return report


def watch(csv_path, outdir, *, kind: str = "alcohol", formats: Sequence[str] = ("png",),
          dpi: int = 300, interval: float = 1.0,
          filename_template: str = "{index:02d}_{name}",
          iterations: Optional[int] = None,
          on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
          sleep: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """监视 CSV：文件一变就重出图（教学演示"边写边看"）。

    * 第一遍立即出图；之后只在 **mtime 变化**时重出
    * ``iterations`` 非 None 时最多循环这么多次（测试用，避免死循环）
    * CSV 出错**不退出**，只记录一次 error 事件，等下次修改再试
    * ``sleep`` 可注入（测试里用它模拟"文件被改了"）
    """
    target = Path(csv_path).expanduser()
    out = Path(outdir).expanduser()
    events: List[Dict[str, Any]] = []
    last_mtime: Optional[float] = None
    runs = 0

    while iterations is None or runs < iterations:
        runs += 1
        try:
            mtime = target.stat().st_mtime
        except OSError as exc:
            event: Dict[str, Any] = {"error": i18n.t("batch.err.not_found", path=target),
                                    "kind": "missing"}
            events.append(event)
            if on_event:
                on_event(event)
            sleep(interval)
            continue

        if last_mtime is not None and mtime == last_mtime:
            sleep(interval)
            continue
        last_mtime = mtime

        try:
            items = read_csv(target)
            report = run_batch(items, out, kind=kind, formats=formats, dpi=dpi,
                               filename_template=filename_template)
        except RenderError as exc:
            event = {"error": str(exc), "kind": "invalid", "mtime": mtime}
        else:
            event = {"kind": "rendered", "mtime": mtime, "total": report.total,
                     "succeeded": len(report.succeeded), "failed": len(report.failed),
                     "warnings": report.warnings,
                     "paths": [item.get("path") for item in report.succeeded]}
        events.append(event)
        if on_event:
            on_event(event)
        sleep(interval)

    return {"runs": runs, "renders": sum(1 for e in events if e.get("kind") == "rendered"),
            "events": events, "dir": str(out)}
