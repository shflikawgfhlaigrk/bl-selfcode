// matte.swift — Apple Vision person segmentation → transparent-background video.
//
// Removes the background from a webcam/phone clip on-device (no green screen,
// no cloud, no paid service) and writes a ProRes 4444 .mov WITH an alpha channel
// that ffmpeg can composite. This is the "Apple software" leg of the studio:
// VNGeneratePersonSegmentationRequest is the same Vision model Apple ships in
// Final Cut / Photos.
//
// Build:  swiftc -O studio/matte.swift -o studio/matte
// Usage:  ./studio/matte IN.mov OUT.mov [--quality accurate|balanced|fast]
//                                       [--start SECONDS] [--dur SECONDS]
//
import Foundation
import AVFoundation
import Vision
import CoreImage
import CoreImage.CIFilterBuiltins

// ---- args -------------------------------------------------------------------
let argv = CommandLine.arguments
guard argv.count >= 3 else {
    FileHandle.standardError.write(Data("usage: matte IN.mov OUT.mov [--quality accurate|balanced|fast] [--start S] [--dur S]\n".utf8))
    exit(2)
}
let inURL = URL(fileURLWithPath: argv[1])
let outURL = URL(fileURLWithPath: argv[2])
func optVal(_ name: String) -> String? {
    if let i = argv.firstIndex(of: name), i + 1 < argv.count { return argv[i + 1] }
    return nil
}
let quality: VNGeneratePersonSegmentationRequest.QualityLevel = {
    switch optVal("--quality") {
    case "fast": return .fast
    case "balanced": return .balanced
    default: return .accurate
    }
}()
let startS = Double(optVal("--start") ?? "")
let durS = Double(optVal("--dur") ?? "")
let testMask = argv.contains("--testmask")
let rotateDeg = Int(optVal("--rotate") ?? "0") ?? 0   // extra rotation: 0/90/180/270

try? FileManager.default.removeItem(at: outURL)

// ---- input ------------------------------------------------------------------
let asset = AVURLAsset(url: inURL)
guard let vTrack = asset.tracks(withMediaType: .video).first else {
    FileHandle.standardError.write(Data("no video track\n".utf8)); exit(1)
}
let natural = vTrack.naturalSize
let transform = vTrack.preferredTransform
// preferred display transform, then an optional manual rotation, then shift to origin.
let rot = CGAffineTransform(rotationAngle: -CGFloat(rotateDeg) * .pi / 180)
let combined = transform.concatenating(rot)
let oriented = CGRect(origin: .zero, size: natural).applying(combined)
let outW = Int(abs(oriented.width).rounded())
let outH = Int(abs(oriented.height).rounded())
let toOrigin = CGAffineTransform(translationX: -oriented.minX, y: -oriented.minY)
let orientXform = combined.concatenating(toOrigin)

let reader = try AVAssetReader(asset: asset)
if let start = startS {
    let st = CMTime(seconds: start, preferredTimescale: 600)
    let dur = durS.map { CMTime(seconds: $0, preferredTimescale: 600) } ?? CMTime.positiveInfinity
    reader.timeRange = CMTimeRange(start: st, duration: dur)
}
let readerOut = AVAssetReaderTrackOutput(
    track: vTrack,
    outputSettings: [kCVPixelBufferPixelFormatTypeKey as String: Int(kCVPixelFormatType_32BGRA)])
readerOut.alwaysCopiesSampleData = false
reader.add(readerOut)

// ---- output (ProRes 4444 keeps the alpha channel) ---------------------------
let writer = try AVAssetWriter(outputURL: outURL, fileType: .mov)
let writerIn = AVAssetWriterInput(mediaType: .video, outputSettings: [
    AVVideoCodecKey: AVVideoCodecType.proRes4444,
    AVVideoWidthKey: outW,
    AVVideoHeightKey: outH,
])
writerIn.expectsMediaDataInRealTime = false
let adaptor = AVAssetWriterInputPixelBufferAdaptor(
    assetWriterInput: writerIn,
    sourcePixelBufferAttributes: [
        kCVPixelBufferPixelFormatTypeKey as String: Int(kCVPixelFormatType_32BGRA),
        kCVPixelBufferWidthKey as String: outW,
        kCVPixelBufferHeightKey as String: outH,
    ])
writer.add(writerIn)

// ---- Vision + CoreImage -----------------------------------------------------
let ciCtx = CIContext(options: [.useSoftwareRenderer: false])
let segReq = VNGeneratePersonSegmentationRequest()
segReq.qualityLevel = quality
segReq.outputPixelFormat = kCVPixelFormatType_OneComponent8
let clearBG = CIImage(color: CIColor(red: 0, green: 0, blue: 0, alpha: 0))

guard reader.startReading() else {
    FileHandle.standardError.write(Data("reader failed: \(String(describing: reader.error))\n".utf8)); exit(1)
}
guard writer.startWriting() else {
    FileHandle.standardError.write(Data("writer failed: \(String(describing: writer.error))\n".utf8)); exit(1)
}

var started = false
var frames = 0
var personFrames = 0

