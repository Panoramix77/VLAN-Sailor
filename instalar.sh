#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  VLAN-Sailor — Script de instalación
#  Ejecutar UNA SOLA VEZ para preparar el entorno
#  Compatible con: Fedora, Arch Linux, Debian/Ubuntu y derivados
# ─────────────────────────────────────────────────────────────

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
PYTHON_MIN_VERSION="3.10"

# ── Colores para mensajes ─────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC}  $1"; }
ok()      { echo -e "${GREEN}[OK]${NC}    $1"; }
warn()    { echo -e "${YELLOW}[AVISO]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
step()    { echo -e "\n${BOLD}▶  $1${NC}"; }

# ── Banner ────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}${BOLD}╔════════════════════════════════════════╗${NC}"
echo -e "${CYAN}${BOLD}║       VLAN-Sailor — Instalación       ║${NC}"
echo -e "${CYAN}${BOLD}╚════════════════════════════════════════╝${NC}"
echo ""

# ── 1. Detectar distribución ──────────────────────────────────
step "Detectando distribución Linux..."

DISTRO=""
PKG_MANAGER=""

if [ -f /etc/os-release ]; then
    . /etc/os-release
    DISTRO="${ID,,}"   # lowercase
fi

case "$DISTRO" in
    fedora|rhel|centos|rocky|almalinux)
        PKG_MANAGER="dnf"
        PYTHON_PKG="python3 python3-pip python3-tkinter"
        info "Distribución detectada: $PRETTY_NAME"
        ;;
    arch|manjaro|endeavouros|garuda)
        PKG_MANAGER="pacman"
        PYTHON_PKG="python python-pip tk"
        info "Distribución detectada: $PRETTY_NAME"
        ;;
    ubuntu|debian|linuxmint|pop|elementary)
        PKG_MANAGER="apt"
        PYTHON_PKG="python3 python3-pip python3-tk python3-venv"
        info "Distribución detectada: $PRETTY_NAME"
        ;;
    *)
        warn "Distribución no reconocida ('$DISTRO'). Intentando continuar..."
        PKG_MANAGER="unknown"
        ;;
esac

# ── 2. Verificar/instalar Python ──────────────────────────────
step "Verificando Python 3..."

PYTHON_BIN=""
for bin in python3 python3.12 python3.11 python3.10; do
    if command -v "$bin" &>/dev/null; then
        VER=$("$bin" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        MAJOR=$(echo "$VER" | cut -d. -f1)
        MINOR=$(echo "$VER" | cut -d. -f2)
        if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 10 ]; then
            PYTHON_BIN="$bin"
            ok "Python $VER encontrado: $(which $bin)"
            break
        else
            warn "Python $VER encontrado pero se necesita >= $PYTHON_MIN_VERSION"
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    warn "Python 3.10+ no encontrado. Intentando instalar..."
    if [ "$PKG_MANAGER" = "dnf" ]; then
        sudo dnf install -y $PYTHON_PKG
    elif [ "$PKG_MANAGER" = "pacman" ]; then
        sudo pacman -S --noconfirm $PYTHON_PKG
    elif [ "$PKG_MANAGER" = "apt" ]; then
        sudo apt update && sudo apt install -y $PYTHON_PKG
    else
        error "No se pudo instalar Python automáticamente. Instala Python 3.10+ manualmente."
    fi
    PYTHON_BIN="python3"
fi

# ── 3. Verificar tkinter (necesario para customtkinter) ───────
step "Verificando tkinter..."

if ! "$PYTHON_BIN" -c "import tkinter" &>/dev/null; then
    warn "tkinter no está disponible. Instalando..."
    if [ "$PKG_MANAGER" = "dnf" ]; then
        sudo dnf install -y python3-tkinter
    elif [ "$PKG_MANAGER" = "pacman" ]; then
        sudo pacman -S --noconfirm tk
    elif [ "$PKG_MANAGER" = "apt" ]; then
        sudo apt install -y python3-tk
    else
        error "Instala tkinter manualmente para tu distribución."
    fi
