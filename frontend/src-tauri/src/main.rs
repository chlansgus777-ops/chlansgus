// MarketLens desktop shell: starts the Python backend sidecar and shows the UI.
// - The backend binds to 127.0.0.1 only, on a free port chosen at launch.
// - A random per-launch API token is passed to the backend (env) and to the UI (initialization script),
//   so other local programs and web pages cannot drive the API.
// - A second launch focuses the running window (single instance); the backend is restarted if it crashes
//   (bounded), and killed when the app exits. MarketLens never places orders.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpListener;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use tauri::{AppHandle, Manager, PhysicalPosition, PhysicalSize, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

const MAX_RESTARTS: u32 = 3;
// backend exit codes that must not trigger a restart (see backend/marketlens/workers/cli.py)
const EXIT_NOT_LOCAL: i32 = 2;
const EXIT_PORT_IN_USE: i32 = 3;
const EXIT_ALREADY_RUNNING: i32 = 4;
const EXIT_MIXED_DATABASE: i32 = 5; // database belongs to the other data mode (MOCK vs LIVE)

fn is_app_url(url: &tauri::Url) -> bool {
    matches!((url.scheme(), url.host_str(), url.port()),
        ("http" | "https", Some("tauri.localhost"), None) |
        ("tauri", Some("localhost"), None))
        || (cfg!(debug_assertions) && url.origin().ascii_serialization() == "http://localhost:5173")
}

struct Backend {
    child: Mutex<Option<CommandChild>>,
    port: u16,
    token: String,
    exiting: AtomicBool,
    restarts: AtomicU32,
}

fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(8765)
}

fn new_token() -> String {
    let mut buf = [0u8; 32];
    getrandom::getrandom(&mut buf).expect("OS random source unavailable");
    buf.iter().map(|b| format!("{b:02x}")).collect()
}

fn spawn_backend(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let state = app.state::<Backend>();
    let port = state.port.to_string();
    let cmd = app
        .shell()
        .sidecar("marketlens-backend")?
        .args(["serve", "--host", "127.0.0.1", "--port", port.as_str()])
        .env("MARKETLENS_API_TOKEN", state.token.as_str())
        // the backend exits by itself when this app process is gone (also covers crashes and the
        // PyInstaller bootloader child that a kill of the sidecar process would leave behind)
        .env("MARKETLENS_PARENT_PID", std::process::id().to_string());
    let (mut rx, child) = cmd.spawn()?;
    *state.child.lock().expect("backend lock poisoned") = Some(child);
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stderr(line) => eprintln!("[backend] {}", String::from_utf8_lossy(&line).trim_end()),
                CommandEvent::Terminated(payload) => {
                    let st = handle.state::<Backend>();
                    st.child.lock().expect("backend lock poisoned").take();
                    if st.exiting.load(Ordering::SeqCst) {
                        break;
                    }
                    let code = payload.code.unwrap_or(-1);
                    if code == EXIT_NOT_LOCAL || code == EXIT_PORT_IN_USE || code == EXIT_ALREADY_RUNNING || code == EXIT_MIXED_DATABASE {
                        eprintln!("marketlens-backend refused to start (exit {code}); not restarting");
                        break;
                    }
                    let n = st.restarts.fetch_add(1, Ordering::SeqCst);
                    if n < MAX_RESTARTS {
                        eprintln!("marketlens-backend exited (code {code}); restarting {}/{MAX_RESTARTS}", n + 1);
                        std::thread::sleep(Duration::from_secs(1));
                        if let Err(e) = spawn_backend(&handle) {
                            eprintln!("failed to restart marketlens-backend: {e}");
                        }
                    } else {
                        eprintln!("marketlens-backend keeps crashing; giving up after {MAX_RESTARTS} restarts");
                    }
                    break;
                }
                _ => {}
            }
        }
    });
    Ok(())
}

// ------------------------------------------------------------------ GLANCE MODE (a frameless desktop widget)
const GLANCE: &str = "glance";
const GLANCE_W: f64 = 320.0;
const GLANCE_H: f64 = 470.0;

fn api_init_script(app: &AppHandle) -> String {
    let st = app.state::<Backend>();
    format!("if (location.hostname === 'tauri.localhost' || (location.protocol === 'tauri:' && location.hostname === 'localhost') || ({} && location.origin === 'http://localhost:5173')) {{ window.__MARKETLENS_API__ = 'http://127.0.0.1:{}'; window.__MARKETLENS_TOKEN__ = '{}'; }}",
        cfg!(debug_assertions), st.port, st.token)
}

