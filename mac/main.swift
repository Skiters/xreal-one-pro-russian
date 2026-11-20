import SwiftUI
import AppKit

final class WindowDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    let model = Model()
    var window: NSWindow!
    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(contentRect:NSRect(x:0,y:0,width:850,height:510),styleMask:[.titled,.closable,.miniaturizable,.resizable],backing:.buffered,defer:false)
        window.title = "XREAL Updater"
        window.contentMinSize = NSSize(width:440,height:350)
        window.contentView = NSHostingView(rootView:Workspace(m:model))
        window.delegate = self
        let menu = NSMenu()
        let appItem = NSMenuItem()
        menu.addItem(appItem)
        let actions = NSMenu()
        actions.addItem(withTitle:"Завершить XREAL Updater",action:#selector(NSApplication.terminate(_:)),keyEquivalent:"q")
        appItem.submenu = actions
        let editItem = NSMenuItem()
        menu.addItem(editItem)
        let edit = NSMenu(title:"Правка")
        for (name,sel,key) in [("Копировать","copy:","c"),("Вставить","paste:","v"),("Выбрать всё","selectAll:","a")] {
            edit.addItem(withTitle:name,action:Selector(sel),keyEquivalent:key)
        }
        editItem.submenu = edit
        let windowItem = NSMenuItem()
        menu.addItem(windowItem)
        let wm = NSMenu(title:"Окно")
        wm.addItem(withTitle:"Свернуть",action:#selector(NSWindow.performMiniaturize(_:)),keyEquivalent:"m")
        wm.addItem(withTitle:"Закрыть",action:#selector(NSWindow.performClose(_:)),keyEquivalent:"w")
        windowItem.submenu = wm
        NSApp.mainMenu = menu
        window.center(); window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps:true)
        model.refresh()
    }
    func mayClose()->Bool {
        if model.activity.isEmpty {return true}
        NSSound.beep()
        model.status = model.activity == "dump" ? "Остановите сбор в разделе «Дамп устройства» и дождитесь завершения чтения." : "Дождитесь завершения текущей операции."
        return false
    }
    func windowShouldClose(_ sender:NSWindow)->Bool { mayClose() }
    func applicationShouldTerminate(_ sender:NSApplication)->NSApplication.TerminateReply { mayClose() ? .terminateNow : .terminateCancel }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender:NSApplication)->Bool { true }
}
if CommandLine.arguments.contains(where:{["--self-test","--check-device","--verify-device"].contains($0)}) {
    let p = Process(); p.executableURL = engine; p.arguments = Array(CommandLine.arguments.dropFirst())
    try p.run(); p.waitUntilExit(); exit(p.terminationStatus)
}
let app = NSApplication.shared
let delegate = WindowDelegate()
app.setActivationPolicy(.regular)
app.delegate = delegate
app.run()
