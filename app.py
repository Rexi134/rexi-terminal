#!/usr/bin/env python3
import eventlet
eventlet.monkey_patch()

import os, sys, time, shutil, pty, select, signal, tempfile, threading, requests, base64, subprocess, json
from flask import Flask, render_template_string, request, jsonify
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet', ping_timeout=60)

# ─────────────────────────── CONFIG ───────────────────────────
GITHUB_TOKEN = "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" 
GITHUB_USER = "Rexi134"
REPO_NAME = "rexi-backup"
GITHUB_REPO = f"{GITHUB_USER}/{REPO_NAME}"

WORKSPACE_PATH = tempfile.mkdtemp(prefix="rexi_terminal_")
AUTOSTART_FILE = os.path.join(WORKSPACE_PATH, "autostart.json")
LOG_FILE = os.path.join(WORKSPACE_PATH, "bot_logs.txt")

active_terminal = None
workspace_last_seen = time.time()
autostart_processes = []

# ─────────────────── GITHUB BACKUP SYSTEM ─────────────────────
def github_api(method, path, data=None):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/{path}"
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    if method == "GET":
        return requests.get(url, headers=headers)
    return requests.put(url, headers=headers, json=data)

def ensure_repo_exists():
    url = f"https://api.github.com/repos/{GITHUB_REPO}"
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    if requests.get(url, headers=headers).status_code == 404:
        requests.post("https://api.github.com/user/repos", headers=headers, json={"name": REPO_NAME, "private": True})

def backup_workspace():
    while True:
        time.sleep(30) # Backup every 30 seconds to be safe
        if not GITHUB_TOKEN.startswith("ghp_"): continue
        try:
            res = github_api("GET", "contents/")
            repo_files = {}
            if res.status_code == 200:
                for item in res.json():
                    repo_files[item["name"]] = item["sha"]

            for filename in os.listdir(WORKSPACE_PATH):
                filepath = os.path.join(WORKSPACE_PATH, filename)
                # Now we backup EVERYTHING except the log file and python cache
                if not os.path.isfile(filepath) or filename.startswith('.') or filename == "bot_logs.txt" or filename.endswith(".pyc"): 
                    continue
                
                with open(filepath, "rb") as f:
                    content = base64.b64encode(f.read()).decode()
                
                data = {"message": f"Auto-backup {filename}", "content": content}
                if filename in repo_files:
                    data["sha"] = repo_files[filename]
                github_api("PUT", f"contents/{filename}", data)
        except: pass

def restore_workspace():
    if not GITHUB_TOKEN.startswith("ghp_"): return
    try:
        res = github_api("GET", "contents/")
        if res.status_code == 200:
            for item in res.json():
                if item["type"] == "file":
                    dl = requests.get(item["download_url"])
                    with open(os.path.join(WORKSPACE_PATH, item["name"]), "wb") as f:
                        f.write(dl.content)
    except: pass

# ─────────────────── AUTO HOST SYSTEM ─────────────────────────
def start_autostart_bots():
    global autostart_processes
    if not os.path.exists(AUTOSTART_FILE):
        with open(AUTOSTART_FILE, "w") as f: json.dump([], f)
        return
        
    try:
        with open(AUTOSTART_FILE, "r") as f:
            cmds = json.load(f)
            
        for cmd in cmds:
            with open(LOG_FILE, "a") as log:
                log.write(f"\n\n--- [SYSTEM BOOT] Starting: {cmd} ---\n")
                p = subprocess.Popen(cmd, shell=True, cwd=WORKSPACE_PATH, stdout=log, stderr=log, preexec_fn=os.setsid)
                autostart_processes.append({"cmd": cmd, "pid": p.pid})
            print(f"[+] Auto-started: {cmd}")
    except Exception as e:
        print(f"Auto-start error: {e}")

ensure_repo_exists()
threading.Thread(target=backup_workspace, daemon=True).start()
threading.Thread(target=restore_workspace, daemon=True).start()
threading.Timer(10.0, start_autostart_bots).start() # Wait 10 seconds for files to download before hosting

