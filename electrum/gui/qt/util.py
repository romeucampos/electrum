from abc import ABC, ABCMeta
import os.path
import time
import sys
import platform
import queue
import os
import webbrowser
import ctypes
from functools import partial, lru_cache, wraps
from typing import (NamedTuple, Callable, Optional, TYPE_CHECKING, List, Any, Sequence, Tuple, Union)

from PyQt6 import QtCore
from PyQt6.QtGui import (QFont, QColor, QCursor, QPixmap, QImage,
                         QPalette, QIcon, QFontMetrics, QPainter, QContextMenuEvent, QMovie)
from PyQt6.QtCore import (Qt, pyqtSignal, QCoreApplication, QSize, QRect, QPoint, QObject)
from PyQt6.QtWidgets import (QPushButton, QLabel, QMessageBox, QHBoxLayout, QVBoxLayout, QLineEdit,
                             QStyle, QDialog, QGroupBox, QButtonGroup, QRadioButton,
                             QFileDialog, QWidget, QToolButton, QPlainTextEdit, QApplication, QToolTip,
                             QGraphicsEffect, QGraphicsScene, QGraphicsPixmapItem, QLayoutItem, QLayout, QMenu,
                             QFrame, QAbstractButton)

from electrum.i18n import _
from electrum.util import (FileImportFailed, FileExportFailed, resource_path, EventListener, event_listener,
                           get_logger, UserCancelled, UserFacingException, ChoiceItem)
from electrum.invoices import (PR_UNPAID, PR_PAID, PR_EXPIRED, PR_INFLIGHT, PR_UNKNOWN, PR_FAILED, PR_ROUTING,
                               PR_UNCONFIRMED, PR_BROADCASTING, PR_BROADCAST)
from electrum.qrreader import MissingQrDetectionLib, QrCodeResult

from electrum.gui.common_qt.util import TaskThread

if TYPE_CHECKING:
    from .main_window import ElectrumWindow
    from .paytoedit import PayToEdit

    from electrum.simple_config import SimpleConfig
    from electrum.simple_config import ConfigVarWithConfig


if platform.system() == 'Windows':
    MONOSPACE_FONT = 'Lucida Console'
elif platform.system() == 'Darwin':
    MONOSPACE_FONT = 'Monaco'
else:
    MONOSPACE_FONT = 'monospace'


_logger = get_logger(__name__)

dialogs = []

pr_icons = {
    PR_UNKNOWN: "warning.png",
    PR_UNPAID: "unpaid.png",
    PR_PAID: "confirmed.png",
    PR_EXPIRED: "expired.png",
    PR_INFLIGHT: "unconfirmed.png",
    PR_FAILED: "warning.png",
    PR_ROUTING: "unconfirmed.png",
    PR_UNCONFIRMED: "unconfirmed.png",
    PR_BROADCASTING: "unconfirmed.png",
    PR_BROADCAST: "unconfirmed.png",
}


# filter tx files in QFileDialog:
TRANSACTION_FILE_EXTENSION_FILTER_ANY = "Transaction (*.txn *.psbt);;All files (*)"
TRANSACTION_FILE_EXTENSION_FILTER_ONLY_PARTIAL_TX = "Partial Transaction (*.psbt)"
TRANSACTION_FILE_EXTENSION_FILTER_ONLY_COMPLETE_TX = "Complete Transaction (*.txn)"
TRANSACTION_FILE_EXTENSION_FILTER_SEPARATE = (f"{TRANSACTION_FILE_EXTENSION_FILTER_ONLY_PARTIAL_TX};;"
                                              f"{TRANSACTION_FILE_EXTENSION_FILTER_ONLY_COMPLETE_TX};;"
                                              f"All files (*)")


class EnterButton(QPushButton):
    def __init__(self, text, func):
        QPushButton.__init__(self, text)
        self.func = func
        self.clicked.connect(func)
        self._orig_text = text

    def keyPressEvent(self, e):
        if e.key() in [Qt.Key.Key_Return, Qt.Key.Key_Enter]:
            self.func()

    def restore_original_text(self):
        self.setText(self._orig_text)


class ThreadedButton(QPushButton):
    def __init__(self, text, task, on_success=None, on_error=None):
        QPushButton.__init__(self, text)
        self.task = task
        self.on_success = on_success
        self.on_error = on_error
        self.clicked.connect(self.run_task)

    def run_task(self):
        self.setEnabled(False)
        self.thread = TaskThread(self)
        self.thread.add(self.task, self.on_success, self.done, self.on_error)

    def done(self):
        self.setEnabled(True)
        self.thread.stop()


class WWLabel(QLabel):
    """Word-wrapping label"""
    def __init__(self, text="", parent=None):
        QLabel.__init__(self, text, parent)
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)


class RichLabel(WWLabel):
    """Word-wrapping label with link activation"""
    def __init__(self, text='', parent=None):
        WWLabel.__init__(self, text, parent)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.setOpenExternalLinks(True)


class AmountLabel(QLabel):
    def __init__(self, *args, **kwargs):
        QLabel.__init__(self, *args, **kwargs)
        self.setFont(QFont(MONOSPACE_FONT))
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)


class Spinner(QLabel):
    def __init__(self, *args, **kwargs):
        QLabel.__init__(self, *args, **kwargs)
        self.spinner = QMovie(icon_path('spinner.gif'))
        self.spinner.setScaledSize(QSize(20, 20))
        self.spinner.frameChanged.connect(lambda: self.setPixmap(self.spinner.currentPixmap()))
        self.setVisible(False)

    def setVisible(self, visible):
        if visible:
            self.spinner.start()
        else:
            self.spinner.stop()
        super().setVisible(visible)


class HelpMixin:
    def __init__(self, help_text: str, *, help_title: str = None):
        assert isinstance(self, QWidget), "HelpMixin must be a QWidget instance!"
        self.help_text = help_text
        self._help_title = help_title or _('Help')
        if isinstance(self, QLabel):
            self.setTextInteractionFlags(
                (self.textInteractionFlags() | Qt.TextInteractionFlag.TextSelectableByMouse)
                & ~Qt.TextInteractionFlag.TextSelectableByKeyboard)

    def show_help(self):
        custom_message_box(
            icon=QMessageBox.Icon.Information,
            parent=self,
            title=self._help_title,
            text=self.help_text,
            rich_text=True,
        )