while reader.status == .reading {
    guard let sb = readerOut.copyNextSampleBuffer() else { break }
    guard let srcPB = CMSampleBufferGetImageBuffer(sb) else { continue }
    let pts = CMSampleBufferGetPresentationTimeStamp(sb)
    if !started { writer.startSession(atSourceTime: pts); started = true }

    // oriented source frame
    let frameCI = CIImage(cvPixelBuffer: srcPB).transformed(by: orientXform)

    var outCI = clearBG.cropped(to: frameCI.extent)   // default: fully transparent

    // --testmask: bypass Vision with a centered white ellipse so the CoreImage
    // keep-the-subject path can be validated without a human in frame.
    if testMask {
        let ext = frameCI.extent
        let grad = CIFilter.radialGradient()
        grad.center = CGPoint(x: ext.midX, y: ext.midY)
        grad.radius0 = Float(ext.height) * 0.20
        grad.radius1 = Float(ext.height) * 0.42
        grad.color0 = CIColor(red: 1, green: 1, blue: 1, alpha: 1)
        grad.color1 = CIColor(red: 0, green: 0, blue: 0, alpha: 1)
        let maskScaled = grad.outputImage!.cropped(to: ext)
        let alphaMask = maskScaled.applyingFilter("CIMaskToAlpha")
        outCI = frameCI.applyingFilter("CIMultiplyCompositing",
                                       parameters: [kCIInputBackgroundImageKey: alphaMask]).cropped(to: ext)
        guard let pool = adaptor.pixelBufferPool else { break }
        var pbT: CVPixelBuffer?
        CVPixelBufferPoolCreatePixelBuffer(kCFAllocatorDefault, pool, &pbT)
        if let dst = pbT {
            ciCtx.render(outCI, to: dst, bounds: CGRect(x: 0, y: 0, width: outW, height: outH),
                         colorSpace: CGColorSpace(name: CGColorSpace.sRGB)!)
            while !writerIn.isReadyForMoreMediaData { usleep(3000) }
            adaptor.append(dst, withPresentationTime: pts)
        }
        frames += 1
        continue
    }

    // person mask
    let handler = VNImageRequestHandler(cvPixelBuffer: srcPB, options: [:])
    if (try? handler.perform([segReq])) != nil,
       let maskPB = segReq.results?.first?.pixelBuffer {
        personFrames += 1
        if frames == 0 && ProcessInfo.processInfo.environment["MATTE_DEBUG"] != nil {
            let mw = CVPixelBufferGetWidth(maskPB), mh = CVPixelBufferGetHeight(maskPB)
            let fmt = CVPixelBufferGetPixelFormatType(maskPB)
            CVPixelBufferLockBaseAddress(maskPB, .readOnly)
            let bpr = CVPixelBufferGetBytesPerRow(maskPB)
            var maxV = 0, whiteCount = 0
            if let base = CVPixelBufferGetBaseAddress(maskPB) {
                let p = base.assumingMemoryBound(to: UInt8.self)
                for y in 0..<mh { for x in 0..<mw {
                    let v = Int(p[y * bpr + x]); if v > maxV { maxV = v }; if v > 128 { whiteCount += 1 }
                } }
            }
            CVPixelBufferUnlockBaseAddress(maskPB, .readOnly)
            let fmtStr = String(format: "%c%c%c%c",
                (fmt>>24)&0xff, (fmt>>16)&0xff, (fmt>>8)&0xff, fmt&0xff)
            let pct = whiteCount * 100 / (mw * mh)
            FileHandle.standardError.write(Data("DEBUG mask \(mw)x\(mh) fmt=\(fmtStr) maxVal=\(maxV) person=\(pct)%\n".utf8))
        }
        let maskRaw = CIImage(cvPixelBuffer: maskPB)
        // scale the mask up to source-native size, then orient exactly like the frame
        let sx = natural.width / maskRaw.extent.width
        let sy = natural.height / maskRaw.extent.height
        let maskScaled = maskRaw
            .transformed(by: CGAffineTransform(scaleX: sx, y: sy))
            .transformed(by: orientXform)
        // feather the mask edge slightly → no hard jaggies / halo on the cutout
        let softened = maskScaled
            .clampedToExtent()
            .applyingFilter("CIGaussianBlur", parameters: ["inputRadius": 1.8])
            .cropped(to: frameCI.extent)
        // mask (white=person, black=bg) → alpha channel, then carry it onto the frame.
        // Premultiplied multiply: out.rgb = frame.rgb, out.alpha = mask. (CIBlendWithMask
        // reads the mask's *alpha* channel, which a 1-component buffer lacks → all transparent.)
        let alphaMask = softened.applyingFilter("CIMaskToAlpha")
        let out = frameCI.applyingFilter(
            "CIMultiplyCompositing",
            parameters: [kCIInputBackgroundImageKey: alphaMask])
        outCI = out.cropped(to: frameCI.extent)
    }

    // render to a pooled BGRA buffer and append
    guard let pool = adaptor.pixelBufferPool else { break }
    var pbOut: CVPixelBuffer?
    CVPixelBufferPoolCreatePixelBuffer(kCFAllocatorDefault, pool, &pbOut)
    guard let dst = pbOut else { continue }
    ciCtx.render(outCI, to: dst, bounds: CGRect(x: 0, y: 0, width: outW, height: outH),
                 colorSpace: CGColorSpace(name: CGColorSpace.sRGB)!)
    while !writerIn.isReadyForMoreMediaData { usleep(3000) }
    adaptor.append(dst, withPresentationTime: pts)
    frames += 1
    if frames % 60 == 0 { FileHandle.standardError.write(Data("…\(frames) frames\n".utf8)) }
}

writerIn.markAsFinished()
let sem = DispatchSemaphore(value: 0)
writer.finishWriting { sem.signal() }
sem.wait()

if writer.status == .completed {
    let pct = frames > 0 ? (personFrames * 100 / frames) : 0
    print("matte: \(frames) frames, person found in \(pct)% → \(outURL.path) (\(outW)x\(outH), ProRes4444+alpha)")
    exit(0)
} else {
    FileHandle.standardError.write(Data("writer error: \(String(describing: writer.error))\n".utf8))
    exit(1)
}
