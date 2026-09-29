import Foundation
import ScreenCaptureKit
import CoreMedia
import CoreVideo
import Darwin

// ScreenCaptureKit owns the capture queue. Python only polls the newest BGRA
// frame; it never runs a callback on the capture or audio thread.
private final class CaptureOutput: NSObject, SCStreamOutput {
    let generation: UInt64
    init(generation: UInt64) { self.generation = generation }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of outputType: SCStreamOutputType) {
        guard outputType == .screen, sampleBuffer.isValid,
              let buffer = sampleBuffer.imageBuffer else { return }
        if let attachments = CMSampleBufferGetSampleAttachmentsArray(
            sampleBuffer, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]],
           let raw = attachments.first?[.status] as? Int,
           SCFrameStatus(rawValue: raw) != .complete { return }
        CVPixelBufferLockBaseAddress(buffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(buffer) else { return }
        let width = CVPixelBufferGetWidth(buffer)
        let height = CVPixelBufferGetHeight(buffer)
        let stride = CVPixelBufferGetBytesPerRow(buffer)
        guard width > 0, height > 0, stride >= width * 4 else { return }
        // Capture pipeline latency: how long ago this frame was captured
        // (its presentation timestamp is on the host time clock) versus now.
        let arrival = CMTimeGetSeconds(CMClockGetTime(CMClockGetHostTimeClock()))
        let captured = CMTimeGetSeconds(sampleBuffer.presentationTimeStamp)
        CaptureState.shared.receive(Data(bytes: base, count: stride * height),
                                    width: width, height: height, stride: stride,
                                    generation: generation,
                                    lagSeconds: max(0, arrival - captured), arrivalSeconds: arrival)
    }
}


// Match the window's own aspect ratio. A fixed 1280x720 frame made
// ScreenCaptureKit scale a non-16:9 window to fit and leave an empty strip,
// which SingWS then stretched to the screen. Crop a small margin so the rounded
// corners and the traffic lights that appear on hover stay out of the picture.
private func makeConfiguration(for window: SCWindow) -> SCStreamConfiguration {
    let config = SCStreamConfiguration()
    let cornerInset: CGFloat = 6
    let topInset: CGFloat = 30
    let crop = CGRect(
        x: cornerInset, y: topInset,
        width: max(1, window.frame.width - 2 * cornerInset),
        height: max(1, window.frame.height - topInset - cornerInset))
    config.sourceRect = crop
    let outputHeight = min(1024.0, max(2.0, (1280.0 * crop.height / crop.width).rounded()))
    config.width = 1280
    config.height = Int(outputHeight)
    config.pixelFormat = kCVPixelFormatType_32BGRA
    // 30 fps. A 60 fps capture doubled the CPU scaling/painting of every frame at the
    // audience window's full size and made the video visibly laggy (2026-09-28).
    config.minimumFrameInterval = CMTime(value: 1, timescale: 30)
    config.queueDepth = 2
    config.showsCursor = false
    return config
}

private final class CaptureState {
    static let shared = CaptureState()
    private let lock = NSLock()
    private var generation: UInt64 = 0
    private var state: Int32 = 0 // 0 stopped, 1 starting, 2 frames ready, -1 failed
    private var frame = Data()
    private var width: Int32 = 0
    private var height: Int32 = 0
    private var stride: Int32 = 0
    private var serial: UInt64 = 0
    private var lagSeconds: Double = 0
    private var arrivalSeconds: Double = 0
    private var stream: SCStream?
    private var output: CaptureOutput?

    func start() -> Int32 {
        lock.lock()
        if state == 1 || state == 2 { lock.unlock(); return 1 }
        generation &+= 1
        let current = generation
        state = 1
        frame = Data()
        serial = 0
        lock.unlock()
        Task {
            do {
                var window: SCWindow?
                for _ in 0..<30 {
                    let content = try await SCShareableContent.excludingDesktopWindows(
                        false, onScreenWindowsOnly: false)
                    window = content.windows.first(where: {
                        $0.title == "Dual Renderer" &&
                        $0.owningApplication?.bundleIdentifier == "com.recisio.kfiphone"
                    })
                    if window != nil || isStale(current) { break }
                    try await Task.sleep(nanoseconds: 300_000_000)
                }
                guard let window else { throw NSError(domain: "SingWSKaraFunCapture", code: 1,
                    userInfo: [NSLocalizedDescriptionKey: "KaraFun Dual Renderer is not open"]) }
                let config = makeConfiguration(for: window)
                let receiver = CaptureOutput(generation: current)
                let active = SCStream(filter: SCContentFilter(desktopIndependentWindow: window),
                                      configuration: config, delegate: nil)
                try active.addStreamOutput(receiver, type: .screen,
                                           sampleHandlerQueue: DispatchQueue(
                                            label: "singws.karafun.capture.frames"))
                let valid = install(active, receiver: receiver, generation: current)
                guard valid else { return }
                try await active.startCapture()
                let stale = isStale(current)
                if stale { try? await active.stopCapture() }
                // KaraFun can resize or move its renderer after the stream
                // starts. The crop and output size were fixed at start, so a
                // resize left an empty strip in the frame (2026-09-28). Follow
                // the window: reconfigure whenever its size changes.
                var lastSize = window.frame.size
                while !isStale(current) {
                    try await Task.sleep(nanoseconds: 500_000_000)
                    guard let content = try? await SCShareableContent.excludingDesktopWindows(
                        false, onScreenWindowsOnly: false),
                          let live = content.windows.first(where: { $0.windowID == window.windowID })
                    else { continue }
                    if live.frame.size != lastSize {
                        lastSize = live.frame.size
                        try? await active.updateConfiguration(makeConfiguration(for: live))
                    }
                }
            } catch {
                fputs("[KARAFUN-CAPTURE] \(error)\n", stderr)
                fail(current)
            }
        }
        return 1
    }

