import QtQuick

Item {
    id: shortcuts
    required property var zoom

    Shortcut {
        sequences: ["Ctrl++", "Ctrl+=", "Ctrl+Shift+="]
        enabled: !!shortcuts.zoom
        onActivated: shortcuts.zoom.zoomIn()
    }
    Shortcut {
        sequence: "Ctrl+-"
        enabled: !!shortcuts.zoom
        onActivated: shortcuts.zoom.zoomOut()
    }
    Shortcut {
        sequence: "Ctrl+0"
        enabled: !!shortcuts.zoom
        onActivated: shortcuts.zoom.reset()
    }
}
