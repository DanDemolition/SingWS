import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

ColumnLayout {
    id: panel
    property var bridge
    property var transport
    spacing: 8

    Card {
        Layout.fillWidth: true
        Layout.fillHeight: true

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 14
            spacing: 10

            Text {
                text: "KARAOKE PREVIEW"
                color: Theme.text
                font.pixelSize: 15
                font.weight: Font.Bold
                Layout.fillWidth: true
                elide: Text.ElideRight
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 238
                radius: Theme.radius
                color: "#02040A"
                border.color: Theme.borderSoft
                clip: true

                Canvas {
                    anchors.fill: parent
                    onPaint: {
                        var ctx = getContext("2d")
                        ctx.clearRect(0, 0, width, height)
                        ctx.strokeStyle = "rgba(34, 0, 255, 0.38)"
                        ctx.lineWidth = 1
                        for (var i = 0; i < 28; i++) {
                            var x = (i * 53) % width
                            var y = (i * 37) % height
                            ctx.beginPath()
                            ctx.moveTo(x, y)
                            ctx.lineTo((x + 120) % width, (y + 70) % height)
                            ctx.stroke()
                        }
                    }
                }

                Column {
                    anchors.centerIn: parent
                    width: Math.max(0, parent.width - 28)
                    spacing: 14
                    Text {
                        text: "helped you get the vote"
                        color: Theme.lyricHot
                        font.pixelSize: 25
                        font.weight: Font.Bold
                        horizontalAlignment: Text.AlignHCenter
                        width: parent.width
                        elide: Text.ElideRight
                    }
                    Text {
                        text: "and I told you bout school"
                        color: Theme.lyricHot
                        font.pixelSize: 25
                        font.weight: Font.Bold
                        horizontalAlignment: Text.AlignHCenter
                        width: parent.width
                        elide: Text.ElideRight
                    }
                    Text {
                        text: "I wanna be elected"
                        color: Theme.text
                        font.pixelSize: 25
                        font.weight: Font.Bold
                        horizontalAlignment: Text.AlignHCenter
                        width: parent.width
                        elide: Text.ElideRight
                    }
                }
            }

            TransportControls {
                transport: panel.transport
                bridge: panel.bridge
                Layout.fillWidth: true
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 88
                radius: Theme.radius
                color: Qt.rgba(0.02, 0.05, 0.10, 0.82)
                border.color: Theme.borderSoft

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 6

                    StepCluster { label: "KEY"; value: "0"; leftText: "−"; rightText: "+" }
                    StepCluster { label: "TEMPO"; value: "100%"; leftText: "−"; rightText: "+"; wideValue: true }
                    GlobalToggle { checked: true }
                }
            }
        }
    }

    Card {
        Layout.fillWidth: true
        Layout.preferredHeight: 146

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 14
            spacing: 7

            RowLayout {
                Layout.fillWidth: true
                Text { text: "NOW PLAYING (BG MUSIC)"; color: Theme.textMuted; font.pixelSize: 12 }
                Item { Layout.fillWidth: true }
                Text { text: "AUTO FADE  ON"; color: Theme.success; font.pixelSize: 12; font.weight: Font.Bold }
            }
            Text {
                text: panel.bridge && panel.bridge.bgTitle ? panel.bridge.bgTitle : "No Background Music"
                color: Theme.text
                font.pixelSize: 17
                font.weight: Font.Bold
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
            Text {
                text: panel.bridge ? panel.bridge.bgArtist : ""
                color: Theme.textMuted
                font.pixelSize: 13
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
        }
    }

    component StepCluster: Rectangle {
        property string label: ""
        property string value: ""
        property string leftText: ""
        property string rightText: ""
        property bool wideValue: false
        Layout.fillWidth: true
        Layout.fillHeight: true
        radius: Theme.radius
        color: "#030812"
        border.color: Theme.borderSoft

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 8
            spacing: 6
            Text {
                text: label
                color: Theme.textMuted
                font.pixelSize: 12
                Layout.fillWidth: true
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 0
                MiniButton { text: leftText }
                Rectangle {
                    Layout.preferredWidth: wideValue ? 70 : 42
                    Layout.fillHeight: true
                    color: Qt.rgba(1, 1, 1, 0.035)
                    border.color: Theme.borderSoft
                    Text {
                        anchors.centerIn: parent
                        text: value
                        color: Theme.text
                        font.pixelSize: 14
                        font.weight: Font.Bold
                    }
                }
                MiniButton { text: rightText }
            }
        }
    }

    component GlobalToggle: Rectangle {
        id: globalToggle
        property bool checked: false
        Layout.preferredWidth: 92
        Layout.fillHeight: true
        radius: Theme.radius
        color: "#030812"
        border.color: Theme.borderSoft

        Column {
            anchors.centerIn: parent
            spacing: 7
            Text {
                text: "GLOBAL"
                color: Theme.textMuted
                font.pixelSize: 12
                horizontalAlignment: Text.AlignHCenter
                width: globalToggle.width
            }
            Rectangle {
                width: 28
                height: 28
                radius: 14
                color: "transparent"
                border.color: checked ? Theme.text : Theme.borderStrong
                anchors.horizontalCenter: parent.horizontalCenter
                Text {
                    anchors.centerIn: parent
                    text: checked ? "✓" : ""
                    color: Theme.text
                    font.pixelSize: 17
                    font.weight: Font.Bold
                }
            }
        }
    }

    component MiniButton: Rectangle {
        id: miniButton
        property string text: ""
        Layout.preferredWidth: 36
        Layout.fillHeight: true
        color: Qt.rgba(1, 1, 1, 0.026)
        border.color: Theme.borderSoft
        Text {
            anchors.centerIn: parent
            text: miniButton.text
            color: Theme.text
            font.pixelSize: 17
            font.weight: Font.DemiBold
        }
    }
}
