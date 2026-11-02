# Gekko Sync para Windows

Cliente Python independiente. Inicia con `Iniciar Gekko Sync.bat` o `.venv\Scripts\python.exe run.py`. Las dependencias del cliente incluyen watchdog, pystray y Pillow. En instalaciones nuevas ejecuta `.venv\Scripts\python.exe -m pip install -e .`.

## Cuenta y equipo

Usa el correo y contraseña de Gekko. La contraseña no se guarda; la sesión y la credencial del equipo se protegen con Windows DPAPI. Al abrir, valida la sesión guardada y consulta la autorización del equipo. Si caducó, solicita acceso de nuevo. Con sesión activa, cuenta y servidor quedan bloqueados; el nombre del equipo queda bloqueado tras registrarlo. Cerrar sesión permite cambiar de cuenta y detiene la restauración automática.

La API Windows debe estar habilitada y el equipo autorizado en la web. Se necesita Lectura y Sync local en los repositorios; Editar local permite publicar cambios. Las reglas se verifican nuevamente en cada operación. Los permisos de descarga/carga manual del kiosco son independientes.

## Carpeta principal y repositorios

1. En Tus carpetas, elige una carpeta principal existente, por ejemplo `E:\aca`.
2. Pulsa **Configurar repositorios**. La aplicación consulta los repositorios autorizados y crea una subcarpeta por cada uno.
3. Pulsa **Sincronizar todos** para recibir su contenido y publicar cambios permitidos.
4. Con **Sincronización automática** marcado, continúa mientras la aplicación esté abierta. Al reabrir, valida la cuenta y autorización antes de retomar la revisión iniciada.

Ejemplo:

```
E:\aca\
  Juridico\       administrado por Gekko
  Contabilidad\   administrado por Gekko
  Mis cosas\      ajeno al sistema; se ignora
```

La carpeta principal nunca se usa como raíz de publicación. Solo se recorren las subcarpetas registradas por ID de repositorio en SQLite y con su identificación `.gekko-repository.json`. Las carpetas que el usuario cree directamente en la principal y los archivos sueltos de esa principal no se leen para sincronización ni se suben. No borrar ni editar el archivo de identificación.

Una carpeta preexistente con el nombre de un repositorio no se adopta ni mezcla: Gekko crea otra con un sufijo. Repositorios con nombres iguales tienen carpetas independientes. Un cambio de nombre remoto conserva la ruta registrada por ID. Al recibir acceso a un nuevo repositorio, la siguiente revisión crea su carpeta. Si pierde acceso, deja de sincronizarlo y conserva su copia local.

## Actualizar una vinculación antigua

Si Jurídico estaba vinculado directamente a `E:\aca`, usa esa misma carpeta principal y pulsa Configurar repositorios. El cliente traslada a su subcarpeta solo los archivos registrados en el estado anterior, incluidas sus ediciones locales, y conserva los IDs, revisiones y operaciones pendientes. Las carpetas registradas se recrean en el destino; no se mueve su contenido ajeno. Los archivos no registrados y directorios antiguos permanecen en la principal y pasan a ignorarse.

El traslado tiene un plan persistente para continuar después de una interrupción. Ante una colisión o archivo cambiado durante el traslado, se detiene sin sobrescribir. No mover los archivos manualmente durante la operación. El traslado posterior de una carpeta principal ya configurada requiere una migración independiente.

## Publicaciones y conservación

Se publican carpetas nuevas, archivos nuevos (incluidos vacíos) y versiones de archivos modificados dentro de repositorios administrados con Editar local. Los temporales de Office, descargas parciales y nombres `.gekko-*` quedan excluidos. No se siguen enlaces o uniones.

Las eliminaciones locales no se envían. Los archivos ausentes pueden recuperarse del servidor. Renombrados o movimientos locales todavía pueden interpretarse como archivos nuevos y recuperar los originales; no reorganizar repositorios así por ahora.

El contenido se verifica con SHA-256. La cola conserva copias estables y claves de idempotencia para reintentar fallos sin duplicar cargas. Los conflictos retenidos se revisan en la web. Un error de un repositorio se registra en Actividad y permite continuar con los demás; sesión vencida o API deshabilitada detienen el ciclo.

- `.gekko-conflictos`: ediciones locales conservadas antes de recibir la copia remota.
- `.gekko-recuperados`: archivos retirados por eliminaciones explícitas del servidor.
- `%LOCALAPPDATA%\GekkoSync\state.db`: configuración, identidades, vínculos, planes y operaciones.
- `%LOCALAPPDATA%\GekkoSync\outbox`: contenido pendiente o conservado para revisión.

No borrar manualmente el estado ni las copias pendientes. Una carpeta ausente o sin identificación válida no se adopta automáticamente. El cliente permite una sola instancia por usuario Windows.

## Pruebas y pendientes

`.venv\Scripts\python.exe -m unittest discover -s tests -v`

Pendientes: resolución visual de cola, renombrados/movimientos y eliminaciones locales, servicio del sistema. La revisión actual calcula hashes; no hay servicio Windows instalado.

## Detección de cambios locales

Con Sincronización automática activa, watchdog observa únicamente las carpetas de repositorios administrados y autorizados. No observa la carpeta principal ni las carpetas ajenas. Detecta creaciones, modificaciones y destinos de guardados mediante renombrado; agrupa los eventos repetidos y espera unos 2 segundos sin cambios antes de iniciar la publicación. El tiempo total también depende del tamaño del archivo y de la conexión.

