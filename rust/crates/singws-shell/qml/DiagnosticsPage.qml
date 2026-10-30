import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import com.singws.shell

Item {
    id: page

    StatusPanel { id: panel }
    property var view: JSON.parse(panel.viewJson)
    Connections { target: panel; function onViewJsonChanged() { page.view = JSON.parse(panel.viewJson) } }
    readonly property bool has: view.checks !== undefined

    Component.onCompleted: panel.refresh()
    Timer { interval: 3000; running: page.visible; repeat: true; onTriggered: panel.refresh() }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10

        Label { text: "PRE-SHOW DIAGNOSTICS (read-only: the app's own checks, as of its last snapshot)"; color: "#e8e6f5"; font.pixelSize: 15; font.weight: Font.DemiBold }
        Label { Layout.fillWidth: true; text: panel.status; color: page.has && page.view.stale ? "#f5c84b" : "#9a97c0"; font.pixelSize: 12; wrapMode: Text.Wrap; Accessible.name: "Status snapshot: " + panel.status }
        Label { visible: page.has; text: page.has ? page.view.reportTitle : ""; color: page.has && page.view.allPassed ? "#34e08a" : "#f5c84b"; font.pixelSize: 18; font.weight: Font.DemiBold; Accessible.name: "Result: " + text }

        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: page.has ? page.view.checks : []
            spacing: 4
            ScrollBar.vertical: ScrollBar {}
            delegate: Rectangle {
                width: list.width
                height: 44
                radius: 6
                color: "#1a1838"
                Accessible.role: Accessible.ListItem
                Accessible.name: (modelData.ok ? "PASS  " : "CHECK  ") + modelData.label + ": " + modelData.detail
                Rectangle { id: badge; anchors.verticalCenter: parent.verticalCenter; anchors.left: parent.left; anchors.leftMargin: 10; width: 62; height: 24; radius: 12; color: modelData.ok ? "#12301f" : "#33290f"
                    Label { anchors.centerIn: parent; text: modelData.ok ? "PASS" : "CHECK"; color: modelData.ok ? "#34e08a" : "#f5c84b"; font.pixelSize: 12; font.weight: Font.Bold } }
                Label { anchors.verticalCenter: parent.verticalCenter; anchors.left: badge.right; anchors.leftMargin: 12; width: 190; text: modelData.label; color: "#ffffff"; font.pixelSize: 14; elide: Text.ElideRight }
                Label { anchors.verticalCenter: parent.verticalCenter; anchors.left: badge.right; anchors.leftMargin: 214; anchors.right: parent.right; anchors.rightMargin: 10; text: modelData.detail; color: "#c9c6e8"; font.pixelSize: 13; elide: Text.ElideMiddle }
            }
        }
    }
}
