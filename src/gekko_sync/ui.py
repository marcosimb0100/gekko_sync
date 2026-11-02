import hashlib
import queue
import socket
import threading
import time
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .api import ApiClient, ApiError, normalize_url
from .workspace import Workspace
from .watcher import ChangeMonitor
from .state import State
from .desktop import Startup, Tray, create_icon
from .configuration import resolve_api_url


class Application:
    def __init__(self, root, state, background=False):
        self.root, self.state = root, state
        self.events = queue.Queue()
        self.busy = False
        self.tray = None
        self.exit_requested = False
        self.startup = Startup()
        self.start_with_windows = tk.BooleanVar(value=self.startup.enabled())
        self.close_to_tray = tk.BooleanVar(value=state.get('close_to_tray', True))
        self.client, self.account = None, None
        self.repositories = []
        self.buttons = []
        self.monitor = ChangeMonitor()
        self.last_remote_check = 0
        self.watch_error = None
        self.device_authorized = False
        self.account_status = tk.StringVar(value='Sesión sin iniciar')
        self.active_mappings = set()
        self.auto_sync = tk.BooleanVar(value=state.get('auto_sync', True))
        api_url, self.server_pinned = resolve_api_url(state)
        self.url = tk.StringVar(value=api_url)
        self.email = tk.StringVar(value=state.get('email', ''))
        self.password = tk.StringVar()
        self.show_password = tk.BooleanVar(value=False)
        self.machine = tk.StringVar(value=state.get('machine', socket.gethostname()))
        self.folder = tk.StringVar()
        self.status = tk.StringVar(value='Bienvenido. Inicia sesión con tu cuenta Gekko.')
        self.device_status = tk.StringVar(value='Registra tu equipo para solicitar acceso.')
        root.title('Gekko Sync')
        root.geometry('860x640')
        root.minsize(860, 640)
        root.configure(background='#f1f5f9')
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=('Segoe UI', 10), background='#f1f5f9', foreground='#24364b')
        style.configure('TButton', padding=(10, 6), background='#e7edf4', borderwidth=0)
        style.map('TButton', background=[('active', '#cbd5e1')])
        style.configure('Accent.TButton', background='#142d4e', foreground='white', font=('Segoe UI', 10, 'bold'))
        style.map('Accent.TButton', background=[('disabled', '#cbd5e1'), ('active', '#0e7490')], foreground=[('disabled', '#64748b')])
        style.configure('TEntry', padding=6, fieldbackground='white')
        style.configure('TCombobox', padding=6, fieldbackground='white')
        style.configure('Card.TFrame', background='white')
        style.configure('Card.TLabel', background='white')
        style.configure('Title.TLabel', font=('Segoe UI', 16, 'bold'), background='white')
        style.configure('Muted.TLabel', foreground='#64748b', background='white')
        style.configure('TNotebook', background='#f1f5f9', borderwidth=0)
        style.configure('TNotebook.Tab', padding=(14, 8))
        style.map('TNotebook.Tab', background=[('selected', 'white')], foreground=[('selected', '#0891b2')])
        style.configure('Treeview', background='white', fieldbackground='white', rowheight=28, borderwidth=0)
        style.configure('Treeview.Heading', font=('Segoe UI', 10, 'bold'), padding=8)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        page = ttk.Frame(root, padding=16)
        page.grid(sticky='nsew')
        page.columnconfigure(0, weight=1)
        page.rowconfigure(2, weight=1)
        header = ttk.Frame(page)
        header.grid(sticky='ew', pady=(0, 12))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text='GEKKO SYNC', font=('Segoe UI', 19, 'bold'), foreground='#142d4e').grid(sticky='w')
        ttk.Label(header, text='Sincronización de documentos empresariales', foreground='#64748b').grid(row=1, sticky='w', pady=2)
        self.server_button = self.button(header, 'Configurar servidor', self.handle_server_dialog)
        self.server_button.grid(row=0, column=1, rowspan=2)
        ttk.Button(header, text='Ocultar', command=self.handle_hide).grid(row=0, column=2, rowspan=2, padx=(8, 0))
        self.notebook = ttk.Notebook(page)
        self.notebook.grid(row=2, sticky='nsew')
        self.login_page = ttk.Frame(self.notebook, padding=18, style='Card.TFrame')
        self.equipment_page = ttk.Frame(self.notebook, padding=18, style='Card.TFrame')
        self.folders_page = ttk.Frame(self.notebook, padding=18, style='Card.TFrame')
        preferences = ttk.Frame(self.notebook, padding=18, style='Card.TFrame')
        activity = ttk.Frame(self.notebook, padding=16, style='Card.TFrame')
        for frame, title in ((self.login_page, 'Cuenta'), (self.equipment_page, 'Equipo'),
                             (self.folders_page, 'Repositorios'), (activity, 'Actividad'), (preferences, 'Preferencias')):
            frame.columnconfigure(0, weight=1)
            self.notebook.add(frame, text=title)

        login = self.login_page
        self.heading(login, '1', 'Cuenta corporativa', 'Acceso con tu cuenta de Gekko.')
        form = ttk.Frame(login, padding=(44, 12, 44, 0), style='Card.TFrame')
        form.grid(row=1, sticky='ew')
        form.columnconfigure(0, weight=1)
        ttk.Label(form, text='Usuario · correo de Gekko', style='Card.TLabel').grid(sticky='w', pady=(0, 6))
        self.email_entry = ttk.Entry(form, textvariable=self.email, font=('Segoe UI', 12))
        self.email_entry.grid(sticky='ew', pady=(0, 10))
        ttk.Label(form, text='Contraseña', style='Card.TLabel').grid(sticky='w', pady=(0, 6))
        self.password_entry = ttk.Entry(form, textvariable=self.password, show='•', font=('Segoe UI', 12))
        self.password_entry.grid(sticky='ew', pady=(0, 10))
        self.password_entry.bind('<Return>', lambda event: self.handle_login() if not self.busy else None)
        self.password_toggle = ttk.Checkbutton(form, text='Mostrar contraseña', variable=self.show_password,
                        command=self.handle_password_visibility)
        self.password_toggle.grid(sticky='w', pady=(0, 10))
        self.login_button = self.button(form, 'Iniciar sesión  →', self.handle_login, primary=True)
        self.login_button.grid(sticky='ew', pady=(0, 10))
        alternatives = ttk.Frame(form, style='Card.TFrame')
        alternatives.grid(sticky='w', pady=8)
        self.saved_button = self.button(alternatives, 'Usar sesión guardada', self.handle_saved_session)
        self.saved_button.pack(side='left', padx=(0, 8))
        self.logout_button = self.button(alternatives, 'Cerrar sesión', self.handle_logout)
        self.logout_button.pack(side='left')
        ttk.Label(form, textvariable=self.account_status, style='Card.TLabel').grid(sticky='w', pady=(12, 0))
        ttk.Label(form, text='Tu contraseña no se guarda. La sesión se protege con Windows.',
                  style='Muted.TLabel', wraplength=650).grid(sticky='w', pady=(10, 4))

        equipment = self.equipment_page
        self.heading(equipment, '2', 'Conecta esta computadora', 'Un administrador debe autorizar el equipo antes de sincronizar.')
        card = ttk.Frame(equipment, padding=(44, 16, 44, 0), style='Card.TFrame')
        card.grid(row=1, sticky='ew')
        card.columnconfigure(0, weight=1)
        ttk.Label(card, text='Nombre de esta computadora', style='Card.TLabel').grid(sticky='w', pady=(0, 8))
        self.machine_entry = ttk.Entry(card, textvariable=self.machine, font=('Segoe UI', 12))
        self.machine_entry.grid(sticky='ew', pady=(0, 18))
        self.register_button = self.button(card, 'Registrar mi equipo', self.handle_register, primary=True)
        self.register_button.grid(sticky='ew', pady=(0, 10))
        self.button(card, 'Consultar autorización', self.handle_status).grid(sticky='ew')
        ttk.Label(card, textvariable=self.device_status, style='Muted.TLabel', wraplength=650).grid(sticky='w', pady=24)

        folders = self.folders_page
        self.heading(folders, '3', 'Tus repositorios sincronizados', 'Elige una carpeta principal. Gekko crea las carpetas de tus repositorios autorizados.')
        card = ttk.Frame(folders, padding=(0, 10, 0, 0), style='Card.TFrame')
        card.grid(row=1, sticky='nsew')
        card.columnconfigure(0, weight=1)
        ttk.Label(card, text='Se incluyen todos los repositorios con Lectura y Sync local.', style='Muted.TLabel').grid(row=0, column=0, sticky='w', pady=6)
        self.button(card, 'Actualizar', self.handle_repositories).grid(row=0, column=1)
        ttk.Label(card, text='Carpeta principal', style='Card.TLabel').grid(row=2, column=0, sticky='w', pady=(10, 6))
        ttk.Entry(card, textvariable=self.folder, state='readonly').grid(row=3, column=0, sticky='ew', padx=(0, 12))
        self.choose_folder_button = self.button(card, 'Elegir carpeta', self.handle_choose_folder)
        self.choose_folder_button.grid(row=3, column=1)
        controls = ttk.Frame(card, style='Card.TFrame')
        controls.grid(row=4, column=0, columnspan=2, sticky='w', pady=10)
        self.button(controls, 'Configurar', self.handle_mapping).pack(side='left', padx=(0, 10))
        self.button(controls, 'Sincronizar todos', self.handle_sync, primary=True).pack(side='left')
        ttk.Checkbutton(controls, text='Sincronización automática', variable=self.auto_sync,
                        command=self.handle_auto_toggle).pack(side='left', padx=12)
        self.mapping_tree = ttk.Treeview(card, columns=('name', 'folder'), show='headings', height=4, selectmode='browse')
        self.mapping_tree.heading('name', text='Repositorio')
        self.mapping_tree.heading('folder', text='Carpeta local')
        self.mapping_tree.column('name', width=180)
        self.mapping_tree.column('folder', width=420)
        self.mapping_tree.grid(row=5, column=0, columnspan=2, sticky='ew')
        repository_scroll = ttk.Scrollbar(card, command=self.mapping_tree.yview)
        repository_scroll.grid(row=5, column=2, sticky='ns')
        self.mapping_tree.configure(yscrollcommand=repository_scroll.set)
        self.mapping_tree.bind('<<TreeviewSelect>>', self.handle_mapping_selection)
        ttk.Label(card, text='Solo se sincronizan repositorios administrados, según tus permisos.\nLas carpetas ajenas se ignoran y las eliminaciones locales no se publican.', style='Muted.TLabel', font=('Segoe UI', 9), wraplength=730).grid(row=6, column=0, columnspan=2, sticky='w', pady=(6, 0))

        self.heading(preferences, '4', 'Preferencias del equipo', 'Comportamiento de Gekko Sync en esta sesión de Windows.')
        ttk.Checkbutton(preferences, text='Iniciar Gekko Sync con Windows', variable=self.start_with_windows,
                        command=self.handle_startup_toggle).grid(sticky='w', pady=(20, 6))
        ttk.Label(preferences, text='Se abrirá oculto junto al reloj al iniciar sesión en Windows.', style='Muted.TLabel').grid(sticky='w')
        ttk.Checkbutton(preferences, text='Al cerrar la ventana, mantener Gekko Sync junto al reloj', variable=self.close_to_tray,
                        command=lambda: self.state.set('close_to_tray', self.close_to_tray.get())).grid(sticky='w', pady=(20, 6))
        ttk.Label(preferences, text='Ocultar conserva la sincronización. Usa Salir para detener la aplicación.', style='Muted.TLabel').grid(sticky='w')
        ttk.Button(preferences, text='Salir de Gekko Sync', command=self.handle_exit).grid(sticky='w', pady=20)
        ttk.Label(preferences, text='Servidor de Gekko', style='Card.TLabel').grid(sticky='w', pady=(6, 4))
        ttk.Label(preferences, textvariable=self.url, style='Muted.TLabel', wraplength=730).grid(sticky='w')

        activity.rowconfigure(0, weight=1)
        self.log = tk.Text(activity, wrap='word', state='disabled', font=('Consolas', 10),
                           background='#f8fafc', foreground='#334155', relief='flat', padx=14, pady=14)
        self.log.grid(sticky='nsew')
        scrollbar = ttk.Scrollbar(activity, command=self.log.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.log.configure(yscrollcommand=scrollbar.set)
        self.progress = ttk.Progressbar(page, mode='indeterminate')
        self.progress.configure(length=100)
        self.progress.grid(row=3, sticky='ew', pady=(10, 0))
        self.status_label = ttk.Label(page, textvariable=self.status, wraplength=800, padding=(0, 6, 0, 0))
        self.status_label.grid(row=4, sticky='ew')
        self.email_entry.focus_set()
        root.protocol('WM_DELETE_WINDOW', self.handle_close)
        root.after(100, self.poll)
        root.after(1000, self.handle_auto_tick)
        self.update_controls()
        root.after(200, self.handle_restore_startup)
        root.after(50, lambda: self.start_tray(background))

    def update_controls(self):
        connected = self.client is not None
        configured = bool(self.account and Workspace(self.state, self.account).config())
        self.choose_folder_button.state(['disabled'] if self.busy or configured else ['!disabled'])
        for widget in (self.email_entry, self.password_entry, self.password_toggle,
                       self.login_button, self.saved_button, self.server_button):
            widget.state(['disabled'] if self.busy or connected else ['!disabled'])
        if self.server_pinned:
            self.server_button.state(['disabled'])
        registered = connected and bool(self.client.device)
        self.machine_entry.state(['disabled'] if self.busy or registered else ['!disabled'])
        self.register_button.state(['disabled'] if self.busy or not connected or registered else ['!disabled'])
        self.logout_button.state(['disabled'] if self.busy or not connected else ['!disabled'])
        if self.tray:
            self.tray.update(self.auto_sync.get(), self.busy)

    def start_tray(self, background=False):
        try:
            self.tray = Tray(lambda command: self.events.put(('tray', command)))
            self.tray.start()
            self.tray.update(self.auto_sync.get(), self.busy)
            from PIL import ImageTk
            self.window_icon = ImageTk.PhotoImage(create_icon(), master=self.root)
            self.root.iconphoto(True, self.window_icon)
            if background:
                self.handle_hide()
        except Exception as error:
            if self.tray:
                self.tray.stop()
            self.tray = None
            self.root.deiconify()
            self.report('El icono junto al reloj no está disponible. La ventana permanece abierta. ' + str(error))

    def handle_hide(self):
        if self.tray:
            self.root.withdraw()
        else:
            self.status.set('El icono junto al reloj aún no está disponible; la ventana seguirá visible.')

    def handle_show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def handle_startup_toggle(self):
        try:
            self.startup.set_enabled(self.start_with_windows.get())
            self.status.set('Inicio con Windows activado.' if self.start_with_windows.get() else 'Inicio con Windows desactivado.')
        except (OSError, ValueError) as error:
            self.start_with_windows.set(not self.start_with_windows.get())
            messagebox.showerror('Inicio con Windows', str(error), parent=self.root)

    def handle_exit(self):
        if self.busy:
            self.exit_requested = True
            self.status.set('La aplicación saldrá cuando termine la operación actual.')
            return
        self.monitor.stop()
        if self.tray:
            self.tray.stop()
            self.tray = None
        self.root.destroy()

    def handle_auto_toggle(self):
        self.state.set('auto_sync', self.auto_sync.get())
        self.update_monitor()
        if self.tray:
            self.tray.update(self.auto_sync.get(), self.busy)

    def update_monitor(self):
        targets = {}
        if self.client and self.account and self.device_authorized and self.auto_sync.get() and self.state.get(self.account + ':workspace_active', False):
            workspace = Workspace(self.state, self.account)
            config = workspace.config()
            if config and not config.get('migration'):
                for identifier in config.get('allowed', config['repositories']):
                    try:
                        targets[identifier] = workspace.validate_repository(identifier)
                    except (ValueError, OSError):
                        pass
        try:
            self.monitor.update(targets)
            self.watch_error = None
        except Exception as error:
            if str(error) != self.watch_error:
                self.report('No se pudo iniciar el detector local; continúa la revisión de respaldo: ' + str(error))
                self.watch_error = str(error)

    def handle_restore_startup(self):
        if self.busy or self.client or not self.email.get() or self.state.get('signed_out', False):
            return
        self.handle_saved_session()

    def finish_session(self, client, account, name):
        self.client, self.account = client, account
        self.device_authorized = False
        self.repositories = []
        self.machine.set(self.state.get(account + ':machine', self.state.get('machine', socket.gethostname())))
        self.password.set('')
        self.account_status.set(f'Sesión activa: {name}')
        self.state.set('signed_out', False)
        self.refresh_mappings()
        self.update_controls()
        self.notebook.select(self.equipment_page)
        if client.device:
            # Defer until the current worker's done event unlocks the UI.
            self.root.after(150, self.handle_resume_device)

    def handle_resume_device(self):
        if self.busy:
            self.root.after(150, self.handle_resume_device)
        elif self.client and self.client.device:
            self.handle_status()

    def handle_mapping_selection(self, event=None):
        if not self.account:
            return
        selected = self.mapping_tree.selection()
        if not selected:
            return
        mapping = next((row for row in self.state.mappings(self.account) if str(row['id']) == selected[0]), None)
        if mapping:
            self.state.set(self.account + ':selected_mapping', mapping['id'])

    def heading(self, parent, number, title, subtitle):
        header = ttk.Frame(parent, style='Card.TFrame')
        header.grid(sticky='ew')
        icon = tk.Canvas(header, width=46, height=46, background='white', highlightthickness=0)
        icon.create_oval(1, 1, 45, 45, fill='#0891b2', outline='')
        icon.create_text(23, 23, text=number, fill='white', font=('Segoe UI', 18, 'bold'))
        icon.grid(row=0, column=0, rowspan=2, padx=(0, 16))
        ttk.Label(header, text=title, style='Title.TLabel').grid(row=0, column=1, sticky='w')
        ttk.Label(header, text=subtitle, style='Muted.TLabel', wraplength=690).grid(row=1, column=1, sticky='w', pady=4)

    def handle_password_visibility(self):
        self.password_entry.configure(show='' if self.show_password.get() else '•')

    def handle_server_dialog(self):
        if self.busy or self.client or self.server_pinned:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title('Servidor de Gekko')
        dialog.geometry('620x220')
        dialog.transient(self.root)
        dialog.grab_set()
        content = ttk.Frame(dialog, padding=24)
        content.pack(fill='both', expand=True)
        content.columnconfigure(0, weight=1)
        ttk.Label(content, text='Dirección de la API de Gekko', font=('Segoe UI', 12, 'bold')).grid(sticky='w', pady=(0, 12))
        value = tk.StringVar(value=self.url.get())
        entry = ttk.Entry(content, textvariable=value)
        entry.grid(sticky='ew')
        ttk.Label(content, text='Usa HTTPS para servidores remotos. El cambio se aplica al iniciar sesión.', wraplength=560).grid(sticky='w', pady=12)
        def save():
            try:
                url = normalize_url(value.get())
                self.state.set('api_url', url)
                self.url.set(url)
                self.status.set('Servidor guardado. Inicia sesión para conectarte a él.')
                dialog.destroy()
            except Exception as error:
                messagebox.showerror('Servidor de Gekko', str(error), parent=dialog)
        ttk.Button(content, text='Guardar servidor', command=save, style='Accent.TButton').grid(sticky='e')
        entry.focus_set()


    def button(self, parent, text, action, primary=False):
        button = ttk.Button(parent, text=text, command=action, style='Accent.TButton' if primary else 'TButton')
        self.buttons.append(button)
        return button

    def report(self, message):
        self.events.put(("message", str(message)))

    def run(self, work, success=lambda result: None):
        if self.busy or self.exit_requested:
            return
        self.busy = True
        self.progress.start(12)
        self.status_label.configure(foreground='#334155')
        for button in self.buttons:
            button.state(["disabled"])
        self.update_controls()
        self.status.set("Procesando…")
        def worker():
            try:
                self.events.put(("success", (success, work())))
            except Exception as error:
                self.events.put(("error", error))
            finally:
                self.events.put(("done", None))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "done":
                    self.busy = False
                    self.progress.stop()
                    for button in self.buttons:
                        button.state(["!disabled"])
                    self.update_controls()
                    self.update_monitor()
                    if self.exit_requested:
                        self.handle_exit()
                        return
                elif kind == 'tray':
                    if value == 'show':
                        self.handle_show()
                    elif value == 'exit':
                        self.handle_exit()
                        if not self.busy:
                            return
                    elif value == 'sync' and not self.busy:
                        self.handle_sync()
                    elif value == 'toggle':
                        self.auto_sync.set(not self.auto_sync.get())
                        self.handle_auto_toggle()
                elif kind == "success":
                    callback, result = value
                    try:
                        callback(result)
                    except Exception as error:
                        self.status.set(str(error))
                else:
                    if kind == 'error' and isinstance(value, ApiError) and value.status == 401:
                        self.active_mappings.clear()
                        self.client, self.account = None, None
                        self.device_authorized = False
                        self.account_status.set('La sesión venció. Inicia sesión nuevamente.')
                        self.notebook.select(self.login_page)
                    value = str(value)
                    self.status.set(value)
                    self.status_label.configure(foreground='#b91c1c' if kind == 'error' else '#334155')
                    self.log.configure(state="normal")
                    self.log.insert("end", value + "\n")
                    self.log.see("end")
                    self.log.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def require_session(self):
        if not self.client:
            raise ValueError("Primero inicia sesión con tu cuenta Gekko.")
        return self.client

    def handle_save_config(self):
        try:
            self.state.set("api_url", normalize_url(self.url.get()))
            self.state.set("email", self.email.get().strip().lower())
            self.state.set("machine", self.machine.get().strip())
            self.status.set("Conexión guardada. Inicia sesión para usarla.")
        except Exception as error:
            self.status.set(str(error))

    def handle_login(self):
        if self.busy or self.client:
            return
        self.active_mappings.clear()
        url, email, password = self.url.get(), self.email.get().strip().lower(), self.password.get()
        self.password.set("")
        if not email or not password:
            self.status.set("Indica correo y contraseña de Gekko.")
            return
        self.client, self.account = None, None
        self.mapping_tree.delete(*self.mapping_tree.get_children())
        def work():
            client = ApiClient(url)
            data = client.login(email, password)
            account = hashlib.sha256((client.url + "|" + email).encode()).hexdigest()
            self.state.save_secret(account + ":session", client.token)
            client.device = self.state.get(account + ":device")
            client.device_key = self.state.secret(account + ":device-key")
            return client, account, data["nombre_completo"]
        def done(result):
            self.client, self.account, name = result
            self.url.set(self.client.url)
            self.email.set(email)
            self.handle_save_config()
            self.refresh_mappings()
            self.device_status.set("Equipo guardado; consulta su autorización." if self.client.device else "Equipo sin registrar")
            self.status.set(f"Sesión iniciada: {name} · {self.client.url}")
            self.notebook.select(self.equipment_page)
            self.finish_session(self.client, self.account, name)
        self.run(work, done)

    def handle_saved_session(self):
        if self.busy or self.client:
            return
        self.active_mappings.clear()
        url, email = self.url.get(), self.email.get().strip().lower()
        self.client, self.account = None, None
        def work():
            client = ApiClient(url)
            account = hashlib.sha256((client.url + '|' + email).encode()).hexdigest()
            client.token = self.state.secret(account + ':session')
            if not client.token:
                raise ValueError('No hay una sesión guardada para esta cuenta y servidor.')
            user = client.json('GET', '/usuarios/usuario')
            if user.get('correo_electronico', '').strip().lower() != email:
                raise ValueError('La sesión guardada pertenece a otra cuenta. Inicia sesión nuevamente.')
            client.device = self.state.get(account + ':device')
            client.device_key = self.state.secret(account + ':device-key')
            return client, account, user['nombre_completo']
        def done(result):
            self.client, self.account, name = result
            self.url.set(self.client.url)
            self.email.set(email)
            self.refresh_mappings()
            self.repositories = []
            self.status.set(f'Sesión recuperada: {name} · {self.client.url}')
            self.notebook.select(self.equipment_page)
            self.device_status.set('Consulta el estado de autorización del equipo.')
            self.finish_session(self.client, self.account, name)
        self.run(work, done)

    def handle_logout(self):
        if self.busy:
            return
        self.monitor.stop()
        self.active_mappings.clear()
        if self.account:
            self.state.forget_secret(self.account + ":session")
        self.client, self.account, self.repositories = None, None, []
        self.device_authorized = False
        self.state.set('signed_out', True)
        self.account_status.set('Sesión cerrada')
        self.folder.set('')
        self.mapping_tree.delete(*self.mapping_tree.get_children())
        self.status.set("Sesión cerrada. Las copias locales permanecen conservadas.")
        self.notebook.select(self.login_page)
        self.device_status.set("Sin consultar")
        self.update_controls()

    def handle_register(self):
        name = self.machine.get().strip()
        def work():
            client = self.require_session()
            if client.device:
                return client.device_status(client.device)
            data = client.json("POST", "/sync/windows/dispositivos", {"nombre": name, "instalacion": self.state.get("installation")})
            self.state.save_secret(self.account + ":device-key", data["credencial"])
            self.state.set(self.account + ":device", data["equipo"])
            client.device, client.device_key = data["equipo"], data["credencial"]
            self.state.set(self.account + ':machine', name)
            self.state.set('machine', name)
            return data["estado"]
        self.run(work, self.show_device_status)

    def show_device_status(self, status):
        self.device_authorized = status == 'autorizado'
        self.update_controls()
        self.device_status.set({"pendiente": "Pendiente: un administrador debe autorizar este equipo en Gekko.",
            "autorizado": "Equipo autorizado. La API y los permisos locales deben estar habilitados.",
            "revocado": "Equipo revocado. Su acceso no puede reactivarse con esta instalación."}.get(status, status))
        self.status.set(self.device_status.get())
        if status == 'autorizado':
            self.notebook.select(self.folders_page)
            self.refresh_mappings()
            self.active_mappings = {row['id'] for row in self.state.mappings(self.account)} if Workspace(self.state, self.account).config() and self.state.get(self.account + ':workspace_active', False) else set()
            if self.auto_sync.get() and self.active_mappings:
                self.status.set('Sesión y equipo autorizados. Revisión automática activa.')
        else:
            self.active_mappings.clear()

    def handle_status(self):
        def work():
            client = self.require_session()
            if not client.device:
                raise ValueError("Registra este equipo primero.")
            return client.device_status(client.device)
        self.run(work, self.show_device_status)

    def handle_repositories(self):
        if self.account and Workspace(self.state, self.account).config():
            self.handle_mapping()
        else:
            self.status.set('Elige la carpeta principal y pulsa Configurar repositorios.')

    def handle_choose_folder(self):
        if self.account and Workspace(self.state, self.account).config():
            self.status.set('La carpeta principal ya está guardada. Su traslado requiere una migración independiente.')
            return
        selected = filedialog.askdirectory(parent=self.root, title="Carpeta principal para los repositorios", mustexist=True)
        if selected:
            self.folder.set(selected)

    def refresh_mappings(self):
        selected = self.mapping_tree.selection()
        self.mapping_tree.delete(*self.mapping_tree.get_children())
        rows = self.state.mappings(self.account) if self.account else []
        config = Workspace(self.state, self.account).config() if self.account else None
        self.folder.set(config['root'] if config else rows[0]['folder'] if rows else '')
        for row in rows:
            self.mapping_tree.insert("", "end", iid=str(row["id"]), values=(row["name"], row["folder"]))
        children = self.mapping_tree.get_children()
        if children:
            saved = str(self.state.get(self.account + ':selected_mapping', ''))
            choice = selected[0] if selected and selected[0] in children else saved if saved in children else children[0]
            self.mapping_tree.selection_set(choice)
            self.handle_mapping_selection()

    def handle_mapping(self):
        try:
            client = self.require_session()
            if not self.device_authorized or not self.folder.get():
                raise ValueError('Se requiere un equipo autorizado y una carpeta principal.')
            folder, account = self.folder.get(), self.account
            def work():
                repositories = client.repositories()
                return Workspace(self.state, account).configure(folder, repositories)
            def done(rows):
                self.refresh_mappings()
                self.status.set(f'{len(rows)} repositorio(s) configurados. Pulsa Sincronizar todos.')
            self.run(work, done)
        except Exception as error:
            self.status.set(str(error))

    def handle_sync(self):
        if not self.client or not self.device_authorized or not Workspace(self.state, self.account).config():
            self.status.set('Inicia sesión, autoriza el equipo y configura la carpeta principal.')
            return
        self.state.set(self.account + ':workspace_active', True)
        self.active_mappings = {row['id'] for row in self.state.mappings(self.account)}
        self.run_workspace_sync()

    def run_workspace_sync(self, local_events=None):
        client, account = self.client, self.account
        if local_events is None:
            self.last_remote_check = time.monotonic()
        def done(result):
            self.refresh_mappings()
            self.status.set('Revisión terminada. Consulta Actividad para ver publicaciones y avisos.')
        self.run(lambda: Workspace(self.state, account).sync(client, self.report, local_events=local_events), done)

    def handle_auto_tick(self):
        if not self.exit_requested and not self.busy and self.client and self.device_authorized and self.auto_sync.get() and self.account and self.state.get(self.account + ':workspace_active', False):
            self.update_monitor()
            events = self.monitor.ready()
            if time.monotonic() - self.last_remote_check >= 300:
                self.run_workspace_sync()
            elif events:
                self.report('Cambios locales detectados; preparando sincronización.')
                self.run_workspace_sync(local_events=events)
        self.root.after(1000, self.handle_auto_tick)

    def handle_close(self):
        if self.close_to_tray.get() and self.tray:
            self.handle_hide()
        else:
            self.handle_exit()


def main():
    root = tk.Tk()
    lock = None
    try:
        state = State()
        lock = state.claim_instance()
        Application(root, state, background='--background' in sys.argv)
    except Exception as error:
        messagebox.showerror("Gekko Sync", str(error), parent=root)
        root.destroy()
        if lock:
            lock.close()
        return
    try:
        root.mainloop()
    finally:
        lock.close()
