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

    LibraryPanel { id: panel }

    property var rows: []
    Connections {
        target: panel
        function onResultsJsonChanged() { page.rows = JSON.parse(panel.resultsJson) }
    }

    Component.onCompleted: { if (arg("--query") !== "") field.text = arg("--query") }
    Timer { id: debounce; interval: 120; onTriggered: panel.search(field.text) }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10

        Label { text: "SONG LIBRARY"; color: "#e8e6f5"; font.pixelSize: 15; font.weight: Font.DemiBold }

        TextField {
            id: field
            Layout.fillWidth: true
            placeholderText: "Search artist, title or disc id"
            color: "#e8e6f5"
            placeholderTextColor: "#7a77a0"
            font.pixelSize: 14
            selectByMouse: true
            background: Rectangle { color: "#12102a"; radius: 8; border.color: field.activeFocus ? "#7c3aed" : "#2a2650"; border.width: 1 }
            onTextChanged: debounce.restart()
            Accessible.name: "Search songs"
        }

        Label { text: panel.status; color: "#9a97c0"; font.pixelSize: 12 }

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
                height: 48
                radius: 6
                color: ListView.isCurrentItem ? "#4b2bb0" : "#1a1838"
                Accessible.role: Accessible.ListItem
                Accessible.name: modelData.artist + ", " + modelData.title + ", " + modelData.duration
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 10
                    anchors.rightMargin: 10
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0
                        Label { text: modelData.artist; color: "#ffffff"; font.pixelSize: 14; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
                        Label { text: modelData.title; color: "#c9c6e8"; font.pixelSize: 12; elide: Text.ElideRight; Layout.fillWidth: true }
                    }
                    Label { text: modelData.kind.toUpperCase() + "  " + modelData.duration; color: "#9a97c0"; font.pixelSize: 11 }
                }
                MouseArea { anchors.fill: parent; onClicked: list.currentIndex = index }
            }
        }
    }
}
