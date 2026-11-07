#!/usr/bin/env python3
"""
REXI'S CLOUD TERMINAL & HOSTING PANEL
"""
import os, sys, time, uuid, shutil, subprocess, pty, select, signal, tempfile, threading
from flask import Flask, render_template_string, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.secret_key = "REXI_SUPREME_SECRET"
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024 # 50MB max upload

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

ACCESS_KEY = "Rexi_Terminal_x60"

# Tracks active sessions: { socket_sid: { "workspace": path, "pid": pid, "master_fd": fd, "last_ping": timestamp } }
active_sessions = {}

# ─────────────────────── CLEANUP WATCHDOG ────────────────────
def cleanup_watchdog():
    """Kills processes and deletes files if the browser tab is closed (no heartbeat for 15s)."""
    while True:
        time.sleep(5)
        now = time.time()
        for sid in list(active_sessions.keys()):
            sess = active_sessions[sid]
            # 15 seconds grace period for page refreshes
            if now - sess["last_ping"] > 15: 
                print(f"[!] Tab closed or timed out. Cleaning up workspace for {sid}")
                if "pid" in sess:
                    try: os.killpg(os.getpgid(sess["pid"]), signal.SIGKILL)
                    except: pass
                if os.path.exists(sess["workspace"]):
                    shutil.rmtree(sess["workspace"], ignore_errors=True)
                del active_sessions[sid]

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
    
    <!-- Top Bar -->
    <div class="glass px-6 py-4 rounded-b-3xl z-10 flex justify-between items-center shadow-2xl border-t-0">
        <div class="flex items-center gap-3">
            <div class="w-3 h-3 rounded-full bg-green-500 animate-pulse"></div>
            <h1 class="text-xl font-bold tracking-wider text-indigo-300">REXI CLOUD ENGINE</h1>
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
                <span class="text-xs text-gray-500 font-mono">Status: <span id="statusText" class="text-green-400">Connected</span></span>
            </div>
            <div id="terminal-container"></div>
        </div>
    </div>

    <script>
        const term = new Terminal({
            cursorBlink: true,
            theme: { background: 'transparent', foreground: '#e2e8f0', cursor: '#6366f1', selectionBackground: '#4f46e5' },
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 14,
            lineHeight: 1.2
        });
        const fitAddon = new FitAddon.FitAddon();
        term.loadAddon(fitAddon);
        term.open(document.getElementById('terminal-container'));
        fitAddon.fit();

        const socket = io();

        socket.on('connect', () => {
            term.write('\\r\\n\\x1b[1;35m[SYSTEM] Initializing secure cloud shell...\\x1b[0m\\r\\n');
            socket.emit('start_pty');
            
            // HEARTBEAT: Keeps the server alive as long as the tab is open
            setInterval(() => { socket.emit('heartbeat'); }, 3000);
        });

        socket.on('pty_output', (data) => { term.write(data); });
        socket.on('upload_success', (filename) => { term.write(`\\r\\n\\x1b[1;32m[+] File ${filename} uploaded successfully.\\x1b[0m\\r\\n`); });
        socket.on('upload_error', (err) => { term.write(`\\r\\n\\x1b[1;31m[!] Upload failed: ${err}\\x1b[0m\\r\\n`); });

        term.onData((data) => { socket.emit('pty_input', data); });

        window.addEventListener('resize', () => {
            fitAddon.fit();
        });

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
            # Create a unique workspace folder for this specific browser session
            if 'workspace' not in session:
                session['workspace'] = tempfile.mkdtemp(prefix="rexi_ws_")
            return redirect(url_for('index'))
        return render_template_string(LOGIN_HTML, error="Invalid Access Key")
    return render_template_string(LOGIN_HTML)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
def index():
    if not session.get('logged_in'): return redirect(url_for('login'))
    return render_template_string(TERMINAL_HTML)

@app.route('/upload', methods=['POST'])
def upload():
    if not session.get('logged_in'): return jsonify({"success": False, "error": "Unauthorized"})
    if 'file' not in request.files: return jsonify({"success": False, "error": "No file part"})
    
    file = request.files['file']
    if file.filename == '': return jsonify({"success": False, "error": "No selected file"})
    
    workspace = session.get('workspace')
    if not workspace or not os.path.exists(workspace):
        return jsonify({"success": False, "error": "Workspace expired. Please refresh the page."})
        
    filepath = os.path.join(workspace, file.filename)
    file.save(filepath)
    
    return jsonify({"success": True})

# ─────────────────────── WEBSOCKET LOGIC ─────────────────────
@socketio.on('connect')
def on_connect():
    pass

@socketio.on('start_pty')
def start_pty():
    sid = request.sid
    workspace = session.get('workspace')
    
    if not workspace:
        workspace = tempfile.mkdtemp(prefix="rexi_ws_")
        session['workspace'] = workspace

    active_sessions[sid] = {
        "workspace": workspace,
        "last_ping": time.time()
    }

    # Spawn bash in a pseudo-terminal
    master, slave = pty.openpty()
    p = subprocess.Popen(
        ['bash'], 
        stdin=slave, stdout=slave, stderr=slave,
        preexec_fn=os.setsid,
        cwd=workspace
    )
    os.close(slave)
    
    active_sessions[sid]["pid"] = p.pid
    active_sessions[sid]["master_fd"] = master  # Store FD to allow typing
    
    # Setup prompt
    os.write(master, b"PS1='\\[\\e[1;34m\\][rexi@cloud \\W]\\$ \\[\\e[0m\\]'\n")
    os.write(master, b"clear\n")

    def read_pty():
        while sid in active_sessions:
            try:
                r, _, _ = select.select([master], [], [], 0.1)
                if master in r:
                    data = os.read(master, 1024)
                    if not data: break
                    socketio.emit('pty_output', data.decode('utf-8', errors='replace'), room=sid)
            except:
                break
        socketio.emit('pty_output', '\r\n\x1b[1;31m[!] Session terminated.\x1b[0m\r\n', room=sid)

    socketio.start_background_task(read_pty)

@socketio.on('pty_input')
def on_pty_input(data):
    sid = request.sid
    if sid in active_sessions:
        master_fd = active_sessions[sid].get("master_fd")
        if master_fd:
            try: os.write(master_fd, data.encode('utf-8'))
            except: pass

@socketio.on('heartbeat')
def on_heartbeat():
    sid = request.sid
    if sid in active_sessions:
        active_sessions[sid]["last_ping"] = time.time()

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
