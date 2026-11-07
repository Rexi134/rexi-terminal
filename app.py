#!/usr/bin/env python3
"""
REXI'S CLOUD TERMINAL & HOSTING PANEL
"""
import os, sys, time, uuid, shutil, pty, select, signal, tempfile, threading
from flask import Flask, render_template_string, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.secret_key = "REXI_SUPREME_SECRET_99"
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024 # 50MB max upload

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet', ping_timeout=30)

ACCESS_KEY = "Rexi_Terminal_x60"

# Global storage
workspaces = {}         # { ws_id: directory_path }
workspace_last_seen = {}# { ws_id: timestamp }
active_terminals = {}   # { socket_id: { fd, pid, ws_id, last_ping } }

# ─────────────────────── CLEANUP WATCHDOG ────────────────────
def cleanup_watchdog():
    """Kills processes and deletes files if the browser tab is closed."""
    while True:
        time.sleep(5)
        now = time.time()
        
        # 1. Kill stalled terminals
        for sid in list(active_terminals.keys()):
            sess = active_terminals[sid]
            if now - sess["last_ping"] > 15:
                print(f"[!] Killing stalled terminal {sid}")
                try: os.kill(sess["pid"], signal.SIGKILL)
                except: pass
                try: os.close(sess["fd"])
                except: pass
                del active_terminals[sid]

        # 2. Delete files for closed workspaces
        for ws_id in list(workspaces.keys()):
            if now - workspace_last_seen.get(ws_id, 0) > 15:
                print(f"[!] Deleting workspace {ws_id}")
                shutil.rmtree(workspaces[ws_id], ignore_errors=True)
                del workspaces[ws_id]
                if ws_id in workspace_last_seen: del workspace_last_seen[ws_id]

threading.Thread(target=cleanup_watchdog, daemon=True).start()

# ─────────────────────── HTML TEMPLATES ──────────────────────
LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>REXI CLOUD | Access</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;700&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Space Grotesk', sans-serif; background: #000; color: white; overflow: hidden; }
        .stars { position: absolute; width: 100%; height: 100%; background: url('https://www.transparenttextures.com/patterns/stardust.png'); opacity: 0.3; z-index: -1; }
        .glow { box-shadow: 0 0 30px rgba(99, 102, 241, 0.4); }
    </style>
</head>
<body class="flex items-center justify-center h-screen relative">
    <div class="stars"></div>
    <div class="bg-gray-900/60 backdrop-blur-xl border border-gray-700/50 p-10 rounded-3xl shadow-2xl w-96 relative z-10">
        <div class="text-center mb-8">
            <div class="w-20 h-20 mx-auto bg-indigo-500/20 rounded-full flex items-center justify-center border border-indigo-500/50 mb-4 glow">
                <span class="text-4xl">🚀</span>
            </div>
            <h1 class="text-2xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-indigo-400 to-purple-400">REXI TERMINAL</h1>
            <p class="text-xs text-gray-400 mt-2">Secure Cloud Environment</p>
        </div>
        <form method="POST">
            <input type="password" name="key" placeholder="Enter Access Key" class="w-full bg-gray-950/50 border border-gray-600 rounded-xl p-4 text-white mb-6 focus:outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20 transition-all text-center tracking-widest font-mono" required>
            {% if error %}<p class="text-red-400 text-sm text-center mb-4">{{ error }}</p>{% endif %}
            <button type="submit" class="w-full bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white font-bold py-4 rounded-xl shadow-lg transition-all active:scale-95">INITIALIZE</button>
        </form>
    </div>