class HelpLabel(HelpMixin, QLabel):

    def __init__(self, text: str, help_text: str):
        QLabel.__init__(self, text)
        HelpMixin.__init__(self, help_text)
        self.app = QCoreApplication.instance()
        self.font = self.font()

    @classmethod
    def from_configvar(cls, cv: 'ConfigVarWithConfig') -> 'HelpLabel':
        return HelpLabel(cv.get_short_desc() + ':', cv.get_long_desc())

    def mouseReleaseEvent(self, x):
        self.show_help()

    def enterEvent(self, event):
        self.font.setUnderline(True)
        self.setFont(self.font)
        self.app.setOverrideCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        return QLabel.enterEvent(self, event)

    def leaveEvent(self, event):
        self.font.setUnderline(False)
        self.setFont(self.font)
        self.app.setOverrideCursor(QCursor(Qt.CursorShape.ArrowCursor))
        return QLabel.leaveEvent(self, event)


class HelpButton(HelpMixin, QToolButton):
    def __init__(self, text: str):
        QToolButton.__init__(self)
        HelpMixin.__init__(self, text)
        self.setText('?')
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedWidth(round(2.2 * char_width_in_lineedit()))
        self.clicked.connect(self.show_help)


class InfoButton(HelpMixin, QPushButton):
    def __init__(self, text: str):
        QPushButton.__init__(self, _('Info'))
        HelpMixin.__init__(self, text, help_title=_('Info'))
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedWidth(6 * char_width_in_lineedit())
        self.clicked.connect(self.show_help)


class Buttons(QHBoxLayout):
    def __init__(self, *buttons):
        QHBoxLayout.__init__(self)
        self.addStretch(1)
        for b in buttons:
            if b is None:
                continue
            self.addWidget(b)


class CloseButton(QPushButton):
    def __init__(self, dialog):
        QPushButton.__init__(self, _("Close"))
        self.clicked.connect(dialog.close)
        self.setDefault(True)


class CopyButton(QPushButton):
    def __init__(self, text_getter, app):
        QPushButton.__init__(self, _("Copy"))
        self.clicked.connect(lambda: app.clipboard().setText(text_getter()))


class CopyCloseButton(QPushButton):
    def __init__(self, text_getter, app, dialog):
        QPushButton.__init__(self, _("Copy and Close"))
        self.clicked.connect(lambda: app.clipboard().setText(text_getter()))
        self.clicked.connect(dialog.close)
        self.setDefault(True)


class OkButton(QPushButton):
    def __init__(self, dialog, label=None):
        QPushButton.__init__(self, label or _("OK"))
        self.clicked.connect(dialog.accept)
        self.setDefault(True)


class CancelButton(QPushButton):
    def __init__(self, dialog, label=None):
        QPushButton.__init__(self, label or _("Cancel"))
        self.clicked.connect(dialog.reject)


class MessageBoxMixin(object):
    def top_level_window_recurse(self, window=None, test_func=None):
        window = window or self
        classes = (WindowModalDialog, QMessageBox)
        if test_func is None:
            test_func = lambda x: True
        for n, child in enumerate(window.children()):
            # Test for visibility as old closed dialogs may not be GC-ed.
            # Only accept children that confirm to test_func.
            if isinstance(child, classes) and child.isVisible() \
                    and test_func(child):
                return self.top_level_window_recurse(child, test_func=test_func)
        return window

    def top_level_window(self, test_func=None):
        return self.top_level_window_recurse(test_func)

    def question(self, msg, parent=None, title=None, icon=None, **kwargs) -> bool:
        yes, no = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
        return yes == self.msg_box(icon=icon or QMessageBox.Icon.Question,
                                   parent=parent,
                                   title=title or '',
                                   text=msg,
                                   buttons=yes | no,
                                   defaultButton=no,
                                   **kwargs)

    def show_warning(self, msg, parent=None, title=None, **kwargs):
        return self.msg_box(QMessageBox.Icon.Warning, parent,
                            title or _('Warning'), msg, **kwargs)

    def show_error(self, msg, parent=None, **kwargs):
        return self.msg_box(QMessageBox.Icon.Warning, parent,
                            _('Error'), msg, **kwargs)

    def show_critical(self, msg, parent=None, title=None, **kwargs):
        return self.msg_box(QMessageBox.Icon.Critical, parent,
                            title or _('Critical Error'), msg, **kwargs)

    def show_message(self, msg, parent=None, title=None, icon=QMessageBox.Icon.Information, **kwargs):
        return self.msg_box(icon, parent, title or _('Information'), msg, **kwargs)

    def msg_box(
            self,
            icon: Union[QMessageBox.Icon, QPixmap],
            parent: QWidget,
            title: str,
            text: str,
            *,
            buttons: Union[QMessageBox.StandardButton,
                           List[Union[QMessageBox.StandardButton, Tuple[QAbstractButton, QMessageBox.ButtonRole, int]]]] = QMessageBox.StandardButton.Ok,
            defaultButton: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
            rich_text: bool = False,
            checkbox: Optional[bool] = None
    ):
        parent = parent or self.top_level_window()
        return custom_message_box(
            icon=icon, parent=parent, title=title, text=text, buttons=buttons, defaultButton=defaultButton,
            rich_text=rich_text, checkbox=checkbox
        )

    def query_choice(
        self,
        msg: Optional[str],
        choices: Sequence['ChoiceItem'],
        *,
        title: Optional[str] = None,
        default_key: Optional[Any] = None,
    ) -> Optional[Any]:
        """Returns ChoiceItem.key (for selected item), or None if the user cancels the dialog.

        Needed by QtHandler for hardware wallets.
        """
        if title is None:
            title = _('Question')
        dialog = WindowModalDialog(self.top_level_window(), title=title)
        dialog.setMinimumWidth(400)
        choice_widget = ChoiceWidget(message=msg, choices=choices, default_key=default_key)
        vbox = QVBoxLayout(dialog)
        vbox.addWidget(choice_widget)
        cancel_button = CancelButton(dialog)
        vbox.addLayout(Buttons(cancel_button, OkButton(dialog)))
        cancel_button.setFocus()
        if not dialog.exec():
            return None
        return choice_widget.selected_key

    def password_dialog(self, msg=None, parent=None):
        from .password_dialog import PasswordDialog
        parent = parent or self
        d = PasswordDialog(parent, msg)
        return d.run()


