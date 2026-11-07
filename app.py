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
autostart_processes = []
backup_lock = threading.Lock()

# ─────────────────── GITHUB BACKUP SYSTEM ─────────────────────
def github_api(method, path, data=None):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/{path}"
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    if method == "GET": return requests.get(url, headers=headers)
    return requests.put(url, headers=headers, json=data)

def ensure_repo_exists():
    url = f"https://api.github.com/repos/{GITHUB_REPO}"
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    if requests.get(url, headers=headers).status_code == 404:
        requests.post("https://api.github.com/user/repos", headers=headers, json={"name": REPO_NAME, "private": True})

def force_backup():
    """Pushes all files to GitHub instantly."""
    with backup_lock:
        if not GITHUB_TOKEN.startswith("ghp_"): return
        try:
            res = github_api("GET", "contents/")
            repo_files = {}
            if res.status_code == 200:
                for item in res.json(): repo_files[item["name"]] = item["sha"]

            for filename in os.listdir(WORKSPACE_PATH):
                filepath = os.path.join(WORKSPACE_PATH, filename)
                if not os.path.isfile(filepath) or filename.startswith('.') or filename == "bot_logs.txt" or filename.endswith(".pyc"): continue
                
                with open(filepath, "rb") as f: content = base64.b64encode(f.read()).decode()
                
                data = {"message": f"Instant backup {filename}", "content": content}
                if filename in repo_files: data["sha"] = repo_files[filename]
                github_api("PUT", f"contents/{filename}", data)
        except Exception as e:
            print(f"[Backup Error] {e}")

def periodic_backup():
    while True:
        time.sleep(15) # Backup every 15 seconds now
        force_backup()

def restore_workspace():
    """Downloads files from GitHub on startup."""
    if not GITHUB_TOKEN.startswith("ghp_"): return
    print("[+] Restoring files from GitHub...")
    try:
        res = github_api("GET", "contents/")
        if res.status_code == 200:
            for item in res.json():
                if item["type"] == "file":
                    dl = requests.get(item["download_url"])
                    with open(os.path.join(WORKSPACE_PATH, item["name"]), "wb") as f: 
                        f.write(dl.content)
            print("[+] Files restored successfully!")
        else:
            print("[-] No files found on GitHub.")
    except Exception as e:
        print(f"[Restore Error] {e}")

# ─────────────────── AUTO HOST SYSTEM ─────────────────────────
def start_autostart_bots():
    global autostart_processes
    if not os.path.exists(AUTOSTART_FILE):
        print("[-] No autostart.json found. Starting empty.")
        with open(AUTOSTART_FILE, "w") as f: json.dump([], f)
        return
        
    try:
        with open(AUTOSTART_FILE, "r") as f: 
            cmds = json.load(f)
            
        print(f"[+] Found {len(cmds)} auto-host commands. Starting them now...")
        for cmd in cmds:
            with open(LOG_FILE, "a") as log:
                log.write(f"\n\n--- [SYSTEM BOOT] Starting: {cmd} ---\n")
                p = subprocess.Popen(cmd, shell=True, cwd=WORKSPACE_PATH, stdout=log, stderr=log, preexec_fn=os.setsid)
                autostart_processes.append({"cmd": cmd, "pid": p.pid})
    except Exception as e:
        print(f"[Auto-start error] {e}")

# ─────────────────── BOOT SEQUENCE ────────────────────────────
ensure_repo_exists()
restore_workspace() # Restore synchronously so files exist before bots start
threading.Thread(target=periodic_backup, daemon=True).start()

