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

    WaitlistPanel { id: panel }
    property var sections: JSON.parse(panel.sectionsJson)
    Connections { target: panel; function onSectionsJsonChanged() { page.sections = JSON.parse(panel.sectionsJson) } }

    Component.onCompleted: panel.refresh(removedBox.checked)
    // the app rewrites its files as the night goes; look again every few seconds while this tab is on screen
    Timer { interval: 4000; running: page.visible; repeat: true; onTriggered: panel.refresh(removedBox.checked) }
    // test hook: --dump-waitlist FILE writes what is on screen
    Timer { interval: 1500; running: page.arg("--dump-waitlist") !== ""; onTriggered: panel.dumpView(page.arg("--dump-waitlist")) }

    function statusColor(kind) {
        return kind === "needs_review" ? "#f5c84b" : kind === "removed" ? "#8a87b0" : kind === "pending_acceptance" ? "#5ac8fa" : "#a78bfa"
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            Label { text: "WAITING FOR ADD"; color: "#e8e6f5"; font.pixelSize: 15; font.weight: Font.DemiBold }
            Item { Layout.fillWidth: true }
            CheckBox {
                id: removedBox
                text: "Show removed tonight"
                checked: page.arg("--waitlist-removed") !== ""
                onToggled: panel.refresh(checked)
                contentItem: Label { text: removedBox.text; color: "#c9c6e8"; font.pixelSize: 12; leftPadding: removedBox.indicator.width + 6; verticalAlignment: Text.AlignVCenter }
            }
            Label { text: panel.countLabel; color: "#a78bfa"; font.pixelSize: 12; font.weight: Font.DemiBold }
        }
        Label { Layout.fillWidth: true; text: panel.status; color: "#9a97c0"; font.pixelSize: 12; wrapMode: Text.Wrap; visible: text !== "" }

        // the audience ticker, as text (the scrolling stays with the app's ticker)
        Rectangle {
            Layout.fillWidth: true
            height: tickerCol.implicitHeight + 16
            radius: 10
            color: "#000000"
            border.color: "#2a2650"
            ColumnLayout {
                id: tickerCol
                anchors.fill: parent
                anchors.margins: 8
                spacing: 2
                Label { text: "ON THE AUDIENCE TICKER"; color: "#6d6a99"; font.pixelSize: 10; font.letterSpacing: 1.2 }
                Label {
                    Layout.fillWidth: true
                    text: panel.tickerText !== "" ? panel.tickerText : "(nothing to show: nobody is queued)"
                    color: panel.tickerText !== "" ? "#ffd479" : "#6d6a99"
                    font.pixelSize: 14
                    elide: Text.ElideRight
                    Accessible.name: "Audience ticker: " + text
                }
            }
        }

        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            ColumnLayout {
                width: page.width - 14
                spacing: 6
                Repeater {
                    model: page.sections
                    delegate: ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4
                        property var section: modelData
                        Label { text: section.title.toUpperCase() + "  (" + section.rows.length + ")"; color: "#a78bfa"; font.pixelSize: 12; font.weight: Font.DemiBold; Layout.topMargin: 6 }
                        Label { visible: section.helper !== null && section.helper !== undefined; text: section.helper || ""; color: "#8a87b0"; font.pixelSize: 11; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        Repeater {
                            model: section.rows
                            delegate: Rectangle {
                                Layout.fillWidth: true
                                implicitHeight: rowCol.implicitHeight + 16
                                radius: 8
                                color: "#1a1838"
                                Accessible.role: Accessible.ListItem
                                Accessible.name: modelData.singer + ", " + modelData.status_label + ", " + modelData.title + " by " + modelData.artist
                                ColumnLayout {
                                    id: rowCol
                                    anchors.fill: parent
                                    anchors.margins: 8
                                    spacing: 2
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Label { text: modelData.singer; color: "#ffffff"; font.pixelSize: 14; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
                                        Label {
                                            text: modelData.status_label + (modelData.first_time_singer ? " · First turn" : "") + (modelData.queue_position ? " · Queue #" + modelData.queue_position : "")
                                            color: page.statusColor(modelData.status_kind); font.pixelSize: 11; font.weight: Font.DemiBold
                                        }
                                    }
                                    Label { text: modelData.title + "  —  " + modelData.artist; color: "#c9c6e8"; font.pixelSize: 13; elide: Text.ElideRight; Layout.fillWidth: true }
                                    Label { text: modelData.version + "   ·   " + modelData.duration + "   ·   requested " + modelData.request_time + "   ·   server " + modelData.server_received_time; color: "#8a87b0"; font.pixelSize: 11; elide: Text.ElideRight; Layout.fillWidth: true }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
