import Speech
import Foundation

guard CommandLine.arguments.count > 1 else {
    fputs("Usage: stt_cli <wav_file_path>\n", stderr)
    exit(64)
}

let wavPath = CommandLine.arguments[1]
let url = URL(fileURLWithPath: wavPath)

// We check authorization status first
let semAuth = DispatchSemaphore(value: 0)
SFSpeechRecognizer.requestAuthorization { status in
    switch status {
    case .authorized:
        semAuth.signal()
    default:
        fputs("Speech Recognition permission denied or restricted.\n", stderr)
        exit(2)
    }
}
semAuth.wait()

guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US")), recognizer.isAvailable else {
    fputs("SFSpeechRecognizer not available or language not supported.\n", stderr)
    exit(2)
}

// Request on-device recognition (never sends data to Apple servers)
let request = SFSpeechURLRecognitionRequest(url: url)
request.requiresOnDeviceRecognition = true

let sem = DispatchSemaphore(value: 0)
var resultText = ""
var exitCode: Int32 = 0

let task = recognizer.recognitionTask(with: request) { result, error in
    if let error = error {
        fputs("transcription error: \(error.localizedDescription)\n", stderr)
        exitCode = 3
        sem.signal()
        return
    }
    if let result = result {
        resultText = result.bestTranscription.formattedString
        if result.isFinal {
            sem.signal()
        }
    }
}

// Timeout after 15 seconds to prevent hangs
let timeoutResult = sem.wait(timeout: .now() + 15.0)
if timeoutResult == .timedOut {
    task.cancel()
    fputs("transcription timed out\n", stderr)
    exit(3)
}

print(resultText.trimmingCharacters(in: .whitespacesAndNewlines))
exit(exitCode)