fi

if "$PYTHON_BIN" -c "import tkinter" &>/dev/null; then
    ok "tkinter disponible"
else
    error "No se pudo instalar tkinter. Consulta cómo instalarlo en tu distribución."
fi

# ── 4. Verificar iproute2 ─────────────────────────────────────
step "Verificando iproute2 (comando 'ip')..."

if command -v ip &>/dev/null; then
    ok "iproute2 disponible: $(ip -V 2>&1 | head -1)"
else
    warn "iproute2 no encontrado. Instalando..."
    if [ "$PKG_MANAGER" = "dnf" ]; then
        sudo dnf install -y iproute
    elif [ "$PKG_MANAGER" = "pacman" ]; then
        sudo pacman -S --noconfirm iproute2
    elif [ "$PKG_MANAGER" = "apt" ]; then
        sudo apt install -y iproute2
    fi
fi

# ── 5. Verificar cliente DHCP ─────────────────────────────────
step "Verificando cliente DHCP..."

if command -v dhclient &>/dev/null; then
    ok "dhclient disponible"
elif command -v dhcpcd &>/dev/null; then
    ok "dhcpcd disponible"
else
    warn "No se encontró dhclient ni dhcpcd. Instalando dhclient..."
    if [ "$PKG_MANAGER" = "dnf" ]; then
        sudo dnf install -y dhcp-client
    elif [ "$PKG_MANAGER" = "pacman" ]; then
        sudo pacman -S --noconfirm dhcpcd
    elif [ "$PKG_MANAGER" = "apt" ]; then
        sudo apt install -y isc-dhcp-client
    fi
fi

# ── 6. Crear entorno virtual ──────────────────────────────────
step "Creando entorno virtual Python..."

if [ -d "$VENV_DIR" ]; then
    warn "El entorno virtual ya existe. Actualizando..."
else
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    ok "Entorno virtual creado en $VENV_DIR"
fi

# ── 7. Instalar dependencias Python ──────────────────────────
step "Instalando dependencias Python (customtkinter)..."

"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install customtkinter --quiet

ok "customtkinter instalado correctamente"

# ── 8. Hacer ejecutable el launcher ──────────────────────────
step "Preparando lanzador..."

chmod +x "$SCRIPT_DIR/lanzar.sh"
ok "lanzar.sh listo"

# ── 9. Crear acceso directo .desktop (doble click en escritorio) ──
step "Creando acceso directo en el escritorio..."

DESKTOP_FILE="$HOME/Escritorio/vlan-sailor.desktop"
# Fallback para sistemas en inglés
[ -d "$HOME/Desktop" ] && DESKTOP_FILE="$HOME/Desktop/vlan-sailor.desktop"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=VLAN-Sailor
Comment=Gestor de VLANs para Arch Linux / Fedora
Exec=bash -c 'cd "$SCRIPT_DIR" && bash "$SCRIPT_DIR/lanzar.sh"'
Icon=network-wired
Terminal=false
Categories=Network;System;
StartupNotify=true
EOF

chmod +x "$DESKTOP_FILE"

# Algunos entornos requieren marcar el .desktop como de confianza
if command -v gio &>/dev/null; then
    gio set "$DESKTOP_FILE" metadata::trusted true 2>/dev/null || true
fi

ok "Acceso directo creado en el escritorio"

# ── Resumen final ─────────────────────────────────────────────
echo ""
echo -e "${CYAN}${BOLD}╔════════════════════════════════════════╗${NC}"
echo -e "${CYAN}${BOLD}║        Instalación completada ✔        ║${NC}"
echo -e "${CYAN}${BOLD}╚════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Ahora puedes:"
echo -e "  ${BOLD}·${NC} Hacer doble click en el icono del escritorio"
echo -e "  ${BOLD}·${NC} O ejecutar directamente:  ${CYAN}bash lanzar.sh${NC}"
echo ""