</body>
</html>
"""

TERMINAL_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>REXI CLOUD TERMINAL</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;700&family=JetBrains+Mono&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css" />
    <script src="https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.js"></script>
    <script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
    <style>
        body { font-family: 'Space Grotesk', sans-serif; background: #000; color: white; overflow: hidden; }
        .stars { position: absolute; width: 100%; height: 100%; background: url('https://www.transparenttextures.com/patterns/stardust.png'); opacity: 0.2; z-index: 0; pointer-events: none; }
        .glass { background: rgba(10, 10, 15, 0.7); backdrop-filter: blur(16px); border: 1px solid rgba(255,255,255,0.1); }
        #terminal-container { height: calc(100vh - 180px); width: 100%; padding: 10px; border-radius: 0 0 16px 16px; }
        .xterm-viewport::-webkit-scrollbar { width: 6px; }
        .xterm-viewport::-webkit-scrollbar-thumb { background: #4b5563; border-radius: 3px; }
    </style>
</head>
<body class="h-screen flex flex-col relative">
    <div class="stars"></div>
    
    <div class="glass px-6 py-4 rounded-b-3xl z-10 flex justify-between items-center shadow-2xl border-t-0">
        <div class="flex items-center gap-3">
            <div class="w-3 h-3 rounded-full bg-green-500 animate-pulse"></div>
            <h1 class="text-xl font-bold tracking-wider text-indigo-300">REXI CLOUD</h1>
        </div>
        <div class="flex gap-4">
            <button onclick="document.getElementById('fileInput').click()" class="bg-indigo-600/20 hover:bg-indigo-600 border border-indigo-500/50 text-indigo-300 hover:text-white px-4 py-2 rounded-lg transition-all text-sm font-bold flex items-center gap-2">
                📁 Upload File
            </button>
            <input type="file" id="fileInput" class="hidden" onchange="uploadFile()">
            <a href="/logout" class="bg-red-500/20 hover:bg-red-500 border border-red-500/50 text-red-300 hover:text-white px-4 py-2 rounded-lg transition-all text-sm font-bold">Terminate</a>
        </div>
    </div>

    <div class="flex-1 p-4 z-10">
        <div class="glass rounded-2xl h-full flex flex-col overflow-hidden shadow-[0_0_50px_rgba(79,70,229,0.1)] border-indigo-500/20">
            <div class="bg-gray-900/80 px-4 py-2 flex items-center justify-between border-b border-gray-800">
                <span class="text-xs text-indigo-400 font-mono tracking-widest">TERMINAL // ISOLATED WORKSPACE</span>
                <span class="text-xs text-gray-500 font-mono">Status: <span class="text-green-400">Connected</span></span>
            </div>
            <div id="terminal-container"></div>
        </div>
    </div>

    <script>
        const ws_id = "{{ session.get('ws_id', '') }}";
        
        const term = new Terminal({
            cursorBlink: true,
            theme: { background: 'transparent', foreground: '#e2e8f0', cursor: '#6366f1', selectionBackground: '#4f46e5' },
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 14
        });
        const fitAddon = new FitAddon.FitAddon();
        term.loadAddon(fitAddon);
        term.open(document.getElementById('terminal-container'));
        fitAddon.fit();

        const socket = io();

        socket.on('connect', () => {
            socket.emit('start_pty', { ws_id: ws_id });
            setInterval(() => { socket.emit('heartbeat', { ws_id: ws_id }); }, 3000);
        });

        socket.on('pty_output', (data) => { term.write(data); });
        socket.on('upload_success', (filename) => { term.write(`\\r\\n\\x1b[1;32m[+] File ${filename} uploaded!\\x1b[0m\\r\\n`); });
        socket.on('upload_error', (err) => { term.write(`\\r\\n\\x1b[1;31m[!] Upload failed: ${err}\\x1b[0m\\r\\n`); });

        term.onData((data) => { socket.emit('pty_input', data); });

        function uploadFile() {
            const fileInput = document.getElementById('fileInput');
            const file = fileInput.files[0];
            if (!file) return;

            term.write(`\\r\\n\\x1b[1;33m[~] Uploading ${file.name}...\\x1b[0m\\r\\n`);
            
            const formData = new FormData();
            formData.append('file', file);

            fetch('/upload', { method: 'POST', body: formData })
                .then(r => r.json())
                .then(res => {
                    if(res.success) socket.emit('show_upload_success', file.name);
                    else socket.emit('show_upload_error', res.error);
                }).catch(e => socket.emit('show_upload_error', e.message));
            
            fileInput.value = ''; 
        }
    </script>
</body>
</html>
"""

