import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

ColumnLayout {
    id: controls
    property var bridge
    property var transport
    spacing: 10

    RowLayout {
        Layout.fillWidth: true
        spacing: 10

        Text {
            text: controls.transport ? controls.transport.elapsed : "00:00"
            color: Theme.textMuted
            font.pixelSize: 13
            Layout.preferredWidth: 48
        }

        Slider {
            id: seek
            Layout.fillWidth: true
            from: 0
            to: 1
            value: controls.transport ? controls.transport.progress : 0

            background: Rectangle {
                x: seek.leftPadding
                y: seek.topPadding + seek.availableHeight / 2 - height / 2
                width: seek.availableWidth
                height: 8
                radius: 4
                color: "#101B2C"
                Rectangle {
                    width: seek.visualPosition * parent.width
                    height: parent.height
                    radius: 4
                    color: Theme.accent
                }
            }

            handle: Rectangle {
                x: seek.leftPadding + seek.visualPosition * (seek.availableWidth - width)
                y: seek.topPadding + seek.availableHeight / 2 - height / 2
                width: 16
                height: 16
                radius: 8
                color: Theme.accent
                border.color: Theme.accentBright
            }
        }

        Text {
            text: controls.transport ? controls.transport.remaining : "-00:00"
            color: Theme.textMuted
            font.pixelSize: 13
            horizontalAlignment: Text.AlignRight
            Layout.preferredWidth: 54
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: 10

        TransportButton { text: "▶"; primary: true; onClicked: if (controls.bridge) controls.bridge.play() }
        TransportButton { text: "↺"; onClicked: if (controls.bridge) controls.bridge.restart() }
        TransportButton { text: "■"; onClicked: if (controls.bridge) controls.bridge.stop() }
        TransportButton { text: "+"; onClicked: if (controls.bridge) controls.bridge.addFile() }
        Item { Layout.fillWidth: true }
        TransportButton { text: "◔" }
    }

    component TransportButton: Button {
        id: transportButton
        property bool primary: false
        Layout.preferredWidth: 56
        Layout.preferredHeight: 48

        background: Rectangle {
            radius: Theme.radius
            color: transportButton.primary ? Theme.accent : "#071120"
            border.color: transportButton.primary ? Theme.accentBright : Theme.border
        }

        contentItem: Text {
            text: transportButton.text
            color: Theme.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            font.pixelSize: 20
            font.weight: Font.Bold
        }
    }
}