def custom_message_box(
        *,
        icon: Union[QMessageBox.Icon, QPixmap],
        parent: QWidget,
        title: str,
        text: str,
        buttons: Union[QMessageBox.StandardButton,
                       List[Union[QMessageBox.StandardButton, Tuple[QAbstractButton, QMessageBox.ButtonRole, int]]]] = QMessageBox.StandardButton.Ok,
        defaultButton: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
        rich_text: bool = False,
        checkbox: Optional[bool] = None
) -> int:
    custom_buttons = []
    standard_buttons = QMessageBox.StandardButton.NoButton
    if buttons:
        if not isinstance(buttons, list):
            buttons = [buttons]
        for button in buttons:
            if isinstance(button, QMessageBox.StandardButton):
                standard_buttons |= button
            else:
                custom_buttons.append(button)
    if type(icon) is QPixmap:
        d = QMessageBox(QMessageBox.Icon.Information, title, str(text), standard_buttons, parent)
        d.setIconPixmap(icon)
    else:
        d = QMessageBox(icon, title, str(text), standard_buttons, parent)
    for button, role, _ in custom_buttons:
        d.addButton(button, role)
    d.setWindowModality(Qt.WindowModality.WindowModal)
    d.setDefaultButton(defaultButton)
    if rich_text:
        d.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse)
        # set AutoText instead of RichText
        # AutoText lets Qt figure out whether to render as rich text.
        # e.g. if text is actually plain text and uses "\n" newlines;
        #      and we set RichText here, newlines would be swallowed
        d.setTextFormat(Qt.TextFormat.AutoText)
    else:
        d.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        d.setTextFormat(Qt.TextFormat.PlainText)
    if checkbox is not None:
        d.setCheckBox(checkbox)
    result = d.exec()
    for button, _, value in custom_buttons:
        if button == d.clickedButton():
            return value
    return result


class WindowModalDialog(QDialog, MessageBoxMixin):
    '''Handy wrapper; window modal dialogs are better for our multi-window
    daemon model as other wallet windows can still be accessed.'''
    def __init__(self, parent, title=None):
        QDialog.__init__(self, parent)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        if title:
            self.setWindowTitle(title)


class WaitingDialog(WindowModalDialog):
    '''Shows a please wait dialog whilst running a task.  It is not
    necessary to maintain a reference to this dialog.'''
    def __init__(self, parent: QWidget, message: str, task, on_success=None, on_error=None, on_cancel=None):
        assert parent
        if isinstance(parent, MessageBoxMixin):
            parent = parent.top_level_window()
        WindowModalDialog.__init__(self, parent, _("Please wait"))
        self.message_label = QLabel(message)
        vbox = QVBoxLayout(self)
        vbox.addWidget(self.message_label)
        if on_cancel:
            self.cancel_button = CancelButton(self)
            self.cancel_button.clicked.connect(on_cancel)
            vbox.addLayout(Buttons(self.cancel_button))
        self.accepted.connect(self.on_accepted)
        self.show()
        self.thread = TaskThread(self)
        self.thread.finished.connect(self.deleteLater)  # see #3956
        self.thread.add(task, on_success, self.accept, on_error)

    def wait(self):
        self.thread.wait()

    def on_accepted(self):
        self.thread.stop()

    def update(self, msg):
        print(msg)
        self.message_label.setText(msg)


class RunCoroutineDialog(WaitingDialog):

    def __init__(self, parent: QWidget, message: str, coroutine):
        from electrum import util
        import asyncio
        import concurrent.futures
        loop = util.get_asyncio_loop()
        assert util.get_running_loop() != loop, 'must not be called from asyncio thread'
        self._exception = None
        self._result = None
        self._future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        def task():
            try:
                self._result = self._future.result()
            except concurrent.futures.CancelledError:
                self._exception = UserCancelled
            except Exception as e:
                self._exception = e
        WaitingDialog.__init__(self, parent, message, task, on_cancel=self._future.cancel)

    def run(self):
        self.exec()
        if self._exception:
            raise self._exception
        else:
            return self._result


def line_dialog(parent, title, label, ok_label, default=None):
    dialog = WindowModalDialog(parent, title)
    dialog.setMinimumWidth(500)
    l = QVBoxLayout()
    dialog.setLayout(l)
    l.addWidget(QLabel(label))
    txt = QLineEdit()
    if default:
        txt.setText(default)
    l.addWidget(txt)
    l.addLayout(Buttons(CancelButton(dialog), OkButton(dialog, ok_label)))
    if dialog.exec():
        return txt.text()


def text_dialog(
        *,
        parent,
        title,
        header_layout,
        ok_label,
        default=None,
        allow_multi=False,
        config: 'SimpleConfig',
):
    from .qrtextedit import ScanQRTextEdit
    dialog = WindowModalDialog(parent, title)
    dialog.setMinimumWidth(600)
    l = QVBoxLayout()
    dialog.setLayout(l)
    if isinstance(header_layout, str):
        l.addWidget(QLabel(header_layout))
    else:
        l.addLayout(header_layout)
    txt = ScanQRTextEdit(allow_multi=allow_multi, config=config)
    if default:
        txt.setText(default)
    l.addWidget(txt)
    l.addLayout(Buttons(CancelButton(dialog), OkButton(dialog, ok_label)))
    if dialog.exec():
        return txt.toPlainText()


class ChoiceWidget(QWidget):
    """Renders a list of ChoiceItems as a radiobuttons group.
    Callers can pre-select an item by key, through the 'default_key' parameter.
    The selected item is made available by index (selected_index),
    by key (selected_key) and by Choice (selected_item).
    """

    itemSelected = pyqtSignal([int], arguments=['index'])

    def __init__(
        self,
        *,
        message: Optional[str] = None,
        choices: Sequence[ChoiceItem] = None,
        default_key: Optional[Any] = None,
    ):
        QWidget.__init__(self)
        vbox = QVBoxLayout()
        self.setLayout(vbox)

        if choices is None:
            choices = []

        self.selected_index = -1   # type: int
        self.selected_item = None  # type: Optional[ChoiceItem]
        self.selected_key = None   # type: Optional[Any]
        self.choices = choices     # type: Sequence[ChoiceItem]

        if message and len(message) > 50:
            vbox.addWidget(WWLabel(message))
            message = ""
        gb2 = QGroupBox(message)
        vbox.addWidget(gb2)
        vbox2 = QVBoxLayout()
        gb2.setLayout(vbox2)
        self.group = group = QButtonGroup()
        assert isinstance(choices, list)
        for i, c in enumerate(choices):
            assert isinstance(c, ChoiceItem), f"{c=!r}"
            button = QRadioButton(gb2)
            button.setText(c.label)
            vbox2.addWidget(button)
            group.addButton(button)
            group.setId(button, i)
            if (i == 0 and default_key is None) or c.key == default_key:
                self.selected_index = i
                self.selected_item = c
                self.selected_key = c.key
                button.setChecked(True)
        group.buttonClicked.connect(self.on_selected)

    def on_selected(self, button):
        self.selected_index = self.group.id(button)
        self.selected_item = self.choices[self.selected_index]
        self.selected_key = self.choices[self.selected_index].key
        self.itemSelected.emit(self.selected_index)

    def select(self, key):
        for i, c in enumerate(self.choices):
            if key == c.key:
                self.group.button(i).click()


