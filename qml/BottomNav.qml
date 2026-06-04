import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Rectangle {
    id: nav
    property var bridge
    radius: Theme.radius
    color: Qt.rgba(0.03, 0.07, 0.12, 0.92)
    border.color: Theme.borderSoft

    RowLayout {
        anchors.fill: parent
        anchors.margins: 5
        spacing: 5

        NavButton { label: "⌂  Show"; shortLabel: "⌂ Show"; iconLabel: "⌂"; view: "show"; onClicked: if (nav.bridge) nav.bridge.setActiveView(view) }
        NavButton { label: "♪  BGM"; shortLabel: "♪ BGM"; iconLabel: "♪"; view: "bgm"; onClicked: if (nav.bridge) nav.bridge.setActiveView(view) }
        NavButton { label: "♙  Singer History"; shortLabel: "♙ History"; iconLabel: "♙"; view: "history"; onClicked: if (nav.bridge) nav.bridge.setActiveView(view) }
        NavButton { label: "◎  Network"; shortLabel: "◎ Net"; iconLabel: "◎"; view: "network"; onClicked: if (nav.bridge) nav.bridge.openNetwork() }
        NavButton { label: "↻  Show Rotation"; shortLabel: "↻ Rotation"; iconLabel: "↻"; view: "rotation"; onClicked: if (nav.bridge) nav.bridge.showRotation() }
        NavButton { label: "▣  Show Karaoke"; shortLabel: "▣ Karaoke"; iconLabel: "▣"; view: "karaoke"; onClicked: if (nav.bridge) nav.bridge.showKaraoke() }
        NavButton { label: "⚙  Settings"; shortLabel: "⚙ Settings"; iconLabel: "⚙"; view: "settings"; onClicked: if (nav.bridge) nav.bridge.openSettings() }
    }

    component NavButton: Button {
        id: navButton
        property string label: ""
        property string shortLabel: label
        property string iconLabel: label
        property string view: ""
        readonly property bool active: nav.bridge && nav.bridge.activeView === navButton.view
        Layout.fillWidth: true
        Layout.fillHeight: true
        text: nav.width < 760 ? iconLabel : (nav.width < 1080 ? shortLabel : label)
        hoverEnabled: true

        background: Rectangle {
            radius: Theme.radius
            color: navButton.active
                ? Qt.rgba(0.42, 0.16, 1.0, 0.34)
                : (navButton.hovered ? Qt.rgba(0.42, 0.16, 1.0, 0.10) : "transparent")
            border.color: navButton.active ? Qt.rgba(0.61, 0.17, 1.0, 0.36) : "transparent"
        }

        contentItem: Text {
            text: navButton.text
            color: navButton.active ? Theme.text : Theme.textMuted
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            font.pixelSize: 13
            font.weight: Font.DemiBold
            elide: Text.ElideRight
        }
    }
}
