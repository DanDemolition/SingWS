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

    MusicPanel { id: panel }
    property var rows: JSON.parse(panel.rowsJson)
    Connections { target: panel; function onRowsJsonChanged() { page.rows = JSON.parse(panel.rowsJson) } }

    Component.onCompleted: { if (arg("--music-query") !== "") field.text = arg("--music-query"); panel.refresh(field.text) }
    Timer { id: debounce; interval: 120; onTriggered: panel.refresh(field.text) }
    // the app rewrites the playlist when it changes; look again every few seconds while this tab is on screen
    Timer { interval: 4000; running: page.visible; repeat: true; onTriggered: panel.refresh(field.text) }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            Label { text: "BACKGROUND MUSIC"; color: "#e8e6f5"; font.pixelSize: 15; font.weight: Font.DemiBold }
            Item { Layout.fillWidth: true }
            Label { text: panel.countLabel; color: "#a78bfa"; font.pixelSize: 12; font.weight: Font.DemiBold }
        }
        Label { Layout.fillWidth: true; text: panel.info; color: "#9a97c0"; font.pixelSize: 12; wrapMode: Text.Wrap; visible: text !== "" }

        TextField {
            id: field
            Layout.fillWidth: true
            placeholderText: "Search the playlist"
            color: "#e8e6f5"
            placeholderTextColor: "#7a77a0"
            font.pixelSize: 14
            selectByMouse: true
            background: Rectangle { color: "#12102a"; radius: 8; border.color: field.activeFocus ? "#7c3aed" : "#2a2650"; border.width: 1 }
            onTextChanged: debounce.restart()
            Accessible.name: "Search the background music playlist"
        }

        Label { text: panel.status; color: "#f0b44c"; font.pixelSize: 12; visible: text !== "" }
        Label { text: "Shows every saved track. The app hides tracks it has already played; the shell cannot see which track is playing."; color: "#7a77a0"; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.Wrap }

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
                height: 34
                radius: 6
                color: "#1a1838"
                Accessible.role: Accessible.ListItem
                Accessible.name: modelData.filename
                Label { anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10; verticalAlignment: Text.AlignVCenter; text: modelData.filename; color: "#ffffff"; font.pixelSize: 13; elide: Text.ElideRight }
                ToolTip.visible: hover.hovered && modelData.path !== ""
                ToolTip.text: modelData.path
                HoverHandler { id: hover }
            }
        }
    }
}
