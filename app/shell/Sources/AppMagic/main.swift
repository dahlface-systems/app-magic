// App Magic's Mac shell.
//
// Deliberately thin. It owns the things only a Mac app can own: the window, the menu bar item, the built-in browser pane
// for design canvases, the open panels, and keeping the local service (service/magicd.py) alive. Everything the person
// sees is the web page in ui/, and everything App Magic does is the service. That split is what lets the workshop change
// most of App Magic without rebuilding this program.

import AppKit
import WebKit

let blush = NSColor(srgbRed: 1.0, green: 0.988, blue: 0.984, alpha: 1)
let args = CommandLine.arguments
let env = ProcessInfo.processInfo.environment

let fm = FileManager.default

func readTrimmed(_ path: String) -> String? {
    (try? String(contentsOfFile: path, encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines)
}

let homeDir: String = {
    if let h = env["APP_MAGIC_HOME"], !h.isEmpty { return h }
    return NSHomeDirectory() + "/App Magic"
}()

/// Where App Magic's own code lives. A developer build points at its source folder. A build that was sent to someone
/// carries its code inside the app and puts a copy in Application Support the first time it opens (and again, keeping
/// the old one, when a newer version arrives). That copy is the person's own: the workshop changes it.
let sourceDir: String = {
    if let s = env["APP_MAGIC_SRC"], !s.isEmpty { return s }
    if let url = Bundle.main.url(forResource: "source-dir", withExtension: nil),
       let s = try? String(contentsOf: url, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines), !s.isEmpty, fm.fileExists(atPath: s + "/service/magicd.py") { return s }
    if let res = Bundle.main.resourcePath, fm.fileExists(atPath: res + "/source/service/magicd.py") { return installSource(from: res + "/source") }
    return fm.currentDirectoryPath
}()

func versionParts(_ v: String) -> [Int] { v.split(separator: ".").map { Int($0) ?? 0 } }
func isOlder(_ a: String, than b: String) -> Bool {
    let x = versionParts(a), y = versionParts(b)
    for i in 0..<max(x.count, y.count) {
        let p = i < x.count ? x[i] : 0, q = i < y.count ? y[i] : 0
        if p != q { return p < q }
    }
    return false
}

func installSource(from bundled: String) -> String {
    let support = env["APP_MAGIC_SUPPORT"].flatMap { $0.isEmpty ? nil : $0 } ?? NSHomeDirectory() + "/Library/Application Support/App Magic"
    let installed = support + "/source"
    let newV = readTrimmed(bundled + "/VERSION") ?? "0"
    try? fm.createDirectory(atPath: support, withIntermediateDirectories: true)
    if let oldV = readTrimmed(installed + "/VERSION"), fm.fileExists(atPath: installed + "/service/magicd.py") {
        if !isOlder(oldV, than: newV) { return installed }
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd-HHmm"
        try? fm.moveItem(atPath: installed, toPath: support + "/source-\(oldV)-replaced-\(f.string(from: Date()))")
    } else if fm.fileExists(atPath: installed) {
        try? fm.removeItem(atPath: installed)                // a half-finished copy from an interrupted first launch
    }
    do {
        try fm.copyItem(atPath: bundled, toPath: installed)
        try? fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: installed + "/magic")
        return installed
    } catch {
        NSLog("App Magic: could not copy its code to \(installed): \(error)")
        return bundled
    }
}

/// Python comes with Apple's developer tools (or Homebrew). Without either, /usr/bin/python3 is only a placeholder that
/// asks to install them, so App Magic checks first and offers to do it.
func pythonReady() -> Bool {
    for p in ["/opt/homebrew/bin/python3", "/usr/local/bin/python3"] where fm.isExecutableFile(atPath: p) { return true }
    let t = Process()
    t.executableURL = URL(fileURLWithPath: "/usr/bin/xcode-select")
    t.arguments = ["-p"]
    t.standardOutput = FileHandle.nullDevice; t.standardError = FileHandle.nullDevice
    do { try t.run(); t.waitUntilExit(); return t.terminationStatus == 0 } catch { return false }
}

func tail(_ path: String, _ n: Int = 3000) -> String {
    guard let s = try? String(contentsOfFile: path, encoding: .utf8) else { return "" }
    return String(s.suffix(n))
}

