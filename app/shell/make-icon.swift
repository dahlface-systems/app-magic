// Draws App Magic's icon (a rose tile with the plum four-point spark) and writes shell/AppMagic.icns.
//   swift shell/make-icon.swift
import AppKit
let out = URL(fileURLWithPath: CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "shell")
let set = out.appendingPathComponent("AppMagic.iconset")
try? FileManager.default.removeItem(at: set)
try! FileManager.default.createDirectory(at: set, withIntermediateDirectories: true)
func draw(_ px: Int) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let s = CGFloat(px), inset = s * 0.09
    let tile = NSBezierPath(roundedRect: NSRect(x: inset, y: inset, width: s - 2 * inset, height: s - 2 * inset), xRadius: s * 0.2, yRadius: s * 0.2)
    NSColor(srgbRed: 0.949, green: 0.710, blue: 0.769, alpha: 1).setFill(); tile.fill()
    // The spark, from the 24-unit path used in the app: M12 2C12.8 8 16 11.2 22 12C16 12.8 12.8 16 12 22C11.2 16 8 12.8 2 12C8 11.2 11.2 8 12 2Z
    let k = (s * 0.52) / 20, ox = s / 2 - 12 * k, oy = s / 2 - 12 * k
    func p(_ x: CGFloat, _ y: CGFloat) -> NSPoint { NSPoint(x: ox + x * k, y: oy + (24 - y) * k) }
    let star = NSBezierPath()
    star.move(to: p(12, 2))
    star.curve(to: p(22, 12), controlPoint1: p(12.8, 8), controlPoint2: p(16, 11.2))
    star.curve(to: p(12, 22), controlPoint1: p(16, 12.8), controlPoint2: p(12.8, 16))
    star.curve(to: p(2, 12), controlPoint1: p(11.2, 16), controlPoint2: p(8, 12.8))
    star.curve(to: p(12, 2), controlPoint1: p(8, 11.2), controlPoint2: p(11.2, 8))
    star.close()
    NSColor(srgbRed: 0.169, green: 0.133, blue: 0.188, alpha: 1).setFill(); star.fill()
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}
for (name, px) in [("16x16", 16), ("16x16@2x", 32), ("32x32", 32), ("32x32@2x", 64), ("128x128", 128), ("128x128@2x", 256), ("256x256", 256), ("256x256@2x", 512), ("512x512", 512), ("512x512@2x", 1024)] {
    try! draw(px).write(to: set.appendingPathComponent("icon_\(name).png"))
}
let t = Process(); t.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
t.arguments = ["-c", "icns", set.path, "-o", out.appendingPathComponent("AppMagic.icns").path]
try! t.run(); t.waitUntilExit()
try? FileManager.default.removeItem(at: set)
print("wrote", out.appendingPathComponent("AppMagic.icns").path)