class ResizableStackedWidget(QWidget):
    """Simple alternative to QStackedWidget, as QStackedWidget always resizes to the largest
       widget in the stack, leaving ugly scrollbars where they're not needed."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setLayout(QVBoxLayout())
        self.widgets = []
        self.current_index = -1

    def sizeHint(self) -> QSize:
        if not self.count() or not self.currentWidget():
            return super().sizeHint()
        return self.currentWidget().sizeHint()

    def addWidget(self, widget: QWidget) -> int:
        self.widgets.append(widget)
        self.layout().addWidget(widget)
        if len(self.widgets) == 1:  # first widget?
            self.current_index = 0
        self.showCurrentWidget()
        return len(self.widgets) - 1

    def removeWidget(self, widget: QWidget):
        i = self.widgets.index(widget)
        self.widgets.remove(widget)
        self.layout().removeWidget(widget)
        if self.current_index >= i:
            self.current_index -= 1
            if self.current_index == self.count() - 1:
                self.showCurrentWidget()

    def setCurrentIndex(self, index: int):
        assert isinstance(index, int)
        assert 0 <= index < len(self.widgets), f'invalid widget index {index}'
        self.current_index = index
        self.showCurrentWidget()

    def currentWidget(self) -> Optional[QWidget]:
        if self.current_index < 0:
            return None
        return self.widgets[self.current_index]

    def showCurrentWidget(self):
        if not self.widgets:
            return

        for i, k in enumerate(self.widgets):
            if i == self.current_index:
                k.show()
            else:
                k.hide()

    def count(self) -> int:
        return len(self.widgets)


class VLine(QFrame):
    """Vertical line separator"""
    def __init__(self):
        super(VLine, self).__init__()
        self.setFrameShape(QFrame.Shape.VLine)
        self.setFrameShadow(QFrame.Shadow.Sunken)
        self.setLineWidth(1)


def address_field(addresses, *, btn_text: str = None):
    if btn_text is None:
        btn_text = _('Get wallet address')
    hbox = QHBoxLayout()
    address_e = QLineEdit()
    if addresses and len(addresses) > 0:
        address_e.setText(addresses[0])
    else:
        addresses = []

    def func():
        try:
            i = addresses.index(str(address_e.text())) + 1
            i = i % len(addresses)
            address_e.setText(addresses[i])
        except ValueError:
            # the user might have changed address_e to an
            # address not in the wallet (or to something that isn't an address)
            if addresses and len(addresses) > 0:
                address_e.setText(addresses[0])
    button = QPushButton(btn_text)
    button.clicked.connect(func)
    hbox.addWidget(button)
    hbox.addWidget(address_e)
    return hbox, address_e


def filename_field(parent, config, defaultname, select_msg):
    vbox = QVBoxLayout()
    vbox.addWidget(QLabel(_("Format")))
    gb = QGroupBox("format", parent)
    b1 = QRadioButton(gb)
    b1.setText(_("CSV"))
    b1.setChecked(True)
    b2 = QRadioButton(gb)
    b2.setText(_("json"))
    vbox.addWidget(b1)
    vbox.addWidget(b2)

    hbox = QHBoxLayout()

    directory = config.IO_DIRECTORY
    path = os.path.join(directory, defaultname)
    filename_e = QLineEdit()
    filename_e.setText(path)

    def func():
        text = filename_e.text()
        _filter = "*.csv" if defaultname.endswith(".csv") else "*.json" if defaultname.endswith(".json") else None
        p = getSaveFileName(
            parent=None,
            title=select_msg,
            filename=text,
            filter=_filter,
            config=config,
        )
        if p:
            filename_e.setText(p)

    button = QPushButton(_('File'))
    button.clicked.connect(func)
    hbox.addWidget(button)
    hbox.addWidget(filename_e)
    vbox.addLayout(hbox)

    def set_csv(v):
        text = filename_e.text()
        text = text.replace(".json",".csv") if v else text.replace(".csv",".json")
        filename_e.setText(text)

    b1.clicked.connect(lambda: set_csv(True))
    b2.clicked.connect(lambda: set_csv(False))

    return vbox, filename_e, b1


def get_icon_qrcode() -> QIcon:
    name = "qrcode_white.png" if ColorScheme.dark_scheme else "qrcode.png"
    return read_QIcon(name)


def get_icon_camera() -> QIcon:
    name = "camera_white.png" if ColorScheme.dark_scheme else "camera_dark.png"
    return read_QIcon(name)


def add_input_actions_to_context_menu(gih: 'GenericInputHandler', m: QMenu) -> None:
    if gih.on_qr_from_camera_input_btn:
        m.addAction(get_icon_camera(), _("Read QR code with camera"), gih.on_qr_from_camera_input_btn)
    if gih.on_qr_from_screenshot_input_btn:
        m.addAction(read_QIcon("picture_in_picture.png"), _("Read QR code from screen"), gih.on_qr_from_screenshot_input_btn)
    if gih.on_qr_from_file_input_btn:
        m.addAction(read_QIcon("qr_file.png"), _("Read QR code from file"), gih.on_qr_from_file_input_btn)
    if gih.on_input_file:
        m.addAction(read_QIcon("file.png"), _("Read text from file"), gih.on_input_file)


def scan_qr_from_screenshot() -> QrCodeResult:
    from .qrreader import scan_qr_from_image
    screenshots = [screen.grabWindow(0).toImage()
                   for screen in QApplication.instance().screens()]
    if all(screen.allGray() for screen in screenshots):
        raise UserFacingException(_("Failed to take screenshot."))
    scanned_qr = None
    for screenshot in screenshots:
        try:
            scan_result = scan_qr_from_image(screenshot)
        except MissingQrDetectionLib as e:
            raise UserFacingException(_("Unable to scan image.") + "\n" + repr(e))
        if len(scan_result) > 0:
            if (scanned_qr is not None) or len(scan_result) > 1:
                raise UserFacingException(_("More than one QR code was found on the screen."))
            scanned_qr = scan_result
    if scanned_qr is None:
        raise UserFacingException(_("No QR code was found on the screen."))
    assert len(scanned_qr) == 1, f"{len(scanned_qr)=}, expected 1"
    return scanned_qr[0]


class GenericInputHandler:
    on_qr_from_camera_input_btn: Callable[[], None] = None
    on_qr_from_screenshot_input_btn: Callable[[], None] = None
    on_qr_from_file_input_btn: Callable[[], None] = None
    on_input_file: Callable[[], None] = None

    def input_qr_from_camera(
            self,
            *,
            config: 'SimpleConfig',
            allow_multi: bool = False,
            show_error: Callable[[str], None],
            setText: Callable[[str], None] = None,
            parent: QWidget = None,
    ) -> None:
        if setText is None:
            setText = self.setText
        def cb(success: bool, error: str, data: Optional[str]):
            if not success:
                if error:
                    show_error(error)
                return
            if not data:
                data = ''
            try:
                if allow_multi:
                    text = self.text()
                    if data in text:
                        return
                    if text and not text.endswith('\n'):
                        text += '\n'
                    text += data
                    text += '\n'
                    setText(text)
                else:
                    new_text = data
                    setText(new_text)
            except Exception as e:
                show_error(_('Invalid payment identifier in QR') + ':\n' + repr(e))

        from .qrreader import scan_qrcode_from_camera
        if parent is None:
            parent = self if isinstance(self, QWidget) else None
        scan_qrcode_from_camera(parent=parent, config=config, callback=cb)

    def input_qr_from_screenshot(
            self,
            *,
            allow_multi: bool = False,
            show_error: Callable[[str], None],
            setText: Callable[[str], None] = None,
    ) -> None:
        if setText is None:
            setText = self.setText
        try:
            scanned_qr = scan_qr_from_screenshot()
        except UserFacingException as e:
            show_error(str(e))
            return
        data = scanned_qr.data
        try:
            if allow_multi:
                text = self.text()
                if data in text:
                    return
                if text and not text.endswith('\n'):
                    text += '\n'
                text += data
                text += '\n'
                setText(text)
            else:
                new_text = data
                setText(new_text)
        except Exception as e:
            show_error(_('Invalid payment identifier in QR') + ':\n' + repr(e))

    def input_file(
            self,
            *,
            config: 'SimpleConfig',
            show_error: Callable[[str], None],
            setText: Callable[[str], None] = None,
    ) -> None:
        if setText is None:
            setText = self.setText
        fileName = getOpenFileName(
            parent=None,
            title='select file',
            # trying to open non-text things like pdfs makes electrum freeze
            filter="Text files (*.txt *.csv);;All files (*)",
            config=config,
        )
        if not fileName:
            return
        try:
            try:
                with open(fileName, "r") as f:
                    data = f.read()
            except UnicodeError as e:
                with open(fileName, "rb") as f:
                    data = f.read()
                data = data.hex()
        except BaseException as e:
            show_error(_('Error opening file') + ':\n' + repr(e))
        else:
            try:
                setText(data)
            except Exception as e:
                show_error(_('Invalid payment identifier in file') + ':\n' + repr(e))

    def input_qr_from_file(
        self,
        *,
        allow_multi: bool = False,
        config: 'SimpleConfig',
        show_error: Callable[[str], None],
        setText: Callable[[str], None] = None,
    ):
        from .qrreader import scan_qr_from_image
        if setText is None:
            setText = self.setText

        file_name = getOpenFileName(
            parent=None,
            title=_("Select image file"),
            config=config,
            filter="Image files (*.png *.jpg *.jpeg *.bmp);;",
        )
        if not file_name:
            return
        image = QImage(file_name)
        if image.isNull():
            show_error(_("Failed to open image file."))
            return
        try:
            scan_result: Sequence[QrCodeResult] = scan_qr_from_image(image)
        except MissingQrDetectionLib as e:
            show_error(_("Unable to scan image.") + "\n" + repr(e))
            return
        if len(scan_result) < 1:
            show_error(_("No QR code was found in the image."))
            return
        if len(scan_result) > 1 and not allow_multi:
            show_error(_("More than one QR code was found in the image."))
            return

        if len(scan_result) > 1:
            result_text = "\n".join([r.data for r in scan_result])
        else:
            result_text = scan_result[0].data

        try:
            setText(result_text)
        except Exception as e:
            show_error(_("Couldn't set result") + ':\n' + repr(e))

    def input_paste_from_clipboard(
            self,
            *,
            setText: Callable[[str], None] = None,
    ) -> None:
        if setText is None:
            setText = self.setText
        app = QApplication.instance()
        setText(app.clipboard().text())


class OverlayControlMixin(GenericInputHandler):
    STYLE_SHEET_COMMON = '''
    QPushButton { border-width: 1px; padding: 0px; margin: 0px; }
    '''

    STYLE_SHEET_LIGHT = '''
    QPushButton { border: 1px solid transparent; }
    QPushButton:hover { border: 1px solid #3daee9; }
    '''

    def __init__(self, middle: bool = False):
        GenericInputHandler.__init__(self)
        assert isinstance(self, QWidget)
        assert isinstance(self, OverlayControlMixin)  # only here for type-hints in IDE
        self.middle = middle
        self.overlay_widget = QWidget(self)
        style_sheet = self.STYLE_SHEET_COMMON
        if not ColorScheme.dark_scheme:
            style_sheet = style_sheet + self.STYLE_SHEET_LIGHT
        self.overlay_widget.setStyleSheet(style_sheet)
        self.overlay_layout = QHBoxLayout(self.overlay_widget)
        self.overlay_layout.setContentsMargins(0, 0, 0, 0)
        self.overlay_layout.setSpacing(1)
        self._updateOverlayPos()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._updateOverlayPos()

    def _updateOverlayPos(self):
        frame_width = self.style().pixelMetric(QStyle.PixelMetric.PM_DefaultFrameWidth)
        overlay_size = self.overlay_widget.sizeHint()
        x = self.rect().right() - frame_width - overlay_size.width()
        y = self.rect().bottom() - overlay_size.height()
        middle = self.middle
        if hasattr(self, 'document'):
            # Keep the buttons centered if we have less than 2 lines in the editor
            line_spacing = QFontMetrics(self.document().defaultFont()).lineSpacing()
            if self.rect().height() < (line_spacing * 2):
                middle = True
        y = (y / 2) + frame_width if middle else y - frame_width
        if hasattr(self, 'verticalScrollBar') and self.verticalScrollBar().isVisible():
            scrollbar_width = self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
            x -= scrollbar_width
        self.overlay_widget.move(int(x), int(y))

    def addWidget(self, widget: QWidget):
        # The old code positioned the items the other way around, so we just insert at position 0 instead
        self.overlay_layout.insertWidget(0, widget)

    def addButton(self, icon: QIcon, on_click, tooltip: str) -> QPushButton:
        button = QPushButton(self.overlay_widget)
        button.setToolTip(tooltip)
        button.setIcon(icon)
        button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        button.clicked.connect(on_click)
        self.addWidget(button)
        return button

    def addCopyButton(self):
        def on_copy():
            app = QApplication.instance()
            app.clipboard().setText(self.text())
            QToolTip.showText(QCursor.pos(), _("Text copied to clipboard"), self)
        self.addButton(read_QIcon("copy.png"), on_copy, _("Copy to clipboard"))

    def addPasteButton(
            self,
            *,
            setText: Callable[[str], None] = None,
    ):
        input_paste_from_clipboard = partial(
            self.input_paste_from_clipboard,
            setText=setText,
        )
        self.addButton(read_QIcon("copy.png"), input_paste_from_clipboard, _("Paste from clipboard"))

    def add_qr_show_button(self, *, config: 'SimpleConfig', title: Optional[str] = None):
        if title is None:
            title = _("QR code")

        def qr_show():
            from .qrcodewidget import QRDialog
            try:
                s = str(self.text())
            except Exception:
                s = self.text()
            if not s:
                return
            QRDialog(
                data=s,
                parent=self,
                title=title,
                config=config,
            ).exec()

        self.addButton(get_icon_qrcode(), qr_show, _("Show as QR code"))
        # side-effect: we export this method:
        self.on_qr_show_btn = qr_show

    def add_qr_input_from_camera_button(
            self,
            *,
            config: 'SimpleConfig',
            allow_multi: bool = False,
            show_error: Callable[[str], None],
            setText: Callable[[str], None] = None,
    ):
        input_qr_from_camera = partial(
            self.input_qr_from_camera,
            config=config,
            allow_multi=allow_multi,
            show_error=show_error,
            setText=setText,
        )
        self.addButton(get_icon_camera(), input_qr_from_camera, _("Read QR code with camera"))
        # side-effect: we export these methods:
        self.on_qr_from_camera_input_btn = input_qr_from_camera

    def add_file_input_button(
            self,
            *,
            config: 'SimpleConfig',
            show_error: Callable[[str], None],
            setText: Callable[[str], None] = None,
    ) -> None:
        input_file = partial(
            self.input_file,
            config=config,
            show_error=show_error,
            setText=setText,
        )
        self.addButton(read_QIcon("file.png"), input_file, _("Read file"))

    def add_menu_button(
            self,
            *,
            options: Sequence[Tuple[Optional[Union[str, QIcon]], str, Callable[[], None]]],  # list of (icon, text, cb)
            icon: Optional[QIcon] = None,
            tooltip: Optional[str] = None,
    ):
        if icon is None:
            icon_name = "menu_vertical_white.png" if ColorScheme.dark_scheme else "menu_vertical.png"
            icon = read_QIcon(icon_name)
        if tooltip is None:
            tooltip = _("Other options")
        btn = self.addButton(icon, lambda: None, tooltip)
        menu = QMenu()
        for opt_icon, opt_text, opt_cb in options:
            if opt_icon is None:
                menu.addAction(opt_text, opt_cb)
            else:
                opt_icon = read_QIcon(opt_icon) if isinstance(opt_icon, str) else opt_icon
                menu.addAction(opt_icon, opt_text, opt_cb)
        btn.setMenu(menu)


class ButtonsLineEdit(OverlayControlMixin, QLineEdit):
    def __init__(self, text=None):
        QLineEdit.__init__(self, text)
        OverlayControlMixin.__init__(self, middle=True)


class ShowQRLineEdit(ButtonsLineEdit):
    """ read-only line with qr and copy buttons """
    def __init__(self, text: str, config, title=None):
        ButtonsLineEdit.__init__(self, text)
        self.setReadOnly(True)
        self.setFont(QFont(MONOSPACE_FONT))
        self.add_qr_show_button(config=config, title=title)
        self.addCopyButton()


class ButtonsTextEdit(OverlayControlMixin, QPlainTextEdit):
    def __init__(self, text=None):
        QPlainTextEdit.__init__(self, text)
        OverlayControlMixin.__init__(self)
        self.setText = self.setPlainText
        self.text = self.toPlainText


class PasswordLineEdit(QLineEdit):
    def __init__(self, *args, **kwargs):
        QLineEdit.__init__(self, *args, **kwargs)
        self.setEchoMode(QLineEdit.EchoMode.Password)

    def clear(self):
        # Try to actually overwrite the memory.
        # This is really just a best-effort thing...
        self.setText(len(self.text()) * " ")
        super().clear()


class ColorSchemeItem:
    def __init__(self, fg_color, bg_color):
        self.colors = (fg_color, bg_color)

    def _get_color(self, background):
        return self.colors[(int(background) + int(ColorScheme.dark_scheme)) % 2]

    def as_stylesheet(self, background=False):
        css_prefix = "background-" if background else ""
        color = self._get_color(background)
        return "QWidget {{ {}color:{}; }}".format(css_prefix, color)

    def as_color(self, background=False):
        color = self._get_color(background)
        return QColor(color)


class ColorScheme:
    dark_scheme = False

    GREEN = ColorSchemeItem("#117c11", "#8af296")
    YELLOW = ColorSchemeItem("#897b2a", "#ffff00")
    RED = ColorSchemeItem("#7c1111", "#f18c8c")
    BLUE = ColorSchemeItem("#123b7c", "#8cb3f2")
    LIGHTBLUE = ColorSchemeItem("black", "#d0f0ff")
    DEFAULT = ColorSchemeItem("black", "white")
    GRAY = ColorSchemeItem("gray", "gray")
    ORANGE = ColorSchemeItem("#ff9b45", "#ff9b45")

    @staticmethod
    def has_dark_background(widget):
        brightness = sum(widget.palette().color(QPalette.ColorRole.Window).getRgb()[0:3])
        return brightness < (255*3/2)

    @staticmethod
    def update_from_widget(widget, force_dark=False):
        ColorScheme.dark_scheme = bool(force_dark or ColorScheme.has_dark_background(widget))


class AcceptFileDragDrop:
    def __init__(self, file_type=""):
        assert isinstance(self, QWidget)
        self.setAcceptDrops(True)
        self.file_type = file_type

    def validateEvent(self, event):
        if not event.mimeData().hasUrls():
            event.ignore()
            return False
        for url in event.mimeData().urls():
            if not url.toLocalFile().endswith(self.file_type):
                event.ignore()
                return False
        event.accept()
        return True

    def dragEnterEvent(self, event):
        self.validateEvent(event)

    def dragMoveEvent(self, event):
        if self.validateEvent(event):
            event.setDropAction(Qt.DropAction.CopyAction)

    def dropEvent(self, event):
        if self.validateEvent(event):
            for url in event.mimeData().urls():
                self.onFileAdded(url.toLocalFile())

    def onFileAdded(self, fn):
        raise NotImplementedError()


def import_meta_gui(electrum_window: 'ElectrumWindow', title, importer, on_success):
    filter_ = "JSON (*.json);;All files (*)"
    filename = getOpenFileName(
        parent=electrum_window,
        title=_("Open {} file").format(title),
        filter=filter_,
        config=electrum_window.config,
    )
    if not filename:
        return
    try:
        importer(filename)
    except FileImportFailed as e:
        electrum_window.show_critical(str(e))
    else:
        electrum_window.show_message(_("Your {} were successfully imported").format(title))
        on_success()


def export_meta_gui(electrum_window: 'ElectrumWindow', title, exporter):
    filter_ = "JSON (*.json);;All files (*)"
    filename = getSaveFileName(
        parent=electrum_window,
        title=_("Select file to save your {}").format(title),
        filename='electrum_{}.json'.format(title),
        filter=filter_,
        config=electrum_window.config,
    )
    if not filename:
        return
    try:
        exporter(filename)
    except FileExportFailed as e:
        electrum_window.show_critical(str(e))
    else:
        electrum_window.show_message(_("Your {0} were exported to '{1}'")
                                     .format(title, str(filename)))


def getOpenFileName(*, parent, title, filter="", config: 'SimpleConfig') -> Optional[str]:
    """Custom wrapper for getOpenFileName that remembers the path selected by the user."""
    directory = config.IO_DIRECTORY
    fileName, __ = QFileDialog.getOpenFileName(parent, title, directory, filter)
    if fileName and directory != os.path.dirname(fileName):
        config.IO_DIRECTORY = os.path.dirname(fileName)
    return fileName


def getSaveFileName(
        *,
        parent,
        title,
        filename,
        filter="",
        default_extension: str = None,
        default_filter: str = None,
        config: 'SimpleConfig',
) -> Optional[str]:
    """Custom wrapper for getSaveFileName that remembers the path selected by the user."""
    directory = config.IO_DIRECTORY
    path = os.path.join(directory, filename)

    file_dialog = QFileDialog(parent, title, path, filter)
    file_dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    if default_extension:
        # note: on MacOS, the selected filter's first extension seems to have priority over this...
        file_dialog.setDefaultSuffix(default_extension)
    if default_filter:
        assert default_filter in filter, f"default_filter={default_filter!r} does not appear in filter={filter!r}"
        file_dialog.selectNameFilter(default_filter)
    if file_dialog.exec() != QDialog.DialogCode.Accepted:
        return None

    selected_path = file_dialog.selectedFiles()[0]
    if selected_path and directory != os.path.dirname(selected_path):
        config.IO_DIRECTORY = os.path.dirname(selected_path)
    return selected_path


def icon_path(icon_basename: str):
    return resource_path('gui', 'icons', icon_basename)


def internal_plugin_icon_path(plugin_name, icon_basename: str):
    return resource_path('plugins', plugin_name, icon_basename)


@lru_cache(maxsize=1000)
def read_QIcon(icon_basename: str) -> QIcon:
    return QIcon(icon_path(icon_basename))


def read_QPixmap_from_bytes(b: bytes) -> QPixmap:
    qp = QPixmap()
    qp.loadFromData(b)
    return qp


def read_QIcon_from_bytes(b: bytes) -> QIcon:
    qp = read_QPixmap_from_bytes(b)
    return QIcon(qp)


class IconLabel(QWidget):
    HorizontalSpacing = 2
    def __init__(self, *, text='', final_stretch=True, reverse=False, hide_if_empty=False):
        super(QWidget, self).__init__()
        self.hide_if_empty = hide_if_empty
        size = max(16, font_height())
        self.icon_size = QSize(size, size)
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)
        self.icon = QLabel()
        self.label = QLabel(text)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.icon if reverse else self.label)
        layout.addSpacing(self.HorizontalSpacing)
        layout.addWidget(self.label if reverse else self.icon)
        if final_stretch:
            layout.addStretch()
        self.setText(text)

    def setText(self, text):
        self.label.setText(text)
        if self.hide_if_empty:
            self.setVisible(bool(text))

    def setIcon(self, icon):
        self.icon.setPixmap(icon.pixmap(self.icon_size))
        self.icon.repaint()  # macOS hack for #6269


def char_width_in_lineedit() -> int:
    char_width = QFontMetrics(QLineEdit().font()).averageCharWidth()
    # 'averageCharWidth' seems to underestimate on Windows, hence 'max()'
    return max(9, char_width)


def font_height(widget: QWidget = None) -> int:
    if widget is None:
        widget = QLabel()
    return QFontMetrics(widget.font()).height()


def webopen(url: str):
    if sys.platform == 'linux' and os.environ.get('APPIMAGE'):
        # When on Linux webbrowser.open can fail in AppImage because it can't find the correct libdbus.
        # We just fork the process and unset LD_LIBRARY_PATH before opening the URL.
        # See #5425
        if os.fork() == 0:
            del os.environ['LD_LIBRARY_PATH']
            webbrowser.open(url)
            os._exit(0)
    else:
        webbrowser.open(url)


class FixedAspectRatioLayout(QLayout):
    def __init__(self, parent: QWidget = None, aspect_ratio: float = 1.0):
        super().__init__(parent)
        self.aspect_ratio = aspect_ratio
        self.items: List[QLayoutItem] = []

    def set_aspect_ratio(self, aspect_ratio: float = 1.0):
        self.aspect_ratio = aspect_ratio
        self.update()

    def addItem(self, item: QLayoutItem):
        self.items.append(item)

    def count(self) -> int:
        return len(self.items)

    def itemAt(self, index: int) -> QLayoutItem:
        if index >= len(self.items):
            return None
        return self.items[index]

    def takeAt(self, index: int) -> QLayoutItem:
        if index >= len(self.items):
            return None
        return self.items.pop(index)

    def _get_contents_margins_size(self) -> QSize:
        margins = self.contentsMargins()
        return QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def setGeometry(self, rect: QRect):
        super().setGeometry(rect)
        if not self.items:
            return

        contents = self.contentsRect()
        if contents.height() > 0:
            c_aratio = contents.width() / contents.height()
        else:
            c_aratio = 1
        s_aratio = self.aspect_ratio
        item_rect = QRect(QPoint(0, 0), QSize(
            contents.width() if c_aratio < s_aratio else int(contents.height() * s_aratio),
            contents.height() if c_aratio > s_aratio else int(contents.width() / s_aratio)
        ))

        content_margins = self.contentsMargins()
        free_space = contents.size() - item_rect.size()

        for item in self.items:
            if free_space.width() > 0 and not item.alignment() & Qt.AlignmentFlag.AlignLeft:
                if item.alignment() & Qt.AlignmentFlag.AlignRight:
                    item_rect.moveRight(contents.width() + content_margins.right())
                else:
                    item_rect.moveLeft(content_margins.left() + (free_space.width() // 2))
            else:
                item_rect.moveLeft(content_margins.left())

            if free_space.height() > 0 and not item.alignment() & Qt.AlignmentFlag.AlignTop:
                if item.alignment() & Qt.AlignmentFlag.AlignBottom:
                    item_rect.moveBottom(contents.height() + content_margins.bottom())
                else:
                    item_rect.moveTop(content_margins.top() + (free_space.height() // 2))
            else:
                item_rect.moveTop(content_margins.top())

            item.widget().setGeometry(item_rect)

    def sizeHint(self) -> QSize:
        result = QSize()
        for item in self.items:
            result = result.expandedTo(item.sizeHint())
        return self._get_contents_margins_size() + result

    def minimumSize(self) -> QSize:
        result = QSize()
        for item in self.items:
            result = result.expandedTo(item.minimumSize())
        return self._get_contents_margins_size() + result

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation.Horizontal | Qt.Orientation.Vertical


def QColorLerp(a: QColor, b: QColor, t: float):
    """
    Blends two QColors. t=0 returns a. t=1 returns b. t=0.5 returns evenly mixed.
    """
    t = max(min(t, 1.0), 0.0)
    i_t = 1.0 - t
    return QColor(
        int((a.red()   * i_t) + (b.red()   * t)),
        int((a.green() * i_t) + (b.green() * t)),
        int((a.blue()  * i_t) + (b.blue()  * t)),
        int((a.alpha() * i_t) + (b.alpha() * t)),
    )


class ImageGraphicsEffect(QObject):
    """
    Applies a QGraphicsEffect to a QImage
    """

    def __init__(self, parent: QObject, effect: QGraphicsEffect):
        super().__init__(parent)
        assert effect, 'effect must be set'
        self.effect = effect
        self.graphics_scene = QGraphicsScene()
        self.graphics_item = QGraphicsPixmapItem()
        self.graphics_item.setGraphicsEffect(effect)
        self.graphics_scene.addItem(self.graphics_item)

    def apply(self, image: QImage):
        assert image, 'image must be set'
        result = QImage(image.size(), QImage.Format.Format_ARGB32)
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        self.graphics_item.setPixmap(QPixmap.fromImage(image))
        self.graphics_scene.render(painter)
        self.graphics_item.setPixmap(QPixmap())
        return result


class QtEventListener(EventListener):
    qt_callback_signal = QtCore.pyqtSignal(tuple)

    def register_callbacks(self):
        self.qt_callback_signal.connect(self.on_qt_callback_signal)
        EventListener.register_callbacks(self)

    def unregister_callbacks(self):
        try:
            self.qt_callback_signal.disconnect()
        except (RuntimeError, TypeError):  # wrapped Qt object might be deleted
            # "TypeError: disconnect() failed between 'qt_callback_signal' and all its connections"
            pass
        EventListener.unregister_callbacks(self)

    def on_qt_callback_signal(self, args):
        func = args[0]
        return func(self, *args[1:])


# decorator for members of the QtEventListener class
def qt_event_listener(func):
    func = event_listener(func)

    @wraps(func)
    def decorator(self, *args):
        self.qt_callback_signal.emit( (func,) + args)
    return decorator


def insert_spaces(text: str, every_chars: int) -> str:
    '''Insert spaces at every Nth character to allow for WordWrap'''
    return ' '.join(text[i:i+every_chars] for i in range(0, len(text), every_chars))


def set_windows_os_screenshot_protection_drm_flag(window: QWidget) -> None:
    """
    sets the windows WDA_MONITOR flag on the window so windows prevents capturing
    screenshots and microsoft recall will not be able to record the window
    https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowdisplayaffinity
    """
    if sys.platform not in ('win32', 'windows'):
        return
    try:
        window_id = int(window.winId())
        WDA_MONITOR = 0x01
        ctypes.windll.user32.SetWindowDisplayAffinity(window_id, WDA_MONITOR)
    except Exception:
        _logger.exception(f"failed to set windows screenshot protection flag")

class _ABCQObjectMeta(type(QObject), ABCMeta): pass
class _ABCQWidgetMeta(type(QWidget), ABCMeta): pass
class AbstractQObject(QObject, ABC, metaclass=_ABCQObjectMeta): pass
class AbstractQWidget(QWidget, ABC, metaclass=_ABCQWidgetMeta): pass


if __name__ == "__main__":
    app = QApplication([])
    t = WaitingDialog(None, 'testing ...', lambda: [time.sleep(1)], lambda x: QMessageBox.information(None, 'done', "done"))
    t.start()
    app.exec()
