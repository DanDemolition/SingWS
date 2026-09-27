// One-frame ScreenCaptureKit probe for KaraFun's Dual Renderer.
// This is a development tool; it does not alter SingWS or KaraFun playback.
import Foundation
import ScreenCaptureKit
import CoreMedia
import CoreImage
import ImageIO
import UniformTypeIdentifiers

final class FrameReceiver: NSObject, SCStreamOutput {
    let ready = DispatchSemaphore(value: 0)
    private let lock = NSLock()
    private var saved = false
    private let output: URL
    private let context = CIContext()

    init(output: URL) { self.output = output }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of outputType: SCStreamOutputType) {
        guard outputType == .screen, sampleBuffer.isValid,
              let pixelBuffer = sampleBuffer.imageBuffer else { return }
        lock.lock()
        defer { lock.unlock() }
        guard !saved else { return }
        guard let image = context.createCGImage(CIImage(cvPixelBuffer: pixelBuffer),
                                                 from: CGRect(x: 0, y: 0,
                                                              width: CVPixelBufferGetWidth(pixelBuffer),
                                                              height: CVPixelBufferGetHeight(pixelBuffer))),
              let destination = CGImageDestinationCreateWithURL(output as CFURL,
                                                                  UTType.png.identifier as CFString, 1, nil) else {
            return
        }
        CGImageDestinationAddImage(destination, image, nil)
        guard CGImageDestinationFinalize(destination) else { return }
        saved = true
        ready.signal()
    }
}

@main struct KaraFunCaptureProbe {
    static func main() async {
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(
                false, onScreenWindowsOnly: false)
            let windows = content.windows.filter {
                ($0.owningApplication?.bundleIdentifier ?? "") == "com.recisio.kfiphone"
            }
            for window in windows {
                print("window id=\(window.windowID) title=\(window.title ?? "") frame=\(window.frame)")
            }
            guard CommandLine.arguments.count == 2 else {
                print("Usage: probe_karafun_capture /private/tmp/karafun-frame.png")
                return
            }
            guard let renderer = windows.first(where: { $0.title == "Dual Renderer" }) else {
                fputs("KaraFun Dual Renderer is not available to ScreenCaptureKit.\n", stderr)
                exit(2)
            }
            let configuration = SCStreamConfiguration()
            configuration.width = max(1, Int(renderer.frame.width * 2))
            configuration.height = max(1, Int(renderer.frame.height * 2))
            configuration.minimumFrameInterval = CMTime(value: 1, timescale: 15)
            configuration.queueDepth = 2
            configuration.capturesAudio = false
            let output = URL(fileURLWithPath: CommandLine.arguments[1])
            let receiver = FrameReceiver(output: output)
            let stream = SCStream(filter: SCContentFilter(desktopIndependentWindow: renderer),
                                  configuration: configuration, delegate: nil)
            try stream.addStreamOutput(receiver, type: .screen,
                                       sampleHandlerQueue: DispatchQueue(label: "singws.karafun.capture.probe"))
            try await stream.startCapture()
            let result = await Task.detached {
                receiver.ready.wait(timeout: .now() + 8)
            }.value
            try await stream.stopCapture()
            guard result == .success else {
                fputs("No KaraFun video frame arrived within 8 seconds.\n", stderr)
                exit(3)
            }
            print("saved \(output.path)")
        } catch {
            fputs("ScreenCaptureKit probe failed: \(error)\n", stderr)
            exit(1)
        }
    }
}
