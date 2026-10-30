"""功能卡片的 Qt 界面：便签、三步任务、下班收尾、健康卡。

单独一个模块而不是塞进 qt_window：这些卡片有输入框、多行文本和按钮组，
和桌宠窗口的生命周期完全不同，混在一起会让右键菜单那部分越来越难维护。

约定：
* 卡片只是编辑器，不持有业务状态；提交都交给 ExpansionHub。
* 中文输入法安全：用 QLineEdit / QPlainTextEdit 的原生行为，Enter 不拦截
  组合输入；提交用按钮或 Ctrl+Enter。
* 关闭卡片不丢已输入内容：草稿留在控件里，重新打开还在。
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

CARD_QSS = """
QDialog { background: #ffffff; }
QLabel { color: #262a38; font-size: 13px; }
QLabel#hint { color: #788499; font-size: 11px; }
QLineEdit, QPlainTextEdit {
    background: #f7f8fb; border: 1px solid #d9dee9; border-radius: 6px;
    padding: 6px; color: #262a38; font-size: 13px;
}
QPushButton {
    background: #5d81df; color: #ffffff; border: none; border-radius: 6px;
    padding: 6px 14px; font-size: 13px;
}
QPushButton#secondary { background: #eef1f7; color: #4a5266; }
QPushButton:disabled { background: #dfe3ec; color: #9aa3b5; }
"""


def _screen_geometry() -> tuple[int, int, int, int]:
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return 0, 0, 800, 600
    geo = screen.availableGeometry()
    return geo.x(), geo.y(), geo.width(), geo.height()


class _Card(QDialog):
    """卡片基类：无边框、置顶、居中偏下、Esc 关闭。"""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setStyleSheet(CARD_QSS)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Dialog
        )
        self.setModal(False)
        self.setMinimumWidth(340)
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(16, 14, 16, 14)
        self._root.setSpacing(8)
        heading = QLabel(title)
        heading.setStyleSheet("font-size: 15px; font-weight: 600;")
        self._root.addWidget(heading)

    def add_hint(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("hint")
        label.setWordWrap(True)
        self._root.addWidget(label)
        return label

    def add_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self._root.addLayout(row)
        return row

    def show_centered(self) -> None:
        self.adjustSize()
        x, y, w, h = _screen_geometry()
        self.move(x + (w - self.width()) // 2, y + int(h * 0.62))
        self.show()
        self.raise_()
        self.activateWindow()


class NoteCard(_Card):
    """F01 快速记一条。"""

    submitted = Signal(str, list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("记一下", parent)
        self.body = QPlainTextEdit()
        self.body.setPlaceholderText("先记下来，稍后再整理。Ctrl+Enter 保存。")
        self.body.setFixedHeight(110)
        self.body.setTabChangesFocus(True)
        self._root.addWidget(self.body)

        self.tags = QLineEdit()
        self.tags.setPlaceholderText("标签，逗号分隔（可留空）")
        self._root.addWidget(self.tags)

        self.status = self.add_hint("")
        row = self.add_row()
        row.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("secondary")
        cancel.clicked.connect(self.close)
        row.addWidget(cancel)
        save = QPushButton("记下")
        save.setDefault(True)
        save.clicked.connect(self._submit)
        row.addWidget(save)

    def _submit(self) -> None:
        text = self.body.toPlainText().strip()
        if not text:
            self.status.setText("内容不能为空。")
            return
        raw = self.tags.text().strip()
        tags = [t.strip() for t in raw.replace("，", ",").split(",") if t.strip()]
        self.submitted.emit(text, tags)
        self.body.clear()
        self.tags.clear()
        self.status.setText("")
        self.close()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self._submit()
            return
        super().keyPressEvent(event)

    def open_card(self) -> None:
        self.show_centered()
        self.body.setFocus()


class TaskCard(_Card):
    """F02 把一个任务拆成最多三步。"""

    submitted = Signal(str, list)

    STEPS = 3

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("陪我做件事", parent)
        self.add_hint("最多三步。开始后只显示当前一步，完成由你自己打勾。")
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("要做的事，例如：把扩展文档写完")
        self._root.addWidget(self.title_edit)

        self.step_edits: list[QLineEdit] = []
        for index in range(self.STEPS):
            edit = QLineEdit()
            edit.setPlaceholderText(f"第 {index + 1} 步" + ("（可留空）" if index else ""))
            self._root.addWidget(edit)
            self.step_edits.append(edit)

        self.status = self.add_hint("")
        row = self.add_row()
        row.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("secondary")
        cancel.clicked.connect(self.close)
        row.addWidget(cancel)
        start = QPushButton("开始陪我做")
        start.setDefault(True)
        start.clicked.connect(self._submit)
        row.addWidget(start)

    def _submit(self) -> None:
        title = self.title_edit.text().strip()
        steps = [e.text().strip() for e in self.step_edits if e.text().strip()]
        if not title:
            self.status.setText("先写一句要做的事。")
            return
        if not steps:
            self.status.setText("至少写一步。")
            return
        self.submitted.emit(title, steps)
        self.title_edit.clear()
        for edit in self.step_edits:
            edit.clear()
        self.status.setText("")
        self.close()

    def open_card(self, *, title: str = "", step: str = "") -> None:
        if title:
            self.title_edit.setText(title)
        if step:
            self.step_edits[0].setText(step)
        self.show_centered()
        (self.step_edits[0] if title else self.title_edit).setFocus()


class WrapupCard(_Card):
    """F03 下班收尾：留下什么、明天第一步、小空间。"""

    submitted = Signal(str, str)   # (next_step, scene_choice)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("今天到这里", parent)
        self.add_hint("按你想要的方式结束。不关电脑，也不取消还在跑的任务。")

        self.next_step = QLineEdit()
        self.next_step.setPlaceholderText("明天第一步（可留空）")
        self._root.addWidget(self.next_step)

        self.scene = QComboBox()
        self.scene.addItem("小空间保持原样", "keep")
        self.scene.addItem("把小空间收起来", "hide")
        self.scene.addItem("只关掉小灯", "lamp_off")
        self._root.addWidget(self.scene)

        self.keep_tasks = QCheckBox("把当前任务留在列表里")
        self.keep_tasks.setChecked(True)
        self._root.addWidget(self.keep_tasks)

        self.status = self.add_hint("")
        row = self.add_row()
        row.addStretch(1)
        cancel = QPushButton("再想想")
        cancel.setObjectName("secondary")
        cancel.clicked.connect(self.close)
        row.addWidget(cancel)
        done = QPushButton("收好今天")
        done.setDefault(True)
        done.clicked.connect(self._submit)
        row.addWidget(done)

    def _submit(self) -> None:
        self.submitted.emit(
            self.next_step.text().strip(), self.scene.currentData() or "keep"
        )
        self.close()

    def open_card(self, *, next_step: str = "") -> None:
        if next_step:
            self.next_step.setText(next_step)
        self.show_centered()
        self.next_step.setFocus()


class HealthCard(_Card):
    """F08 一张健康小卡：做过了 / 稍后 / 今天先关。"""

    done_clicked = Signal()
    snooze_clicked = Signal()
    mute_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("休息一下", parent)
        self.message = QLabel("")
        self.message.setWordWrap(True)
        self._root.addWidget(self.message)

        row = self.add_row()
        mute = QPushButton("今天先关")
        mute.setObjectName("secondary")
        mute.clicked.connect(self._mute)
        row.addWidget(mute)
        row.addStretch(1)
        later = QPushButton("稍后 10 分钟")
        later.setObjectName("secondary")
        later.clicked.connect(self._snooze)
        row.addWidget(later)
        done = QPushButton("做过了")
        done.setDefault(True)
        done.clicked.connect(self._done)
        row.addWidget(done)

    def _done(self) -> None:
        self.done_clicked.emit()
        self.close()

    def _snooze(self) -> None:
        self.snooze_clicked.emit()
        self.close()

    def _mute(self) -> None:
        self.mute_clicked.emit()
        self.close()

    def open_card(self, name: str) -> None:
        self.message.setText(f"{name}时间到啦，要不要一起？")
        self.show_centered()


class HealthSettingsCard(_Card):
    """F08 计划配置：间隔、时段、每日上限。"""

    saved = Signal(str, int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("健康小卡设置", parent)
        self.add_hint("只在本机计算，不会记录你的健康数据。")
        self.kind = QComboBox()
        for key, label in (("water", "喝口水"), ("eyes", "看看远处"), ("stretch", "活动肩颈")):
            self.kind.addItem(label, key)
        form = QFormLayout()
        form.addRow("提醒什么", self.kind)
        self.interval = QSpinBox()
        self.interval.setRange(15, 180)
        self.interval.setSingleStep(5)
        self.interval.setValue(60)
        self.interval.setSuffix(" 分钟")
        form.addRow("间隔", self.interval)
        self.cap = QSpinBox()
        self.cap.setRange(1, 8)
        self.cap.setValue(4)
        self.cap.setSuffix(" 次/天")
        form.addRow("每日上限", self.cap)
        self._root.addLayout(form)

        row = self.add_row()
        row.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("secondary")
        cancel.clicked.connect(self.close)
        row.addWidget(cancel)
        save = QPushButton("保存")
        save.setDefault(True)
        save.clicked.connect(self._submit)
        row.addWidget(save)

    def _submit(self) -> None:
        self.saved.emit(self.kind.currentData(), self.interval.value(), self.cap.value())
        self.close()

    def open_card(self, *, kind: str = "water", interval: int = 60, cap: int = 4) -> None:
        index = self.kind.findData(kind)
        if index >= 0:
            self.kind.setCurrentIndex(index)
        self.interval.setValue(interval)
        self.cap.setValue(cap)
        self.show_centered()


class NotesCard(_Card):
    """F01 便签列表：搜索、打开、归档、删除。"""

    open_note = Signal(str)
    archive_note = Signal(str, bool)
    trash_note = Signal(str)
    new_note = Signal()
    search_changed = Signal(str)
    show_trash = Signal()
    restore_note = Signal(str)
    purge_note = Signal(str)
    export_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("便签", parent)
        self.setMinimumWidth(380)
        self.setMinimumHeight(320)

        self.query = QLineEdit()
        self.query.setPlaceholderText("搜索标题、正文或标签")
        self.query.textChanged.connect(self.search_changed.emit)
        self._root.addWidget(self.query)

        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(4)
        self._root.addWidget(self.list_container, 1)

        self.empty = self.add_hint("还没有便签。点下面的「记一条」开始。")
        row = self.add_row()
        add = QPushButton("记一条")
        add.clicked.connect(self.new_note.emit)
        row.addWidget(add)
        trash = QPushButton("回收站")
        trash.setObjectName("secondary")
        trash.clicked.connect(self.show_trash.emit)
        row.addWidget(trash)
        export = QPushButton("导出")
        export.setObjectName("secondary")
        export.clicked.connect(self.export_requested.emit)
        row.addWidget(export)
        row.addStretch(1)
        close = QPushButton("关闭")
        close.setObjectName("secondary")
        close.clicked.connect(self.close)
        row.addWidget(close)

    def set_items(self, items: list[dict]) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.empty.setVisible(not items)
        for note in items:
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(6)
            button = QPushButton(note.get("title") or "（空便签）")
            button.setObjectName("secondary")
            button.setStyleSheet("text-align: left; padding: 5px 8px;")
            button.setToolTip(note.get("excerpt") or note.get("title") or "")
            note_id = note.get("id", "")
            button.clicked.connect(lambda _c=False, nid=note_id: self.open_note.emit(nid))
            line.addWidget(button, 1)
            if note.get("archived"):
                tag = QLabel("归档")
                tag.setObjectName("hint")
                line.addWidget(tag)
            arch = QPushButton("归档" if not note.get("archived") else "恢复")
            arch.setObjectName("secondary")
            arch.clicked.connect(
                lambda _c=False, nid=note_id, cur=bool(note.get("archived")):
                self.archive_note.emit(nid, not cur)
            )
            line.addWidget(arch)
            trash = QPushButton("删除")
            trash.setObjectName("secondary")
            trash.clicked.connect(lambda _c=False, nid=note_id: self.trash_note.emit(nid))
            line.addWidget(trash)
            self.list_layout.addWidget(row)
        self.list_layout.addStretch(1)

    def set_trash(self, items: list[dict]) -> None:
        """回收站视图：只有恢复和永久删除两个动作。"""
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.empty.setVisible(not items)
        self.empty.setText("回收站是空的。")
        for note in items:
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            label = QLabel(note.get("title") or "（空便签）")
            line.addWidget(label, 1)
            note_id = note.get("id", "")
            restore = QPushButton("恢复")
            restore.setObjectName("secondary")
            restore.clicked.connect(lambda _c=False, nid=note_id: self.restore_note.emit(nid))
            line.addWidget(restore)
            purge = QPushButton("永久删除")
            purge.setObjectName("secondary")
            purge.clicked.connect(lambda _c=False, nid=note_id: self.purge_note.emit(nid))
            line.addWidget(purge)
            self.list_layout.addWidget(row)
        self.list_layout.addStretch(1)

    def open_card(self, *, query: str = "") -> None:
        if query:
            self.query.setText(query)
        self.show_centered()
        self.query.setFocus()


class NoteEditorCard(_Card):
    """编辑一条已有便签。"""

    saved = Signal(str, str, int)     # (note_id, body, expected_revision)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("编辑便签", parent)
        self._note_id = ""
        self._revision = 0
        self.body = QPlainTextEdit()
        self.body.setFixedHeight(150)
        self._root.addWidget(self.body)
        self.meta = self.add_hint("")
        row = self.add_row()
        row.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("secondary")
        cancel.clicked.connect(self.close)
        row.addWidget(cancel)
        save = QPushButton("保存")
        save.setDefault(True)
        save.clicked.connect(self._submit)
        row.addWidget(save)

    def _submit(self) -> None:
        text = self.body.toPlainText().strip()
        if not text:
            self.meta.setText("内容不能为空。")
            return
        self.saved.emit(self._note_id, text, self._revision)
        self.close()

    def open_note(self, note) -> None:
        self._note_id = note.id
        self._revision = note.revision
        self.body.setPlainText(note.body)
        stamp = ""
        if note.updated_at:
            import time as _time
            stamp = _time.strftime("%Y-%m-%d %H:%M", _time.localtime(note.updated_at))
        tags = " ".join(f"#{t}" for t in note.tags)
        self.meta.setText(" · ".join(x for x in (stamp, tags) if x) or "还没有标签。")
        self.show_centered()
        self.body.setFocus()


class TaskListCard(_Card):
    """F02 任务列表：继续、暂停、取消。"""

    activate_task = Signal(str)
    pause_task = Signal(str)
    cancel_task = Signal(str)
    undo_step = Signal(str)
    new_task = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("陪我做件事", parent)
        self.setMinimumWidth(380)
        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(6)
        self._root.addWidget(self.list_container, 1)
        self.empty = self.add_hint("现在没有进行中的任务。")
        row = self.add_row()
        add = QPushButton("新任务")
        add.clicked.connect(self.new_task.emit)
        row.addWidget(add)
        row.addStretch(1)
        close = QPushButton("关闭")
        close.setObjectName("secondary")
        close.clicked.connect(self.close)
        row.addWidget(close)

    def set_items(self, items: list[dict]) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.empty.setVisible(not items)
        for task in items:
            box = QWidget()
            layout = QVBoxLayout(box)
            layout.setContentsMargins(0, 2, 0, 2)
            layout.setSpacing(3)
            head = QLabel(f"{task['title']}　{task['progress']}")
            head.setStyleSheet("font-weight: 600;")
            layout.addWidget(head)
            if task.get("current"):
                step = QLabel(f"现在：{task['current']}")
                step.setObjectName("hint")
                layout.addWidget(step)
            buttons = QHBoxLayout()
            buttons.setSpacing(6)
            task_id = task["id"]
            if task.get("status") == "active":
                pause = QPushButton("先停一下")
                pause.setObjectName("secondary")
                pause.clicked.connect(lambda _c=False, tid=task_id: self.pause_task.emit(tid))
                buttons.addWidget(pause)
                if task.get("done_steps"):
                    undo = QPushButton("撤销上一步")
                    undo.setObjectName("secondary")
                    undo.clicked.connect(lambda _c=False, tid=task_id: self.undo_step.emit(tid))
                    buttons.addWidget(undo)
            else:
                go = QPushButton("接着做")
                go.clicked.connect(lambda _c=False, tid=task_id: self.activate_task.emit(tid))
                buttons.addWidget(go)
            cancel = QPushButton("取消")
            cancel.setObjectName("secondary")
            cancel.clicked.connect(lambda _c=False, tid=task_id: self.cancel_task.emit(tid))
            buttons.addWidget(cancel)
            buttons.addStretch(1)
            layout.addLayout(buttons)
            self.list_layout.addWidget(box)
        self.list_layout.addStretch(1)

    def open_card(self) -> None:
        self.show_centered()