# ─────────────────────── CLEANUP WATCHDOG ────────────────────
def cleanup_watchdog():
    global active_terminal
    while True:
        time.sleep(10)
        if active_terminal and time.time() - active_terminal["last_ping"] > 7200:
            try: os.kill(active_terminal["pid"], signal.SIGKILL)
            except: pass
            try: os.close(active_terminal["fd"])
            except: pass
            active_terminal = None

threading.Thread(target=cleanup_watchdog, daemon=True).start()

# ─────────────────────── HTML TEMPLATE ───────────────────────
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
        #terminal-container { height: calc(100vh - 200px); width: 100%; padding: 10px; border-radius: 0 0 16px 16px; }
        .xterm-viewport::-webkit-scrollbar { width: 6px; }
        .xterm-viewport::-webkit-scrollbar-thumb { background: #4b5563; border-radius: 3px; }
    </style>
</head>
<body class="h-screen flex flex-col relative">
    <div class="stars"></div>
    
    <div class="glass px-4 md:px-6 py-4 rounded-b-3xl z-10 flex flex-wrap gap-2 justify-between items-center shadow-2xl border-t-0">
        <div class="flex items-center gap-3">
            <div class="w-3 h-3 rounded-full bg-green-500 animate-pulse"></div>
            <h1 class="text-lg md:text-xl font-bold tracking-wider text-indigo-300">REXI CLOUD</h1>
        </div>
        <div class="flex flex-wrap gap-2">
            <button onclick="document.getElementById('fileInput').click()" class="bg-indigo-600/20 hover:bg-indigo-600 border border-indigo-500/50 text-indigo-300 hover:text-white px-3 py-2 rounded-lg transition-all text-xs font-bold flex items-center gap-1">
                📁 Upload
            </button>
            <input type="file" id="fileInput" class="hidden" onchange="uploadFile()">
            
            <button onclick="openFileManager()" class="bg-purple-600/20 hover:bg-purple-600 border border-purple-500/50 text-purple-300 hover:text-white px-3 py-2 rounded-lg transition-all text-xs font-bold flex items-center gap-1">
                📝 Files
            </button>

            <button onclick="openAutoHost()" class="bg-green-600/20 hover:bg-green-600 border border-green-500/50 text-green-300 hover:text-white px-3 py-2 rounded-lg transition-all text-xs font-bold flex items-center gap-1">
                🚀 Auto-Host
            </button>
            
            <button onclick="sendCtrlC()" class="bg-yellow-600/20 hover:bg-yellow-600 border border-yellow-500/50 text-yellow-300 hover:text-white px-3 py-2 rounded-lg transition-all text-xs font-bold flex items-center gap-1">
                🛑 Ctrl+C
            </button>
        </div>
    </div>

    <div class="flex-1 p-4 z-10">
        <div class="glass rounded-2xl h-full flex flex-col overflow-hidden shadow-[0_0_50px_rgba(79,70,229,0.1)] border-indigo-500/20">
            <div class="bg-gray-900/80 px-4 py-2 flex items-center justify-between border-b border-gray-800">
                <span class="text-xs text-indigo-400 font-mono tracking-widest">MASTER TERMINAL</span>
                <span class="text-xs text-gray-500 font-mono">Status: <span class="text-green-400" id="statusText">Connected</span></span>
            </div>
            <div id="terminal-container" onclick="focusTerm()"></div>
        </div>
    </div>

    <!-- File Manager Modal -->
    <div id="fileManagerModal" class="fixed inset-0 bg-black/80 z-50 flex items-center justify-center hidden backdrop-blur-sm">
        <div class="glass w-11/12 md:w-3/4 max-w-2xl h-3/4 rounded-2xl flex flex-col overflow-hidden border-indigo-500/30">
            <div class="px-6 py-4 border-b border-gray-700 flex justify-between items-center">
                <h2 class="text-xl font-bold text-purple-400">📝 File Manager</h2>
                <button onclick="closeFileManager()" class="text-gray-400 hover:text-white text-2xl">&times;</button>
            </div>
            <div class="flex-1 flex overflow-hidden">
                <div id="fileListDiv" class="w-1/3 border-r border-gray-700 overflow-y-auto p-2"></div>
                <div class="w-2/3 flex flex-col">
                    <div id="editorHeader" class="p-2 border-b border-gray-700 text-sm text-gray-400 font-mono hidden">Select a file to edit</div>
                    <textarea id="codeEditor" class="flex-1 bg-transparent text-white font-mono text-sm p-4 focus:outline-none resize-none hidden"></textarea>
                    <div id="editorFooter" class="p-3 border-t border-gray-700 flex justify-end gap-2 hidden">
                        <button onclick="saveFile()" class="bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded-lg font-bold text-sm">Save File</button>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Auto-Host Modal -->
    <div id="autoHostModal" class="fixed inset-0 bg-black/80 z-50 flex items-center justify-center hidden backdrop-blur-sm">
        <div class="glass w-11/12 md:w-3/4 max-w-2xl h-3/4 rounded-2xl flex flex-col overflow-hidden border-green-500/30">
            <div class="px-6 py-4 border-b border-gray-700 flex justify-between items-center">
                <h2 class="text-xl font-bold text-green-400">🚀 Auto-Host Engine</h2>
                <button onclick="closeAutoHost()" class="text-gray-400 hover:text-white text-2xl">&times;</button>
            </div>
            <div class="p-4 flex gap-2 border-b border-gray-700">
                <input type="text" id="hostCmd" placeholder="e.g. python tg_bot.py" class="flex-1 bg-gray-900 border border-gray-700 rounded-lg p-3 text-white text-sm focus:outline-none focus:border-green-500">
                <button onclick="addAutoHost()" class="bg-green-600 hover:bg-green-500 text-white font-bold px-6 rounded-lg">Add</button>
            </div>
            <div class="flex-1 flex overflow-hidden">
                <div class="w-1/2 border-r border-gray-700 overflow-y-auto p-4">
                    <h3 class="text-sm font-bold text-gray-400 mb-2">Active Commands</h3>
                    <div id="autoHostList" class="space-y-2"></div>
                </div>
                <div class="w-1/2 p-4 flex flex-col">
                    <h3 class="text-sm font-bold text-gray-400 mb-2">Live Logs</h3>
                    <pre id="hostLogs" class="flex-1 bg-black text-green-400 font-mono text-xs overflow-y-auto whitespace-pre-wrap rounded p-2"></pre>
                </div>
            </div>
        </div>
    </div>

    <script>
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
        
        function focusTerm() { term.focus(); }
        setTimeout(focusTerm, 500);
        document.getElementById('terminal-container').addEventListener('touchstart', focusTerm, {passive: true});
        document.getElementById('terminal-container').addEventListener('click', focusTerm, {passive: true});

        const socket = io();

        socket.on('connect', () => {
            document.getElementById('statusText').innerText = "Connected";
            document.getElementById('statusText').className = "text-green-400";
            socket.emit('join_master');
            socket.emit('start_pty');
            setInterval(() => { socket.emit('heartbeat'); }, 10000);
        });

        socket.on('disconnect', () => {
            document.getElementById('statusText').innerText = "Disconnected";
            document.getElementById('statusText').className = "text-red-400";
        });

        socket.on('pty_output', (data) => { term.write(data); });
        socket.on('upload_success', (filename) => { term.write(`\\r\\n\\x1b[1;32m[+] File ${filename} uploaded!\\x1b[0m\\r\\n`); });
        socket.on('upload_error', (err) => { term.write(`\\r\\n\\x1b[1;31m[!] Upload failed: ${err}\\x1b[0m\\r\\n`); });

        term.onData((data) => { socket.emit('pty_input', data); });

        function sendCtrlC() { socket.emit('pty_input', '\\x03'); term.focus(); }

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

        // ── FILE MANAGER ──
        let currentFile = null;
        function openFileManager() { document.getElementById('fileManagerModal').classList.remove('hidden'); loadFiles(); }
        function closeFileManager() { document.getElementById('fileManagerModal').classList.add('hidden'); }

        function loadFiles() {
            fetch('/api/files').then(r => r.json()).then(files => {
                const listDiv = document.getElementById('fileListDiv');
                listDiv.innerHTML = '';
                if (files.length === 0) { listDiv.innerHTML = '<div class="text-gray-500 text-xs text-center mt-4">No files.</div>'; return; }
                files.forEach(f => {
                    listDiv.innerHTML += `
                        <div class="flex justify-between items-center p-2 hover:bg-gray-800 rounded cursor-pointer group" onclick="loadFile('${f}')">
                            <span class="text-sm font-mono text-indigo-300">${f}</span>
                            <button onclick="event.stopPropagation(); deleteFile('${f}')" class="text-red-500 opacity-0 group-hover:opacity-100 p-1">✖</button>
                        </div>`;
                });
            });
        }

        function loadFile(filename) {
            currentFile = filename;
            fetch('/api/read?file=' + encodeURIComponent(filename)).then(r => r.json()).then(res => {
                if (res.success) {
                    document.getElementById('editorHeader').innerText = "Editing: " + filename;
                    document.getElementById('editorHeader').classList.remove('hidden');
                    const editor = document.getElementById('codeEditor');
                    editor.value = res.content;
                    editor.classList.remove('hidden');
                    document.getElementById('editorFooter').classList.remove('hidden');
                } else { alert("Error: " + res.error); }
            });
        }

        function saveFile() {
            if (!currentFile) return;
            const content = document.getElementById('codeEditor').value;
            fetch('/api/save', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({file: currentFile, content: content}) })
            .then(r => r.json()).then(res => { if (res.success) alert("Saved!"); else alert("Error: " + res.error); });
        }

        function deleteFile(filename) {
            if (!confirm("Delete " + filename + "?")) return;
            fetch('/api/delete', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({file: filename}) })
            .then(r => r.json()).then(res => {
                if (res.success) { loadFiles(); if (currentFile === filename) { document.getElementById('editorHeader').classList.add('hidden'); document.getElementById('codeEditor').classList.add('hidden'); document.getElementById('editorFooter').classList.add('hidden'); } }
            });
        }

        // ── AUTO-HOST ──
        function openAutoHost() { document.getElementById('autoHostModal').classList.remove('hidden'); loadAutoHost(); }
        function closeAutoHost() { document.getElementById('autoHostModal').classList.add('hidden'); }

        function loadAutoHost() {
            fetch('/api/autohost').then(r => r.json()).then(data => {
                const list = document.getElementById('autoHostList');
                list.innerHTML = '';
                if (data.length === 0) { list.innerHTML = '<div class="text-gray-500 text-xs">No auto-host commands active.</div>'; return; }
                data.forEach(c => {
                    list.innerHTML += `
                        <div class="flex justify-between items-center bg-gray-800 p-2 rounded text-sm border border-gray-700">
                            <span class="text-green-400 font-mono text-xs">${c}</span>
                            <button onclick="removeAutoHost('${c}')" class="text-red-500 hover:text-red-400 font-bold px-2">✖</button>
                        </div>`;
                });
            });
            fetch('/api/logs').then(r => r.text()).then(logs => {
                const logBox = document.getElementById('hostLogs');
                logBox.innerText = logs;
                logBox.scrollTop = logBox.scrollHeight;
            });
        }

        function addAutoHost() {
            const cmd = document.getElementById('hostCmd').value.trim();
            if (!cmd) return;
            fetch('/api/autohost', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({cmd: cmd, action: 'add'}) })
            .then(() => { document.getElementById('hostCmd').value = ''; loadAutoHost(); });
        }

        function removeAutoHost(cmd) {
            fetch('/api/autohost', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({cmd: cmd, action: 'remove'}) })
            .then(() => loadAutoHost());
        }

        setInterval(() => {
            if(!document.getElementById('autoHostModal').classList.contains('hidden')) {
                fetch('/api/logs').then(r => r.text()).then(logs => {
                    const logBox = document.getElementById('hostLogs');
                    logBox.innerText = logs;
                    logBox.scrollTop = logBox.scrollHeight;
                });
            }
        }, 3000);
    </script>
