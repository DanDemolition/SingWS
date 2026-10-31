import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import com.singws.shell

Item {
    id: page

    StatusPanel { id: panel }
    property var view: JSON.parse(panel.viewJson)
    Connections { target: panel; function onViewJsonChanged() { page.view = JSON.parse(panel.viewJson) } }
    readonly property var hero: view.hero ? view.hero : null
    readonly property bool live: hero !== null && hero.kicker === "NOW SINGING"

    Component.onCompleted: panel.refresh()
    Timer { interval: 3000; running: page.visible; repeat: true; onTriggered: panel.refresh() }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10

        Label { text: "NOW PLAYING (read-only: the Show tab's hero card, as of the app's last snapshot)"; color: "#e8e6f5"; font.pixelSize: 15; font.weight: Font.DemiBold }
        Label { Layout.fillWidth: true; text: panel.status; color: page.view.stale ? "#f5c84b" : "#9a97c0"; font.pixelSize: 12; wrapMode: Text.Wrap; Accessible.name: "Status snapshot: " + panel.status }
        Label {
            visible: page.hero === null && panel.status !== ""
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: "#c9c6e8"
            font.pixelSize: 13
            text: "The snapshot has no now-playing card yet (the app writes it when the card refreshes)."
        }

        Rectangle {
            Layout.fillWidth: true
            visible: page.hero !== null
            height: heroCol.implicitHeight + 40
            radius: 14
            color: "#12102a"
            border.color: page.live ? "#7c3aed" : "#2a2650"
            border.width: page.live ? 2 : 1
            Accessible.role: Accessible.StaticText
            Accessible.name: page.hero ? (page.hero.kicker + ": " + page.hero.singer + ". " + page.hero.subtitle + ". " + page.hero.meta + ". Time left " + page.hero.remaining) : ""
            ColumnLayout {
                id: heroCol
                anchors.fill: parent
                anchors.margins: 20
                spacing: 6
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: page.hero ? page.hero.kicker : ""; color: "#a78bfa"; font.pixelSize: 12; font.weight: Font.DemiBold; font.letterSpacing: 2 }
                    Item { Layout.fillWidth: true }
                    Rectangle {
                        visible: page.hero !== null && page.hero.format !== ""
                        height: 22
                        width: fmtText.implicitWidth + 18
                        radius: 11
                        color: "#1a1838"
                        border.color: "#4b2bb0"
                        Label { id: fmtText; anchors.centerIn: parent; text: page.hero ? page.hero.format : ""; color: "#c9c6e8"; font.pixelSize: 11; font.weight: Font.DemiBold }
                    }
                }
                Label { Layout.fillWidth: true; text: page.hero ? page.hero.singer : ""; color: "#ffffff"; font.pixelSize: 40; font.weight: Font.ExtraBold; elide: Text.ElideRight }
                Label { Layout.fillWidth: true; text: page.hero ? page.hero.subtitle : ""; color: "#c9c6e8"; font.pixelSize: 18; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight }
                Label { Layout.fillWidth: true; visible: page.hero !== null && page.hero.meta !== ""; text: page.hero ? page.hero.meta : ""; color: "#9a97c0"; font.pixelSize: 14; elide: Text.ElideRight }
                Label { visible: page.hero !== null && page.hero.remaining !== ""; text: page.hero ? page.hero.remaining : ""; color: "#34e08a"; font.pixelSize: 28; font.weight: Font.DemiBold; Accessible.name: "Time left " + text }
            }
        }
        Item { Layout.fillHeight: true }
    }
}