// MARK: - the local service

final class Service {
    private var process: Process?
    private var stopping = false
    private var deaths: [Date] = []
    var onBroken: (() -> Void)?
    func resetDeaths() { deaths = []; stopping = false }
    private(set) var attached = false          // true when a service was already running and we did not start it

    var port: Int? { readTrimmed(homeDir + "/.magic/port").flatMap { Int($0) } }
    var token: String? { readTrimmed(homeDir + "/.magic/token") }

    func healthy() -> Bool {
        guard let port = port, let url = URL(string: "http://127.0.0.1:\(port)/api/health") else { return false }
        var ok = false
        let sem = DispatchSemaphore(value: 0)
        var req = URLRequest(url: url)
        req.timeoutInterval = 1.5
        URLSession.shared.dataTask(with: req) { data, resp, _ in
            if let http = resp as? HTTPURLResponse, http.statusCode == 200, let data = data,
               let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any], obj["ok"] as? Bool == true { ok = true }
            sem.signal()
        }.resume()
        _ = sem.wait(timeout: .now() + 2)
        return ok
    }

    func start() {
        if healthy() { attached = true; return }
        launch()
    }

    func launch() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        p.arguments = ["python3", sourceDir + "/service/magicd.py"]
        var e = env
        e["PATH"] = NSHomeDirectory() + "/.local/bin:/opt/homebrew/bin:/usr/local/bin:" + (env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin")
        e["APP_MAGIC_HOME"] = homeDir
        p.environment = e
        p.currentDirectoryURL = URL(fileURLWithPath: sourceDir)
        p.standardOutput = FileHandle.nullDevice
        let logPath = homeDir + "/.magic/logs/service-stderr.log"
        try? FileManager.default.createDirectory(atPath: homeDir + "/.magic/logs", withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: logPath, contents: nil)
        p.standardError = FileHandle(forWritingAtPath: logPath) ?? FileHandle.nullDevice
        p.terminationHandler = { [weak self] _ in
            guard let self = self, !self.stopping else { return }
            // The workshop restarts the service on purpose: bring it back. Five deaths inside 20 seconds means it is
            // broken, not restarting, and the window says so instead of trying forever.
            let now = Date()
            self.deaths = self.deaths.filter { now.timeIntervalSince($0) < 20 } + [now]
            if self.deaths.count >= 5 { DispatchQueue.main.async { self.onBroken?() }; return }
            DispatchQueue.global().asyncAfter(deadline: .now() + 0.6) { self.launch() }
        }
        do { try p.run(); process = p } catch { NSLog("App Magic: could not start the service: \(error)") }
    }

    func waitUntilHealthy(seconds: Double) -> Bool {
        let end = Date().addingTimeInterval(seconds)
        while Date() < end {
            if healthy() { return true }
            Thread.sleep(forTimeInterval: 0.25)
        }
        return false
    }

    func stop() {
        stopping = true
        if let p = process, p.isRunning { p.terminate() }
    }

    func url(_ path: String = "/") -> URL? {
        guard let port = port, let token = token else { return nil }
        return URL(string: "http://127.0.0.1:\(port)\(path)?t=\(token)")
    }
}

