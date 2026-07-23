#!/usr/bin/env swift
//
// swift_ocr_bridge.swift
// macOS Vision OCR via subprocess bridge (avoid PyObjC VNRecognizeTextRequest garbled Chinese bug)
//
// Usage:
//   swift_ocr_bridge.swift <pdf_or_image_path> [output_json_path]
//
// Output: <output_json_path> with {"text": "...", "pages": [...], "engine": "macos_vision"}
//
// 当 output_json_path 省略时, 输出到 stdout (JSON).

import Foundation
import Vision
import AppKit

guard CommandLine.arguments.count >= 2 else {
    let stderr = FileHandle.standardError
    stderr.write("usage: swift_ocr_bridge <pdf_or_image_path> [output_json_path]\n".data(using: .utf8)!)
    exit(1)
}

let inputPath = CommandLine.arguments[1]
let outputPath = CommandLine.arguments.count >= 3 ? CommandLine.arguments[2] : nil

let start = Date()

func ocrCGImage(_ cgImage: CGImage, languages: [String]) -> [(text: String, conf: Float)] {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = languages
    request.usesLanguageCorrection = true

    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    do {
        try handler.perform([request])
    } catch {
        return []
    }

    var results: [(text: String, conf: Float)] = []
    if let observations = request.results {
        for obs in observations {
            if let top = obs.topCandidates(1).first {
                results.append((text: top.string, conf: top.confidence))
            }
        }
    }
    return results
}

func renderPDFPage(_ pageIndex: Int, pdfDoc: CGPDFDocument, dpi: CGFloat) -> CGImage? {
    guard let page = pdfDoc.page(at: pageIndex + 1) else { return nil }
    let mediaBox = page.getBoxRect(.mediaBox)
    let scale = dpi / 72.0
    let width = Int(mediaBox.width * scale)
    let height = Int(mediaBox.height * scale)
    let colorSpace = CGColorSpaceCreateDeviceRGB()
    let bitmapInfo = CGImageAlphaInfo.noneSkipLast.rawValue
    guard let ctx = CGContext(
        data: nil, width: width, height: height,
        bitsPerComponent: 8, bytesPerRow: 0,
        space: colorSpace, bitmapInfo: bitmapInfo
    ) else { return nil }
    ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
    ctx.fill(CGRect(x: 0, y: 0, width: width, height: height))
    ctx.scaleBy(x: scale, y: scale)
    ctx.drawPDFPage(page)
    return ctx.makeImage()
}

func ocrImageFile(_ path: String) -> [(text: String, conf: Float)] {
    guard let img = NSImage(contentsOfFile: path),
          let tiff = img.tiffRepresentation,
          let bitmap = NSBitmapImageRep(data: tiff),
          let cgImage = bitmap.cgImage else {
        return []
    }
    return ocrCGImage(cgImage, languages: ["zh-Hans", "en-US"])
}

func ocrPDFFile(_ path: String) -> [(text: String, conf: Float, page: Int)] {
    guard let url = CFURLCreateWithFileSystemPath(nil, path as CFString, .cfurlposixPathStyle, false),
          let pdfDoc = CGPDFDocument(url) else {
        return []
    }
    var results: [(text: String, conf: Float, page: Int)] = []
    for i in 0..<pdfDoc.numberOfPages {
        if let cgImage = renderPDFPage(i, pdfDoc: pdfDoc, dpi: 200) {
            let lines = ocrCGImage(cgImage, languages: ["zh-Hans", "en-US"])
            for line in lines {
                results.append((text: line.text, conf: line.conf, page: i + 1))
            }
        }
    }
    return results
}

// Main
let ext = (inputPath as NSString).pathExtension.lowercased()
var pagesData: [(page: Int, text: String, confidence: Float)] = []
var fullText = ""

if ["png", "jpg", "jpeg"].contains(ext) {
    let lines = ocrImageFile(inputPath)
    let text = lines.map { $0.text }.joined(separator: "\n")
    pagesData.append((page: 1, text: text, confidence: lines.map { $0.conf }.reduce(0, +) / Float(max(lines.count, 1))))
    fullText = text
} else {
    let lines = ocrPDFFile(inputPath)
    var byPage: [Int: [String]] = [:]
    for line in lines {
        byPage[line.page, default: []].append(line.text)
    }
    let sortedPages = byPage.keys.sorted()
    for page in sortedPages {
        let pageText = byPage[page]!.joined(separator: "\n")
        pagesData.append((page: page, text: pageText, confidence: 0.85))
    }
    fullText = sortedPages.map { byPage[$0]!.joined(separator: "\n") }.joined(separator: "\n")
}

let elapsed = Date().timeIntervalSince(start)
let result: [String: Any] = [
    "status": "success",
    "engine": "macos_vision",
    "text": fullText,
    "pages": pagesData.map { ["page": $0.page, "text": $0.text, "confidence": $0.confidence] },
    "elapsed_seconds": round(elapsed * 100) / 100
]

let jsonData = try! JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted])
let jsonString = String(data: jsonData, encoding: .utf8)!

if let outputPath = outputPath {
    try! jsonString.write(toFile: outputPath, atomically: true, encoding: .utf8)
} else {
    print(jsonString)
}
