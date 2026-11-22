import QtQuick
import QtQuick.Controls
import "TableKeys.js" as TableKeys

Row {
    id: entry
    property string score: ""
    property string variant: ""
    property bool legacy: false
    property bool alternate: false
    property string label: ""
    signal scoreEdited(string value)
    signal variantEdited(string value)
    signal submitted(string score, string variant)
    signal clearRequested()

    function submit(fromVariant) {
        const amount = scoreInput.text.trim();
        const code = variantInput.text.trim();
        if (amount && !code) {
            variantInput.beginEditing(false);
        } else if (fromVariant && !amount && !score) {
            scoreInput.beginEditing(false);
        } else {
            scoreInput.editing = false;
            variantInput.editing = false;
            submitted(amount, code);
        }
    }

    component CellInput: OfficeTextField {
        id: field
        property bool editing: false
        height: entry.height
        leftPadding: 5
        rightPadding: 5
        topPadding: 0
        bottomPadding: 0
        readOnly: !editing
        selectByMouse: editing
        onActiveFocusChanged: if (!activeFocus) editing = false

        function beginEditing(replace) {
            forceActiveFocus();
            editing = true;
            if (replace)
                selectAll();
        }

        TapHandler {
            onDoubleTapped: field.beginEditing(false)
        }
        Keys.onPressed: event => {
            if (editing) {
                if (event.key === Qt.Key_Escape) {
                    editing = false;
                    deselect();
                    event.accepted = true;
                }
                return;
            }

            const shortcut = event.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier);
            const modified = shortcut || (event.modifiers & Qt.ShiftModifier);
            if (TableKeys.isClearKey(event)) {
                if (!event.isAutoRepeat)
                    entry.clearRequested();
                event.accepted = true;
            } else if (!modified && (event.key === Qt.Key_F2 || event.key === Qt.Key_Return || event.key === Qt.Key_Enter)) {
                beginEditing(false);
                event.accepted = true;
            } else if (!shortcut && event.text.trim()) {
                beginEditing(true);
            }
        }
        ToolTip.visible: hovered
        background: Rectangle {
            color: parent.activeFocus ? "#deebfa" : entry.alternate ? "#f5f8fc" : "white"
            border.color: parent.activeFocus ? "#1768b8" : "#e8edf4"
        }
    }

    CellInput {
        id: scoreInput
        width: Math.round(entry.width * 0.42)
        text: entry.score
        placeholderText: "Балл"
        inputMethodHints: Qt.ImhFormattedNumbersOnly
        Accessible.name: entry.label + ", балл"
        ToolTip.text: editing ? "Enter — сохранить балл и вариант" : "F2 или двойной клик — изменить. Delete или Backspace — очистить результат"
        onTextEdited: entry.scoreEdited(text)
        onAccepted: entry.submit(false)
    }

    CellInput {
        id: variantInput
        width: entry.width - scoreInput.width
        text: entry.variant
        placeholderText: entry.legacy ? "Не указан" : "Вариант"
        placeholderTextColor: entry.legacy ? "#965816" : "#62718a"
        Accessible.name: entry.label + ", вариант контрольной работы"
        ToolTip.text: editing ? "Вариант обязателен для сохранения балла" : "F2 или двойной клик — изменить. Delete или Backspace — очистить результат"
        onTextEdited: entry.variantEdited(text)
        onAccepted: entry.submit(true)
    }
}
