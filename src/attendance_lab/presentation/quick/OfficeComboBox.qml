pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Window
import QtQuick.Controls as Controls

Controls.ComboBox {
    id: control
    readonly property real interfaceScale: Window.window?.interfaceScale ?? 1
    readonly property real popupAvailableHeight: Math.min(Math.round(360 * interfaceScale), Window.window ? Window.window.height - 12 : 480)
    font.family: "Arial"
    font.pixelSize: Math.round(16 * interfaceScale)
    spacing: padding
    padding: Math.round(6 * interfaceScale)
    leftPadding: mirrored ? Math.round(28 * interfaceScale) : padding
    rightPadding: mirrored ? padding : Math.round(28 * interfaceScale)
    implicitWidth: Math.max(120, implicitContentWidth + leftPadding + rightPadding + 2)
    implicitHeight: Math.max(Math.round(36 * interfaceScale), implicitContentHeight + topPadding + bottomPadding + 2)
    indicator: Text {
        x: control.mirrored ? control.padding : control.width - width - control.padding
        y: (control.height - height) / 2
        width: Math.round(15 * control.interfaceScale)
        text: "▾"
        font: control.font
        color: control.enabled ? "#62718a" : "#7b8798"
        horizontalAlignment: Text.AlignHCenter
    }
    contentItem: Controls.TextField {
        padding: 0
        implicitWidth: contentWidth
        implicitHeight: contentHeight
        text: control.editable ? control.editText : control.displayText
        font: control.font
        enabled: control.editable
        autoScroll: control.editable
        readOnly: control.down
        inputMethodHints: control.inputMethodHints
        validator: control.validator
        selectByMouse: control.selectTextByMouse
        color: control.enabled ? "#17243c" : "#7b8798"
        selectionColor: "#1768b8"
        selectedTextColor: "white"
        verticalAlignment: TextInput.AlignVCenter
        background: null
    }
    background: Rectangle {
        radius: 4
        color: "white"
        border.width: 1
        border.color: control.activeFocus ? "#1768b8" : "#cbd5e2"
    }
    delegate: Controls.ItemDelegate {
        id: option
        required property var model
        required property int index
        width: ListView.view.width
        height: Math.round(36 * control.interfaceScale)
        padding: control.padding
        text: control.textRole ? model[control.textRole] : model.modelData
        font: control.font
        highlighted: control.highlightedIndex === index
        hoverEnabled: true
        contentItem: Text {
            text: option.text
            font: option.font
            textFormat: Text.PlainText
            color: option.highlighted ? "#142f53" : "#17243c"
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            color: option.highlighted ? "#deebfa" : "white"
        }
    }
    popup: Controls.Popup {
        width: control.width
        height: Math.min(contentItem.implicitHeight + topPadding + bottomPadding, control.popupAvailableHeight)
        topMargin: 6
        bottomMargin: 6
        padding: 1
        font: control.font
        palette: control.palette
        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: control.delegateModel
            currentIndex: control.highlightedIndex
            highlightRangeMode: ListView.ApplyRange
            highlightMoveDuration: 0
            Controls.ScrollBar.vertical: Controls.ScrollBar {}
        }
        background: Rectangle {
            color: "white"
            border.color: "#cbd5e2"
        }
    }
}
