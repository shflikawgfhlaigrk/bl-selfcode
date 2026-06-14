// On-device LLM bridge: reads a prompt on stdin (or argv), prints the Foundation Models
// response on stdout. Exit 2 = model unavailable, 3 = generation error. No network, no RAM-heavy weights.
import FoundationModels
import Foundation

let prompt: String = {
    let arg = CommandLine.arguments.dropFirst().joined(separator: " ")
    if !arg.isEmpty { return arg }
    return String(data: FileHandle.standardInput.readDataToEndOfFile(), encoding: .utf8) ?? ""
}().trimmingCharacters(in: .whitespacesAndNewlines)

guard !prompt.isEmpty else { FileHandle.standardError.write("empty prompt\n".data(using: .utf8)!); exit(64) }

switch SystemLanguageModel.default.availability {
case .available:
    let sem = DispatchSemaphore(value: 0)
    var code: Int32 = 0
    Task {
        do {
            let session = LanguageModelSession()
            let r = try await session.respond(to: prompt)
            print(r.content)
        } catch {
            FileHandle.standardError.write("gen error: \(error)\n".data(using: .utf8)!); code = 3
        }
        sem.signal()
    }
    sem.wait()
    exit(code)
case .unavailable(let reason):
    FileHandle.standardError.write("unavailable: \(reason)\n".data(using: .utf8)!); exit(2)
@unknown default:
    exit(2)
}
