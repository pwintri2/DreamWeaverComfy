use std::{
    env,
    net::{SocketAddr, TcpListener, TcpStream},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::Duration,
};

use tauri::{Manager, WindowEvent};

struct ServerState {
    child: Mutex<Option<Child>>,
    port: u16,
}

const DEFAULT_SERVER_PORT: u16 = 8791;

fn port_is_open(port: u16) -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    TcpStream::connect_timeout(&address, Duration::from_millis(250)).is_ok()
}

fn find_available_port() -> std::io::Result<u16> {
    let listener = TcpListener::bind(("127.0.0.1", 0))?;
    Ok(listener.local_addr()?.port())
}

fn preferred_port() -> u16 {
    env::var("DREAMWEAVER_DESKTOP_PORT")
        .ok()
        .and_then(|value| value.parse::<u16>().ok())
        .filter(|port| *port > 0)
        .unwrap_or(DEFAULT_SERVER_PORT)
}

fn server_port() -> std::io::Result<u16> {
    let preferred = preferred_port();
    if !port_is_open(preferred) {
        return Ok(preferred);
    }
    find_available_port()
}

fn wait_for_port(port: u16) -> bool {
    for _ in 0..50 {
        if port_is_open(port) {
            return true;
        }
        thread::sleep(Duration::from_millis(150));
    }
    false
}

fn server_script_path(app: &tauri::App) -> tauri::Result<PathBuf> {
    let dev_server_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .map(|path| path.join("server.py"))
        .filter(|path| path.exists());
    if let Some(path) = dev_server_path {
        return Ok(path);
    }

    let resource_dir = app.path().resource_dir()?;
    let direct_server_path = resource_dir.join("server.py");
    if direct_server_path.exists() {
        return Ok(direct_server_path);
    }

    Ok(resource_dir.join("_up_").join("server.py"))
}

pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let port = server_port()?;
            let server_path = server_script_path(app)?;
            let server_dir = server_path
                .parent()
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("."));
            let spawned = Command::new("python3")
                .arg("-u")
                .arg(&server_path)
                .arg("--host")
                .arg("127.0.0.1")
                .arg("--port")
                .arg(port.to_string())
                .env("COMFYUI_PATH", env::var("COMFYUI_PATH").unwrap_or_else(|_| "/home/pwintri2/ComfyUI".into()))
                .env("COMFYUI_URL", env::var("COMFYUI_URL").unwrap_or_else(|_| "http://127.0.0.1:8188".into()))
                .current_dir(server_dir)
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()?;

            if !wait_for_port(port) {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::TimedOut,
                    format!("Dreamweaver backend did not start on port {port}"),
                )
                .into());
            }

            if let Some(window) = app.get_webview_window("main") {
                let url = format!("http://127.0.0.1:{port}/?v=0.2.4")
                    .parse()
                    .map_err(|error| std::io::Error::new(std::io::ErrorKind::InvalidInput, error))?;
                window.navigate(url)?;
            }

            app.manage(ServerState {
                child: Mutex::new(Some(spawned)),
                port,
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { .. } = event {
                let child = {
                    let state = window.app_handle().state::<ServerState>();
                    state.child.lock().ok().and_then(|mut guard| guard.take())
                };
                if let Some(mut child) = child {
                    let _ = child.kill();
                }
                let state = window.app_handle().state::<ServerState>();
                eprintln!("Dreamweaver backend stopped on port {}", state.port);
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Dreamweaver Comfy");
}
