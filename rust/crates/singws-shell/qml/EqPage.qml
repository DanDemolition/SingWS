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

    EqPanel { id: panel }
    property var control: null   // the shared ControlPanel (Main.qml); null when the page is used on its own
    property var cview: control ? JSON.parse(control.viewJson) : ({enabled: false})
    // editing needs the app: connected and online. Otherwise the bars are a read-only picture of settings.json
    property bool live: cview.enabled === true
    Connections { target: page.control; function onViewJsonChanged() { page.cview = JSON.parse(page.control.viewJson) } }
    property var doc: JSON.parse(panel.eqJson)
    Connections { target: panel; function onEqJsonChanged() { page.doc = JSON.parse(panel.eqJson) } }
    Component.onCompleted: panel.refresh()
    Timer { interval: 4000; running: page.visible; repeat: true; onTriggered: panel.refresh() }
    // the app saves its settings as part of the command; look again shortly after one so the page shows what the app now holds
    Timer { id: soon; interval: 900; onTriggered: panel.refresh() }
    // test hook: --dump-eq FILE writes what the tab shows once everything has settled
    Timer { interval: 8000; running: page.arg("--dump-eq") !== ""; onTriggered: panel.dumpEq(page.arg("--dump-eq")) }

    function hz(f) { return f >= 1000 ? (f / 1000) + "k" : String(f) }
    function send(target, gains, enabled, label) {
        if (!control || !live) return
        var g = []
        for (var i = 0; i < gains.length; i++) g.push(Math.round(Math.max(-12, Math.min(12, gains[i])) * 10) / 10)
        control.send(label, "eq.set", JSON.stringify({target: target, gains: g, enabled: enabled}), false)
        soon.restart()
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            Label { text: page.live ? "EQUALISERS" : "EQUALISERS (read-only)"; color: "#e8e6f5"; font.pixelSize: 15; font.weight: Font.DemiBold }
            Label { visible: !page.live; text: "Connect on the Controls tab to change them."; color: "#7a77a0"; font.pixelSize: 12 }
            Item { Layout.fillWidth: true }
        }
        Label { Layout.fillWidth: true; visible: panel.status !== ""; text: panel.status; color: "#f0b44c"; font.pixelSize: 12; wrapMode: Text.Wrap }

        Repeater {
            model: page.doc.bands ? [{key: "bgm", title: "Background music"}, {key: "karaoke", title: "Karaoke"}] : []
            delegate: Rectangle {
                id: card
                property var eq: page.doc[modelData.key]
                property var work: eq.gains.slice()          // what the sliders show while one is being dragged
                onEqChanged: work = eq.gains.slice()
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
                        spacing: 10
                        Label { text: modelData.title; color: "#ffffff"; font.pixelSize: 14; font.weight: Font.DemiBold }
                        Label {
                            text: card.eq.enabled ? (card.eq.auto_enabled ? "ON (the gains are not flat, so the app turned it on)" : "ON") : (card.eq.flat ? "OFF (flat)" : "OFF")
                            color: card.eq.enabled ? "#7ee787" : "#9a97c0"
                            font.pixelSize: 12
                        }
                        Item { Layout.fillWidth: true }
                        ComboBox {
                            id: presetBox
                            visible: page.live
                            implicitWidth: 150
                            implicitHeight: 28
                            font.pixelSize: 12
                            model: ["Preset…"].concat(page.doc.presets.map(function (p) { return p.name }))
                            onActivated: function (i) {
                                if (i < 1) return
                                var p = page.doc.presets[i - 1]
                                card.work = p.gains.slice()
                                page.send(modelData.key, p.gains, true, modelData.title + " preset " + p.name)
                                currentIndex = 0
                            }
                            Accessible.name: modelData.title + " preset"
                        }
                        Button {
                            visible: page.live
                            text: card.eq.enabled ? "Turn off" : "Turn on"
                            implicitHeight: 28
                            font.pixelSize: 12
                            contentItem: Label { text: parent.text; color: "#ffffff"; font: parent.font; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                            background: Rectangle { radius: 5; color: parent.down ? "#3a2290" : "#4b2bb0" }
                            onClicked: page.send(modelData.key, card.eq.gains, !card.eq.enabled, modelData.title + (card.eq.enabled ? " off" : " on"))
                            Accessible.name: card.eq.enabled ? "Turn the " + modelData.title + " equaliser off" : "Turn the " + modelData.title + " equaliser on"
                        }
                        Button {
                            visible: page.live
                            text: "Reset"
                            implicitHeight: 28
                            font.pixelSize: 12
                            contentItem: Label { text: parent.text; color: "#ffffff"; font: parent.font; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                            background: Rectangle { radius: 5; color: parent.down ? "#2a2650" : "#1f1c45" }
                            onClicked: { card.work = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]; page.send(modelData.key, card.work, card.eq.enabled, modelData.title + " reset") }
                            Accessible.name: "Reset the " + modelData.title + " equaliser to flat"
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        spacing: 4
                        Repeater {
                            model: 10
                            delegate: ColumnLayout {
                                Layout.fillWidth: true
                                Layout.preferredWidth: 1
                                Layout.fillHeight: true
                                spacing: 2
                                property real g: card.work[index]
                                Label { Layout.alignment: Qt.AlignHCenter; text: (g > 0 ? "+" : "") + (Math.round(g * 10) / 10); color: g === 0 ? "#6a678f" : "#e8e6f5"; font.pixelSize: 11 }
                                Item {
                                    Layout.fillWidth: true
                                    Layout.fillHeight: true
                                    Slider {
                                        id: sl
                                        anchors.horizontalCenter: parent.horizontalCenter
                                        height: parent.height
                                        orientation: Qt.Vertical
                                        from: -12; to: 12; stepSize: 0.1
                                        enabled: page.live
                                        opacity: card.eq.enabled ? 1.0 : 0.55
                                        Binding { target: sl; property: "value"; value: card.work[index]; when: !sl.pressed }
                                        onMoved: { var w = card.work.slice(); w[index] = value; card.work = w }
                                        onPressedChanged: if (!pressed) page.send(modelData.key, card.work, true, modelData.title + " " + page.hz(page.doc.bands[index]) + " Hz")
                                        Accessible.name: modelData.title + " " + page.hz(page.doc.bands[index]) + " hertz gain"
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
