"""Optional Bloodborne PC lobby browser for the Windows launcher.

The lobby lists compatible shadNet endpoints and mod packages. Game credentials go
directly to shadNet when the game starts; this module never sends them to the lobby.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path


def catalog(base_url: str, profile_id: str) -> tuple[list[dict], list[dict]]:
    base = base_url.strip().rstrip('/')
    if not base.startswith(('http://', 'https://')):
        raise ValueError('Lobby URL must start with http:// or https://')
    query = urllib.parse.urlencode({'profile_id': profile_id})
    def load(path):
        request = urllib.request.Request(f'{base}/api/v1/{path}?{query}',
                                         headers={'User-Agent': 'bbport-lobby/1.0'})
        with urllib.request.urlopen(request, timeout=8) as reply:
            return json.load(reply)
    return load('servers'), load('mods')


def install_mod(base_url: str, metadata: dict, data_dir: Path) -> Path:
    """Fetch, verify and install a loose-file mod ZIP without following archive paths."""
    slug = str(metadata['slug'])
    version = str(metadata['version'])
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{1,79}', slug) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,40}', version):
        raise ValueError('Invalid mod name or version')
    path = str(metadata['download_url'])
    if not path.startswith('/api/v1/mods/') or not path.endswith('/download'):
        raise ValueError('Invalid mod download URL')
    request = urllib.request.Request(base_url.rstrip('/') + path, headers={'User-Agent': 'bbport-lobby/1.0'})
    with urllib.request.urlopen(request, timeout=30) as reply:
        raw = reply.read(100 * 1024 * 1024 + 1)
    if len(raw) > 100 * 1024 * 1024:
        raise ValueError('Mod archive exceeds 100 MiB')
    digest = hashlib.sha256(raw).hexdigest()
    if digest != metadata['sha256']:
        raise ValueError('Mod archive SHA-256 mismatch')
    with zipfile.ZipFile(io.BytesIO(raw)) as package:
        entries = package.infolist()
        if len(entries) > 10000 or sum(x.file_size for x in entries) > 500 * 1024 * 1024:
            raise ValueError('Mod archive expands too large')
        for entry in entries:
            entry_path = entry.filename.replace('\\', '/')
            parts = entry_path.rstrip('/').split('/')
            if (entry_path.startswith('/') or '\\' in entry.filename or
                    any(p in ('', '.', '..') or ':' in p or p.rstrip(' .') != p or
                        p.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                                                    *{f'COM{i}' for i in range(1, 10)},
                                                    *{f'LPT{i}' for i in range(1, 10)}} for p in parts)):
                raise ValueError('Unsafe path in mod archive')
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Mod archive contains symlinks')
        manifest = json.loads(package.read('mod.json'))
        if manifest.get('slug') != slug or manifest.get('version') != version:
            raise ValueError('Mod manifest mismatch')
        downloads = data_dir / 'mods-down' / 'lobby'
        downloads.mkdir(parents=True, exist_ok=True)
        (downloads / f'{slug}-{version}.zip').write_bytes(raw)
        mods = data_dir / 'mods'
        mods.mkdir(parents=True, exist_ok=True)
        stage = mods / f'.{slug}.new'
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir()
        target = mods / slug
        backup = None
        try:
            package.extractall(stage)
            if target.exists():
                backups = data_dir / 'mods-down' / 'lobby-backups'
                backups.mkdir(parents=True, exist_ok=True)
                backup = backups / f'{slug}-{int(time.time())}'
                target.rename(backup)
            stage.rename(target)
            return target
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            if backup and not target.exists() and backup.exists():
                backup.rename(target)
            raise


def open_lobby(parent, base_url: str, profile_id: str, npid_var, password_var,
               select_server, data_dir: Path, ui_calls):
    """Show the catalog in a Tk Toplevel; all network operations stay off the UI thread."""
    import tkinter as tk
    from tkinter import messagebox, ttk

    box = tk.Toplevel(parent)
    box.title('Bloodborne 联机大厅')
    box.geometry('820x560')
    box.transient(parent)
    frame = ttk.Frame(box, padding=16)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text=f'兼容版本：{profile_id}', font=('Segoe UI', 11, 'bold')).pack(anchor='w')
    status = ttk.Label(frame, text='正在读取服务器…')
    status.pack(anchor='w', pady=(4, 8))
    server_list = tk.Listbox(frame, height=8)
    server_list.pack(fill='x')
    credentials = ttk.Frame(frame)
    credentials.pack(fill='x', pady=10)
    ttk.Label(credentials, text='shadNet NPID').pack(side='left')
    ttk.Entry(credentials, textvariable=npid_var, width=20).pack(side='left', padx=8)
    ttk.Label(credentials, text='密码').pack(side='left')
    ttk.Entry(credentials, textvariable=password_var, show='•', width=20).pack(side='left', padx=8)
    ttk.Label(frame, text='Mod 包（仅显示当前兼容版本）').pack(anchor='w', pady=(10, 3))
    mod_list = tk.Listbox(frame, height=8)
    mod_list.pack(fill='both', expand=True)
    controls = ttk.Frame(frame)
    controls.pack(fill='x', pady=(10, 0))
    servers, mods = [], []

    def refresh():
        status.configure(text='正在读取服务器…')
        def work():
            try:
                new_servers, new_mods = catalog(base_url, profile_id)
                def show():
                    servers[:] = new_servers
                    mods[:] = new_mods
                    server_list.delete(0, 'end')
                    mod_list.delete(0, 'end')
                    for s in servers:
                        server_list.insert('end', f"{s['name']}  |  {s['online_players']}/{s['max_players']}  |  {s['host']}:{s['port']}")
                    for m in mods:
                        mod_list.insert('end', f"{m['name']}  v{m['version']}  ({m['size'] // 1024} KiB)")
                    status.configure(text=f'{len(servers)} 个服务器 · {len(mods)} 个 Mod')
                ui_calls.put(show)
            except (OSError, ValueError, KeyError) as error:
                ui_calls.put(lambda e=error: status.configure(text=f'大厅连接失败：{e}'))
        threading.Thread(target=work, daemon=True).start()

    def choose():
        if not server_list.curselection():
            messagebox.showinfo('联机大厅', '请先选择服务器。', parent=box)
            return
        if not npid_var.get().strip() or not password_var.get():
            messagebox.showwarning('联机大厅', '请输入 shadNet NPID 和密码。', parent=box)
            return
        select_server(servers[server_list.curselection()[0]])
        box.destroy()

    def download():
        if not mod_list.curselection():
            messagebox.showinfo('联机大厅', '请先选择 Mod。', parent=box)
            return
        mod = mods[mod_list.curselection()[0]]
        status.configure(text=f"正在下载 {mod['name']}…")
        def work():
            try:
                target = install_mod(base_url, mod, data_dir)
                ui_calls.put(lambda: status.configure(text=f'Mod 已安装：{target.name}'))
            except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
                ui_calls.put(lambda e=error: messagebox.showerror('Mod 安装失败', str(e), parent=box))
        threading.Thread(target=work, daemon=True).start()

    ttk.Button(controls, text='刷新', command=refresh).pack(side='left')
    ttk.Button(controls, text='选择服务器', command=choose).pack(side='left', padx=8)
    ttk.Button(controls, text='下载并安装 Mod', command=download).pack(side='right')
    refresh()