/// Last position, size and always-on-top of the Glance window (app config dir; restored on the next open).
#[derive(Clone, Copy)]
struct GlanceGeom { x: i32, y: i32, w: u32, h: u32, on_top: bool }

fn geom_path(app: &AppHandle) -> Option<std::path::PathBuf> {
    app.path().app_config_dir().ok().map(|d| d.join("glance.json"))
}

fn load_geom(app: &AppHandle) -> Option<GlanceGeom> {
    let raw = std::fs::read_to_string(geom_path(app)?).ok()?;
    let num = |k: &str| -> Option<i64> {
        let i = raw.find(&format!("\"{k}\":"))? + k.len() + 3;
        let rest = raw[i..].trim_start();
        let end = rest.find(|c: char| !(c == '-' || c.is_ascii_digit())).unwrap_or(rest.len());
        rest[..end].parse().ok()
    };
    Some(GlanceGeom { x: num("x")? as i32, y: num("y")? as i32, w: num("w")?.max(1) as u32, h: num("h")?.max(1) as u32,
                      on_top: !raw.contains("\"on_top\":false") })
}

fn save_geom(app: &AppHandle, g: GlanceGeom) {
    if let Some(p) = geom_path(app) {
        if let Some(dir) = p.parent() { let _ = std::fs::create_dir_all(dir); }
        let _ = std::fs::write(p, format!("{{\"x\":{},\"y\":{},\"w\":{},\"h\":{},\"on_top\":{}}}", g.x, g.y, g.w, g.h, g.on_top));
    }
}

/// A saved position is used only when the window's top strip lands on a monitor that is connected now
/// (a monitor unplugged since would otherwise put the widget off screen).
fn on_some_monitor(app: &AppHandle, g: &GlanceGeom) -> bool {
    let Some(w) = app.get_webview_window("main") else { return false };
    let Ok(monitors) = w.available_monitors() else { return false };
    monitors.iter().any(|m| {
        let (p, s) = (m.position(), m.size());
        let (cx, cy) = (g.x + (g.w as i32).min(200) / 2, g.y + 20);
        cx >= p.x && cx < p.x + s.width as i32 && cy >= p.y && cy < p.y + s.height as i32
    })
}

#[tauri::command]
fn glance_open(app: AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window(GLANCE) {
        let _ = w.unminimize();
        let _ = w.show();
        return w.set_focus().map_err(|e| e.to_string());
    }
    let saved = load_geom(&app);
    let on_top = saved.map(|g| g.on_top).unwrap_or(true);
    // the route is set by the init script (a '#' in the App path could be escaped into the file name)
    let init = format!("{} if (!location.hash || location.hash === '#/') {{ history.replaceState(null, '', '#/glance'); }}", api_init_script(&app));
    let w = WebviewWindowBuilder::new(&app, GLANCE, WebviewUrl::App("index.html".into()))
        .title("MarketLens Glance")
        .inner_size(GLANCE_W, GLANCE_H)
        .min_inner_size(280.0, 380.0)
        .max_inner_size(480.0, 760.0)
        .decorations(false)
        .transparent(true)
        .shadow(false)
        .resizable(true)
        .always_on_top(on_top)
        .initialization_script(&init)
        .on_navigation(is_app_url)
        .build()
        .map_err(|e| e.to_string())?;
    match saved {
        Some(g) if on_some_monitor(&app, &g) => {
            let _ = w.set_size(PhysicalSize::new(g.w, g.h));
            let _ = w.set_position(PhysicalPosition::new(g.x, g.y));
        }
        _ => {
            // first open (or the saved monitor is gone): the right edge of the main window's monitor
            if let Ok(Some(m)) = w.current_monitor() {
                let (p, s) = (m.position(), m.size());
                let ws = w.outer_size().unwrap_or(PhysicalSize::new(GLANCE_W as u32, GLANCE_H as u32));
                let _ = w.set_position(PhysicalPosition::new(p.x + s.width as i32 - ws.width as i32 - 24, p.y + 80));
            }
        }
    }
    let handle = app.clone();
    w.on_window_event(move |e| {
        if matches!(e, WindowEvent::Moved(_) | WindowEvent::Resized(_)) {
            if let Some(w) = handle.get_webview_window(GLANCE) {
                if let (Ok(p), Ok(s)) = (w.outer_position(), w.inner_size()) {
                    let on_top = load_geom(&handle).map(|g| g.on_top).unwrap_or(true);
                    save_geom(&handle, GlanceGeom { x: p.x, y: p.y, w: s.width, h: s.height, on_top });
                }
            }
        }
    });
    Ok(())
}