</body>
</html>
"""

# ─────────────────────── ROUTES ──────────────────────────────
@app.route('/')
def index():
    return render_template_string(TERMINAL_HTML)

@app.route('/upload', methods=['POST'])
def upload():
    file = request.files.get('file')
    if not file: return jsonify({"success": False, "error": "No file"})
    filepath = os.path.join(WORKSPACE_PATH, file.filename)
    file.save(filepath)
    return jsonify({"success": True})

@app.route('/api/files')
def api_files():
    # Hide the log and json to keep the UI clean
    files = [f for f in os.listdir(WORKSPACE_PATH) if os.path.isfile(os.path.join(WORKSPACE_PATH, f)) and not f.startswith('.') and f not in ('bot_logs.txt', 'autostart.json')]
    return jsonify(files)

@app.route('/api/read')
def api_read():
    filename = request.args.get('file')
    path = os.path.join(WORKSPACE_PATH, filename)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return jsonify({"success": True, "content": f.read()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/save', methods=['POST'])
def api_save():
    data = request.json
    path = os.path.join(WORKSPACE_PATH, data['file'])
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(data['content'])
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/delete', methods=['POST'])
def api_delete():
    filename = request.json.get('file')
    path = os.path.join(WORKSPACE_PATH, filename)
    try:
        os.remove(path)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/autohost', methods=['GET', 'POST'])
def api_autohost():
    global autostart_processes
    if request.method == 'GET':
        if not os.path.exists(AUTOSTART_FILE): return jsonify([])
        with open(AUTOSTART_FILE, 'r') as f: return jsonify(json.load(f))
        
    data = request.json
    action = data.get('action')
    cmd = data.get('cmd')
    
    cmds = []
    if os.path.exists(AUTOSTART_FILE):
        with open(AUTOSTART_FILE, 'r') as f: cmds = json.load(f)
        
    if action == 'add' and cmd not in cmds:
        cmds.append(cmd)
        with open(AUTOSTART_FILE, 'w') as f: json.dump(cmds, f)
        
        with open(LOG_FILE, "a") as log:
            log.write(f"\n\n--- [MANUAL] Starting: {cmd} ---\n")
            p = subprocess.Popen(cmd, shell=True, cwd=WORKSPACE_PATH, stdout=log, stderr=log, preexec_fn=os.setsid)
            autostart_processes.append({"cmd": cmd, "pid": p.pid})
            
    elif action == 'remove' and cmd in cmds:
        cmds.remove(cmd)
        with open(AUTOSTART_FILE, 'w') as f: json.dump(cmds, f)
        
        for p_info in autostart_processes:
            if p_info["cmd"] == cmd:
                try: os.killpg(os.getpgid(p_info["pid"]), signal.SIGKILL)
                except: pass
        autostart_processes = [p for p in autostart_processes if p["cmd"] != cmd]
        
    return jsonify({"success": True})

@app.route('/api/logs')
def api_logs():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r', errors='ignore') as f:
            return f.read()[-10000:]
    return ""

# ─────────────────────── WEBSOCKET LOGIC ─────────────────────
@socketio.on('join_master')
def on_join():
    join_room("master_terminal")
    emit('pty_output', '\r\n\x1b[1;32m[+] Connected to Master Terminal.\x1b[0m\r\n', room="master_terminal")

@socketio.on('start_pty')
def start_pty():
    global active_terminal
    if active_terminal:
        active_terminal["last_ping"] = time.time()
        return

    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(WORKSPACE_PATH)
        os.execvp("bash", ["bash", "-i"])
    else:
        active_terminal = {"fd": fd, "pid": pid, "last_ping": time.time()}
        os.write(fd, b"PS1='\\[\\e[1;34m\\][rexi@cloud \\W]\\$ \\[\\e[0m\\]'\n")
        os.write(fd, b"clear\n")

        def read_pty():
            global active_terminal
            while active_terminal:
                try:
                    r, _, _ = select.select([fd], [], [], 0.1)
                    if fd in r:
                        output = os.read(fd, 1024)
                        if not output: break
                        socketio.emit('pty_output', output.decode('utf-8', errors='replace'), room="master_terminal")
                except: break
            active_terminal = None
            socketio.emit('pty_output', '\r\n\x1b[1;31m[!] Terminal closed.\x1b[0m\r\n', room="master_terminal")
        socketio.start_background_task(read_pty)

@socketio.on('pty_input')
def on_pty_input(data):
    if active_terminal:
        try: os.write(active_terminal["fd"], data.encode('utf-8'))
        except: pass

@socketio.on('heartbeat')
def on_heartbeat():
    if active_terminal: active_terminal["last_ping"] = time.time()

@socketio.on('show_upload_success')
def show_upload_success(filename): emit('upload_success', filename, room="master_terminal")

@socketio.on('show_upload_error')
def show_upload_error(error): emit('upload_error', error, room="master_terminal")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)
