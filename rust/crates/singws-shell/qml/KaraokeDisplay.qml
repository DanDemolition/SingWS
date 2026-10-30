import QtQuick
import QtQuick.Window
import com.singws.shell

// STAGE E DEMO of the Rust-owned karaoke display. Shown ONLY with `--display-demo FILE.cdg`; it is connected to nothing (no app, no queue, no sound).
// It draws a CDG file with the Rust decoder (singws-cdg, frame-exact against FFmpeg's decoder, which is what the app's libmpv uses) through the
// image://cdg/<ms> provider, fitted into the window on black the way the app's default "fit" display mode does, with a ticker strip at the bottom.
// Options: --display-at SECONDS (hold that moment instead of playing), --display-ticker "TEXT", --display-time "MM:SS" (the ticker's countdown),
// --display-snapshot FILE.png (save the window, then quit).
// IDLE mode (--display-idle): no picture, the show's idle background stretched edge to edge (--display-idle-bg IMAGE, black without) and the "now playing"
// card at the bottom left that the app draws while background music plays: --display-idle-track PATH (artist and title from the file name rules of
// track_display.py; --display-idle-artist / --display-idle-title stand in for tags, which the Rust side does not read yet), --display-idle-art IMAGE,
// --display-idle-level 0..1 (the background player's meter), --display-idle-silent (background music not playing: no card).
// MONITOR RULES (opt-in so the other demos keep their 1280x720 window): --display-place puts the window where the app's VideoWindow would open
// (singws_cdg::window_rules, proven equal to the app by tools/window_rules_parity.py): a saved karaoke_window_pos from settings.json (or --display-saved JSON /
// `none`) is restored on a screen that exists now, otherwise a 480x360 window in the bottom right of the screen under the cursor. --display-screens FILE.json
// replaces the live screens with a described topology (see placement_panel.rs); --display-where prints the decision and quits without opening a window.
// Screen changes (Qt.application.screens) are debounced for 600 ms like the app's `_screen_surface_refresh_timer`, then the surfaces are refreshed once;
// --display-simulate-screen-events COUNT,GAP_MS fires that many events and prints when the refresh happens.
// Keys: F or double-click = full screen on / off, Esc = leave full screen.
Window {
    id: disp
    width: 1280
    height: 720
    visible: false
    title: "SingWS - Karaoke Display (demo)"
    color: "#000000"

    function hasArg(name) {
        return Qt.application.arguments.indexOf(name) >= 0
    }
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
        applyPlacement()
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
    // idle screen (see the header comment): the rules are singws_cdg::idle_overlay, proven equal to the app's drawing by tools/idle_overlay_parity.py
    IdleOverlay { id: idleCard }
    property bool idleMode: false
    function openIdle() {
        idleMode = true
        idleCard.setTrack(arg("--display-idle-track"), arg("--display-idle-artist"), arg("--display-idle-title"))
        loaded = true
        applyPlacement()
        show()
    }
    // ---- monitor rules
    DisplayPlacement { id: placement }
    property int refreshes: 0
    property double lastScreenEvent: 0
    property double startedMs: Date.now()
    function applyPlacement() {
        if (!hasArg("--display-place")) return
        var d = JSON.parse(placement.decide(arg("--display-screens"), arg("--display-saved"), visibility === Window.FullScreen, visible))
        if (d.error) { console.log("[PLACE] " + d.error); return }
        if (d.geometry) { x = d.geometry.x; y = d.geometry.y; width = d.geometry.w; height = d.geometry.h }
        if (d.resize) { width = d.resize[0]; height = d.resize[1] }
        if (d.move) { x = d.move[0]; y = d.move[1] }
        console.log("[PLACE] " + JSON.stringify(d))
    }
    function where() {
        var d = JSON.parse(placement.decide(arg("--display-screens"), arg("--display-saved"), false, false))
        console.log("[WHERE] topology " + placement.topology(arg("--display-screens")))
        console.log("[WHERE] decision " + JSON.stringify(d))
        Qt.quit()
    }
    // the app's `_screen_surface_refresh_timer`: single shot, 600 ms, restarted by every screen added / removed / primary changed event
    function screenEvent(why) {
        lastScreenEvent = Date.now()
        console.log("[SCREEN] " + why)
        settle.restart()
    }
    Timer {
        id: settle
        interval: 600
        repeat: false
        onTriggered: {
            disp.refreshes += 1
            console.log("[SCREEN] settled " + (Date.now() - disp.lastScreenEvent) + " ms after the last event; surfaces refreshed (#" + disp.refreshes + ")")
            console.log("[SCREEN] topology " + placement.topology(disp.arg("--display-screens")))
            if (disp.hasArg("--display-place")) console.log("[SCREEN] window now at " + disp.x + "," + disp.y + " " + disp.width + "x" + disp.height + " on screen " + (disp.screen ? disp.screen.name : "?"))
            if (disp.hasArg("--display-simulate-screen-events")) Qt.quit()
        }
    }
    Connections { target: Qt.application; function onScreensChanged() { disp.screenEvent("screens changed") } }
    property int simLeft: 0
    Timer {
        id: simTimer
        repeat: true
        onTriggered: {
            disp.screenEvent("simulated event")
            disp.simLeft -= 1
            if (disp.simLeft <= 0) running = false
        }
    }
    function simulateScreenEvents() {
        var spec = arg("--display-simulate-screen-events").split(",")
        simLeft = parseInt(spec[0])
        simTimer.interval = parseInt(spec[1] || "100")
        simTimer.start()
    }
    property bool loaded: false
    property real fixedAt: arg("--display-at") !== "" ? parseFloat(arg("--display-at")) : -1
    property real positionMs: 0
    property double startedAt: 0

    function open(path) {
        loaded = demo.load(path)
        startedAt = Date.now()
        applyPlacement()
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
            visible: disp.loaded && !disp.videoMode && !disp.idleMode
            fillMode: Image.PreserveAspectFit
            smooth: true
            cache: false
            asynchronous: false
            source: disp.loaded && !disp.videoMode && !disp.idleMode ? "image://cdg/" + Math.floor(disp.positionMs) : ""
        }
        // ---- the idle screen: background image stretched over the whole picture area (the app's _draw_background_pixmap ignores the aspect ratio),
        // then the now-playing card
        Image {
            anchors.fill: parent
            visible: disp.idleMode && source != ""
            source: disp.arg("--display-idle-bg") !== "" ? "file://" + disp.arg("--display-idle-bg") : ""
            fillMode: Image.Stretch
            smooth: true
            cache: false
        }
        FontMetrics { id: fmTitle; font.bold: true; font.pointSize: idleCard.titlePointSize(stage.height) }
        FontMetrics { id: fmArtist; font.pointSize: idleCard.artistPointSize(stage.height) }
        Image { id: artProbe; visible: false; asynchronous: false; cache: false; source: disp.arg("--display-idle-art") !== "" ? "file://" + disp.arg("--display-idle-art") : "" }
        property int idleTick: 0
        readonly property bool cardOn: disp.idleMode && !disp.hasArg("--display-idle-silent")
        readonly property var card: {
            idleTick
            if (!cardOn) return null
            return JSON.parse(idleCard.layoutJson(stage.width, stage.height, artProbe.status === Image.Ready, Math.round(fmTitle.ascent), Math.round(fmArtist.ascent)))
        }
        // the app's idle timer: every 50 ms the meter moves (advance_visualizer) while the card is drawn
        Timer {
            interval: 50; repeat: true; running: disp.visible && stage.cardOn
            onTriggered: {
                idleCard.step(true, disp.arg("--display-idle-level") !== "" ? parseFloat(disp.arg("--display-idle-level")) : 0.5)
                stage.idleTick += 1
            }
        }
        Item {
            id: cardItem
            anchors.fill: parent
            visible: stage.card !== null
            Rectangle { // card, near-solid so it reads over busy artwork
                x: stage.card ? stage.card.card.x : 0; y: stage.card ? stage.card.card.y : 0
                width: stage.card ? stage.card.card.w : 0; height: stage.card ? stage.card.card.h : 0
                radius: 16; color: Qt.rgba(8 / 255, 6 / 255, 20 / 255, 225 / 255)
            }
            Rectangle { // artwork placeholder behind the picture
                visible: stage.card && stage.card.art !== null
                x: visible ? stage.card.art.x : 0; y: visible ? stage.card.art.y : 0
                width: visible ? stage.card.art.w : 0; height: visible ? stage.card.art.h : 0
                radius: 10; color: Qt.rgba(20 / 255, 20 / 255, 20 / 255, 220 / 255)
            }
            Image { // the app scales the artwork to cover the square and draws it from the corner without clipping
                id: artPic
                visible: stage.card && stage.card.art !== null
                source: artProbe.source
                cache: false
                smooth: true
                fillMode: Image.Stretch
                readonly property var sz: visible ? idleCard.expand(artProbe.sourceSize.width, artProbe.sourceSize.height, stage.card.art.w).split(",") : ["0", "0"]
                x: visible ? stage.card.art.x : 0; y: visible ? stage.card.art.y : 0
                width: parseInt(sz[0]); height: parseInt(sz[1])
            }
            Text {
                id: titleText
                visible: stage.card && stage.card.text !== null
                x: visible ? stage.card.text.x : 0
                y: visible ? stage.card.text.titleBaseline - baselineOffset : 0
                width: visible ? stage.card.text.width : 0
                text: idleCard.title; color: "#E6E8F0"; elide: Text.ElideRight
                font.bold: true; font.pointSize: idleCard.titlePointSize(stage.height)
            }
            Text {
                id: artistText
                visible: stage.card && stage.card.text !== null && stage.card.text.artistBaseline !== null
                x: visible ? stage.card.text.x : 0
                y: visible ? stage.card.text.artistBaseline - baselineOffset : 0
                width: visible ? stage.card.text.width : 0
                text: idleCard.artist; color: "#B4B8C6"; elide: Text.ElideRight
                font.pointSize: idleCard.artistPointSize(stage.height)
            }
            Rectangle {
                visible: stage.card && stage.card.lane !== null
                x: visible ? stage.card.lane.x : 0; y: visible ? stage.card.lane.y : 0
                width: visible ? stage.card.lane.w : 0; height: visible ? stage.card.lane.h : 0
                radius: 6; color: Qt.rgba(1, 1, 1, 22 / 255)
            }
            Repeater {
                model: stage.card ? stage.card.bars : []
                delegate: Item {
                    Rectangle { x: modelData.bar.x; y: modelData.bar.y; width: modelData.bar.w; height: modelData.bar.h; radius: 2; color: Qt.rgba(245 / 255, 245 / 255, 245 / 255, 215 / 255) }
                    Rectangle { x: modelData.cap.x; y: modelData.cap.y; width: modelData.cap.w; height: modelData.cap.h; radius: 1; color: Qt.rgba(1, 1, 1, 245 / 255) }
                }
            }
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
