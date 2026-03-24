#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  VLAN-Sailor — Lanzador
#  Doble click para abrir la aplicación.
#  Pide contraseña con ventana gráfica si hace falta sudo.
# ─────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python3"
APP="$SCRIPT_DIR/VLAN-Sailor.py"

# ── Verificar que la instalación se hizo ─────────────────────
if [ ! -f "$VENV_PYTHON" ]; then
    # Intentar mostrar error gráfico si hay entorno de escritorio
    MSG="VLAN-Sailor no está instalado.\n\nEjecuta primero:\n  bash instalar.sh"
    if command -v zenity &>/dev/null; then
        zenity --error --title="VLAN-Sailor" --text="$MSG" --width=320
    elif command -v kdialog &>/dev/null; then
        kdialog --error "$MSG" --title "VLAN-Sailor"
    else
        echo -e "$MSG"
    fi
    exit 1
fi

# ── Detectar herramienta gráfica para pedir contraseña ───────
# Orden de preferencia: pkexec > gksudo > kdesudo > zenity > x-terminal
SUDO_GUI=""

if command -v pkexec &>/dev/null; then
    SUDO_GUI="pkexec"
elif command -v gksudo &>/dev/null; then
    SUDO_GUI="gksudo"
elif command -v kdesudo &>/dev/null; then
    SUDO_GUI="kdesudo"
fi

# ── Función: lanzar con ventana gráfica de contraseña ────────
launch_with_gui_sudo() {
    if [ "$SUDO_GUI" = "pkexec" ]; then
        # pkexec necesita DISPLAY y XAUTHORITY para apps gráficas
        pkexec env \
            DISPLAY="$DISPLAY" \
            XAUTHORITY="$XAUTHORITY" \
            XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
            DBUS_SESSION_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS" \
            "$VENV_PYTHON" "$APP"

    elif [ "$SUDO_GUI" = "gksudo" ]; then
        gksudo -- "$VENV_PYTHON" "$APP"

    elif [ "$SUDO_GUI" = "kdesudo" ]; then
        kdesudo -- "$VENV_PYTHON" "$APP"

    else
        # Fallback: abrir terminal con sudo y esperar contraseña ahí
        launch_via_terminal
    fi
}

# ── Función: fallback por terminal ────────────────────────────
launch_via_terminal() {
    CMD="cd '$SCRIPT_DIR' && sudo '$VENV_PYTHON' '$APP'; echo '--- Puedes cerrar esta ventana ---'; read"

    if command -v gnome-terminal &>/dev/null; then
        gnome-terminal -- bash -c "$CMD"
    elif command -v konsole &>/dev/null; then
        konsole -e bash -c "$CMD"
    elif command -v xfce4-terminal &>/dev/null; then
        xfce4-terminal -e "bash -c \"$CMD\""
    elif command -v xterm &>/dev/null; then
        xterm -e bash -c "$CMD"
    else
        # Último recurso: sudo en la terminal actual
        cd "$SCRIPT_DIR"
        sudo "$VENV_PYTHON" "$APP"
    fi
}

# ── Lanzar ────────────────────────────────────────────────────
cd "$SCRIPT_DIR"

# Si ya somos root (caso raro), lanzar directo
if [ "$EUID" -eq 0 ]; then
    "$VENV_PYTHON" "$APP"
    exit $?
fi

# Intentar con herramienta gráfica de autenticación
if [ -n "$SUDO_GUI" ]; then
    launch_with_gui_sudo
else
    # Sin herramienta gráfica → terminal con sudo
    launch_via_terminal
fi
