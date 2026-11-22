.pragma library

function isClearKey(event) {
    const modifiers = Qt.ShiftModifier | Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier;
    return !(event.modifiers & modifiers) && (event.key === Qt.Key_Delete || event.key === Qt.Key_Backspace);
}