# ─────────────────────── ROUTES ──────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('key') == ACCESS_KEY:
            session['logged_in'] = True
            if 'ws_id' not in session:
                session['ws_id'] = str(uuid.uuid4())
            return redirect(url_for('index'))
        return render_template_string(LOGIN_HTML, error="Invalid Access Key")
    return render_template_string(LOGIN_HTML)

@app.route('/logout')
def logout():
    ws_id = session.get('ws_id')
    if ws_id in workspaces:
        shutil.rmtree(workspaces[ws_id], ignore_errors=True)
        del workspaces[ws_id]
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
def index():
    if not session.get('logged_in'): return redirect(url_for('login'))
    
    # Ensure workspace exists
    ws_id = session.get('ws_id')
    if ws_id not in workspaces:
        workspaces[ws_id] = tempfile.mkdtemp(prefix=f"rexi_ws_")
    workspace_last_seen[ws_id] = time.time()
    
    return render_template_string(TERMINAL_HTML)

@app.route('/upload', methods=['POST'])
def upload():
    if not session.get('logged_in'): return jsonify({"success": False, "error": "Unauthorized"})
    
    ws_id = session.get('ws_id')
    if ws_id not in workspaces:
        workspaces[ws_id] = tempfile.mkdtemp(prefix=f"rexi_ws_") # Auto-recover
        
    file = request.files.get('file')
    if not file: return jsonify({"success": False, "error": "No file"})
        
    filepath = os.path.join(workspaces[ws_id], file.filename)
    file.save(filepath)
    return jsonify({"success": True})

# ─────────────────────── WEBSOCKET LOGIC ─────────────────────
@socketio.on('start_pty')
def start_pty(data):
    sid = request.sid
    ws_id = data.get('ws_id')
    
    # Ensure workspace exists
    if ws_id not in workspaces:
        workspaces[ws_id] = tempfile.mkdtemp(prefix=f"rexi_ws_")
    workspace = workspaces[ws_id]
    workspace_last_seen[ws_id] = time.time()

    # Fork a real bash terminal
    pid, fd = pty.fork()
    
    if pid == 0:
        # CHILD PROCESS
        os.chdir(workspace)
        os.execvp("bash", ["bash", "-i"])
    else:
        # PARENT PROCESS
        active_terminals[sid] = {
            "fd": fd,
            "pid": pid,
            "ws_id": ws_id,
            "last_ping": time.time()
        }
        
        # Send initial prompt styling
        os.write(fd, b"PS1='\\[\\e[1;34m\\][rexi@cloud \\W]\\$ \\[\\e[0m\\]'\n")
        os.write(fd, b"clear\n")

        def read_pty():
            while sid in active_terminals:
                try:
                    r, _, _ = select.select([fd], [], [], 0.1)
                    if fd in r:
                        output = os.read(fd, 1024)
                        if not output: break
                        socketio.emit('pty_output', output.decode('utf-8', errors='replace'), room=sid)
                except:
                    break
                    
            if sid in active_terminals:
                del active_terminals[sid]
            socketio.emit('pty_output', '\r\n\x1b[1;31m[!] Connection closed.\x1b[0m\r\n', room=sid)

        socketio.start_background_task(read_pty)

@socketio.on('pty_input')
def on_pty_input(data):
    sid = request.sid
    if sid in active_terminals:
        fd = active_terminals[sid]["fd"]
        try:
            os.write(fd, data.encode('utf-8'))
        except:
            pass

@socketio.on('heartbeat')
def on_heartbeat(data):
    sid = request.sid
    ws_id = data.get('ws_id')
    
    if ws_id:
        workspace_last_seen[ws_id] = time.time()
    if sid in active_terminals:
        active_terminals[sid]["last_ping"] = time.time()

@socketio.on('show_upload_success')
def show_upload_success(filename):
    emit('upload_success', filename, room=request.sid)

@socketio.on('show_upload_error')
def show_upload_error(error):
    emit('upload_error', error, room=request.sid)

if __name__ == "__main__":
    print("[+] REXI CLOUD BOOTING...")
    print(f"[+] Access Key: {ACCESS_KEY}")
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)
