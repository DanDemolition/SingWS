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

    property var panel: null   // the one shared ControlPanel (Main.qml)
    property var view: JSON.parse(panel.viewJson)
    property string confirmSinger: ""
    property bool scriptDone: false
    Connections {
        target: panel
        function onViewJsonChanged() { page.view = JSON.parse(panel.viewJson) }
    }

    function act(label, command, args) {
        panel.send(label, command, JSON.stringify(args || {}), command.indexOf("queue.") === 0)
    }

    component Btn: Button {
        id: b
        property color tone: "#2a2650"
        implicitHeight: 34
        font.pixelSize: 13
        enabled: page.view.enabled
        contentItem: Label { text: b.text; color: b.enabled ? "#ffffff" : "#6a678f"; font: b.font; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
        background: Rectangle { radius: 6; color: !b.enabled ? "#15132b" : (b.down ? Qt.darker(b.tone, 1.3) : b.tone) }
    }

    // A slider that follows the app's value until the user grabs it; it sends ONE command when released (or on a keyboard change), so dragging never floods the relay.
    component Dial: RowLayout {
        id: d
        property string caption: ""
        property real shown: 0
        property real maximum: 1
        property string valueText: ""
        signal commit(real value)
        spacing: 8
        Label { text: d.caption; color: "#9a97c0"; font.pixelSize: 12; Layout.preferredWidth: 92 }
        Slider {
            id: sl
            Layout.fillWidth: true
            from: 0
            to: d.maximum
            enabled: page.view.enabled && d.maximum > 0
            Accessible.name: d.caption
            Binding { target: sl; property: "value"; value: Math.min(d.shown, d.maximum); when: !sl.pressed }
            onPressedChanged: if (!pressed) d.commit(value)
            onMoved: if (!pressed) d.commit(value)
        }
        Label { text: d.valueText; color: "#c9c6e8"; font.pixelSize: 12; Layout.preferredWidth: 84; horizontalAlignment: Text.AlignRight }
    }

    Component.onCompleted: {
        urlField.text = arg("--control-url") !== "" ? arg("--control-url") : panel.savedUrl
        userField.text = arg("--control-user") !== "" ? arg("--control-user") : panel.savedUser
        if (arg("--control-pin") !== "" && urlField.text !== "" && userField.text !== "")
            panel.connectTo(urlField.text, userField.text, arg("--control-pin"))
    }

    // Test hook: `--do '[{"label":..,"command":..,"args":{..}}, {"wait":ms}]'` runs once the app is reachable, one step per tick;
    // `--dump-view FILE` then writes the final snapshot. Each step goes through act(), the same function the buttons call.
    property var script: arg("--do") !== "" ? JSON.parse(arg("--do")) : []
    property int step: 0
    Timer {
        interval: 450
        running: page.script.length > 0 && !page.scriptDone
        repeat: true
        onTriggered: {
            if (!page.view.enabled) return
            if (page.step >= page.script.length) {
                page.scriptDone = true
                dumpTimer.start()
                return
            }
            var s = page.script[page.step++]
            if (s.command && s.sig) panel.sendWithSig(s.label || s.command, s.command, JSON.stringify(s.args || {}), s.sig)
            else if (s.command && s.nosig) panel.send(s.label || s.command, s.command, JSON.stringify(s.args || {}), false)   // how the Library tab's Add sends: no list fingerprint
            else if (s.command) page.act(s.label || s.command, s.command, s.args)
        }
    }

    // let the last acknowledgement and queue update arrive before the snapshot is taken
    Timer { id: dumpTimer; interval: 1500; onTriggered: if (page.arg("--dump-view") !== "") panel.dumpView(page.arg("--dump-view")) }
    Timer { interval: 3000; running: page.script.length === 0 && page.arg("--dump-view") !== ""; onTriggered: panel.dumpView(page.arg("--dump-view")) }
    Timer { id: bannerTimer; interval: 9000; onTriggered: banner.visible = false }
    property int shownSeq: 0
    onViewChanged: {
        if (view.result && view.result.seq !== shownSeq) { shownSeq = view.result.seq; banner.visible = true; bannerTimer.restart() }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10

        Label { text: "LIVE CONTROLS"; color: "#e8e6f5"; font.pixelSize: 15; font.weight: Font.DemiBold }

        // ---- connection
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            TextField {
                id: urlField
                Layout.fillWidth: true
                Layout.preferredWidth: 3
                placeholderText: "Server, e.g. https://wskar.com"
                color: "#e8e6f5"
                placeholderTextColor: "#7a77a0"
                selectByMouse: true
                enabled: !page.view.connected
                Accessible.name: "Server address"
                background: Rectangle { color: "#12102a"; radius: 8; border.color: urlField.activeFocus ? "#7c3aed" : "#2a2650" }
            }
            TextField {
                id: userField
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                placeholderText: "Tenant"
                color: "#e8e6f5"
                placeholderTextColor: "#7a77a0"
                selectByMouse: true
                enabled: !page.view.connected
                Accessible.name: "Tenant name"
                background: Rectangle { color: "#12102a"; radius: 8; border.color: userField.activeFocus ? "#7c3aed" : "#2a2650" }
            }
            TextField {
                id: pinField
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                placeholderText: "Host PIN"
                echoMode: TextInput.Password
                color: "#e8e6f5"
                placeholderTextColor: "#7a77a0"
                selectByMouse: true
                enabled: !page.view.connected
                Accessible.name: "Host PIN"
                background: Rectangle { color: "#12102a"; radius: 8; border.color: pinField.activeFocus ? "#7c3aed" : "#2a2650" }
                onAccepted: connectButton.clicked()
            }
            Button {
                id: connectButton
                text: page.view.connected || page.view.logged_in ? "Disconnect" : "Connect"
                implicitHeight: 34
                contentItem: Label { text: connectButton.text; color: "#ffffff"; font.pixelSize: 13; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { radius: 6; color: "#4b2bb0" }
                onClicked: {
                    if (page.view.connected || page.view.logged_in) { panel.endSession(); pinField.text = "" }
                    else if (urlField.text.trim() !== "" && userField.text.trim() !== "" && pinField.text !== "") { panel.connectTo(urlField.text, userField.text, pinField.text); pinField.text = "" }
                }
            }
        }
        Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            font.pixelSize: 12
            text: page.view.login_error !== "" ? "Login refused: " + page.view.login_error
                  : (page.view.enabled ? "Connected. App " + page.view.app_version + " is online" : (page.view.connected ? "Connected to the relay; waiting for the app" : page.view.status))
            color: page.view.login_error !== "" ? "#ff8a80" : (page.view.enabled ? "#7ee787" : "#c9c6e8")
        }

        // ---- last result
        Rectangle {
            id: banner
            visible: false
            Layout.fillWidth: true
            height: bannerText.implicitHeight + 14
            radius: 8
            color: !page.view.result ? "transparent" : (page.view.result.kind === "ok" ? "#12301f" : (page.view.result.kind === "warn" ? "#33290f" : "#3a1414"))
            border.color: !page.view.result ? "transparent" : (page.view.result.kind === "ok" ? "#2f7a4b" : (page.view.result.kind === "warn" ? "#8a6d1c" : "#a33"))
            Label { id: bannerText; anchors.fill: parent; anchors.margins: 7; wrapMode: Text.Wrap; color: "#ffffff"; font.pixelSize: 13; text: page.view.result ? page.view.result.text : "" }
            Accessible.role: Accessible.AlertMessage
            Accessible.name: page.view.result ? page.view.result.text : ""
        }

        // ---- now playing + transport
        Rectangle {
            Layout.fillWidth: true
            height: nowCol.implicitHeight + 20
            radius: 10
            color: "#12102a"
            border.color: "#2a2650"
            ColumnLayout {
                id: nowCol
                anchors.fill: parent
                anchors.margins: 10
                spacing: 6
                Label {
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                    font.pixelSize: 15
                    font.weight: Font.DemiBold
                    color: "#ffffff"
                    text: page.view.rotation.current.singer !== "" ? page.view.rotation.current.singer + " — " + (page.view.playback.title || page.view.rotation.current.title) : "Nobody is singing"
                }
                Label {
                    color: "#9a97c0"; font.pixelSize: 12
                    function clock(s) { var m = Math.floor(s / 60), r = Math.floor(s % 60); return m + ":" + (r < 10 ? "0" : "") + r }
                    text: page.view.playback.duration > 0 ? clock(page.view.playback.position) + " / " + clock(page.view.playback.duration) + "   Key " + (page.view.playback.key > 0 ? "+" : "") + page.view.playback.key + "   Tempo " + Math.round(page.view.playback.tempo * 100) + "%" : "No song loaded"
                }
                RowLayout {
                    spacing: 6
                    Btn { text: "Restart"; onClicked: page.act("Restart", "restart") }
                    Btn { text: page.view.playback.playing ? "Pause" : "Play"; tone: "#4b2bb0"; onClicked: page.act("Play/pause", "play_pause") }
                    Btn { text: "Next"; onClicked: page.act("Next", "skip") }
                    Btn { text: "Stop"; tone: "#5a1f2a"; onClicked: page.act("Stop", "stop") }
                    Item { width: 10 }
                    Btn { text: "Key −"; onClicked: page.act("Key down", "key_down") }
                    Btn { text: "Key +"; onClicked: page.act("Key up", "key_up") }
                    Btn { text: "Tempo −"; onClicked: page.act("Tempo down", "tempo_down") }
                    Btn { text: "Tempo +"; onClicked: page.act("Tempo up", "tempo_up") }
                }
                Dial {
                    Layout.fillWidth: true
                    caption: "Position"
                    shown: page.view.playback.position
                    maximum: page.view.playback.duration
                    valueText: Math.floor(page.view.playback.position / 60) + ":" + ("0" + Math.floor(page.view.playback.position % 60)).slice(-2)
                    onCommit: function (v) { page.act("Seek to " + Math.floor(v / 60) + ":" + ("0" + Math.floor(v % 60)).slice(-2), "seek_to", {seconds: Math.round(v * 10) / 10}) }
                }
                Dial {
                    Layout.fillWidth: true
                    caption: "Karaoke volume"
                    shown: page.view.playback.karaoke_volume
                    valueText: Math.round(page.view.playback.karaoke_volume * 100) + "%"
                    onCommit: function (v) { page.act("Karaoke volume " + Math.round(v * 100) + "%", "set_karaoke_volume", {value: Math.round(v * 100) / 100}) }
                }
                Dial {
                    Layout.fillWidth: true
                    caption: "Music volume"
                    shown: page.view.playback.bgm_volume
                    valueText: Math.round(page.view.playback.bgm_volume * 100) + "%"
                    onCommit: function (v) { page.act("Music volume " + Math.round(v * 100) + "%", "set_bg_volume", {value: Math.round(v * 100) / 100}) }
                }
            }
        }

        // ---- rotation
        Label { text: "ROTATION  " + page.view.singers.length + (page.view.singers.length === 1 ? " singer" : " singers"); color: "#a78bfa"; font.pixelSize: 12; font.weight: Font.DemiBold }
        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 6
            model: page.view.singers
            ScrollBar.vertical: ScrollBar {}
            delegate: Rectangle {
                id: card
                property var singer: modelData
                width: list.width
                height: cardCol.implicitHeight + 16
                radius: 8
                color: singer.skipped ? "#15132b" : "#1a1838"
                opacity: singer.skipped ? 0.7 : 1.0
                Accessible.role: Accessible.ListItem
                Accessible.name: (index + 1) + ", " + singer.name + (singer.skipped ? ", skipped" : "")
                ColumnLayout {
                    id: cardCol
                    anchors.fill: parent
                    anchors.margins: 8
                    spacing: 4
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        Label { text: (index + 1); color: "#a78bfa"; font.pixelSize: 16; font.weight: Font.DemiBold; Layout.preferredWidth: 22 }
                        Label { text: card.singer.name; color: "#ffffff"; font.pixelSize: 14; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
                        Label { visible: card.singer.marker; text: "START"; color: "#adff2f"; font.pixelSize: 11 }
                        Label { visible: card.singer.skipped; text: "SKIPPED"; color: "#f0b44c"; font.pixelSize: 11 }
                        Btn { text: "▲"; implicitWidth: 34; onClicked: page.act("Move " + card.singer.name + " up", "queue.move_singer", {singer_id: card.singer.singer_id, direction: "up"}) }
                        Btn { text: "▼"; implicitWidth: 34; onClicked: page.act("Move " + card.singer.name + " down", "queue.move_singer", {singer_id: card.singer.singer_id, direction: "down"}) }
                        Btn { text: card.singer.skipped ? "Unskip" : "Skip"; onClicked: page.act((card.singer.skipped ? "Unskip " : "Skip ") + card.singer.name, "queue.skip_singer", {singer_id: card.singer.singer_id, skipped: !card.singer.skipped}) }
                        Btn {
                            text: page.confirmSinger === card.singer.singer_id ? "Confirm remove" : "Remove"
                            tone: "#5a1f2a"
                            onClicked: {
                                if (page.confirmSinger === card.singer.singer_id) { page.confirmSinger = ""; page.act("Remove " + card.singer.name, "queue.remove_singer", {singer_id: card.singer.singer_id}) }
                                else page.confirmSinger = card.singer.singer_id
                            }
                        }
                    }
                    Repeater {
                        model: card.singer.songs
                        delegate: RowLayout {
                            property var song: modelData
                            Layout.fillWidth: true
                            Layout.leftMargin: 28
                            spacing: 6
                            Label { text: (song.artist !== "" ? song.artist + " • " : "") + song.title; color: song.skipped ? "#8a87aa" : "#c9c6e8"; font.pixelSize: 12; elide: Text.ElideRight; Layout.fillWidth: true }
                            Btn { text: "▲"; implicitWidth: 28; implicitHeight: 26; onClicked: page.act("Move song up", "queue.move_song", {entry_id: song.entry_id, direction: "up"}) }
                            Btn { text: "▼"; implicitWidth: 28; implicitHeight: 26; onClicked: page.act("Move song down", "queue.move_song", {entry_id: song.entry_id, direction: "down"}) }
                            Btn { text: "✕"; implicitWidth: 28; implicitHeight: 26; tone: "#5a1f2a"; onClicked: page.act("Remove song", "queue.remove_song", {entry_id: song.entry_id}) }
                        }
                    }
                }
            }
        }
    }
}
