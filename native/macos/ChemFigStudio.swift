// ChemFigStudio —— chemfigstudio 的 macOS 原生外壳
//
// 设计要点（见 ARCHITECTURE.md / docs/DECISIONS.md ADR-009）：
//   * 本文件**不含任何业务逻辑**：它只做三件事 —— 起本地服务、开窗口显示网页、退出时收尾。
//     所以界面功能永远与网页版同步（加参数、加图类型都不用改 Swift）。
//   * 零第三方依赖：只用系统自带的 Cocoa / WebKit，`swiftc` 直接编译。
//   * 跨平台策略：这份外壳只在 macOS 用；Windows/Linux 直接用浏览器跑同一个网页版。
//
// 编译：scripts/build-macos-app.sh（内部调用 swiftc -framework Cocoa -framework WebKit）

import Cocoa
import WebKit
import Darwin

// MARK: - 小工具

/// 让系统分配一个空闲端口：绑定 0 号端口后读回真实端口号。
func findFreePort() -> Int {
    let sock = socket(AF_INET, SOCK_STREAM, 0)
    if sock < 0 { return 8765 }
    defer { close(sock) }
    var addr = sockaddr_in()
    addr.sin_family = sa_family_t(AF_INET)
    addr.sin_port = 0                                  // 交给内核挑
    addr.sin_addr.s_addr = inet_addr("127.0.0.1")
    var reuse: Int32 = 1
    setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &reuse, socklen_t(MemoryLayout<Int32>.size))
    let bindResult = withUnsafePointer(to: &addr) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            Darwin.bind(sock, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
        }
    }
    if bindResult != 0 { return 8765 }
    var len = socklen_t(MemoryLayout<sockaddr_in>.size)
    var out = sockaddr_in()
    let nameResult = withUnsafeMutablePointer(to: &out) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(sock, $0, &len) }
    }
    if nameResult != 0 { return 8765 }
    return Int(UInt16(bigEndian: out.sin_port))
}

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {

    private var window: NSWindow!
    private var webView: WKWebView!
    private var server: Process?
    private var port: Int = 8765
    private var outdir: String = ""

    // 由 build 脚本写进 Info.plist（CFBundleName 等），这里只做展示用
    private let appName = "chemfigstudio"

    // ---------------------------------------------------------------- 生命周期

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        buildWindow()
        startServer()
    }

    func applicationWillTerminate(_ notification: Notification) {
        stopServer()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    // ---------------------------------------------------------------- 界面

    private func buildWindow() {
        let rect = NSRect(x: 0, y: 0, width: 1120, height: 760)
        window = NSWindow(contentRect: rect,
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "\(appName) —— 化学图生成器"
        window.minSize = NSSize(width: 860, height: 560)
        window.center()
        window.setFrameAutosaveName("MainWindow")       // 记住位置和大小

        let config = WKWebViewConfiguration()
        // 非持久化数据存储：本应用只是显示本地页面，不需要 cookie/缓存落盘。
        // 好处：不往 ~/Library/WebKit 写东西（沙箱/受管机器上也能跑），也不会留垃圾。
        config.websiteDataStore = .nonPersistent()
        config.preferences.setValue(true, forKey: "developerExtrasEnabled")  // 右键可"检查元素"
        webView = WKWebView(frame: rect, configuration: config)

        if #available(macOS 13.3, *) { webView.isInspectable = true }   // 右键→检查元素
        webView.navigationDelegate = self
        webView.autoresizingMask = [.width, .height]
        window.contentView = webView

        showHTML(title: "正在启动…",
                 body: "<p>正在启动本地绘图服务，请稍候。</p>")
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func buildMenu() {
        let main = NSMenu()

        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "关于 \(appName)", action: #selector(showAbout), keyEquivalent: "")
            .target = self
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "重新载入界面", action: #selector(reload), keyEquivalent: "r")
            .target = self
        appMenu.addItem(withTitle: "打开输出目录", action: #selector(openOutdir), keyEquivalent: "o")
            .target = self
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "退出 \(appName)",
                        action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu

        let editItem = NSMenuItem()
        main.addItem(editItem)
        let editMenu = NSMenu(title: "编辑")
        editMenu.addItem(withTitle: "撤销", action: Selector(("undo:")), keyEquivalent: "z")
        editMenu.addItem(withTitle: "重做", action: Selector(("redo:")), keyEquivalent: "Z")
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "剪切", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "拷贝", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "粘贴", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "全选", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu

        NSApp.mainMenu = main
    }

    // ---------------------------------------------------------------- 服务

    /// 找 Python：优先 PATH 里的 python3，其次系统自带位置。
    private func pythonPath() -> String? {
        let candidates = ["/usr/bin/python3",
                          "/Library/Developer/CommandLineTools/usr/bin/python3",
                          "/opt/homebrew/bin/python3",
                          "/usr/local/bin/python3"]
        return candidates.first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    private func startServer() {
        guard let python = pythonPath() else {
            showFailure("找不到 Python 3",
                        "请先安装 Python 3（macOS 装了 Xcode 命令行工具就有），然后重新打开本应用。")
            return
        }
        port = findFreePort()
        outdir = resolveOutdir()

        let resources = Bundle.main.resourceURL?.path ?? ""
        let srcPath = "\(resources)/src"
        let vendorPath = "\(resources)/vendor"

        var env = ProcessInfo.processInfo.environment
        let pathEntries = [srcPath, vendorPath].filter { FileManager.default.fileExists(atPath: $0) }
        env["PYTHONPATH"] = (pathEntries + [env["PYTHONPATH"] ?? ""])
            .filter { !$0.isEmpty }.joined(separator: ":")
        // 让输出不被缓冲，日志能实时写进文件
        env["PYTHONUNBUFFERED"] = "1"

        let task = Process()
        task.executableURL = URL(fileURLWithPath: python)
        task.arguments = ["-m", "chemfigstudio.gui.server",
                          "--port", String(port),
                          "--outdir", outdir,
                          "--no-browser",
                          "--quiet",
                          // 外壳被强杀（SIGKILL）时也能让服务自己收尸
                          "--parent-pid", String(ProcessInfo.processInfo.processIdentifier)]
        task.environment = env
        task.currentDirectoryURL = URL(fileURLWithPath: outdir)

        let logURL = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("chemfigstudio-server.log")
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        if let handle = try? FileHandle(forWritingTo: logURL) {
            task.standardOutput = handle
            task.standardError = handle
        }

        do {
            try task.run()
            server = task
            appendStartupLog("已启动服务 pid=\(task.processIdentifier) port=\(port) outdir=\(outdir) python=\(python)")
        } catch {
            appendStartupLog("启动失败：\(error.localizedDescription)")
            showFailure("无法启动绘图服务",
                        "\(error.localizedDescription)<br>输出目录：<code>\(outdir)</code>")
            return
        }

        waitForServer(attemptsLeft: 60)
    }

    /// 轮询 /api/health，通了就加载界面。
    private func waitForServer(attemptsLeft: Int) {
        if attemptsLeft <= 0 {
            showFailure("服务启动超时",
                        "本地服务没有在预期时间内就绪。日志：\(NSTemporaryDirectory())chemfigstudio-server.log")
            return
        }
        guard let url = URL(string: "http://127.0.0.1:\(port)/api/health") else { return }
        var request = URLRequest(url: url)
        request.timeoutInterval = 2

        URLSession.shared.dataTask(with: request) { [weak self] data, response, _ in
            let ok = (response as? HTTPURLResponse)?.statusCode == 200 && data != nil
            DispatchQueue.main.async {
                guard let self = self else { return }
                if ok {
                    self.webView.load(URLRequest(url: URL(string: "http://127.0.0.1:\(self.port)/")!))
                } else {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) {
                        self.waitForServer(attemptsLeft: attemptsLeft - 1)
                    }
                }
            }
        }.resume()
    }

    private func stopServer() {
        guard let task = server, task.isRunning else { return }
        task.terminate()                                  // 先礼后兵
        let deadline = Date().addingTimeInterval(2.0)
        while task.isRunning && Date() < deadline {
            usleep(50_000)
        }
        if task.isRunning { kill(task.processIdentifier, SIGKILL) }
    }

    /// 输出目录优先级：CHEMFIG_OUTDIR 环境变量 > ~/Documents/chemfigstudio > 临时目录。
    /// 逐个尝试创建，全都失败时退回临时目录（宁可放临时目录，也不要启动不了）。
    private func resolveOutdir() -> String {
        if let custom = ProcessInfo.processInfo.environment["CHEMFIG_OUTDIR"], !custom.isEmpty {
            let path = (custom as NSString).expandingTildeInPath
            if ensureDirectory(path) { return path }
        }
        let home = FileManager.default.homeDirectoryForCurrentUser
        let preferred = home.appendingPathComponent("Documents/chemfigstudio").path
        if ensureDirectory(preferred) { return preferred }

        let fallback = (NSTemporaryDirectory() as NSString).appendingPathComponent("chemfigstudio")
        _ = ensureDirectory(fallback)
        return fallback
    }

    private func ensureDirectory(_ path: String) -> Bool {
        var isDir: ObjCBool = false
        if FileManager.default.fileExists(atPath: path, isDirectory: &isDir) {
            return isDir.boolValue && FileManager.default.isWritableFile(atPath: path)
        }
        do {
            try FileManager.default.createDirectory(atPath: path,
                                                    withIntermediateDirectories: true)
            return true
        } catch {
            appendStartupLog("无法创建目录 \(path)：\(error.localizedDescription)")
            return false
        }
    }

    /// 启动过程的诊断日志（网页里看不到终端输出，出问题时看这里）
    private func appendStartupLog(_ message: String) {
        let path = (NSTemporaryDirectory() as NSString)
            .appendingPathComponent("chemfigstudio-app.log")
        let line = "[\(Date())] \(message)\n"
        if let handle = FileHandle(forWritingAtPath: path) {
            handle.seekToEndOfFile()
            handle.write(line.data(using: .utf8)!)
            handle.closeFile()
        } else {
            try? line.write(toFile: path, atomically: true, encoding: .utf8)
        }
    }

    // ---------------------------------------------------------------- 提示页

    private func showHTML(title: String, body: String) {
        let html = """
        <!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
        <style>
          body{font:15px/1.6 -apple-system,"PingFang SC",Arial,sans-serif;color:#111827;
               background:#f5f5f7;margin:0;padding:56px 64px;}
          h1{font-size:22px;margin:0 0 12px;} code{background:#e5e7eb;padding:2px 6px;border-radius:5px;}
          p{max-width:640px;}
        </style></head><body><h1>\(title)</h1>\(body)</body></html>
        """
        webView.loadHTMLString(html, baseURL: nil)
    }

    private func showFailure(_ title: String, _ message: String) {
        showHTML(title: title, body: "<p>\(message)</p><p>网页版仍可用：在终端里执行 "
                 + "<code>make gui</code>，然后打开 <code>http://127.0.0.1:8765/</code>。</p>")
    }

    // ---------------------------------------------------------------- 菜单动作

    @objc private func reload() {
        webView.load(URLRequest(url: URL(string: "http://127.0.0.1:\(port)/")!))
    }

    @objc private func openOutdir() {
        NSWorkspace.shared.open(URL(fileURLWithPath: outdir))
    }

    @objc private func showAbout() {
        let alert = NSAlert()
        alert.messageText = "\(appName) \(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "")"
        alert.informativeText = """
        课本风格化学图生成器：示性式 + 氧上孤对电子。

        界面是本地网页（127.0.0.1），所以网页版与本应用功能完全一致。
        输出目录：\(outdir)
        """
        alert.runModal()
    }
}

// MARK: - 启动

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = AppDelegate()
app.delegate = delegate
app.run()
