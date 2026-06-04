import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: shell
    property var bridge
    property var songModel
    property var rotationQueueModel
    property var bgmListModel
    property var transport

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 9
        spacing: 8

        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 46

            RowLayout {
                anchors.fill: parent
                spacing: 12

                Text {
                    text: "<b>Sing</b><font color='" + Theme.accentBright + "'><b>WS</b></font>"
                    textFormat: Text.RichText
                    color: Theme.text
                    font.pixelSize: 27
                    Layout.alignment: Qt.AlignVCenter
                }

                Item { Layout.fillWidth: true }

                Rectangle {
                    radius: Theme.radius
                    color: Qt.rgba(0.02, 0.04, 0.08, 0.76)
                    border.color: Theme.borderSoft
                    Layout.preferredWidth: 244
                    Layout.preferredHeight: 40

                    Row {
                        anchors.centerIn: parent
                        spacing: 10
                        Rectangle {
                            width: 10
                            height: 10
                            radius: 5
                            color: Theme.success
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        Text {
                            text: (shell.bridge ? shell.bridge.requestsStatus : "Requests Closed")
                                + "  •  "
                                + (shell.bridge ? shell.bridge.connectionStatus : "Offline")
                            color: Theme.text
                            font.pixelSize: 14
                            font.weight: Font.DemiBold
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                }

                Button {
                    text: "QR"
                    Layout.preferredWidth: 58
                    Layout.preferredHeight: 40
                    background: Rectangle {
                        radius: Theme.radius
                        color: "#070E1A"
                        border.color: Theme.accent
                    }
                    contentItem: Text {
                        text: parent.text
                        color: Theme.text
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        font.pixelSize: 14
                        font.weight: Font.Bold
                    }
                }
            }
        }

        Item {
            id: viewArea
            Layout.fillWidth: true
            Layout.fillHeight: true

            readonly property string activeView: shell.bridge ? shell.bridge.activeView : "show"

            RowLayout {
                anchors.fill: parent
                spacing: 8
                visible: viewArea.activeView !== "bgm"

                SongLibraryPanel {
                    bridge: shell.bridge
                    songModel: shell.songModel
                    Layout.preferredWidth: Math.max(330, shell.width * 0.285)
                    Layout.fillHeight: true
                }

                RotationPanel {
                    bridge: shell.bridge
                    rotationQueueModel: shell.rotationQueueModel
                    transport: shell.transport
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                }

                KaraokePreviewPanel {
                    bridge: shell.bridge
                    transport: shell.transport
                    Layout.preferredWidth: Math.max(360, shell.width * 0.315)
                    Layout.fillHeight: true
                }
            }

            BGMPanel {
                anchors.fill: parent
                visible: viewArea.activeView === "bgm"
                bridge: shell.bridge
                bgmModel: shell.bgmListModel
            }
        }

        BottomNav {
            bridge: shell.bridge
            Layout.fillWidth: true
            Layout.preferredHeight: 58
        }
    }
}
