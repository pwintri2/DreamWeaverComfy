use std::{
    net::{SocketAddr, TcpStream},
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::Duration,
};

use tauri::{Manager, WindowEvent};

struct ServerState {
    child: Mutex<Option<Child>>,
}

const SERVER_PORT: u16 = 8791;

fn port_is_open(port: u16) -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    TcpStream::connect_timeout(&address, Duration::from_millis(250)).is_ok()
}

fn wait_for_port(port: u16) {
    for _ in 0..50 {
        if port_is_open(port) {
            return;
        }
        thread::sleep(Duration::from_millis(150));
    }
}

pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let mut child = None;
            if !port_is_open(SERVER_PORT) {
                let resource_dir = app.path().resource_dir()?;
                let direct_server_path = resource_dir.join("server.py");
                let bundled_server_path = resource_dir.join("_up_").join("server.py");
                let server_path = if direct_server_path.exists() {
                    direct_server_path
                } else {
                    bundled_server_path
                };
                let spawned = Command::new("python3")
                    .arg("-u")
                    .arg(server_path)
                    .arg("--host")
                    .arg("127.0.0.1")
                    .arg("--port")
                    .arg(SERVER_PORT.to_string())
                    .env("COMFYUI_PATH", "/home/pwintri2/ComfyUI")
                    .env("COMFYUI_URL", "http://127.0.0.1:8188")
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .spawn()?;
                child = Some(spawned);
                wait_for_port(SERVER_PORT);
            }

            app.manage(ServerState {
                child: Mutex::new(child),
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
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Dreamweaver Comfy");
}
