import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import com.singws.shell

// Room chat: Everyone, Private conversations (read-only) and Host chat. Everything on screen is PLAIN text from the app's summary; the only action is "Send", which
// is the app's validated `chat.say` command (Everyone, or the open Host chat conversation). Private threads cannot be spoken in from here.
Item {
    id: page

    ChatPanel { id: panel }
    property var control: null
    property string demo: ""   // --chat-demo FILE: show this summary instead of the app's
    property var cview: control ? JSON.parse(control.viewJson) : ({enabled: false, chat: null})
    Connections { target: page.control; function onViewJsonChanged() { page.cview = JSON.parse(page.control.viewJson) } }
    property bool live: cview.enabled === true
    property string demoTab: ""   // --chat-demo-tab: which tab a demo opens on (screenshots)
    property string tab: demo !== "" && demoTab !== "" ? demoTab : "group"
    property string hostKey: ""
    property string sureFor: ""   // "group" while the Everyone send waits for its second tap
    property var view: JSON.parse(panel.render((demo !== "" ? panel.readDemo(demo) : JSON.stringify(cview.chat === undefined ? null : cview.chat)), tab, -new Date().getTimezoneOffset()))

    // pictures and GIFs: the app sends small thumbnails on request (chat.media); a demo file may carry its own under "media"
    property var demoMedia: demo !== "" ? (JSON.parse(panel.readDemo(demo) === "null" ? "{}" : panel.readDemo(demo)).media || ({})) : ({})
    function mediaSrc(ref) {
        if (ref === "") return ""
        if (demo !== "") return demoMedia[ref] || ""
        var rev = cview.media_rev   // re-evaluate when the app has sent another thumbnail
        return control ? control.mediaUri(ref) : ""
    }
    function askForMissingMedia() {
        if (!control || !live) return
        var asked = 0
        for (var i = items.length - 1; i >= 0 && asked < 6; i--) {
            var it = items[i]
            if (it.kind === "row" && it.row.media_ref !== "" && mediaSrc(it.row.media_ref) === "") { control.requestMedia(it.row.media_ref); asked++ }
        }
    }
    Timer { interval: 3000; repeat: true; running: page.live && page.visible; triggeredOnStart: true; onTriggered: page.askForMissingMedia() }

    // the flat list the ListView draws: for each conversation a header, then its rows
    property var items: {
        var out = []
        if (tab === "group") {
            for (var i = 0; i < view.group.length; i++) out.push({kind: "row", row: view.group[i]})
        } else if (tab === "dm") {
            for (var d = 0; d < view.dm.length; d++) {
                out.push({kind: "header", title: view.dm[d].title, key: ""})
                for (var j = 0; j < view.dm[d].rows.length; j++) out.push({kind: "row", row: view.dm[d].rows[j]})
            }
        } else {
            for (var h = 0; h < view.host.length; h++) {
                var t = view.host[h]
                out.push({kind: "header", title: t.title + (t.unread > 0 ? "   •   " + t.unread + " new" : ""), key: t.key})
                for (var k = 0; k < t.rows.length; k++) out.push({kind: "row", row: t.rows[k]})
            }
        }
        return out
    }
    readonly property bool canSpeak: live && ((tab === "group" && view.group_enabled) || (tab === "host" && hostKey !== ""))
    function say() {
        var text = input.text.trim()
        if (!control || text === "" || !canSpeak) return
        if (tab === "group" && sureFor !== "group") { sureFor = "group"; sureTimer.restart(); return }
        sureFor = ""
        var args = {channel: tab, text: text}
        if (tab === "host") args.singer_key = hostKey
        control.send(tab === "group" ? "Say to Everyone" : "Say in Host chat", "chat.say", JSON.stringify(args), false)
        input.text = ""
    }
    Timer { id: sureTimer; interval: 3000; onTriggered: page.sureFor = "" }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10

        Label { text: page.live ? "CHAT" : "CHAT (read-only: not connected to the app)"; color: "#e8e6f5"; font.pixelSize: 15; font.weight: Font.DemiBold }
        Label {
            Layout.fillWidth: true
            visible: text !== ""
            text: demo === "" && (page.cview.chat === null || page.cview.chat === undefined) ? "Waiting for the app to send the chat. It does when remote control (shell_commands) is on and the chat page has loaded." : page.view.status
            color: "#9a97c0"; font.pixelSize: 12; wrapMode: Text.Wrap
        }
        Rectangle {
            Layout.fillWidth: true
            visible: page.live && page.cview.result !== undefined && page.cview.result !== null
            height: visible ? resultText.implicitHeight + 14 : 0
            radius: 6
            color: !page.cview.result ? "transparent" : (page.cview.result.kind === "ok" ? "#12301f" : (page.cview.result.kind === "warn" ? "#33290f" : "#3a1414"))
            Label { id: resultText; anchors.fill: parent; anchors.margins: 7; wrapMode: Text.Wrap; color: "#ffffff"; font.pixelSize: 13; text: page.cview.result ? page.cview.result.text : "" }
            Accessible.role: Accessible.StaticText
            Accessible.name: page.cview.result ? page.cview.result.text : ""
        }
        RowLayout {
            spacing: 8
            Repeater {
                model: page.view.tabs
                delegate: Button {
                    text: modelData.label + (modelData.attention > 0 ? "  ·  " + modelData.attention + " to review" : "")
                    checkable: true
                    checked: page.tab === modelData.key
                    implicitHeight: 32
                    onClicked: { page.tab = modelData.key; page.sureFor = "" }
                    Accessible.name: modelData.label + (modelData.attention > 0 ? ", " + modelData.attention + " to review" : "")
                }
            }
        }

        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: page.items
            spacing: 3
            ScrollBar.vertical: ScrollBar {}
            onCountChanged: positionViewAtEnd()
            delegate: Rectangle {
                width: list.width
                height: modelData.kind === "header" ? 32 : bodyText.implicitHeight + 12 + (pic.visible ? pic.height + 6 : 0)
                radius: 6
                color: modelData.kind === "header" ? (modelData.key !== "" && modelData.key === page.hostKey ? "#2d2a66" : "#1f2540")
                                                   : (modelData.row.mine ? "#1b2a45" : "#1a1838")
                Accessible.role: Accessible.ListItem
                Accessible.name: modelData.kind === "header" ? modelData.title : (modelData.row.who + ": " + modelData.row.body)
                MouseArea {
                    anchors.fill: parent
                    enabled: modelData.kind === "header" && modelData.key !== ""
                    onClicked: page.hostKey = modelData.key
                }
                Label {
                    id: bodyText
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                    anchors.margins: 6
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    color: "#ffffff"
                    font.pixelSize: 13
                    font.weight: modelData.kind === "header" ? Font.DemiBold : Font.Normal
                    text: modelData.kind === "header" ? modelData.title + (modelData.key !== "" ? "   (click to talk to this singer)" : "")
                          : (modelData.row.who + (modelData.row.tags.length ? "   " + modelData.row.tags.join(" · ") : "") + (modelData.row.stamp ? "   " + modelData.row.stamp : "") + "\n"
                             + modelData.row.body + (modelData.row.attachment ? (modelData.row.body ? " " : "") + modelData.row.attachment : ""))
                }
                Image {
                    id: pic
                    readonly property string src: modelData.kind === "row" ? page.mediaSrc(modelData.row.media_ref) : ""
                    visible: src !== "" && status === Image.Ready
                    source: src
                    anchors.left: parent.left; anchors.top: bodyText.bottom; anchors.leftMargin: 6; anchors.topMargin: 2
                    width: Math.min(sourceSize.width, 240); height: visible ? width * sourceSize.height / Math.max(1, sourceSize.width) : 0
                    fillMode: Image.PreserveAspectFit
                    asynchronous: true
                    Accessible.role: Accessible.Graphic
                    Accessible.name: modelData.kind === "row" ? modelData.row.attachment : ""
                }
            }
        }

        // speaking: Everyone, or the Host chat conversation picked above. Private conversations are read-only here.
        Label {
            Layout.fillWidth: true
            text: (page.tab === "dm" ? "Private conversations are read-only here; speak in them from the app."
                  : (page.tab === "host" ? (page.hostKey === "" ? "Click a singer above to talk to them." : "Talking to this singer only.")
                                         : (page.view.group_enabled ? "Everyone in the room will see this." : "Everyone chat is off in the web dashboard.")))
                  + (page.tab !== "group" && page.view.more[page.tab] !== "" ? "  " + page.view.more[page.tab] + "." : "")
            color: "#9a97c0"; font.pixelSize: 12; wrapMode: Text.Wrap
        }
        RowLayout {
            Layout.fillWidth: true
            visible: page.tab !== "dm"
            spacing: 8
            TextField {
                id: input
                Layout.fillWidth: true
                maximumLength: 300
                selectByMouse: true
                enabled: page.canSpeak
                placeholderText: page.live ? "Type a message…" : "Connect to the app to speak"
                onAccepted: page.say()
                onTextChanged: page.sureFor = ""
                Accessible.name: "Chat message"
            }
            Button {
                text: page.sureFor === "group" ? "Sure?" : "Send"
                enabled: page.canSpeak && input.text.trim() !== ""
                implicitHeight: 34
                onClicked: page.say()
                Accessible.name: page.tab === "group" ? (page.sureFor === "group" ? "Confirm sending to everyone" : "Send to everyone") : "Send to this singer"
            }
        }
    }
}