// MARK: - the app

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKScriptMessageHandler, WKUIDelegate, WKNavigationDelegate {
    let service = Service()
    var window: NSWindow!
    var container: NSView!
    var ui: WKWebView!
    var browser: WKWebView!
    var browserURL = ""
    var browserWatch: NSKeyValueObservation?
    var statusItem: NSStatusItem?
    var popover: NSPopover?
    var menuWeb: WKWebView?
    let selfcheck = args.contains("--selfcheck")
    var checkResult: [String: Any] = [:]

    func applicationDidFinishLaunching(_ note: Notification) {
        try? fm.createDirectory(atPath: homeDir + "/.magic/logs", withIntermediateDirectories: true)
        service.onBroken = { [weak self] in self?.showBroken() }
        if selfcheck { startService(); return }
        try? Bundle.main.bundlePath.write(toFile: homeDir + "/.magic/app-path", atomically: true, encoding: .utf8)
        buildMenu()
        buildWindow()
        buildStatusItem()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        if pythonReady() { startService() } else { showLocalPage("setup-tools.html") }
    }

    func startService() {
        DispatchQueue.global().async {
            self.service.resetDeaths()
            self.service.start()
            let ok = self.service.waitUntilHealthy(seconds: 25)
            DispatchQueue.main.async {
                if self.selfcheck { self.runSelfcheck(serviceOK: ok); return }
                if ok, let url = self.service.url() { self.ui.load(URLRequest(url: url)) } else { self.showBroken() }
            }
        }
    }

    /// A page that works with the service down: the developer-tools setup, and the "something is broken" page.
    func showLocalPage(_ name: String, query: String = "") {
        let file = URL(fileURLWithPath: sourceDir + "/ui/" + name)
        if fm.fileExists(atPath: file.path), var c = URLComponents(url: file, resolvingAgainstBaseURL: false) {
            if !query.isEmpty { c.query = query }
            ui.loadFileURL(c.url ?? file, allowingReadAccessTo: file.deletingLastPathComponent())
        } else {
            ui.loadHTMLString("<body style='font:16px -apple-system;padding:60px;color:#2b2230;background:#fffcfb'><h2>App Magic is missing part of itself.</h2><p>\(file.path) is not there.</p></body>", baseURL: nil)
        }
    }

    func showBroken() {
        guard ui != nil else { return }
        let log = tail(homeDir + "/.magic/logs/service-stderr.log", 1800)
        var c = URLComponents(); c.queryItems = [URLQueryItem(name: "log", value: log)]
        showLocalPage("broken.html", query: c.percentEncodedQuery ?? "")
    }

    func runMagic(_ arguments: [String]) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: sourceDir + "/magic")
        p.arguments = arguments
        var e = env
        e["PATH"] = NSHomeDirectory() + "/.local/bin:/opt/homebrew/bin:/usr/local/bin:" + (env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin")
        e["APP_MAGIC_HOME"] = homeDir
        p.environment = e
        try? p.run()
    }

    /// Where the workshop's Terminal window goes: over App Magic's own window, on the same screen. Terminal counts from
    /// the top-left of the main screen; AppKit counts from the bottom-left.
    func saveWindowPlace() {
        guard let w = window, let main = NSScreen.screens.first else { return }
        let f = w.frame, top = main.frame.maxY - f.maxY
        let width = min(f.width - 80, 1100), height = min(f.height - 80, 720)
        let b = [f.minX + 40, top + 40, f.minX + 40 + width, top + 40 + height].map { Int($0) }
        if let d = try? JSONSerialization.data(withJSONObject: ["bounds": b]) { try? d.write(to: URL(fileURLWithPath: homeDir + "/.magic/window.json")) }
    }
    func windowDidMove(_ notification: Notification) { saveWindowPlace() }
    func windowDidResize(_ notification: Notification) { saveWindowPlace() }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { webView.reload() }

    func webConfig(bridge: Bool) -> WKWebViewConfiguration {
        let c = WKWebViewConfiguration()
        if bridge { c.userContentController.add(self, name: "magic") }
        c.preferences.setValue(true, forKey: "developerExtrasEnabled")
        return c
    }

    func buildWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1180, height: 780), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "App Magic"
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.backgroundColor = blush
        window.minSize = NSSize(width: 900, height: 620)
        window.isReleasedWhenClosed = false
        window.center()
        window.setFrameAutosaveName("AppMagicMain")
        window.delegate = self
        container = NSView(frame: window.contentLayoutRect)
        container.autoresizingMask = [.width, .height]
        window.contentView = container

        ui = WKWebView(frame: container.bounds, configuration: webConfig(bridge: true))
        ui.autoresizingMask = [.width, .height]
        ui.uiDelegate = self
        ui.navigationDelegate = self
        ui.setValue(false, forKey: "drawsBackground")
        container.addSubview(ui)

        // The built-in browser pane: a real, signed-in browser for claude.ai design canvases and live apps. It keeps its
        // own cookies between launches, so signing in to Claude happens once.
        browser = WKWebView(frame: .zero, configuration: webConfig(bridge: false))
        browser.customUserAgent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"
        browser.uiDelegate = self
        browser.navigationDelegate = self
        // Every address the pane reaches goes to the page, which shows the sign-in help when it lands on a sign-in page.
        // Watching the address (not page loads) also catches claude.ai's in-page moves.
        browserWatch = browser.observe(\.url, options: [.new]) { [weak self] web, _ in
            guard let self = self, let u = web.url?.absoluteString else { return }
            self.js("window.magicNative && window.magicNative.browserAt(\(self.jsString(u)))")
        }
        browser.isHidden = true
        container.addSubview(browser)
        saveWindowPlace()
    }

    func buildStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let b = item.button {
            b.image = NSImage(systemSymbolName: "sparkle", accessibilityDescription: "App Magic")
            b.target = self
            b.action = #selector(togglePopover(_:))
        }
        statusItem = item
    }

    @objc func togglePopover(_ sender: Any?) {
        if let p = popover, p.isShown { p.performClose(nil); return }
        let vc = NSViewController()
        let web = WKWebView(frame: NSRect(x: 0, y: 0, width: 400, height: 520), configuration: webConfig(bridge: true))
        web.uiDelegate = self
        vc.view = web
        if let url = service.url("/menubar") { web.load(URLRequest(url: url)) }
        menuWeb = web
        let p = NSPopover()
        p.contentSize = NSSize(width: 400, height: 520)
        p.behavior = .transient
        p.contentViewController = vc
        if let b = statusItem?.button { p.show(relativeTo: b.bounds, of: b, preferredEdge: .minY) }
        popover = p
        NSApp.activate(ignoringOtherApps: true)
    }

    func buildMenu() {
        let main = NSMenu()
        func add(_ title: String, _ items: [NSMenuItem]) {
            let m = NSMenuItem(); let sub = NSMenu(title: title)
            items.forEach { sub.addItem($0) }
            m.submenu = sub; main.addItem(m)
        }
        func item(_ title: String, _ sel: Selector?, _ key: String = "", target: AnyObject? = nil) -> NSMenuItem {
            let i = NSMenuItem(title: title, action: sel, keyEquivalent: key); i.target = target; return i
        }
        add("App Magic", [item("About App Magic", #selector(NSApplication.orderFrontStandardAboutPanel(_:))), .separator(),
                          item("Make Changes to App Magic…", #selector(changeItself), ",", target: self), .separator(),
                          item("Hide App Magic", #selector(NSApplication.hide(_:)), "h"), item("Quit App Magic", #selector(NSApplication.terminate(_:)), "q")])
        add("Edit", [item("Undo", Selector(("undo:")), "z"), item("Redo", Selector(("redo:")), "Z"), .separator(),
                     item("Cut", #selector(NSText.cut(_:)), "x"), item("Copy", #selector(NSText.copy(_:)), "c"),
                     item("Paste", #selector(NSText.paste(_:)), "v"), item("Select All", #selector(NSText.selectAll(_:)), "a")])
        add("View", [item("Ask", #selector(ask), "k", target: self), item("Set Up…", #selector(setup), "", target: self), item("Reload", #selector(reload), "r", target: self)])
        add("Window", [item("Minimize", #selector(NSWindow.performMiniaturize(_:)), "m"), item("Close", #selector(NSWindow.performClose(_:)), "w"),
                       item("App Magic", #selector(showMain), "1", target: self)])
        NSApp.mainMenu = main
    }

    func js(_ code: String) { ui?.evaluateJavaScript(code, completionHandler: nil) }
    func jsString(_ s: String) -> String {
        let data = (try? JSONSerialization.data(withJSONObject: [s])) ?? Data("[\"\"]".utf8)
        let arr = String(data: data, encoding: .utf8) ?? "[\"\"]"
        return String(arr.dropFirst().dropLast())
    }
    @objc func reload() { ui?.reload() }
    @objc func setup() { showMain(); js("window.magicNative && window.magicNative.setup()") }
    @objc func ask() { showMain(); js("window.magicNative && window.magicNative.ask()") }
    @objc func changeItself() { showMain(); js("window.magicNative && window.magicNative.change('')") }
    @objc func showMain() { window?.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true) }

    // MARK: messages from the page

    func userContentController(_ c: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let m = message.body as? [String: Any], let cmd = m["cmd"] as? String else { return }
        if selfcheck || window == nil || browser == nil { return }       // no window to act on (the headless self-check)
        func num(_ k: String) -> CGFloat { CGFloat((m[k] as? NSNumber)?.doubleValue ?? 0) }
        switch cmd {
        case "browserOpen", "browserFrame":
            // The page gives the pane's place in web coordinates (top-left origin); AppKit counts from the bottom.
            let h = container.bounds.height
            browser.frame = NSRect(x: num("x"), y: h - num("y") - num("h"), width: num("w"), height: num("h"))
            browser.autoresizingMask = [.width, .height]
            if cmd == "browserOpen", let s = m["url"] as? String, let url = URL(string: s) {
                if s != browserURL { browserURL = s; browser.load(URLRequest(url: url)) }
                browser.isHidden = false
            }
        case "browserLoad": if let s = m["url"] as? String, let url = URL(string: s) { browser.load(URLRequest(url: url)) }
        case "browserPasteLink":
            // The link from Claude's sign-in email, copied by the person; only Claude's own addresses are opened.
            let s = (NSPasteboard.general.string(forType: .string) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            if let url = URL(string: s), url.scheme == "https", let host = url.host, host == "claude.ai" || host.hasSuffix(".claude.ai") || host.hasSuffix(".anthropic.com") {
                browser.load(URLRequest(url: url))
            } else { js("window.magicNative && window.magicNative.noLink()") }
        case "browserClose": browser.isHidden = true
        case "browserReload": browser.reload()
        case "openExternal": if let s = m["url"] as? String, let url = URL(string: s) { NSWorkspace.shared.open(url) }
        case "reveal": if let p = m["path"] as? String { NSWorkspace.shared.selectFile(nil, inFileViewerRootedAtPath: p) }
        case "pickFolder":
            let panel = NSOpenPanel()
            panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.allowsMultipleSelection = false
            panel.prompt = "Look here"; panel.message = "Choose an app's folder, or a folder with several apps in it."
            panel.beginSheetModal(for: window) { r in
                if r == .OK, let p = panel.url?.path { self.js("window.magicNative.folderPicked(\(self.jsString(p)))") }
            }
        case "openMain":
            popover?.performClose(nil)
            showMain()
            if let v = m["view"] as? String { js("window.magicNative && window.magicNative.show(\(jsString(v)))") }
            if (m["then"] as? String) == "selfAudit" { js("document.getElementById('self-audit') && document.getElementById('self-audit').click()") }
        case "note": statusItem?.button?.toolTip = m["text"] as? String
        case "installTools":                                   // Apple's own installer for its developer tools (and Python)
            let t = Process(); t.executableURL = URL(fileURLWithPath: "/usr/bin/xcode-select"); t.arguments = ["--install"]
            try? t.run()
            Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { timer in
                if pythonReady() { timer.invalidate(); self.js("window.toolsReady && window.toolsReady()"); self.startService() }
            }
        case "toolsCheck": if pythonReady() { startService() } else { js("window.toolsMissing && window.toolsMissing()") }
        case "retryService": service.stop(); startService()
        case "fixService":
            let request = "App Magic's service will not start, so its window cannot open. Find out why from the log below and from running ./magic service, fix the cause in App Magic's code, run ./magic test, then ./magic restart.\n\nThe end of the service log (\(homeDir)/.magic/logs/service-stderr.log):\n" + tail(homeDir + "/.magic/logs/service-stderr.log", 2500)
            runMagic(["workshop", request])
        case "openSource": NSWorkspace.shared.selectFile(nil, inFileViewerRootedAtPath: sourceDir)
        default: break
        }
    }

    // File pickers inside the page (the + button).
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = false
        panel.begin { r in completionHandler(r == .OK ? panel.urls : nil) }
    }

    // Links that want a new window: the app's own page hands them to the default browser; the browser pane follows them
    // in place, so sign-in pop-ups work.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url {
            if webView === browser { browser.load(navigationAction.request) } else { NSWorkspace.shared.open(url) }
        }
        return nil
    }

    // The app's own page never leaves the local service.
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if webView === ui, let url = navigationAction.request.url, url.scheme != "file", let host = url.host, host != "127.0.0.1", host != "localhost" {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { showMain(); return true }
    func applicationWillTerminate(_ note: Notification) { if !service.attached { service.stop() } }

    // MARK: selfcheck: prove the pieces work without showing anything

    func runSelfcheck(serviceOK: Bool) {
        checkResult = ["service": serviceOK, "attached": service.attached, "port": service.port ?? 0, "home": homeDir, "source": sourceDir]
        guard serviceOK, let url = service.url() else { finishSelfcheck(); return }
        ui = WKWebView(frame: NSRect(x: 0, y: 0, width: 1180, height: 760), configuration: webConfig(bridge: true))
        ui.navigationDelegate = self
        ui.load(URLRequest(url: url))
        DispatchQueue.main.asyncAfter(deadline: .now() + 12) { self.checkResult["page"] = "timed out"; self.finishSelfcheck() }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard selfcheck else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) {
            let probe = "JSON.stringify({title: document.title, firstMessage: (document.querySelector('#thread .msg')||{}).textContent || '', status: document.getElementById('status-line').textContent, nav: document.querySelectorAll('.topbar nav button').length, bridge: !!(window.webkit && window.webkit.messageHandlers.magic), tiles: document.querySelectorAll('#caps .tile').length})"
            webView.evaluateJavaScript(probe) { value, error in
                self.checkResult["page"] = (value as? String) ?? "error: \(String(describing: error))"
                self.finishSelfcheck()
            }
        }
    }

    var finished = false
    func finishSelfcheck() {
        if finished { return }
        finished = true
        if let data = try? JSONSerialization.data(withJSONObject: checkResult, options: [.prettyPrinted, .sortedKeys]), let s = String(data: data, encoding: .utf8) { print(s) }
        let page = (checkResult["page"] as? String) ?? ""
        let ok = (checkResult["service"] as? Bool == true) && page.contains("\"title\":\"App Magic\"") && page.contains("\"bridge\":true")
        if !service.attached { service.stop() }
        exit(ok ? 0 : 1)
    }
}

// MARK: - snapshot: draw a web page (a file or an address) to a PNG without showing anything
//
//   AppMagic --snapshot <file-or-url> <out.png>
//
// Used by the service to make a picture of an app that is just a page: no installing, no running. The page gets its own
// throwaway browser session, so it shares no cookies with the browser pane.

final class Snapshotter: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    let input: String, output: String
    var window: NSWindow!
    var web: WKWebView!
    var done = false
    init(input: String, output: String) { self.input = input; self.output = output }

    func applicationDidFinishLaunching(_ note: Notification) {
        let size = NSRect(x: 0, y: 0, width: 1280, height: 820)
        let c = WKWebViewConfiguration()
        c.websiteDataStore = .nonPersistent()
        web = WKWebView(frame: size, configuration: c)
        web.navigationDelegate = self
        window = NSWindow(contentRect: size, styleMask: [.borderless], backing: .buffered, defer: false)   // never put on screen
        window.contentView = web
        if input.hasPrefix("http://") || input.hasPrefix("https://"), let url = URL(string: input) {
            web.load(URLRequest(url: url, timeoutInterval: 10))
        } else {
            let file = URL(fileURLWithPath: input)
            web.loadFileURL(file, allowingReadAccessTo: file.deletingLastPathComponent())
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 12) { self.take() }      // a slow page still gets one try
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.4) { self.take() }     // let it lay out and run its script
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { exit(2) }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { exit(2) }

    func take() {
        if done { return }
        done = true
        let cfg = WKSnapshotConfiguration()
        cfg.snapshotWidth = 720
        web.takeSnapshot(with: cfg) { image, _ in
            guard let image = image, let tiff = image.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
                  let png = rep.representation(using: .png, properties: [:]) else { exit(3) }
            do { try png.write(to: URL(fileURLWithPath: self.output)); exit(0) } catch { exit(4) }
        }
    }
}

let app = NSApplication.shared
if let at = args.firstIndex(of: "--snapshot"), args.count > at + 2 {
    let snap = Snapshotter(input: args[at + 1], output: args[at + 2])
    app.delegate = snap
    app.setActivationPolicy(.prohibited)
    app.run()
}
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(args.contains("--selfcheck") ? .prohibited : .regular)
app.run()
