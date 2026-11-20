import UniformTypeIdentifiers
import AppKit
import SwiftUI

struct Device: Identifiable, Hashable {
    let id: String
    let pid: Int
    let name: String
    var label: String {
        let model = [0x436:"XREAL One Pro", 0x438:"XREAL One", 0x43e:"XREAL 1S"][pid] ?? name
        return "\(model) · \(String(format:"%04X",pid))"
    }
}

typealias Object = [String: Any]
let engine = Bundle.main.bundleURL.appendingPathComponent("Contents/Helpers/Engine.app/Contents/MacOS/XREAL Updater")

final class Model: ObservableObject {
    @Published var section = "Прошивка"
    @Published var devices = [Device]()
    @Published var selected = ""
    @Published var identity = [String:String]()
    @Published var known = false
    @Published var bin: URL?
    @Published var package = ""
    @Published var activity = ""
    @Published var status = ""
    @Published var statusSection = ""
    @Published var failure = false
    @Published var percent: Double?
    @Published var report = ""
    @Published var folder: URL?
    @Published var canStop = false
    private var previousReport = ""
    private var task: Process?
    var current: Device? { devices.first { $0.id == selected } }
    var ready: Bool { activity.isEmpty && current != nil && known && bin != nil }

    func run(_ op: String, extra: Object = [:], message: String, finish: @escaping (Object)->Void = { _ in }) {
        guard activity.isEmpty else { return }
        var request: Object = ["op":op,"id":selected,"path":bin?.path ?? ""]
        request.merge(extra) { _,new in new }
        let process = Process(), input = Pipe(), output = Pipe()
        process.executableURL = engine
        process.standardInput = input
        process.standardOutput = output
        statusSection = section; activity = op; status = message; percent = nil; failure = false; canStop = false
        do {
            let bytes = try JSONSerialization.data(withJSONObject:request)
            try process.run()
            task = process
            try input.fileHandleForWriting.write(contentsOf: bytes)
            try input.fileHandleForWriting.close()
        } catch {
            activity = ""; status = error.localizedDescription; failure = true
            return
        }
        DispatchQueue.global().async {
            var buffer = Data()
            var result: Object = [:]
            while true {
                let data = output.fileHandleForReading.availableData
                if data.isEmpty { break }
                buffer.append(data)
                while let end = buffer.firstIndex(of:10) {
                    let line = buffer[..<end]
                    if let event = (try? JSONSerialization.jsonObject(with:line)) as? Object {
                        if event["done"] as? Bool == true { result = event }
                        else { DispatchQueue.main.async { self.progress(event) } }
                    }
                    buffer.removeSubrange(...end)
                }
            }
            process.waitUntilExit()
            let final = result
            DispatchQueue.main.async {
                self.task = nil; self.activity = ""; self.percent = nil; self.canStop = false
                if ["update","verify"].contains(op) { self.selected = ""; self.identity = [:]; self.known = false }
                if let error = final["error"] as? String {
                    self.failure = true; self.status = error
                    if ["bin","identity","list","dump"].contains(op), let report = final["report"] as? String {
                        self.previousReport = report
                        self.report = report
                    }
                } else if process.terminationStatus != 0 || final.isEmpty {
                    self.failure = true; self.status = "Связь с процессом прервана. Проверьте устройство перед повторной операцией."
                } else {
                    self.status = final["status"] as? String ?? ""
                    finish(final)
                }
            }
        }
    }
    func pretty(_ obj: Object) -> String {
        guard let data = try? JSONSerialization.data(withJSONObject:obj,options:[.prettyPrinted,.sortedKeys]) else { return "" }
        return String(decoding:data,as:UTF8.self)
    }
    private func progress(_ event: Object) {
        if let message = event["status"] as? String { status = message }
        if let value = event["percent"] as? Double { percent = value }
        if let text = event["report"] as? String { report = text; previousReport = text }
        if event["canStop"] as? Bool == true { canStop = true }
    }
    func refresh() {
        selected = ""; known = false; identity = [:]; devices = []
        run("list",message:"Поиск устройств…") { result in
            self.devices = (result["devices"] as? [Object] ?? []).compactMap { row in
                guard let id = row["id"] as? String, let pid = row["pid"] as? Int else {return nil}
                return Device(id:id,pid:pid,name:row["name"] as? String ?? "XREAL")
            }
        }
    }
    func select(_ device: Device) {
        selected = device.id; known = false; identity = [:]
        run("identity",message:"Чтение идентификаторов…") { r in
            self.identity = r["identity"] as? [String:String] ?? [:]
            self.known = r["known"] as? Bool ?? false
            if !self.known { self.status = "Для этого варианта пока нет профиля. Сохраните дамп для сравнения." }
        }
    }
    func choose() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [UTType(filenameExtension:"bin") ?? .data]
        guard panel.runModal() == .OK, let url = panel.url else {return}
        bin = nil; package = ""
        run("bin",extra:["path":url.path],message:"Проверка содержимого BIN…") { result in
            self.bin = url
            let names = ["standard":"One / One Pro","experimental":"One Pro","one-experimental":"One","1s-standard":"1S"]
            self.package = "Pilot " + (result["pilot"] as? String ?? "") + " · " + (names[result["firmware"] as? String ?? ""] ?? "XREAL")
        }
    }
    func diagnostic() {
        run("diag",message:"Проверка USB и совместимости…") { r in
            self.report = r["report"] as? String ?? ""
            func parse(_ s:String)->Object? { (try? JSONSerialization.jsonObject(with:Data(s.utf8))) as? Object }
            if let current = parse(self.report) {
                self.failure = current["ok"] as? Bool == false
                if let previous = parse(self.previousReport) { self.report = self.pretty(["last_operation":previous,"diagnostic":current]) }
            }
        }
    }
    func dump() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false; panel.canChooseDirectories = true; panel.canCreateDirectories = true
        panel.prompt = "Сохранить здесь"
        guard panel.runModal() == .OK, let url = panel.url else {return}
        folder = nil
        run("dump",extra:["folder":url.path],message:"Подготовка папки…") { r in
            if let path = r["folder"] as? String {self.folder = URL(fileURLWithPath:path)}
            let labels = ["cancelled":"Сбор остановлен.","finished":"Сбор доступных файлов завершён.","limit":"Достигнут лимит данных.","interrupted":"Связь прервана."]
            self.failure = r["state"] as? String == "interrupted"
            self.status = (labels[r["state"] as? String ?? ""] ?? "Сбор завершён.") + " " + (r["detail"] as? String ?? "")
        }
    }
    func stop() {
        guard activity == "dump", canStop, let process = task, process.isRunning else {return}
        kill(process.processIdentifier,SIGUSR1); canStop = false; status = "Остановка после текущего чтения…"
    }
    func saveReport() {
        let panel = NSSavePanel(); panel.nameFieldStringValue = "XREAL-diagnostic.txt"
        guard panel.runModal() == .OK, let url = panel.url else {return}
        do {try report.write(to:url,atomically:true,encoding:.utf8)}
        catch {status = error.localizedDescription; failure = true}
    }
}
