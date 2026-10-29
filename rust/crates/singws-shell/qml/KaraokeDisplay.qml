import QtQuick
import QtQuick.Window
import com.singws.shell

// STAGE E DEMO of the Rust-owned karaoke display. Shown ONLY with `--display-demo FILE.cdg`; it is connected to nothing (no app, no queue, no sound).
// It draws a CDG file with the Rust decoder (singws-cdg, frame-exact against FFmpeg's decoder, which is what the app's libmpv uses) through the
// image://cdg/<ms> provider, fitted into the window on black the way the app's default "fit" display mode does, with a ticker strip at the bottom.
// Options: --display-at SECONDS (hold that moment instead of playing), --display-ticker "TEXT", --display-time "MM:SS" (the ticker's countdown),
// --display-snapshot FILE.png (save the window, then quit).
// Keys: F or double-click = full screen on / off, Esc = leave full screen.
Window {
    id: disp
    width: 1280
    height: 720
    visible: false
    title: "SingWS - Karaoke Display (demo)"
    color: "#000000"

    function arg(name) {
        var a = Qt.application.arguments
        for (var i = 0; i < a.length - 1; i++) if (a[i] === name) return a[i + 1]
        return ""
    }

    CdgDemo { id: demo }
    // video demo (--display-demo-video FILE): libmpv's software renderer through image://video; no sound
    VideoDemo { id: vdemo }
    property bool videoMode: false
    property int videoSerial: 0
    function openVideo(path) {
        var at = arg("--display-at") !== "" ? parseFloat(arg("--display-at")) : -1
        videoMode = vdemo.load(path, at)
        loaded = videoMode
        show()
    }
    Timer {
        interval: 40
        running: disp.visible && disp.videoMode
        repeat: true
        onTriggered: { vdemo.poll(); disp.videoSerial += 1 }
    }
    // the audience ticker's text: the real rules (singws-uimodel::ticker, proven equal to the app's), read-only from queue.json / settings.json in SINGWS_HOME
    TickerPanel { id: tickerPanel }
    Timer { interval: 3000; running: disp.visible; repeat: true; triggeredOnStart: true; onTriggered: tickerPanel.refresh() }
    property bool loaded: false
    property real fixedAt: arg("--display-at") !== "" ? parseFloat(arg("--display-at")) : -1
    property real positionMs: 0
    property double startedAt: 0

    function open(path) {
        loaded = demo.load(path)
        startedAt = Date.now()
        show()
    }

    Rectangle { anchors.fill: parent; color: "#000000"; z: -1 }
    Shortcut { sequence: "F"; onActivated: disp.visibility = disp.visibility === Window.FullScreen ? Window.Windowed : Window.FullScreen }
    Shortcut { sequence: "Esc"; onActivated: disp.visibility = Window.Windowed }
    MouseArea { anchors.fill: parent; onDoubleClicked: disp.visibility = disp.visibility === Window.FullScreen ? Window.Windowed : Window.FullScreen }

    // the clock: a held moment, or real time looping over the file
    Timer {
        interval: 33
        running: disp.visible && disp.loaded && !disp.videoMode
        repeat: true
        triggeredOnStart: true
        onTriggered: {
            if (disp.fixedAt >= 0) disp.positionMs = Math.round(disp.fixedAt * 1000)
            else disp.positionMs = demo.durationMs > 0 ? (Date.now() - disp.startedAt) % (demo.durationMs + 1000) : 0
        }
    }

    readonly property real tickerH: Math.round(height * 0.085)

    // the picture: 300 x 216 fitted into the area above the ticker, never stretched
    Item {
        id: stage
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: ticker.top
        Image {
            id: vpic
            anchors.fill: parent
            visible: disp.videoMode
            fillMode: Image.PreserveAspectFit
            smooth: true
            cache: false
            asynchronous: false
            source: disp.videoMode ? "image://video/" + disp.videoSerial + "/960/540" : ""
        }
        Image {
            id: pic
            anchors.fill: parent
            visible: disp.loaded && !disp.videoMode
            fillMode: Image.PreserveAspectFit
            smooth: true
            cache: false
            asynchronous: false
            source: disp.loaded && !disp.videoMode ? "image://cdg/" + Math.floor(disp.positionMs) : ""
        }
        Text {
            anchors.centerIn: parent
            visible: !disp.loaded
            text: demo.status !== "" ? demo.status : "no CDG file loaded"
            color: "#6a678f"
            font.pixelSize: Math.round(disp.height * 0.04)
        }
    }

    // the ticker strip: the app's QML ticker rules (black bar, green text scrolling at 78 px/s from the right edge to off the left, a countdown
    // on the right, no scrolling area under the countdown). The glass/ribbon effects are not ported yet.
    Rectangle {
        id: ticker
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: disp.tickerH
        color: "#000000"
        clip: true
        property int namesPx: Math.round(height * 0.5)
        property string timeText: disp.arg("--display-time") !== "" ? disp.arg("--display-time") : "--:--"
        property real timerPad: 8
        property real rightMargin: 32
        property real gap: 16
        readonly property real timerW: timerLabel.contentWidth + timerPad * 2
        readonly property real scrollW: Math.max(0, width - timerW - rightMargin - gap)

        Item {
            x: 0; y: 0; width: ticker.scrollW; height: parent.height; clip: true
            Text {
                id: names
                anchors.verticalCenter: parent.verticalCenter
                text: disp.arg("--display-ticker") !== "" ? disp.arg("--display-ticker") : (tickerPanel.text !== "" ? tickerPanel.text : "1  Sample Singer   2  Another Singer   3  Third Singer")
                color: "#39FF88"
                font.pixelSize: ticker.namesPx
                font.weight: Font.Bold
                NumberAnimation on x {
                    id: slide
                    running: disp.visible && disp.fixedAt < 0 && ticker.scrollW > 0 && names.contentWidth > 0
                    from: ticker.scrollW
                    to: -names.contentWidth
                    duration: Math.max(900, Math.round((ticker.scrollW + names.contentWidth) / 78 * 1000))
                    loops: Animation.Infinite
                }
                // a held moment (snapshots): sit at the left edge so the text can be read
                Binding on x { when: disp.fixedAt >= 0; value: 0 }
            }
        }
        Text {
            id: timerLabel
            anchors.right: parent.right
            anchors.rightMargin: ticker.rightMargin
            anchors.verticalCenter: parent.verticalCenter
            text: ticker.timeText
            color: "#39FF88"
            font.pixelSize: ticker.namesPx
            font.weight: Font.Bold
        }
    }

    // the show-screen effects: the app's own QML (ShowScreenVfx.qml is byte-identical to QML_SHOW_SCREEN_VFX_SOURCE; tools/vfx_source_check.py), on top
    // of the picture. --display-effect next-up|singer-start|outro, --display-style STYLE, --display-singer/-song/-artist set what it shows.
    ShowScreenVfx { id: vfx; anchors.fill: parent; z: 5 }
    function startEffect() {
        var kind = arg("--display-effect")
        if (kind === "") return
        var singer = arg("--display-singer") || "Sample Singer", song = arg("--display-song") || "Sample Song", artist = arg("--display-artist") || "Sample Artist"
        var style = arg("--display-style") || "moving_spotlights"
        if (kind === "next-up") vfx.showNextUp(singer, song, artist, arg("--display-ondeck") || "On deck: Another Singer", 10)
        else if (kind === "singer-start") vfx.showSingerStart(singer, song, artist, style)
        else if (kind === "outro") vfx.showSongOutro(singer, song, artist, style)
    }
    onLoadedChanged: if (loaded) startEffect()

    Timer {
        interval: arg("--display-snapshot-after") !== "" ? parseInt(arg("--display-snapshot-after")) : 900
        running: disp.visible && disp.loaded && disp.arg("--display-snapshot") !== ""
        onTriggered: { disp.contentItem.grabToImage(function(r) { r.saveToFile(disp.arg("--display-snapshot")); Qt.quit() }) }
    }
}