Los eventos recibidos durante una sincronización permanecen pendientes para el siguiente ciclo. Los temporales, rutas internas y avisos de lectura se ignoran. Las notificaciones producidas por descargas confirmadas se comparan con el estado para evitar ciclos de publicación. Una revisión completa cada 5 minutos recibe cambios remotos y recupera eventos perdidos; Sincronizar todos sigue disponible para revisar al momento.

Desmarcar Sincronización automática, cerrar sesión o cerrar la aplicación detiene el detector. La revocación de permisos se verifica con el servidor antes de publicar; al actualizarse la lista de repositorios autorizados se retiran sus observadores. Las eliminaciones locales siguen sin propagarse. Si el detector falla, se informa en Actividad y permanece la revisión de respaldo.

Referencia: https://python-watchdog.readthedocs.io/en/stable/quickstart.html


## Ventana compacta, bandeja e inicio con Windows

La interfaz usa una ventana de 860 × 640 con Cuenta, Equipo, Repositorios, Actividad y Preferencias. El botón Ocultar mantiene la sincronización y deja el icono de Gekko junto al reloj. Windows puede agruparlo dentro de los iconos ocultos.

Desde el icono puedes abrir la ventana, sincronizar, activar o pausar la sincronización automática y salir. En Preferencias puedes elegir que la X oculte la ventana (activado por defecto). Salir detiene el detector y retira el icono; si hay una operación en curso, espera a que termine y no arranca otra. Si el icono no puede iniciarse, la ventana permanece visible.

Iniciar Gekko Sync con Windows es opcional y no se activa por instalar esta actualización. Al marcarlo, registra únicamente GekkoSync en HKCU\Software\Microsoft\Windows\CurrentVersion\Run para el usuario actual, sin administrador, con las rutas absolutas de pythonw.exe y run.py y el argumento --background. Al desmarcarlo elimina solo su entrada. No incluye contraseñas ni tokens en el comando. Al iniciar sesión en Windows abre oculto y recupera la sesión Gekko si sigue vigente. La sincronización conserva sus requisitos de autorización y configuración previos.

Mantén el proyecto y su entorno virtual en la misma ubicación mientras el inicio esté activado. Si los trasladas, desactívalo antes y actívalo desde la nueva ubicación. No se instala un servicio del sistema ni se ejecuta antes del inicio de sesión de Windows.


## Instalador de Windows x64

Archivo generado: dist\GekkoSync-Setup-0.1.0-x64.exe. Incluye el ejecutable y Python con sus dependencias; los usuarios finales no necesitan instalar Python. Antes de instalar o actualizar, usa Salir en Gekko Sync, también desde el icono junto al reloj. Instala para el usuario actual en %LOCALAPPDATA%\Programs\GekkoSync, crea accesos directos y registra la desinstalación. No requiere administrador.

Los repositorios y %LOCALAPPDATA%\GekkoSync se conservan al actualizar y desinstalar. La desinstalación elimina la entrada de inicio automático solo si apunta al ejecutable instalado. Activa Iniciar con Windows desde Preferencias de la aplicación instalada para usar su nueva ruta.

El ejecutable y el instalador aún no tienen firma Authenticode. Para distribución empresarial se debe firmar con el certificado del editor. Se comprobó el arranque y cierre del ejecutable con un perfil temporal, sin conectar una cuenta real; falta validar el asistente completo en un equipo limpio antes de distribuirlo masivamente.

Para regenerar en Windows x64: instala las dependencias del proyecto y requirements-build.txt en .venv, consigue NSIS 3.13 desde su sitio oficial y ejecuta scripts\build_windows.ps1 -NsisCompiler <ruta-a-makensis.exe>. El compilador portátil local se encuentra en .tools\nsis-3.13. El script ejecuta las pruebas, empaqueta con PyInstaller y genera el instalador NSIS. No incorpora .env, estado local, sesiones ni documentos del usuario.

Referencias de empaquetado: https://pyinstaller.org/en/stable/runtime-information.html y https://nsis.sourceforge.io/Docs/Chapter4.html


## Compilar para diferentes clientes y publicar en la API

Configura GEKKO_API_URL en .env (o usa un archivo por cliente) y ejecuta scripts\build_windows.ps1 -EnvFile .env. La dirección incorporada en el instalador tiene prioridad sobre SQLite y bloquea el cambio manual del servidor. La compilación produce también dist\GekkoSync-release.json para publicar el paquete verificado en la API correspondiente.

Guía completa: [Publicar instalador de Gekko Sync](../api/docs/publicar-instalador-gekko-sync.md). El paquete local actual usa http://localhost:7509/api; genera uno con la URL HTTPS real antes de distribuirlo a un cliente.












1) GEKKO_API_URL=https://api.cliente.com/api

2) .\scripts\build_windows.ps1 -EnvFile .env


3) Copia estos dos archivos de sync/dist:

GekkoSync-Setup-0.1.0-x64.exe
GekkoSync-release.json

4) Pégalos juntos en: 
api/installer-build/

5) Desde api, ejecuta con la URL que usaste al compilar:
.\env\Scripts\python.exe scripts/publish_sync_installer.py --release installer-build\GekkoSync-release.json --expected-api-url https://api.cliente.com/api


%LOCALAPPDATA%\Programs\GekkoSync\Desinstalar.exe