    private func install(_ active: SCStream, receiver: CaptureOutput,
                         generation current: UInt64) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        let valid = current == generation && state == 1
        if valid { stream = active; output = receiver }
        return valid
    }

    private func isStale(_ current: UInt64) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return current != generation
    }

    private func fail(_ current: UInt64) {
        lock.lock()
        defer { lock.unlock() }
        if current == generation { state = -1; stream = nil; output = nil }
    }

    func stop() {
        lock.lock()
        generation &+= 1
        let old = stream
        stream = nil
        output = nil
        frame = Data()
        state = 0
        serial = 0
        lock.unlock()
        if let old { Task { try? await old.stopCapture() } }
    }

    func receive(_ data: Data, width: Int, height: Int, stride: Int,
                 generation current: UInt64, lagSeconds lag: Double, arrivalSeconds arrival: Double) {
        lock.lock()
        defer { lock.unlock() }
        guard current == generation, state == 1 || state == 2 else { return }
        frame = data
        self.width = Int32(width)
        self.height = Int32(height)
        self.stride = Int32(stride)
        lagSeconds = lag
        arrivalSeconds = arrival
        serial &+= 1
        state = 2
    }

    func status() -> Int32 { lock.lock(); defer { lock.unlock() }; return state }

    // Cheap change check so Python does not copy a multi-megabyte frame on every poll.
    func currentSerial() -> UInt64 { lock.lock(); defer { lock.unlock() }; return serial }

    func stats(lagMs: UnsafeMutablePointer<Double>?, arrival: UnsafeMutablePointer<Double>?) {
        lock.lock()
        defer { lock.unlock() }
        lagMs?.pointee = lagSeconds * 1000.0
        arrival?.pointee = arrivalSeconds
    }

    func copy(to destination: UnsafeMutableRawPointer?, capacity: Int32,
              width outWidth: UnsafeMutablePointer<Int32>?,
              height outHeight: UnsafeMutablePointer<Int32>?,
              stride outStride: UnsafeMutablePointer<Int32>?,
              serial outSerial: UnsafeMutablePointer<UInt64>?) -> Int32 {
        lock.lock()
        defer { lock.unlock() }
        guard state == 2, let destination, capacity >= frame.count,
              let outWidth, let outHeight, let outStride, let outSerial else { return 0 }
        frame.withUnsafeBytes { raw in
            if let base = raw.baseAddress { memcpy(destination, base, frame.count) }
        }
        outWidth.pointee = width
        outHeight.pointee = height
        outStride.pointee = stride
        outSerial.pointee = serial
        return Int32(frame.count)
    }
}

@_cdecl("singws_karafun_capture_start")
public func singws_karafun_capture_start() -> Int32 { CaptureState.shared.start() }

@_cdecl("singws_karafun_capture_stop")
public func singws_karafun_capture_stop() { CaptureState.shared.stop() }

@_cdecl("singws_karafun_capture_status")
public func singws_karafun_capture_status() -> Int32 { CaptureState.shared.status() }

@_cdecl("singws_karafun_capture_serial")
public func singws_karafun_capture_serial() -> UInt64 { CaptureState.shared.currentSerial() }

@_cdecl("singws_karafun_capture_stats")
public func singws_karafun_capture_stats(
    _ lagMs: UnsafeMutablePointer<Double>?, _ arrival: UnsafeMutablePointer<Double>?
) { CaptureState.shared.stats(lagMs: lagMs, arrival: arrival) }

@_cdecl("singws_karafun_capture_copy_frame")
public func singws_karafun_capture_copy_frame(
    _ destination: UnsafeMutableRawPointer?, _ capacity: Int32,
    _ width: UnsafeMutablePointer<Int32>?, _ height: UnsafeMutablePointer<Int32>?,
    _ stride: UnsafeMutablePointer<Int32>?, _ serial: UnsafeMutablePointer<UInt64>?
) -> Int32 {
    CaptureState.shared.copy(to: destination, capacity: capacity, width: width,
                             height: height, stride: stride, serial: serial)
}
