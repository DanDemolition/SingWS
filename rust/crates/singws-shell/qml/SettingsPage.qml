import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import com.singws.shell

Item {
    id: page
    function arg(name) {
        var a = Qt.application.arguments
        for (var i = 0; i < a.length - 1; i++) if (a[i] === name) return a[i + 1]
        return ""
    }

    SettingsPanel { id: panel }
    property var rows: JSON.parse(panel.rowsJson)
    Connections { target: panel; function onRowsJsonChanged() { page.rows = JSON.parse(panel.rowsJson) } }

    Component.onCompleted: { if (arg("--settings-query") !== "") field.text = arg("--settings-query"); panel.refresh(field.text) }
    Timer { id: debounce; interval: 120; onTriggered: panel.refresh(field.text) }
    Timer { interval: 5000; running: page.visible; repeat: true; onTriggered: panel.refresh(field.text) }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10

        Label { text: "SETTINGS (read-only)"; color: "#e8e6f5"; font.pixelSize: 15; font.weight: Font.DemiBold }

        TextField {
            id: field
            Layout.fillWidth: true
            placeholderText: "Search settings by name"
            color: "#e8e6f5"
            placeholderTextColor: "#7a77a0"
            font.pixelSize: 14
            selectByMouse: true
            background: Rectangle { color: "#12102a"; radius: 8; border.color: field.activeFocus ? "#7c3aed" : "#2a2650"; border.width: 1 }
            onTextChanged: debounce.restart()
            Accessible.name: "Search settings"
        }

        Label { Layout.fillWidth: true; text: panel.status; color: "#9a97c0"; font.pixelSize: 12; wrapMode: Text.Wrap }

        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: page.rows
            spacing: 2
            ScrollBar.vertical: ScrollBar {}
            delegate: Rectangle {
                width: list.width
                height: 32
                radius: 6
                color: "#1a1838"
                Accessible.role: Accessible.ListItem
                Accessible.name: modelData.key + ", " + modelData.value
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 10
                    anchors.rightMargin: 10
                    Label { text: modelData.key; color: "#c9c6e8"; font.pixelSize: 13; elide: Text.ElideRight; Layout.preferredWidth: 320 }
                    Label { text: modelData.value; color: modelData.hidden ? "#f0b44c" : "#ffffff"; font.pixelSize: 13; font.italic: modelData.hidden; elide: Text.ElideRight; Layout.fillWidth: true }
                }
            }
        }
    }
}
