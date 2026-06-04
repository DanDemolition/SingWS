import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Card {
    id: panel
    property var bridge
    property var rotationQueueModel
    property var transport

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            Text {
                text: "ROTATION"
                color: Theme.text
                font.pixelSize: 15
                font.weight: Font.Bold
            }
            Item { Layout.fillWidth: true }
            Text {
                text: "Singers: " + (panel.bridge ? panel.bridge.singerCount : 0)
                    + "  •  Mode: "
                    + (panel.bridge ? panel.bridge.mode : "Rotation")
                color: Theme.textMuted
                font.pixelSize: 13
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 86
            radius: Theme.radius
            color: "#050B16"
            border.color: Theme.borderSoft

            RowLayout {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 10

                component SingerSlot: ColumnLayout {
                    property string label: ""
                    property string name: ""
                    property bool emphasised: false
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 6
                    Text {
                        text: label
                        color: emphasised ? Theme.accentBright : Theme.textSoft
                        font.pixelSize: 11
                        font.weight: Font.Bold
                        Layout.fillWidth: true
                    }
                    Text {
                        text: name && name.length > 0 ? name : "—"
                        color: emphasised ? Theme.text : (name && name.length > 0 ? Theme.text : Theme.textSoft)
                        font.pixelSize: 20
                        font.weight: emphasised ? Font.Bold : Font.DemiBold
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                }

                SingerSlot {
                    label: "LAST"
                    name: panel.bridge ? panel.bridge.lastSinger : ""
                }
                Rectangle {
                    Layout.preferredWidth: 1
                    Layout.fillHeight: true
                    Layout.topMargin: 4
                    Layout.bottomMargin: 4
                    color: Theme.borderSoft
                }
                SingerSlot {
                    label: "CURRENT"
                    name: panel.bridge ? panel.bridge.nowSinger : ""
                    emphasised: true
                }
                Rectangle {
                    Layout.preferredWidth: 1
                    Layout.fillHeight: true
                    Layout.topMargin: 4
                    Layout.bottomMargin: 4
                    color: Theme.borderSoft
                }
                SingerSlot {
                    label: "NEXT"
                    name: panel.bridge ? panel.bridge.nextSinger : ""
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 224
            radius: Theme.radius
            color: "#050B16"
            border.color: Qt.rgba(0.61, 0.17, 1.0, 0.72)

            RowLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 18

                Rectangle {
                    width: 98
                    height: 98
                    radius: 49
                    color: "#080D18"
                    border.color: Qt.rgba(0.61, 0.17, 1.0, 0.76)
                    Text {
                        anchors.centerIn: parent
                        text: panel.bridge && panel.bridge.nowSinger ? panel.bridge.nowSinger : "-"
                        color: Theme.text
                        font.pixelSize: 24
                        font.weight: Font.Bold
                        elide: Text.ElideRight
                        width: parent.width - 18
                        horizontalAlignment: Text.AlignHCenter
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Text { text: "NOW SINGING"; color: Theme.text; font.pixelSize: 13; font.weight: Font.Bold }
                    Text {
                        text: panel.bridge && panel.bridge.nowSinger ? panel.bridge.nowSinger : "No singer is active"
                        color: Theme.text
                        font.pixelSize: 26
                        font.weight: Font.Bold
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                    Text {
                        text: panel.bridge && panel.bridge.nowArtist ? panel.bridge.nowArtist : "Queue a song to start the rotation."
                        color: Theme.textMuted
                        font.pixelSize: 17
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                    Text {
                        text: panel.bridge ? panel.bridge.nowTitle : ""
                        color: Theme.text
                        font.pixelSize: 16
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Layout.topMargin: 12
                        Text {
                            text: panel.transport ? panel.transport.elapsed : "00:00"
                            color: Theme.textMuted
                            font.pixelSize: 13
                            Layout.preferredWidth: 46
                        }
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 8
                            radius: 4
                            color: "#101B2C"
                            Rectangle {
                                width: parent.width * (panel.transport ? panel.transport.progress : 0)
                                height: parent.height
                                radius: 4
                                color: Theme.accent
                            }
                        }
                        Text {
                            text: panel.transport ? panel.transport.remaining : "-00:00"
                            color: Theme.textMuted
                            font.pixelSize: 13
                            horizontalAlignment: Text.AlignRight
                            Layout.preferredWidth: 54
                        }
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Text {
                text: "UP NEXT"
                color: Theme.text
                font.pixelSize: 15
                font.weight: Font.Bold
            }
            Item { Layout.fillWidth: true }
        }

        ListView {
            id: rotationList
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 2
            model: panel.rotationQueueModel || []

            delegate: Rectangle {
                id: rotationDelegate
                required property int index
                required property int number
                required property string singer
                required property string song
                required property string eta
                required property string status
                required property bool isCurrent

                width: rotationList.width
                height: 45
                radius: 6
                color: rotationDelegate.isCurrent
                    ? Qt.rgba(0.42, 0.16, 1.0, 0.56)
                    : (rotationDelegate.index % 2 === 1 ? Theme.rowAlt : "transparent")
                border.color: rotationDelegate.isCurrent ? Qt.rgba(0.63, 0.13, 1.0, 0.56) : Qt.rgba(1, 1, 1, 0.035)

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 14
                    anchors.rightMargin: 14
                    spacing: 12

                    Text { text: rotationDelegate.number; color: Theme.text; font.pixelSize: 15; Layout.preferredWidth: 28 }
                    Text { text: rotationDelegate.singer; color: Theme.text; font.pixelSize: 15; font.weight: Font.DemiBold; Layout.preferredWidth: 120; elide: Text.ElideRight }
                    Text { text: rotationDelegate.song; color: Theme.textMuted; font.pixelSize: 14; Layout.fillWidth: true; elide: Text.ElideRight }
                    Rectangle {
                        visible: rotationDelegate.status === "DUET"
                        Layout.preferredWidth: visible ? 44 : 0
                        Layout.preferredHeight: 22
                        radius: 5
                        color: Theme.accent
                        border.color: Theme.accentBright
                        Text {
                            anchors.centerIn: parent
                            text: "DUET"
                            color: "white"
                            font.pixelSize: 11
                            font.weight: Font.Bold
                        }
                    }
                    Text { text: rotationDelegate.eta; color: Theme.textMuted; font.pixelSize: 13; Layout.preferredWidth: 70; horizontalAlignment: Text.AlignRight }
                }
            }
        }
    }
}
