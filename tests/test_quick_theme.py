"""Quick controls retain the established readable light operator theme."""

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QUrl
from PySide6.QtGui import QColor, QPalette
from PySide6.QtQml import QQmlComponent, QQmlEngine, QQmlExpression
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from attendance_lab.presentation.quick.theme import apply_light_theme
from attendance_lab.presentation.theme import apply_light_theme as apply_widget_theme


@pytest.fixture
def light_quick_app():
    app = QApplication.instance() or QApplication([])
    previous = app.palette(), app.font(), app.styleHints().colorScheme()
    previous_style = app.style().objectName()
    previous_dialogs = app.testAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
    palette = QPalette()
    for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base, QPalette.ColorRole.Button):
        palette.setColor(role, QColor("#121212"))
    app.setPalette(palette)
    app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    apply_light_theme(app)
    yield app
    app.setPalette(previous[0])
    app.setFont(previous[1])
    app.styleHints().setColorScheme(previous[2])
    app.setStyle(previous_style)
    app.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, previous_dialogs)


def test_quick_palette_and_font_match_the_original_operator_theme(light_quick_app):
    app = light_quick_app
    quick_palette, quick_font = app.palette(), app.font()
    assert QQuickStyle.name() == "Fusion"
    assert app.styleHints().colorScheme() != Qt.ColorScheme.Dark
    apply_widget_theme(app)
    for group in (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
        QPalette.ColorGroup.Disabled,
    ):
        for role in QPalette.ColorRole:
            if role not in (QPalette.ColorRole.NoRole, QPalette.ColorRole.NColorRoles):
                assert quick_palette.color(group, role) == app.palette().color(group, role)
    assert quick_font.family() == app.font().family() == "Arial"
    assert quick_font.pointSize() == app.font().pointSize() == 12


