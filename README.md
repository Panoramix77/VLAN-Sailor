# ⚓ VLAN-Sailor ⚓

Herramienta gráfica para Linux que permite cambiar la conectividad de red entre VLANs de forma rápida sobre un enlace trunk 802.1Q, con detección pasiva automática de VLANs mediante LLDP.

---

## Índice

1. [Motivación y objeto](#1-motivación-y-objeto)
2. [Requisitos](#2-requisitos)
3. [Instalación](#3-instalación)
4. [Estructura de archivos](#4-estructura-de-archivos)
5. [Uso de la aplicación](#5-uso-de-la-aplicación)
6. [Detección automática de VLANs mediante LLDP](#6-detección-automática-de-vlans-mediante-lldp)
7. [Configuración del switch Allied Telesis](#7-configuración-del-switch-allied-telesis)
8. [Consideraciones técnicas](#8-consideraciones-técnicas)
9. [Limitaciones conocidas](#9-limitaciones-conocidas)
10. [Notas de versión](#10-Notas-de-versión)

---

## 1. Motivación y objeto

En entornos donde un equipo Linux se conecta a una infraestructura de red segmentada en múltiples VLANs a través de un enlace trunk, cambiar de VLAN normalmente implica ejecutar una secuencia de comandos `ip link` manualmente cada vez: eliminar la subinterfaz existente, crear una nueva, levantar el enlace, solicitar dirección por DHCP o asignar una IP estática. En entornos con decenas o centenares de VLANs, y donde este cambio se realiza con frecuencia (técnicos de campo, equipos de auditoría, laboratorios de integración), ese flujo resulta tedioso y propenso a errores.

**VLAN-Sailor** resuelve exactamente eso: proporciona una interfaz gráfica que permite seleccionar una VLAN y conectarse a ella con un solo clic, gestionando automáticamente toda la secuencia de comandos del kernel. El flujo de trabajo habitual es:

```
arrancar la app → escaneo LLDP automático → ver las VLANs del puerto → clic → conectado
```

La herramienta está pensada para correr sobre el propio equipo que necesita cambiar de VLAN, conectado mediante un enlace trunk desde su interfaz física hasta un switch de distribución o core.

---

## 2. Requisitos

### Sistema operativo

Linux con kernel ≥ 5.x. Probado sobre **Arch Linux** y **Fedora**. Debería funcionar en cualquier distribución con `systemd` e `iproute2`.

### Paquetes del sistema

| Paquete | Uso | Arch Linux | Fedora / RHEL |
| --- | --- | --- | --- |
| `python` ≥ 3.10 | Runtime | `python` | `python3` |
| `tk` | Backend gráfico de customtkinter | `tk` | `python3-tkinter` |
| `iproute2` | Gestión de interfaces y VLANs | `iproute2` | `iproute` |
| `lldpd` | Recepción y consulta de tramas LLDP | `lldpd` | `lldpd` |
| `dhclient` o `dhcpcd` | Cliente DHCP | `dhcpcd` | `dhcp-client` |

La aplicación **requiere ejecutarse como root** para poder crear y eliminar subinterfaces de red mediante `ip link`.

### Python (entorno virtual)

Las dependencias Python se instalan en un entorno virtual aislado, sin tocar los paquetes del sistema. La única dependencia es:

- `customtkinter` — librería de UI moderna sobre Tkinter

---

## 3. Instalación

### Clonado del repositorio

```bash
https://gitea.ast3rix.myds.me/panoramix/VLAN-Sailor.git
cd vlan-manager
```

### Instalación del entorno

El script `instalar.sh` detecta la distribución automáticamente e instala los paquetes del sistema necesarios, crea el entorno virtual Python e instala `customtkinter` dentro de él. Solo es necesario ejecutarlo una vez.

```bash
bash instalar.sh
```

Al finalizar crea también un acceso directo `.desktop` en el escritorio.

### Ejecución

```bash
bash lanzar.sh
```

El lanzador detecta si hay herramienta gráfica de autenticación disponible (`pkexec`, `gksudo`, `kdesudo`) y solicita contraseña mediante ventana emergente. Si no hay ninguna disponible, abre un terminal con `sudo`.

También puede lanzarse directamente desde consola:

```bash
sudo /ruta/al/venv/bin/python3 vlan_manager.py
```

---

## 4. Estructura de archivos

```
vlan-manager/
├── vlan_manager.py   # aplicación principal
├── vlans.csv         # catálogo de VLANs (fuente estática, opcional)
├── instalar.sh       # instalador del entorno
└── lanzar.sh         # lanzador con elevación de privilegios
```

### Formato de `vlans.csv`

El archivo CSV actúa como fuente de referencia estática y es opcional si se usa detección LLDP. Cuando ambas fuentes están presentes, la información del CSV (descripción extendida) enriquece los datos recibidos por LLDP.

```csv
id,nombre,descripcion
1,LAN-CONTROLADOR,Red de gestión de equipos
100,GOTHAM,Red de datos edificio principal
101,CESIN,Centro de simulación
2130,ZONA_9_R4_2130,Zona 9 rack 4
```

---

## 5. Uso de la aplicación

### Interfaz principal

La ventana se divide en tres zonas:

**Barra superior** — muestra el nombre de la herramienta y permite seleccionar la interfaz física trunk (`enp0s20f0u3`, `eno2`, etc.). La aplicación detecta automáticamente las interfaces del sistema y preselecciona la más probable mediante un sistema de puntuación heurístico (carrier activo, velocidad, presencia de subinterfaces VLAN ya creadas, patrón de nombre).

**Panel lateral izquierdo** — dividido en dos secciones:

- `⬡ LLDP — Switch vecino`: VLANs detectadas en tiempo real desde el switch mediante LLDP. Incluye botón de escaneo manual y muestra el nombre del switch y el puerto al que está conectado el equipo.
- `📄 VLANs del archivo CSV`: catálogo estático leído de `vlans.csv`, con buscador por ID, nombre o descripción.

**Panel principal** — muestra la conexión activa (VLAN ID, nombre, dirección IP con máscara, velocidad de enlace), el detalle de la VLAN seleccionada, el selector de modo IP y el botón de conexión.

### Flujo de trabajo típico

1. Arrancar la aplicación — el escaneo LLDP se lanza automáticamente 1,5 s después del inicio.
2. Seleccionar la VLAN deseada desde la lista LLDP o desde el CSV.
3. Elegir el modo de direccionamiento: **DHCP automático** o **IP estática** (con campo de gateway opcional).
4. Pulsar **⚡ Conectar a esta VLAN**.

La aplicación elimina la subinterfaz VLAN anterior, crea la nueva y aplica la configuración de red. Todo queda registrado en el log de actividad de la parte inferior.

### Modo IP estática

Al seleccionar _IP estática_ aparecen dos campos:

- **IP / Máscara**: formato CIDR obligatorio — `192.168.10.5/24`
- **Gateway**: dirección del gateway por defecto — opcional

La aplicación valida el formato CIDR antes de ejecutar ningún comando.

---

## 6. Detección automática de VLANs mediante LLDP

### Estrategia general

Los switches modernos emiten periódicamente tramas LLDP (Link Layer Discovery Protocol, IEEE 802.1AB) por cada puerto activo. Estas tramas son Layer 2 puro (`EtherType 0x88CC`) y viajan **sin etiqueta 802.1Q**, por lo que el equipo Linux las recibe directamente sobre la interfaz física, sin necesidad de tener ninguna subinterfaz VLAN activa.

La extensión IEEE 802.1 de LLDP define el TLV `VLAN Name` (organizationally-specific, OUI `00-80-C2`, SubType 3), que anuncia todas las VLANs permitidas en el puerto junto con sus nombres. El demonio `lldpd` recibe y almacena esas tramas en memoria; `lldpctl` las consulta y las expone en distintos formatos.

La aplicación usa `lldpctl -f keyvalue` para obtener una salida estructurada línea a línea, que parsea internamente para extraer los IDs y nombres de VLAN.

### Requerimientos en el equipo Linux

```bash
# Instalar lldpd
# Arch Linux:
sudo pacman -S lldpd

# Fedora:
sudo dnf install lldpd

# Activar e iniciar el demonio
sudo systemctl enable --now lldpd
```

`lldpd` debe estar corriendo **antes** de conectar el cable o, al menos, antes de que el switch emita su siguiente trama (cada 30 s por defecto). La interfaz física debe estar levantada:

```bash
sudo ip link set enp0s20f0u3 up
```

No es necesario asignar ninguna dirección IP a la interfaz para que LLDP funcione.

### Verificación manual

Antes de usar la aplicación, conviene confirmar que las tramas llegan correctamente:

```bash
# Ver vecinos LLDP en formato legible
sudo lldpctl

# Ver la salida que consume la aplicación
sudo lldpctl -f keyvalue | grep -E "chassis|port\.|vlan"

# Captura de bajo nivel — confirmar que las tramas salen del switch
sudo tcpdump -i enp0s20f0u3 -nn ether proto 0x88cc -v
```

Si `lldpctl` no muestra vecinos tras 60 s, revisar que el switch tiene LLDP activo en el puerto y que la interfaz tiene carrier (cable conectado).

### Parser: formato real de lldpd con Allied Telesis

> **Nota**: el parser de detección LLDP está probado y ajustado para switches **Allied Telesis** con firmware **AlliedWare Plus (AWP)**. Otros fabricantes pueden generar el TLV `vlan-name` en un formato diferente dentro de `lldpctl -f keyvalue` y requerir ajustes en la función `lldp_scan()`.

La salida real de `lldpctl -f keyvalue` con Allied Telesis AWP no sigue el esquema `vlan.vlan-name=NOMBRE` que cabría esperar. El formato efectivo es el siguiente, con tres líneas por VLAN siempre en el mismo orden:

```
lldp.enp0s20f0u3.vlan.vlan-id=1
lldp.enp0s20f0u3.vlan.pvid=yes
lldp.enp0s20f0u3.vlan=LAN-CONTROLADOR
lldp.enp0s20f0u3.vlan.vlan-id=100
lldp.enp0s20f0u3.vlan.pvid=no
lldp.enp0s20f0u3.vlan=GOTHAM
```

El nombre de la VLAN aparece en una línea donde **la propia clave termina en `.vlan`** y el valor es el nombre — sin ningún subtlv `vlan-name`. La función `lldp_scan()` implementa una máquina de estados por interfaz que abre una entrada de VLAN al detectar `vlan.vlan-id` y la cierra con el nombre al detectar la línea corta `vlan=NOMBRE`.

---

## 7. Configuración del switch Allied Telesis

A continuación se describe la configuración mínima necesaria en un AT-x530 con AlliedWare Plus para que la detección LLDP funcione correctamente.

### Consideraciones previas sobre el trunk

**Añadir VLANs al trunk de forma explícita.** En AWP, el comando `switchport trunk allowed vlan all` tiene un comportamiento diferente al esperado en otros fabricantes: no añade dinámicamente las VLANs futuras al trunk. La práctica recomendada es añadir explícitamente cada VLAN:

```
awplus(config-if)# switchport trunk allowed vlan add 100,101,102,103,2130
```

**VLAN nativa.** La VLAN nativa por defecto es la VLAN 1, y **no aparece declarada explícitamente en `show running-config`** - es el comportamiento implícito del switch -. Si se configurara `switchport trunk native vlan none`, sí aparecería en la configuración y el puerto dejaría de tener VLAN nativa. Por tanto, la ausencia de esa línea en el `show run` confirma que la VLAN 1 es la nativa.

### Configuración del puerto trunk

```
! Crear las VLANs
vlan database
  vlan 100 name GOTHAM
  vlan 101 name CESIN
  vlan 102 name INTERVENCION
  vlan 2130 name ZONA_9_R4_2130
exit

! Configurar el puerto trunk
interface port1.0.17
  switchport mode trunk
  switchport trunk allowed vlan add 100,101,102,103,2130
  ! La VLAN 1 es la nativa de forma implícita — no declarar native vlan
exit
```

### Configuración LLDP

Para que el switch anuncie los nombres de VLAN es necesario habilitar **todos los TLVs** en el puerto. La habilitación global activa el protocolo; la configuración por puerto selecciona qué información se transmite:

```
! Activar LLDP globalmente
lldp run
lldp timer 30
lldp holdtime-multiplier 4

! Habilitar todos los TLVs en el puerto trunk 1.0.17 (por ejemplo)
interface port1.0.17
  lldp transmit
  lldp receive
  lldp tlv-select all
exit

write memory
```

El TLV `vlan-name` está incluido dentro de `tlv-select all`. Si se prefiere habilitación selectiva, el TLV específico es:

```
lldp tlv-select vlan-name
```

Para forzar el reenvío inmediato sin esperar el próximo ciclo de 30 s:

```
lldp transmit-delay 1
```

### Verificación en el switch

```
awplus# show lldp interface port1.0.17
awplus# show vlan port port1.0.17
```

La segunda salida debe mostrar la VLAN 1 como `(u)` (untagged/nativa) y el resto como `(t)` (tagged).

---

## 8. Consideraciones técnicas

### Límite de longitud de nombre de interfaz (IFNAMSIZ)

El kernel Linux limita los nombres de interfaz de red a **15 caracteres útiles** (`IFNAMSIZ = 16`, incluyendo el terminador nulo). Con interfaces de nombre largo como `enp0s20f0u3` (11 caracteres) y VLANs de cuatro dígitos, el nombre canónico `enp0s20f0u3.2130` tiene 16 caracteres y es rechazado por el kernel.

La función `vlan_iface_name()` resuelve esto recortando el nombre base por la derecha hasta que el nombre completo quepa en 15 caracteres, preservando siempre el VLAN ID intacto:

```
enp0s20f0u3.10   →  enp0s20f0u3.10    (14 chars, sin cambios)
enp0s20f0u3.2130 →  enp0s20f0u.2130   (15 chars, base recortada)
```

La detección de la interfaz activa usa el campo `@padre` que devuelve `ip -o link show` (por ejemplo `enp0s20f0u.2130@enp0s20f0u3`) para identificar la subinterfaz correcta independientemente del nombre acortado.

### Normalización del nombre de interfaz

`ip -o link show` puede devolver nombres de subinterfaz con el sufijo `@padre` — por ejemplo `enp0s20f0u3.100@enp0s20f0u3`. Todos los comandos de gestión (`ip link delete`, `ip addr flush`, etc.) requieren solo la parte anterior al `@`. La aplicación normaliza este sufijo en todos los puntos donde procesa nombres de interfaz.

### Extracción del VLAN ID desde el kernel

Para obtener el VLAN ID de la subinterfaz activa no se usa el nombre de la interfaz (que puede estar acortado), sino `ip -d link show <interfaz>`, cuya salida incluye la línea:

```
vlan protocol 802.1Q id 2130 <REORDER_HDR>
```

El ID se extrae con una expresión regular sobre esa línea, lo que garantiza la lectura correcta independientemente del nombre.

### Gestión del cliente DHCP

La aplicación intenta usar `dhclient` en primer lugar y cae a `dhcpcd` si no está disponible. Al desconectar, libera la concesión DHCP con `-r` (dhclient) o `-k` (dhcpcd) antes de eliminar la interfaz para evitar concesiones huérfanas en el servidor.

### Ventana de consola

Al lanzar la aplicación con `sudo`, la función `detach_from_terminal()` crea una nueva sesión de proceso (`os.setsid()`) y redirige `stdin/stdout/stderr` a `/dev/null`, desvinculando la GUI del terminal que la lanzó. El terminal queda libre inmediatamente.

---

## 9. Limitaciones conocidas

**Una sola subinterfaz VLAN activa por interfaz física.** La aplicación asume y gestiona exactamente una subinterfaz VLAN sobre cada interfaz trunk. Si existieran varias de forma previa (configuración manual externa), solo detectará y eliminará la primera que encuentre.

**Parser LLDP ajustado a Allied Telesis AWP.** El formato de `lldpctl -f keyvalue` varía entre versiones de `lldpd` y entre fabricantes. El parser actual está verificado contra Allied Telesis AlliedWare Plus 5.5.x. Con otros fabricantes los TLVs de VLAN pueden aparecer bajo claves distintas y requerir adaptación de la función `lldp_scan()`.

**TTL corto en lldpd.** Si el switch tiene `holdtime-multiplier 1` (TTL = 30 s), un ciclo perdido puede hacer que `lldpd` descarte al vecino. Se recomienda configurar `holdtime-multiplier 4` en el switch para un TTL de 120 s, más robusto ante retardos en la red de gestión.

**Requiere root.** La creación y eliminación de subinterfaces VLAN mediante `ip link` requiere privilegios de superusuario. No hay alternativa a esto salvo configurar capacidades de red específicas (`CAP_NET_ADMIN`) sobre el intérprete Python, lo cual escapa al alcance de esta herramienta.

inmediatamente.

---

## 10. Notas de versión

Esta herramienta está creada por el departamento I3D-BC de la BNR y está orientada a su uso propio, pero siempre es bueno poder compartir el código y el conocimiento al resto del mundo 👨‍✈️.

Panoramix, 24 de marzo de 2026