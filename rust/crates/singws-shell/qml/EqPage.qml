import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import com.singws.shell

Item {
    id: page

    EqPanel { id: panel }
    property var doc: JSON.parse(panel.eqJson)
    Connections { target: panel; function onEqJsonChanged() { page.doc = JSON.parse(panel.eqJson) } }
    Component.onCompleted: panel.refresh()
    Timer { interval: 4000; running: page.visible; repeat: true; onTriggered: panel.refresh() }

    function hz(f) { return f >= 1000 ? (f / 1000) + "k" : String(f) }

    ColumnLayout {
        anchors.fill: parent
        spacing: 12

        Label { text: "EQUALISERS (read-only)"; color: "#e8e6f5"; font.pixelSize: 15; font.weight: Font.DemiBold }
        Label { Layout.fillWidth: true; visible: panel.status !== ""; text: panel.status; color: "#f0b44c"; font.pixelSize: 12; wrapMode: Text.Wrap }

        Repeater {
            model: page.doc.bands ? [{key: "bgm", title: "Background music"}, {key: "karaoke", title: "Karaoke"}] : []
            delegate: Rectangle {
                id: card
                property var eq: page.doc[modelData.key]
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: 10
                color: "#12102a"
                border.color: "#2a2650"
                Accessible.name: modelData.title + " equaliser, " + (eq.enabled ? "on" : "off")
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 6
                    RowLayout {
                        Layout.fillWidth: true
                        Label { text: modelData.title; color: "#ffffff"; font.pixelSize: 14; font.weight: Font.DemiBold }
                        Label {
                            text: card.eq.enabled ? (card.eq.auto_enabled ? "ON (the gains are not flat, so the app turned it on)" : "ON") : (card.eq.flat ? "OFF (flat)" : "OFF")
                            color: card.eq.enabled ? "#7ee787" : "#9a97c0"
                            font.pixelSize: 12
                        }
                        Item { Layout.fillWidth: true }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        spacing: 4
                        Repeater {
                            model: 10
                            delegate: ColumnLayout {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                spacing: 2
                                property real g: card.eq.gains[index]
                                Label { Layout.alignment: Qt.AlignHCenter; text: (g > 0 ? "+" : "") + (Math.round(g * 10) / 10); color: g === 0 ? "#6a678f" : "#e8e6f5"; font.pixelSize: 11 }
                                Item {
                                    Layout.fillWidth: true
                                    Layout.fillHeight: true
                                    // zero line in the middle; the bar grows up for a boost and down for a cut (12 dB = half the height)
                                    Rectangle { anchors.verticalCenter: parent.verticalCenter; width: parent.width; height: 1; color: "#3a3670" }
                                    Rectangle {
                                        width: Math.min(parent.width * 0.5, 26)
                                        anchors.horizontalCenter: parent.horizontalCenter
                                        radius: 3
                                        color: card.eq.enabled ? (g >= 0 ? "#7c3aed" : "#c084fc") : "#3a3670"
                                        property real span: Math.min(Math.abs(g), 12) / 12 * (parent.height / 2)
                                        height: Math.max(span, g === 0 ? 0 : 2)
                                        y: g >= 0 ? parent.height / 2 - height : parent.height / 2
                                    }
                                }
                                Label { Layout.alignment: Qt.AlignHCenter; text: page.hz(page.doc.bands[index]); color: "#9a97c0"; font.pixelSize: 11 }
                            }
                        }
                    }
                }
            }
        }
    }
}