#[tauri::command]
fn glance_close(app: AppHandle) -> Result<(), String> {
    match app.get_webview_window(GLANCE) {
        Some(w) => w.close().map_err(|e| e.to_string()),
        None => Ok(()),
    }
}

#[tauri::command]
fn glance_set_on_top(app: AppHandle, on: bool) -> Result<(), String> {
    if let Some(w) = app.get_webview_window(GLANCE) {
        w.set_always_on_top(on).map_err(|e| e.to_string())?;
        let (p, s) = (w.outer_position().map_err(|e| e.to_string())?, w.inner_size().map_err(|e| e.to_string())?);
        save_geom(&app, GlanceGeom { x: p.x, y: p.y, w: s.width, h: s.height, on_top: on });
    }
    Ok(())
}

/// Analyze Mode at ``path`` (a route of the app, e.g. "/stocks/NVDA"): the main window, shown and focused.
#[tauri::command]
fn main_show(app: AppHandle, path: String) -> Result<(), String> {
    let w = app.get_webview_window("main").ok_or("main window missing")?;
    let _ = w.unminimize();
    let _ = w.show();
    let _ = w.set_focus();
    let safe: String = path.chars().filter(|c| c.is_ascii_alphanumeric() || "/.-_?=&".contains(*c)).collect();
    if safe.starts_with('/') {
        let _ = w.eval(&format!("if (document.querySelector('#root')) {{ location.hash = '#{safe}'; }}"));
    }
    Ok(())
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.unminimize();
                let _ = w.set_focus();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![glance_open, glance_close, glance_set_on_top, main_show])
        .setup(|app| {
            let port = free_port();
            let token = new_token();
            app.manage(Backend { child: Mutex::new(None), port, token: token.clone(), exiting: AtomicBool::new(false), restarts: AtomicU32::new(0) });
            // the UI learns where the backend is and the token before any script runs
            let init = format!("if (location.hostname === 'tauri.localhost' || (location.protocol === 'tauri:' && location.hostname === 'localhost') || ({} && location.origin === 'http://localhost:5173')) {{ window.__MARKETLENS_API__ = 'http://127.0.0.1:{port}'; window.__MARKETLENS_TOKEN__ = '{token}'; }}", cfg!(debug_assertions));
            let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("desktop-start.html".into()))
                .title("MarketLens")
                .inner_size(1500.0, 950.0)
                .min_inner_size(1100.0, 700.0)
                .initialization_script(&init)
                // External links use the scoped opener; don't expose the API token to remote pages.
                .on_navigation(is_app_url)
                .build()?;
            if let Err(error) = spawn_backend(app.handle()) {
                eprintln!("failed to start marketlens-backend: {error}");
                let _ = window.eval("window.marketlensStartupFailure?.('앱 서버를 실행하지 못했습니다. 앱을 닫고 다시 실행해주세요. 계속 실패하면 데이터 폴더의 logs를 확인해주세요.');");
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build MarketLens");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(state) = handle.try_state::<Backend>() {
                state.exiting.store(true, Ordering::SeqCst);
                if let Ok(mut guard) = state.child.lock() {
                    if let Some(child) = guard.take() {
                        let _ = child.kill();
                    }
                }
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::is_app_url;
    #[test]
    fn app_documents_can_navigate() {
        for url in ["http://tauri.localhost/index.html#/stocks", "https://tauri.localhost/desktop-start.html", "tauri://localhost/index.html"] {
            assert!(is_app_url(&url.parse().unwrap()));
        }
    }
    #[test]
    fn remote_and_local_server_documents_cannot_replace_the_app() {
        for url in ["https://saveticker.com/news", "http://127.0.0.1:8765/", "https://tauri.localhost.attacker.example/", "file:///C:/Windows/explorer.exe", "http://tauri.localhost:8000/"] {
            assert!(!is_app_url(&url.parse().unwrap()), "{url}");
        }
    }
}
