"""chemfigstudio.history — 编辑会话：多标签页 + 撤销/重做。

契约（、5、6）：

* **多标签**：每个标签一份独立文档与独立历史；关标签不影响其它标签
* **撤销/重做 ≥50 步**（本实现默认保留 100 步快照）
* **图层面板**的操作（增/切换可见性）同样进历史，可撤销
* ``snapshot()/restore()`` 支持崩溃恢复：标签、当前标签、历史一起存

实现取向：历史存**文档快照**（``Document.as_dict()``，很小的字典），
简单、可序列化、不可能出现"重放不回去"的状态不一致。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .document import Document

__all__ = ["Tab", "DocumentSession", "HISTORY_LIMIT"]

#: 每个标签保留的历史步数（验收要求 ≥50）
HISTORY_LIMIT = 100


@dataclass
class Tab:
    """一个标签页：文档 + 历史 + 游标。"""

    id: str
    document: Document
    history: List[Dict[str, Any]] = field(default_factory=list)
    index: int = -1                      # 指向 history 里的当前快照
    title: str = ""

    # ------------------------------------------------------------------ 基本
    def label(self) -> str:
        return self.title or str(self.document.figure.get("kind") or "document")

    def snapshot(self) -> Dict[str, Any]:
        return self.document.as_dict()


class DocumentSession:
    """编辑会话：管理多个标签页与它们的历史。"""

    def __init__(self, limit: int = HISTORY_LIMIT) -> None:
        self.limit = max(10, int(limit))
        self.tabs: List[Tab] = []
        self.active_id: Optional[str] = None

    # ------------------------------------------------------------------ 标签
    def new_tab(self, document: Optional[Document] = None,
                title: str = "") -> str:
        doc = document or Document.new("alcohol", {})
        tab = Tab(id=uuid.uuid4().hex[:8], document=doc, title=title)
        tab.history = [tab.snapshot()]
        tab.index = 0
        self.tabs.append(tab)
        self.active_id = tab.id
        return tab.id

    def rename_tab(self, tab_id: str, title: str) -> bool:
        """给标签改名（只影响显示，不进历史栈）。"""
        tab = self.tab(tab_id)
        if tab is None:
            return False
        tab.title = str(title or "").strip()[:60]
        return True

    def close_tab(self, tab_id: str) -> bool:
        for position, tab in enumerate(self.tabs):
            if tab.id != tab_id:
                continue
            self.tabs.pop(position)
            if self.active_id == tab_id:
                self.active_id = self.tabs[min(position, len(self.tabs) - 1)].id if self.tabs else None
            return True
        return False

    def select(self, tab_id: str) -> bool:
        if any(tab.id == tab_id for tab in self.tabs):
            self.active_id = tab_id
            return True
        return False

    @property
    def active(self) -> Optional[Tab]:
        for tab in self.tabs:
            if tab.id == self.active_id:
                return tab
        return self.tabs[0] if self.tabs else None

    def tab(self, tab_id: str) -> Optional[Tab]:
        for item in self.tabs:
            if item.id == tab_id:
                return item
        return None

    # ------------------------------------------------------------------ 历史
    def _push(self, tab: Tab) -> None:
        """把当前文档状态作为新快照追加，并截断到上限。"""
        snapshot = tab.snapshot()
        tab.history = tab.history[:tab.index + 1]          # 撤销后再改动 → 丢掉"未来"
        tab.history.append(snapshot)
        if len(tab.history) > self.limit:
            drop = len(tab.history) - self.limit
            tab.history = tab.history[drop:]
        tab.index = len(tab.history) - 1

    def can_undo(self, tab_id: Optional[str] = None) -> bool:
        tab = self.tab(tab_id) if tab_id else self.active
        return bool(tab and tab.index > 0)

    def can_redo(self, tab_id: Optional[str] = None) -> bool:
        tab = self.tab(tab_id) if tab_id else self.active
        return bool(tab and tab.index < len(tab.history) - 1)

    def undo(self, tab_id: Optional[str] = None) -> bool:
        tab = self.tab(tab_id) if tab_id else self.active
        if not tab or tab.index <= 0:
            return False
        tab.index -= 1
        tab.document = Document.from_dict(tab.history[tab.index])
        return True

    def redo(self, tab_id: Optional[str] = None) -> bool:
        tab = self.tab(tab_id) if tab_id else self.active
        if not tab or tab.index >= len(tab.history) - 1:
            return False
        tab.index += 1
        tab.document = Document.from_dict(tab.history[tab.index])
        return True

    def history_depth(self, tab_id: Optional[str] = None) -> int:
        tab = self.tab(tab_id) if tab_id else self.active
        return len(tab.history) if tab else 0

    # ------------------------------------------------------------------ 编辑
    def mutate(self, tab_id: Optional[str], action: Callable[[Document], Any]) -> Any:
        """在标签上改文档并记入历史（撤销/重做的唯一入口）。"""
        tab = self.tab(tab_id) if tab_id else self.active
        if tab is None:
            raise ValueError("no such tab: %r" % (tab_id,))
        result = action(tab.document)
        self._push(tab)
        return result

    def set_figure(self, kind: str, params: Optional[Dict[str, Any]] = None,
                   tab_id: Optional[str] = None) -> None:
        """「应用」：只改文档 + 记历史，**不落盘**（/2）。"""
        self.mutate(tab_id, lambda doc: doc.set_figure(kind, params))

    def add_layer(self, name: str, tab_id: Optional[str] = None) -> Dict[str, Any]:
        return self.mutate(tab_id, lambda doc: doc.add_layer(name))

    def toggle_layer(self, layer_id: str, visible: Optional[bool] = None,
                     tab_id: Optional[str] = None) -> None:
        self.mutate(tab_id, lambda doc: doc.toggle_layer(layer_id, visible))

    # ------------------------------------------------------- 快照 / 崩溃恢复
    def snapshot(self) -> Dict[str, Any]:
        return {
            "savedAt": time.time(),
            "activeId": self.active_id,
            "limit": self.limit,
            "tabs": [{"id": tab.id, "title": tab.title, "index": tab.index,
                      "history": tab.history} for tab in self.tabs],
        }

    @classmethod
    def restore(cls, state: Dict[str, Any]) -> "DocumentSession":
        session = cls(limit=int(state.get("limit") or HISTORY_LIMIT))
        for raw in state.get("tabs") or []:
            history = [Document.from_dict(item).as_dict() for item in raw.get("history") or []]
            if not history:
                continue
            index = max(0, min(int(raw.get("index") or 0), len(history) - 1))
            tab = Tab(id=str(raw.get("id") or uuid.uuid4().hex[:8]),
                      document=Document.from_dict(history[index]),
                      history=history, index=index, title=str(raw.get("title") or ""))
            session.tabs.append(tab)
        session.active_id = state.get("activeId") or (session.tabs[0].id if session.tabs else None)
        if session.active_id and not session.tab(session.active_id):
            session.active_id = session.tabs[0].id if session.tabs else None
        return session

    # ------------------------------------------------------------------ 展示
    def as_dict(self) -> Dict[str, Any]:
        return {
            "activeId": self.active_id,
            "tabs": [{"id": tab.id, "title": tab.title, "label": tab.label(),
                      "kind": tab.document.figure.get("kind"),
                      "dirty": tab.index > 0,
                      "canUndo": self.can_undo(tab.id), "canRedo": self.can_redo(tab.id),
                      "layers": len(tab.document.layers)} for tab in self.tabs],
        }
