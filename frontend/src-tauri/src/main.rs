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

use tauri::{AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

const MAX_RESTARTS: u32 = 3;
// backend exit codes that must not trigger a restart (see backend/marketlens/workers/cli.py)
const EXIT_NOT_LOCAL: i32 = 2;
const EXIT_PORT_IN_USE: i32 = 3;
const EXIT_ALREADY_RUNNING: i32 = 4;

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
        .env("MARKETLENS_API_TOKEN", state.token.as_str());
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
                    if code == EXIT_NOT_LOCAL || code == EXIT_PORT_IN_USE || code == EXIT_ALREADY_RUNNING {
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

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.unminimize();
                let _ = w.set_focus();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let port = free_port();
            let token = new_token();
            app.manage(Backend { child: Mutex::new(None), port, token: token.clone(), exiting: AtomicBool::new(false), restarts: AtomicU32::new(0) });
            spawn_backend(app.handle())?;
            // the UI learns where the backend is and the token before any script runs
            let init = format!("window.__MARKETLENS_API__ = 'http://127.0.0.1:{port}'; window.__MARKETLENS_TOKEN__ = '{token}';");
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("MarketLens")
                .inner_size(1500.0, 950.0)
                .min_inner_size(1100.0, 700.0)
                .initialization_script(&init)
                .build()?;
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