def test_quick_inputs_tabs_and_popups_use_light_controls(light_quick_app):
    app = light_quick_app
    engine = QQmlEngine()
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    component = QQmlComponent(engine)
    component.setData(
        b"""
        import QtQuick
        import QtQuick.Controls
        ApplicationWindow {
            width: 980; height: 720; visible: true
            Column {
                TextField { objectName: "field"; text: "Readable input" }
                Button { objectName: "button"; text: "Save" }
                TabButton { objectName: "tab"; text: "Reports" }
                ComboBox { objectName: "choice"; model: ["First", "Second"] }
            }
            Dialog { objectName: "dialog"; title: "Report"; standardButtons: Dialog.Save | Dialog.Cancel }
        }
    """,
        QUrl(),
    )
    assert not component.errors(), [error.toString() for error in component.errors()]
    root = component.create()
    assert root is not None
    try:
        app.processEvents()
        from PySide6.QtCore import QObject

        def evaluate(obj, source):
            expression = QQmlExpression(QQmlEngine.contextForObject(obj), obj, source, engine)
            result = expression.evaluate()
            assert not expression.hasError(), expression.error().toString()
            return result[0] if isinstance(result, tuple) else result

        for name in ("field", "button", "tab", "choice", "dialog"):
            control = root.findChild(QObject, name)
            assert control is not None
            assert evaluate(control, "palette.window") == QColor("#f3f6fa")
            assert evaluate(control, "palette.text") == QColor("#17243c")
            assert control.property("font").family() == "Arial"
            assert control.property("font").pointSize() == 12
        for name in ("field", "button", "tab", "choice"):
            control = root.findChild(QObject, name)
            assert 20 <= control.property("height") <= 36
        button = root.findChild(QObject, "button")
        evaluate(button, "enabled = false")
        assert evaluate(button, "palette.buttonText") == QColor("#7b8798")
        choice = root.findChild(QObject, "choice")
        evaluate(choice, "popup.open()")
        app.processEvents()
        assert evaluate(choice, "popup.palette.base") == QColor("#ffffff")
        evaluate(choice, "popup.close()")
        assert not warnings, warnings
    finally:
        root.close()
        root.deleteLater()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_office_controls_restore_padding_colors_and_native_editing(light_quick_app):
    from importlib.resources import files

    from PySide6.QtCore import QObject, QPoint
    from PySide6.QtTest import QTest

    app = light_quick_app
    engine = QQmlEngine()
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    component = QQmlComponent(engine)
    component.setData(
        b"""
        import QtQuick
        import QtQuick.Controls
        ApplicationWindow {
            width: 650; height: 420; visible: true
            Column {
                spacing: 8
                OfficeButton { objectName: "officeButton"; text: "Save" }
                OfficeButton { objectName: "primaryButton"; text: "Apply"; primary: true }
                OfficeButton { objectName: "disabledButton"; text: "Unavailable"; enabled: false }
                TabBar {
                    OfficeTabButton { objectName: "activeTab"; text: "Reports" }
                    OfficeTabButton { objectName: "inactiveTab"; text: "Works" }
                }
                OfficeTextField { objectName: "officeField"; width: 250; text: "Original" }
                OfficeComboBox {
                    objectName: "officeChoice"; width: 250
                    textRole: "label"; valueRole: "id"
                    model: [{id: 3, label: "First"}, {id: 8, label: "Second"}]
                }
                OfficeComboBox { objectName: "editableChoice"; width: 250; editable: true; model: ["Old", "New"] }
                OfficeButton {
                    objectName: "rubricOverride"; text: "Neutral"; implicitHeight: 44
                    contentItem: Text { text: parent.text; color: "white" }
                    background: Rectangle { color: "#245a91" }
                }
            }
        }
        """,
        QUrl.fromLocalFile(
            str(files("attendance_lab.presentation.quick").joinpath("style-test.qml"))
        ),
    )
    assert not component.errors(), [error.toString() for error in component.errors()]
    root = component.create()
    assert root is not None, [error.toString() for error in component.errors()]
    try:
        app.processEvents()
        root.requestActivate()
        QTest.mouseMove(root, QPoint(600, 400))
        app.processEvents()

        def control(name):
            item = root.findChild(QObject, name)
            assert item is not None
            return item

        def evaluate(item, source):
            expression = QQmlExpression(QQmlEngine.contextForObject(item), item, source, engine)
            result = expression.evaluate()
            assert not expression.hasError(), expression.error().toString()
            return result[0] if isinstance(result, tuple) else result

        button = control("officeButton")
        assert button.property("height") >= 38
        assert button.property("leftPadding") == button.property("rightPadding") == 13
        assert button.property("topPadding") == button.property("bottomPadding") == 8
        assert evaluate(button, "background.radius") == 5
        assert evaluate(button, "background.border.color") == QColor("#c8d3e2")
        assert evaluate(button, "background.color") == QColor("white")
        QTest.mouseMove(root, QPoint(20, 16))
        assert evaluate(button, "background.color") == QColor("#e8f0fa")
        assert evaluate(button, "background.border.color") == QColor("#90abc9")
        QTest.mousePress(root, Qt.MouseButton.LeftButton, pos=QPoint(20, 16))
        assert evaluate(button, "background.color") == QColor("#d8e7f8")
        QTest.mouseRelease(root, Qt.MouseButton.LeftButton, pos=QPoint(20, 16))
        QTest.mouseMove(root, QPoint(600, 400))
        assert evaluate(control("primaryButton"), "background.color") == QColor("#1768b8")
        assert evaluate(control("primaryButton"), "palette.buttonText") == QColor("white")
        assert evaluate(control("disabledButton"), "background.color") == QColor("#f1f3f6")
        assert evaluate(control("disabledButton"), "palette.buttonText") == QColor("#9ba6b4")
        for name in ("activeTab", "inactiveTab"):
            tab = control(name)
            assert tab.property("leftPadding") == tab.property("rightPadding") == 17
            assert tab.property("topPadding") == tab.property("bottomPadding") == 9
        assert evaluate(control("activeTab"), "background.color") == QColor("white")
        assert evaluate(control("activeTab"), "palette.buttonText") == QColor("#1455a0")
        assert evaluate(control("inactiveTab"), "background.color") == QColor("#e7ecf4")
        assert evaluate(control("inactiveTab"), "palette.buttonText") == QColor("#384b67")
        for name in ("officeButton", "officeField", "officeChoice"):
            item = control(name)
            assert item.property("font").family() == "Arial"
            assert item.property("font").pixelSize() == 16
        field = control("officeField")
        assert field.property("height") >= 36
        assert field.property("padding") == 6
        assert evaluate(field, "background.radius") == 4
        assert evaluate(field, "background.border.color") == QColor("#cbd5e2")
        evaluate(field, "forceActiveFocus(); selectAll()")
        app.clipboard().setText("Clipboard")
        QTest.keyClick(root, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        assert field.property("text") == "Clipboard"
        choice = control("officeChoice")
        assert choice.property("height") >= 36
        assert choice.property("currentValue") == 3
        evaluate(choice, "forceActiveFocus()")
        QTest.keyClick(root, Qt.Key.Key_Down)
        assert choice.property("currentValue") == 8
        evaluate(choice, "popup.open()")
        app.processEvents()
        assert evaluate(choice, "popup.background.color") == QColor("white")
        assert evaluate(choice, "popup.contentItem.itemAtIndex(0).height") == 36
        assert evaluate(choice, "popup.contentItem.itemAtIndex(0).text") == "First"
        evaluate(choice, "popup.close()")
        editable = control("editableChoice")
        evaluate(
            editable, "forceActiveFocus(); contentItem.forceActiveFocus(); contentItem.selectAll()"
        )
        for key in (
            Qt.Key.Key_M,
            Qt.Key.Key_A,
            Qt.Key.Key_N,
            Qt.Key.Key_U,
            Qt.Key.Key_A,
            Qt.Key.Key_L,
        ):
            QTest.keyClick(root, key)
        assert editable.property("editText") == "manual"
        overridden = control("rubricOverride")
        assert overridden.property("height") == 44
        assert evaluate(overridden, "background.color") == QColor("#245a91")
        # The generated controls actually paint their light backgrounds, not a native gradient.
        image = root.grabWindow()
        assert not image.isNull()
        sample = image.pixelColor(int(button.property("width")) - 10, 22)
        assert sample == QColor("white")
        assert not warnings, warnings
    finally:
        root.close()
        root.deleteLater()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