# Wait 10 seconds to ensure GitHub download finishes before starting bots
threading.Timer(10.0, start_autostart_bots).start()

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
        #terminal-container { height: calc(100vh - 120px); width: 100%; padding: 10px; background: #000; border-radius: 8px; }
        .xterm-viewport::-webkit-scrollbar { width: 6px; }
        .xterm-viewport::-webkit-scrollbar-thumb { background: #4b5563; border-radius: 3px; }
        .xterm-helper-textarea { z-index: 9999 !important; }
    </style>
</head>
<body class="h-screen flex flex-col p-2 relative bg-gray-900">
    
    <!-- Top Bar -->
    <div class="bg-gray-800 border border-gray-700 px-4 py-3 rounded-lg flex flex-wrap gap-2 justify-between items-center shadow-lg mb-2">
        <div class="flex items-center gap-2">
            <div class="w-3 h-3 rounded-full bg-green-500 animate-pulse"></div>
            <h1 class="text-lg font-bold text-indigo-400">REXI CLOUD</h1>
        </div>
        <div class="flex flex-wrap gap-2">
            <button onclick="document.getElementById('fileInput').click()" class="bg-indigo-600 hover:bg-indigo-500 text-white px-3 py-1.5 rounded text-xs font-bold">📁 Upload</button>
            <input type="file" id="fileInput" class="hidden" onchange="uploadFile()">
            
            <button onclick="openModal('fileManagerModal')" class="bg-purple-600 hover:bg-purple-500 text-white px-3 py-1.5 rounded text-xs font-bold">📝 Files</button>
            <button onclick="openModal('autoHostModal')" class="bg-green-600 hover:bg-green-500 text-white px-3 py-1.5 rounded text-xs font-bold">🚀 Auto-Host</button>
            <button onclick="sendCtrlC()" class="bg-yellow-600 hover:bg-yellow-500 text-black px-3 py-1.5 rounded text-xs font-bold">🛑 Ctrl+C</button>
        </div>
    </div>

    <!-- Terminal -->
    <div id="terminal-container" onclick="term.focus()"></div>

    <!-- File Manager Modal -->
    <div id="fileManagerModal" class="fixed inset-0 bg-black z-50 flex items-center justify-center hidden p-4">
        <div class="bg-gray-800 border border-gray-600 w-full max-w-3xl h-[80vh] rounded-xl flex flex-col overflow-hidden shadow-2xl relative">
            <button onclick="closeModal('fileManagerModal')" class="absolute top-3 right-3 text-white bg-red-600 hover:bg-red-500 w-8 h-8 rounded-full font-bold flex items-center justify-center z-50">✖</button>
            <div class="px-6 py-4 border-b border-gray-600">
                <h2 class="text-lg font-bold text-purple-400">📝 File Manager</h2>
            </div>
            <div class="flex-1 flex overflow-hidden">
                <div id="fileListDiv" class="w-1/3 border-r border-gray-600 overflow-y-auto p-2 bg-gray-900"></div>
                <div class="w-2/3 flex flex-col bg-gray-800">
                    <div id="editorHeader" class="p-2 border-b border-gray-600 text-sm text-gray-400 font-mono hidden">Select a file</div>
                    <textarea id="codeEditor" class="flex-1 bg-gray-900 text-white font-mono text-sm p-4 focus:outline-none resize-none hidden" spellcheck="false"></textarea>
                    <div id="editorFooter" class="p-3 border-t border-gray-600 flex justify-end hidden">
                        <button onclick="saveFile()" class="bg-indigo-600 hover:bg-indigo-500 text-white px-6 py-2 rounded font-bold text-sm">Save File</button>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Auto-Host Modal -->
    <div id="autoHostModal" class="fixed inset-0 bg-black z-50 flex items-center justify-center hidden p-4">
        <div class="bg-gray-800 border border-gray-600 w-full max-w-3xl h-[80vh] rounded-xl flex flex-col overflow-hidden shadow-2xl relative">
            <button onclick="closeModal('autoHostModal')" class="absolute top-3 right-3 text-white bg-red-600 hover:bg-red-500 w-8 h-8 rounded-full font-bold flex items-center justify-center z-50">✖</button>
            <div class="px-6 py-4 border-b border-gray-600">
                <h2 class="text-lg font-bold text-green-400">🚀 Auto-Host Engine</h2>
            </div>
            <div class="p-4 flex gap-2 border-b border-gray-600 bg-gray-900">
                <input type="text" id="hostCmd" placeholder="e.g. python ok.py" class="flex-1 bg-gray-800 border border-gray-600 rounded p-2 text-white text-sm focus:outline-none focus:border-green-500">
                <button onclick="addAutoHost()" class="bg-green-600 hover:bg-green-500 text-white font-bold px-6 rounded text-sm">Add</button>
            </div>
            <div class="flex-1 flex overflow-hidden">
                <div class="w-1/2 border-r border-gray-600 overflow-y-auto p-4 bg-gray-800">
                    <h3 class="text-sm font-bold text-gray-400 mb-2">Active Commands</h3>
                    <div id="autoHostList" class="space-y-2"></div>
                </div>
                <div class="w-1/2 p-4 flex flex-col bg-black">
                    <h3 class="text-sm font-bold text-gray-400 mb-2">Live Logs</h3>
                    <pre id="hostLogs" class="flex-1 text-green-400 font-mono text-xs overflow-y-auto whitespace-pre-wrap"></pre>
                </div>
            </div>
        </div>
    </div>

    <script>
        const term = new Terminal({
            cursorBlink: true,
            theme: { background: '#000000', foreground: '#ffffff', cursor: '#6366f1' },
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 14
        });
        const fitAddon = new FitAddon.FitAddon();
        term.loadAddon(fitAddon);
        term.open(document.getElementById('terminal-container'));
        fitAddon.fit();

        const terminalDiv = document.getElementById('terminal-container');
        function forceFocus(e) { if(e) e.preventDefault(); term.focus(); }
        terminalDiv.addEventListener('touchstart', forceFocus, {passive: false});
        terminalDiv.addEventListener('click', forceFocus, {passive: false});
        setTimeout(forceFocus, 500);

        const socket = io();

        socket.on('connect', () => {
            socket.emit('join_master');
            socket.emit('start_pty');
            setInterval(() => { socket.emit('heartbeat'); }, 10000);
        });

        socket.on('restart_pty', () => { socket.emit('start_pty'); });
        socket.on('pty_output', (data) => { term.write(data); });
        socket.on('upload_success', (filename) => { term.write(`\\r\\n\\x1b[1;32m[+] File ${filename} uploaded!\\x1b[0m\\r\\n`); });
        socket.on('upload_error', (err) => { term.write(`\\r\\n\\x1b[1;31m[!] Upload failed: ${err}\\x1b[0m\\r\\n`); });

        term.onData((data) => { socket.emit('pty_input', data); });

        function sendCtrlC() { socket.emit('pty_input', '\\x03'); term.focus(); }

        function openModal(id) {
            document.getElementById(id).classList.remove('hidden');
            if(id === 'fileManagerModal') loadFiles();
            if(id === 'autoHostModal') loadAutoHost();
        }
        function closeModal(id) { document.getElementById(id).classList.add('hidden'); }

        // ── FILE MANAGER ──
        let currentFile = null;
        function loadFiles() {
            const listDiv = document.getElementById('fileListDiv');
            listDiv.innerHTML = '<div class="text-gray-400 text-xs text-center mt-4">Loading...</div>';
            fetch('/api/files').then(r => r.json()).then(files => {
                listDiv.innerHTML = '';
                if (files.length === 0) { listDiv.innerHTML = '<div class="text-gray-400 text-xs text-center mt-4">No files.</div>'; return; }
                files.forEach(f => {
                    listDiv.innerHTML += `
                        <div class="flex justify-between items-center p-2 hover:bg-gray-700 rounded cursor-pointer group" onclick="loadFile('${f}')">
                            <span class="text-sm font-mono text-indigo-300">${f}</span>
                            <button onclick="event.stopPropagation(); deleteFile('${f}')" class="text-red-500 hover:text-red-400 px-2">✖</button>
                        </div>`;
                });
            });
        }

        function loadFile(filename) {
            currentFile = filename;
            document.getElementById('editorHeader').innerText = "Loading " + filename + "...";
            document.getElementById('editorHeader').classList.remove('hidden');
            document.getElementById('codeEditor').classList.add('hidden');
            document.getElementById('editorFooter').classList.add('hidden');

            fetch('/api/read?file=' + encodeURIComponent(filename)).then(r => r.json()).then(res => {
                if (res.success) {
                    document.getElementById('editorHeader').innerText = "Editing: " + filename;
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
            .then(r => r.json()).then(res => { if (res.success) alert("Saved and pushed to GitHub!"); else alert("Error: " + res.error); });
        }

        function deleteFile(filename) {
            if (!confirm("Delete " + filename + "?")) return;
            fetch('/api/delete', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({file: filename}) })
            .then(r => r.json()).then(res => {
                if (res.success) { 
                    loadFiles(); 
                    if (currentFile === filename) { 
                        document.getElementById('editorHeader').classList.add('hidden'); 
                        document.getElementById('codeEditor').classList.add('hidden'); 
                        document.getElementById('editorFooter').classList.add('hidden'); 
                    } 
                }
            });
        }

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
                });
            fileInput.value = ''; 
            loadFiles();
        }

        // ── AUTO-HOST ──
        function loadAutoHost() {
            fetch('/api/autohost').then(r => r.json()).then(data => {
                const list = document.getElementById('autoHostList');
                list.innerHTML = '';
                if (data.length === 0) { list.innerHTML = '<div class="text-gray-500 text-xs">No commands.</div>'; return; }
                data.forEach(c => {
                    list.innerHTML += `
                        <div class="flex justify-between items-center bg-gray-900 p-2 rounded text-sm border border-gray-700">
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
    force_backup() # INSTANT BACKUP
    return jsonify({"success": True})

@app.route('/api/files')
def api_files():
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
        force_backup() # INSTANT BACKUP
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/delete', methods=['POST'])
def api_delete():
    filename = request.json.get('file')
    path = os.path.join(WORKSPACE_PATH, filename)
    try:
        os.remove(path)
        force_backup()
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
        
    force_backup() # INSTANT BACKUP of autostart.json
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
    global active_terminal
    if active_terminal:
        active_terminal["last_ping"] = time.time()
    else:
        emit('restart_pty', room=request.sid)

@socketio.on('show_upload_success')
def show_upload_success(filename): emit('upload_success', filename, room="master_terminal")

@socketio.on('show_upload_error')
def show_upload_error(error): emit('upload_error', error, room="master_terminal")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)
