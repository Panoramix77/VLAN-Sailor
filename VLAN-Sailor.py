#!/usr/bin/env python3
"""
VLAN-Sailor - Gestor gráfico de VLANs para Arch Linux
Requiere: customtkinter, iproute2, dhclient o dhcpcd
Ejecutar con: sudo python VLAN-Sailor.py
"""

import customtkinter as ctk
import subprocess
import csv
import os
import sys
import re
import threading
import time
from pathlib import Path
from datetime import datetime

# ──────────────────────────────────────────────
# CONFIGURACIÓN
# ──────────────────────────────────────────────
VLANS_FILE = Path(__file__).parent / "vlans.csv"
LOG_MAX_LINES = 200

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

COLORS = {
    "bg":        "#0d1117",
    "panel":     "#161b22",
    "card":      "#1c2128",
    "border":    "#30363d",
    "accent":    "#00bfae",
    "accent2":   "#0077b6",
    "success":   "#3fb950",
    "warning":   "#d29922",
    "error":     "#f85149",
    "text":      "#e6edf3",
    "muted":     "#8b949e",
    "highlight": "#1f6feb",
    "selected":  "#1a3a4a",
}

# ──────────────────────────────────────────────
# DETECCIÓN INTELIGENTE DE INTERFACES
# ──────────────────────────────────────────────

def run_cmd(cmd: list) -> tuple:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return 1, "", "Timeout"
    except FileNotFoundError:
        return 1, "", f"No encontrado: {cmd[0]}"


def _iface_speed(name: str) -> int:
    """Lee velocidad en Mbps desde /sys, 0 si no disponible."""
    try:
        return int(Path(f"/sys/class/net/{name}/speed").read_text().strip())
    except Exception:
        return 0


def _iface_carrier(name: str) -> bool:
    """True si la interfaz tiene cable/señal."""
    try:
        return Path(f"/sys/class/net/{name}/carrier").read_text().strip() == "1"
    except Exception:
        return False


def _iface_operstate(name: str) -> str:
    """up / down / unknown."""
    try:
        return Path(f"/sys/class/net/{name}/operstate").read_text().strip()
    except Exception:
        return "unknown"


def _iface_mac(name: str) -> str:
    try:
        return Path(f"/sys/class/net/{name}/address").read_text().strip()
    except Exception:
        return ""


def _has_existing_vlan_subifs(name: str) -> bool:
    """True si ya existe alguna subinterfaz VLAN sobre esta interfaz."""
    _, out, _ = run_cmd(["ip", "-o", "link", "show"])
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].rstrip(":").startswith(f"{name}."):
            return True
    return False


def _is_virtual(name: str) -> bool:
    """True si la interfaz es virtual (bridge, tun, veth, bond, dummy, etc.)."""
    if Path(f"/sys/devices/virtual/net/{name}").exists():
        return True
    for prefix in ("virbr", "docker", "veth", "tun", "tap", "dummy",
                   "bond", "team", "br-", "vxlan", "ovs"):
        if name.startswith(prefix):
            return True
    return False


def _trunk_score(info: dict) -> int:
    """
    Puntuación heurística para estimar probabilidad de ser interfaz trunk.
    Mayor puntuación = más probable que sea trunk.
    Criterios:
      - Tiene cable (carrier)         → +40
      - Estado operativo UP           → +20
      - Ya tiene subinterfaces VLAN   → +50  (casi certeza)
      - Velocidad 10G / 1G / 100M     → +30 / +20 / +10
      - Nombre típico de NIC física   → +10
    """
    score = 0
    if info["carrier"]:
        score += 40
    if info["operstate"] == "up":
        score += 20
    if info["has_vlan_subifs"]:
        score += 50
    spd = info["speed"]
    if spd >= 10000:
        score += 30
    elif spd >= 1000:
        score += 20
    elif spd >= 100:
        score += 10
    name = info["name"]
    for pat in (r"^eth\d+$", r"^en[opsx]", r"^eno\d+", r"^enp\d+s\d+"):
        if re.match(pat, name):
            score += 10
            break
    return score


def discover_interfaces() -> list:
    """
    Devuelve lista de dicts con info de cada interfaz física,
    ordenada por puntuación descendente (trunk más probable primero).
    """
    _, out, _ = run_cmd(["ip", "-o", "link", "show"])
    results = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        raw = parts[1].rstrip(":")
        if raw == "lo" or "." in raw or "@" in raw or _is_virtual(raw):
            continue
        carrier   = _iface_carrier(raw)
        operstate = _iface_operstate(raw)
        speed     = _iface_speed(raw)
        mac       = _iface_mac(raw)
        has_vlans = _has_existing_vlan_subifs(raw)
        info = {
            "name": raw, "carrier": carrier, "operstate": operstate,
            "speed": speed, "mac": mac, "has_vlan_subifs": has_vlans,
        }
        info["score"] = _trunk_score(info)
        results.append(info)
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def speed_label(mbps: int) -> str:
    if mbps <= 0:
        return "? Mbps"
    if mbps >= 1000:
        return f"{mbps // 1000} Gbps"
    return f"{mbps} Mbps"


# ──────────────────────────────────────────────
# LÓGICA DE RED (VLAN)
# ──────────────────────────────────────────────

def get_current_ip_and_mask(iface: str) -> tuple:
    """Devuelve (ip, prefixlen) p.ej. ('192.168.10.5', '24'), o ('', '')."""
    _, out, _ = run_cmd(["ip", "-o", "-4", "addr", "show", iface])
    for line in out.splitlines():
        parts = line.split()
        for i, p in enumerate(parts):
            if p == "inet" and i + 1 < len(parts):
                cidr = parts[i + 1]          # ej: 192.168.10.5/24
                if "/" in cidr:
                    ip, prefix = cidr.split("/", 1)
                    return ip, prefix
                return cidr, ""
    return "", ""


def cidr_to_mask(prefix: str) -> str:
    """Convierte prefijo CIDR a máscara decimal. '24' → '255.255.255.0'."""
    try:
        n = int(prefix)
        mask = (0xFFFFFFFF >> (32 - n)) << (32 - n)
        return ".".join(str((mask >> s) & 0xFF) for s in (24, 16, 8, 0))
    except (ValueError, TypeError):
        return ""


def get_link_speed_live(iface: str) -> int:
    """Lee la velocidad real negociada en Mbps desde /sys. 0 si no disponible."""
    try:
        return int(Path(f"/sys/class/net/{iface}/speed").read_text().strip())
    except Exception:
        return 0


IFNAMSIZ = 15  # límite real del kernel Linux (16 - 1 para el \0)


def vlan_iface_name(base_iface: str, vlan_id: int) -> str:
    """
    Genera un nombre de subinterfaz VLAN que siempre cabe en IFNAMSIZ (≤15 chars).

    Estrategia:
      1. Intentar el nombre canónico  '<base>.<vlan_id>'
      2. Si excede 15 chars, acortar la base hasta que quepa:
           'enp0s20f0u3' → 'enp0s20' → ... → mínimo 3 chars de base
      3. Si aun así no cabe (vlan_id de 4 dígitos con base muy larga),
         usar prefijo fijo 'vl' + vlan_id, que siempre cabe (máx 6 chars).

    Ejemplos:
      enp0s3,    10   → 'enp0s3.10'         (9  chars, OK)
      enp0s20f0u3, 10  → 'enp0s20f0u3.10'  (14 chars, OK)
      enp0s20f0u3, 2130→ 'enp0s20.2130'    (12 chars, OK)
    """
    suffix = f".{vlan_id}"
    candidate = base_iface + suffix
    if len(candidate) <= IFNAMSIZ:
        return candidate
    # Acortar la base progresivamente
    max_base = IFNAMSIZ - len(suffix)
    if max_base >= 3:
        return base_iface[:max_base] + suffix
    # Último recurso: 'vl<vlan_id>' (máx 6 chars para vlan 4094)
    return f"vl{vlan_id}"


def get_active_vlan_iface(base_iface: str):
    """
    Devuelve el nombre base de la primera subinterfaz VLAN activa cuyo padre
    sea base_iface, sin importar cómo se haya acortado el nombre.

    'ip -o link show' devuelve líneas como:
        5: enp0s20f0u.2130@enp0s20f0u3: <...>
    El campo @<padre> es la fuente fiable: si el padre coincide con base_iface,
    esa es nuestra subinterfaz. Normalizamos quitando el sufijo '@...' para
    obtener el nombre real que aceptan todos los comandos ip.
    """
    _, out, _ = run_cmd(["ip", "-o", "link", "show"])
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        raw = parts[1].rstrip(":")          # ej: "enp0s20f0u.2130@enp0s20f0u3"
        if "@" in raw:
            name, parent = raw.split("@", 1)
            if parent == base_iface:        # padre exacto → es nuestra subif
                return name
        else:
            # Sin @: subinterfaz creada sin trunk explícito (nombre canónico)
            name = raw
            if name.startswith(f"{base_iface}."):
                return name
    return None



def teardown_vlan(base_iface: str, log_cb=None) -> bool:
    active = get_active_vlan_iface(base_iface)
    if not active:
        if log_cb:
            log_cb("INFO", f"No hay subinterfaz VLAN activa en {base_iface}")
        return True

    # Normalización defensiva: eliminar sufijo '@parent' si el kernel lo devuelve
    # Ej: 'enp0s20f0u3.666@enp0s20f0u3'  →  'enp0s20f0u3.666'
    active = active.split("@")[0]

    if log_cb:
        log_cb("INFO", f"Eliminando interfaz {active}...")

    # 1. Liberar concesión DHCP (fallos ignorados; puede no haber cliente activo)
    run_cmd(["dhclient", "-r", active])
    run_cmd(["dhcpcd", "-k", active])

    # 2. Limpiar IPs y bajar la subinterfaz antes de eliminarla
    run_cmd(["ip", "addr", "flush", "dev", active])
    run_cmd(["ip", "link", "set", active, "down"])

    # 3. Eliminar — primer intento estándar; segundo con 'dev' explícito
    code, _, err = run_cmd(["ip", "link", "delete", active])
    if code != 0:
        code, _, err = run_cmd(["ip", "link", "delete", "dev", active])
    if code != 0:
        if log_cb:
            log_cb("ERROR", f"No se pudo eliminar {active}: {err}")
        return False

    if log_cb:
        log_cb("OK", f"Interfaz {active} eliminada")
    return True


def validate_cidr(ip_cidr: str) -> bool:
    """Valida que la cadena sea una IP con prefijo CIDR válido, ej: 192.168.10.5/24."""
    m = re.match(
        r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})/(\d{1,2})$",
        ip_cidr.strip()
    )
    if not m:
        return False
    octets = [int(m.group(i)) for i in range(1, 5)]
    prefix = int(m.group(5))
    return all(0 <= o <= 255 for o in octets) and 0 <= prefix <= 32


def setup_vlan(base_iface: str, vlan_id: int,
               ip_cidr: str = "", gateway: str = "",
               log_cb=None) -> bool:
    """
    Crea y configura una subinterfaz VLAN.

    Modos:
      - ip_cidr vacío            → DHCP automático
      - ip_cidr = "x.x.x.x/yy" → IP estática; gateway opcional
    """
    sub_iface = vlan_iface_name(base_iface, vlan_id)
    run_cmd(["ip", "link", "set", base_iface, "up"])
    if log_cb:
        log_cb("INFO", f"Creando {sub_iface} (VLAN {vlan_id})...")
    code, _, err = run_cmd([
        "ip", "link", "add", "link", base_iface,
        "name", sub_iface, "type", "vlan", "id", str(vlan_id)
    ])
    if code != 0:
        if log_cb:
            log_cb("ERROR", f"Error creando subinterfaz: {err}")
        return False
    run_cmd(["ip", "link", "set", sub_iface, "up"])

    if ip_cidr:
        # ── Modo IP estática ──────────────────────────────────
        if log_cb:
            log_cb("INFO", f"Asignando IP estática {ip_cidr} a {sub_iface}...")
        run_cmd(["ip", "addr", "flush", "dev", sub_iface])
        code, _, err = run_cmd(["ip", "addr", "add", ip_cidr, "dev", sub_iface])
        if code != 0:
            if log_cb:
                log_cb("ERROR", f"No se pudo asignar {ip_cidr}: {err}")
            return False
        if log_cb:
            log_cb("OK", f"IP estática {ip_cidr} asignada")
        if gateway.strip():
            if log_cb:
                log_cb("INFO", f"Añadiendo ruta por defecto vía {gateway}...")
            code_gw, _, err_gw = run_cmd([
                "ip", "route", "add", "default",
                "via", gateway.strip(), "dev", sub_iface
            ])
            if code_gw != 0:
                if log_cb:
                    log_cb("WARNING", f"No se pudo añadir la ruta: {err_gw}")
            else:
                if log_cb:
                    log_cb("OK", f"Ruta por defecto vía {gateway} añadida")
    else:
        # ── Modo DHCP ─────────────────────────────────────────
        if log_cb:
            log_cb("INFO", f"{sub_iface} levantada, solicitando DHCP...")
        code, _, err = run_cmd(["dhclient", "-v", sub_iface])
        if code != 0:
            code2, _, err2 = run_cmd(["dhcpcd", sub_iface])
            if code2 != 0:
                if log_cb:
                    log_cb("WARNING", f"DHCP puede no haberse aplicado: {err2}")
            else:
                if log_cb:
                    log_cb("OK", "DHCP asignado via dhcpcd")
        else:
            if log_cb:
                log_cb("OK", "DHCP asignado via dhclient")
    return True



# ──────────────────────────────────────────────
# DETECCIÓN LLDP
# ──────────────────────────────────────────────

def lldpd_running() -> bool:
    """Comprueba si lldpd está activo (sin depender de systemctl)."""
    rc, _, _ = run_cmd(["lldpctl", "-f", "keyvalue"])
    return rc == 0


def lldp_scan(iface: str | None = None) -> dict:
    """
    Consulta lldpctl y extrae, por cada interfaz, la lista de VLANs
    anunciadas por el vecino LLDP (switch Allied Telesis).

    Formato real de lldpd con Allied Telesis AWP (verificado):
      lldp.<iface>.vlan.vlan-id=<N>     abre una nueva VLAN
      lldp.<iface>.vlan.pvid=yes|no     indica si es la nativa
      lldp.<iface>.vlan=<NOMBRE>        nombre de la VLAN actual

    Las tres líneas se emiten siempre en ese orden y con la misma
    clave base "vlan" — se usa una máquina de estados por interfaz.
    """
    cmd = ["lldpctl", "-f", "keyvalue"]
    if iface:
        cmd.append(iface)
    rc, out, err = run_cmd(cmd)
    if rc != 0:
        return {}

    results: dict = {}
    pending: dict = {}   # ifname -> VLAN en construcción

    for line in out.splitlines():
        line = line.strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        parts = key.split(".")
        if len(parts) < 3 or parts[0] != "lldp":
            continue

        ifname = parts[1]
        if ifname not in results:
            results[ifname] = {"vlans": [], "switch": "", "port": ""}
        if ifname not in pending:
            pending[ifname] = None

        tlv = parts[2]

        # ── Chassis ──────────────────────────────────────────────
        if tlv == "chassis" and parts[-1] == "name":
            results[ifname]["switch"] = val

        # ── Puerto ───────────────────────────────────────────────
        if tlv == "port":
            sub = parts[-1]
            if sub in ("descr", "ifname") and not results[ifname]["port"]:
                results[ifname]["port"] = val

        # ── VLANs (máquina de estados) ───────────────────────────
        if tlv == "vlan":
            if len(parts) == 3:
                # lldp.<iface>.vlan=NOMBRE → nombre de la VLAN pendiente
                if pending[ifname] is not None:
                    pending[ifname]["nombre"] = val
                    results[ifname]["vlans"].append(pending[ifname])
                    pending[ifname] = None
            elif len(parts) == 4 and parts[3] == "vlan-id":
                # lldp.<iface>.vlan.vlan-id=N → nueva VLAN
                if pending[ifname] is not None:
                    # volcar la anterior si quedó sin nombre
                    results[ifname]["vlans"].append(pending[ifname])
                try:
                    pending[ifname] = {"id": int(val), "nombre": ""}
                except ValueError:
                    pending[ifname] = None

    # Volcar cualquier VLAN que quedara pendiente al final del stream
    for ifname in pending:
        if pending[ifname] is not None and ifname in results:
            results[ifname]["vlans"].append(pending[ifname])

    # Ordenar y eliminar entradas sin ID
    for ifname in results:
        results[ifname]["vlans"] = sorted(
            [v for v in results[ifname]["vlans"] if v.get("id") is not None],
            key=lambda x: x["id"]
        )

    return results

# ──────────────────────────────────────────────
# CARGA DE VLANs
# ──────────────────────────────────────────────

def load_vlans(filepath: Path) -> list:
    vlans = []
    if not filepath.exists():
        return vlans
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                vlans.append({
                    "id":          int(row.get("id", row.get("ID", 0))),
                    "nombre":      row.get("nombre", row.get("NOMBRE", "")),
                    "descripcion": row.get("descripcion", row.get("DESCRIPCION", "")),
                })
            except (ValueError, KeyError):
                continue
    return sorted(vlans, key=lambda x: x["id"])


# ──────────────────────────────────────────────
# DIÁLOGO SELECTOR DE INTERFAZ
# ──────────────────────────────────────────────

class InterfacePickerDialog(ctk.CTkToplevel):
    """
    Ventana modal que muestra todas las interfaces detectadas con sus
    métricas y permite confirmar o cambiar la selección.
    """

    def __init__(self, parent, interfaces: list, current: str):
        super().__init__(parent)
        self.title("Seleccionar interfaz trunk")
        self.geometry("640x480")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg"])
        self.grab_set()

        self.result = None
        self._interfaces = interfaces
        self._selected  = current or (interfaces[0]["name"] if interfaces else "")
        self._cards = {}

        self._build()
        self.transient(parent)
        self.after(80, self._center)

    def _center(self):
        self.update_idletasks()
        pw = self.master.winfo_x() + self.master.winfo_width() // 2
        ph = self.master.winfo_y() + self.master.winfo_height() // 2
        self.geometry(f"640x480+{pw - 320}+{ph - 240}")

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Cabecera
        hdr = ctk.CTkFrame(self, fg_color=COLORS["panel"],
                           corner_radius=0, height=64)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        hdr.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(hdr,
            text="Interfaces de red detectadas",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=COLORS["text"],
        ).grid(row=0, column=0, padx=20, pady=(14, 2), sticky="w")

        ctk.CTkLabel(hdr,
            text="★  Candidata más probable a trunk",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["muted"],
        ).grid(row=1, column=0, padx=22, pady=(0, 10), sticky="w")

        # Lista scrollable
        scroll = ctk.CTkScrollableFrame(self, fg_color=COLORS["bg"],
                                        scrollbar_button_color=COLORS["border"])
        scroll.grid(row=1, column=0, sticky="nsew", padx=12, pady=8)
        scroll.grid_columnconfigure(0, weight=1)

        best_score = max((i["score"] for i in self._interfaces), default=0)
        for idx, info in enumerate(self._interfaces):
            self._make_card(scroll, info, idx, best_score)

        # Botones
        btn_row = ctk.CTkFrame(self, fg_color=COLORS["panel"],
                               corner_radius=0, height=64)
        btn_row.grid(row=2, column=0, sticky="ew")
        btn_row.grid_propagate(False)
        btn_row.grid_columnconfigure(0, weight=1)

        ctk.CTkButton(btn_row, text="Cancelar", width=110,
            fg_color=COLORS["card"], hover_color=COLORS["border"],
            border_color=COLORS["border"], border_width=1,
            text_color=COLORS["muted"],
            command=self.destroy,
        ).grid(row=0, column=0, padx=20, pady=14, sticky="e")

        ctk.CTkButton(btn_row, text="✔  Usar esta interfaz", width=180,
            fg_color=COLORS["accent2"], hover_color=COLORS["accent"],
            text_color="#ffffff", font=ctk.CTkFont(weight="bold"),
            command=self._confirm,
        ).grid(row=0, column=1, padx=(4, 20), pady=14)

    def _make_card(self, parent, info: dict, idx: int, best_score: int):
        is_best = (idx == 0 and info["score"] == best_score)
        is_sel  = (info["name"] == self._selected)

        border = COLORS["accent"] if is_sel else (
                 COLORS["highlight"] if is_best else COLORS["border"])
        bg     = COLORS["selected"] if is_sel else COLORS["card"]

        card = ctk.CTkFrame(parent, fg_color=bg,
                            border_color=border, border_width=2,
                            corner_radius=8, cursor="hand2")
        card.grid(sticky="ew", pady=4, padx=2)
        card.grid_columnconfigure(2, weight=1)
        self._cards[info["name"]] = (card, idx, info["score"] == best_score)

        # Punto de estado (carrier)
        dot = ctk.CTkLabel(card, text="●",
            text_color=COLORS["success"] if info["carrier"] else COLORS["muted"],
            font=ctk.CTkFont(size=14), width=24)
        dot.grid(row=0, column=0, rowspan=2, padx=(12, 6), pady=12)

        # Nombre + estrella si es la mejor
        star = " ★" if is_best else ""
        name_lbl = ctk.CTkLabel(card,
            text=f"{info['name']}{star}",
            font=ctk.CTkFont(family="Courier New", size=16, weight="bold"),
            text_color=COLORS["accent"] if is_best else COLORS["text"],
            width=140, anchor="w")
        name_lbl.grid(row=0, column=1, padx=(0, 10), pady=(10, 2), sticky="w")

        # MAC
        mac_lbl = ctk.CTkLabel(card,
            text=info["mac"] or "??:??:??:??:??:??",
            font=ctk.CTkFont(family="Courier New", size=11),
            text_color=COLORS["muted"], anchor="w")
        mac_lbl.grid(row=1, column=1, padx=(0, 10), pady=(0, 10), sticky="w")

        # Chips de métricas
        chips = ctk.CTkFrame(card, fg_color="transparent")
        chips.grid(row=0, column=2, rowspan=2, padx=8, pady=8, sticky="e")

        def chip(txt, color):
            ctk.CTkLabel(chips, text=txt,
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=color, fg_color=COLORS["bg"],
                corner_radius=4, padx=6, pady=2,
            ).pack(side="left", padx=3)

        chip(speed_label(info["speed"]), COLORS["text"])
        op = info["operstate"].upper()
        chip(op, COLORS["success"] if op == "UP" else COLORS["muted"])
        if info["has_vlan_subifs"]:
            chip("VLAN activa", COLORS["warning"])

        # Click en toda la tarjeta
        for w in [card, dot, name_lbl, mac_lbl, chips]:
            w.bind("<Button-1>", lambda e, n=info["name"]: self._pick(n))

    def _pick(self, name: str):
        self._selected = name
        best_score = max((i["score"] for i in self._interfaces), default=0)
        for iname, (card, idx, is_best_iface) in self._cards.items():
            is_sel = (iname == name)
            card.configure(
                fg_color=COLORS["selected"] if is_sel else COLORS["card"],
                border_color=(COLORS["accent"]    if is_sel    else
                              COLORS["highlight"] if is_best_iface else
                              COLORS["border"]),
            )

    def _confirm(self):
        self.result = self._selected
        self.destroy()


# ──────────────────────────────────────────────
# APLICACIÓN PRINCIPAL
# ──────────────────────────────────────────────

class VLANSailor(ctk.CTk):

    def __init__(self):
        super().__init__()
        self.title("VLAN-Sailor — Arch Linux")
        self.geometry("1000x700")
        self.minsize(800, 580)
        self.configure(fg_color=COLORS["bg"])

        self.vlans         = []
        self.lldp_vlans    = []   # VLANs detectadas por LLDP (lista activa)
        self.iface_info    = []   # interfaces descubiertas
        self.selected_vlan = None
        self.iface_var     = ctk.StringVar()
        self.search_var    = ctk.StringVar()
        self.search_var.trace_add("write", self._on_search)
        self._switching    = False
        self._lldp_scanning = False

        self._build_ui()
        self._load_vlans()
        self._discover_and_set_iface()
        self._refresh_status()
        # Escaneo LLDP automático al arrancar (en hilo, no bloquea la UI)
        self.after(1500, self._lldp_auto_scan)

    # ── DESCUBRIMIENTO ───────────────────────

    def _discover_and_set_iface(self):
        """Lanza descubrimiento en hilo secundario para no bloquear la UI."""
        self._log("INFO", "Analizando interfaces de red del sistema...")
        threading.Thread(target=self._discover_worker, daemon=True).start()

    def _discover_worker(self):
        ifaces = discover_interfaces()
        self.after(0, lambda: self._apply_discovered(ifaces))

    def _apply_discovered(self, ifaces: list):
        self.iface_info = ifaces
        names = [i["name"] for i in ifaces]

        if not names:
            self._log("WARNING", "No se encontraron interfaces físicas de red")
            return

        self.iface_combo.configure(values=names)

        # Preseleccionar la de mayor puntuación
        best = ifaces[0]
        self.iface_var.set(best["name"])
        self._update_iface_badge(best)

        # Log informativo
        self._log("OK",
            f"Interfaz trunk recomendada: [{best['name']}]  "
            f"{speed_label(best['speed'])}  "
            f"{'● Conectada' if best['carrier'] else '○ Sin cable'}"
            f"{'  [ya tiene VLANs configuradas]' if best['has_vlan_subifs'] else ''}")

        if len(ifaces) > 1:
            alts = ", ".join(i["name"] for i in ifaces[1:])
            self._log("INFO", f"Otras interfaces disponibles: {alts}")

    def _update_iface_badge(self, info):
        if info is None:
            self.iface_state_lbl.configure(text="", text_color=COLORS["muted"])
            return
        if info["carrier"]:
            self.iface_state_lbl.configure(
                text=f"●  {speed_label(info['speed'])}  {info['operstate'].upper()}",
                text_color=COLORS["success"])
        else:
            self.iface_state_lbl.configure(
                text="●  Sin cable / señal",
                text_color=COLORS["error"])

    def _on_iface_changed(self, name: str):
        info = next((i for i in self.iface_info if i["name"] == name), None)
        self._update_iface_badge(info)
        self._refresh_status()
        # Relanzar escaneo LLDP para la nueva interfaz seleccionada
        if lldpd_running():
            self.after(300, self._run_lldp_scan)

    def _open_iface_picker(self):
        if not self.iface_info:
            self._log("WARNING", "Todavía detectando interfaces, espera un momento...")
            return
        dlg = InterfacePickerDialog(self, self.iface_info, self.iface_var.get())
        self.wait_window(dlg)
        if dlg.result:
            self.iface_var.set(dlg.result)
            info = next((i for i in self.iface_info if i["name"] == dlg.result), None)
            self._update_iface_badge(info)
            self._log("INFO", f"Interfaz trunk seleccionada manualmente: {dlg.result}")
            self._refresh_status()

    # ── CONSTRUCCIÓN UI ──────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=0)
        self._build_header()
        self._build_sidebar()
        self._build_main()
        self._build_log()

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color=COLORS["panel"],
                              corner_radius=0, height=72)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(header,
            text="  ⬡  VLAN-SAILOR",
            font=ctk.CTkFont(family="Courier New", size=20, weight="bold"),
            text_color=COLORS["accent"],
        ).grid(row=0, column=0, padx=20, pady=(16, 2), sticky="w")

        ctk.CTkLabel(header,
            text="Arch Linux · Trunk 802.1Q",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["muted"],
        ).grid(row=1, column=0, padx=22, pady=(0, 12), sticky="w")

        # Bloque interfaz (derecha)
        iface_block = ctk.CTkFrame(header, fg_color="transparent")
        iface_block.grid(row=0, column=2, rowspan=2, padx=16, pady=10, sticky="e")

        ctk.CTkLabel(iface_block, text="Interfaz trunk:",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=12),
        ).grid(row=0, column=0, padx=(0, 8), sticky="e")

        self.iface_combo = ctk.CTkComboBox(
            iface_block, variable=self.iface_var, width=140,
            fg_color=COLORS["card"], border_color=COLORS["border"],
            button_color=COLORS["accent2"], dropdown_fg_color=COLORS["card"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(family="Courier New", size=13),
            command=self._on_iface_changed,
        )
        self.iface_combo.grid(row=0, column=1)

        ctk.CTkButton(iface_block, text="Ver todas ▾", width=100,
            fg_color=COLORS["card"], hover_color=COLORS["border"],
            border_color=COLORS["border"], border_width=1,
            text_color=COLORS["accent"], font=ctk.CTkFont(size=12),
            command=self._open_iface_picker,
        ).grid(row=0, column=2, padx=(6, 0))

        self.iface_state_lbl = ctk.CTkLabel(iface_block,
            text="Detectando…",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["muted"],
        )
        self.iface_state_lbl.grid(row=1, column=0, columnspan=3,
                                  sticky="e", pady=(4, 0))

    def _build_sidebar(self):
        sidebar = ctk.CTkFrame(self, fg_color=COLORS["panel"],
                               corner_radius=0, width=300)
        sidebar.grid(row=1, column=0, sticky="nsew", padx=(0, 1))
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(2, weight=0)   # lista LLDP
        sidebar.grid_rowconfigure(5, weight=1)   # lista CSV (peso)
        sidebar.grid_columnconfigure(0, weight=1)

        # ── Sección LLDP ─────────────────────────────────────────
        lldp_hdr = ctk.CTkFrame(sidebar, fg_color="transparent")
        lldp_hdr.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 4))
        lldp_hdr.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(lldp_hdr,
            text="⬡  LLDP — Switch vecino",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["accent"],
        ).grid(row=0, column=0, sticky="w")

        self.lldp_scan_btn = ctk.CTkButton(lldp_hdr,
            text="⟳ Escanear", width=90, height=26,
            fg_color=COLORS["card"], hover_color=COLORS["accent2"],
            border_color=COLORS["accent"], border_width=1,
            text_color=COLORS["accent"], font=ctk.CTkFont(size=12),
            command=self._lldp_manual_scan)
        self.lldp_scan_btn.grid(row=0, column=1, padx=(6, 0))

        # Etiqueta de estado LLDP (switch + puerto o mensaje)
        self.lldp_status_lbl = ctk.CTkLabel(sidebar,
            text="Esperando escaneo…",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["muted"],
        )
        self.lldp_status_lbl.grid(row=1, column=0, padx=16, pady=(0, 4), sticky="w")

        # Lista LLDP scrollable (altura fija, se expande si hay VLANs)
        self.lldp_list_frame = ctk.CTkScrollableFrame(sidebar,
            fg_color=COLORS["bg"],
            scrollbar_button_color=COLORS["border"],
            corner_radius=6, height=120)
        self.lldp_list_frame.grid(row=2, column=0, padx=8, pady=(0, 6),
                                  sticky="ew")
        self.lldp_list_frame.grid_columnconfigure(0, weight=1)

        # Mensaje inicial dentro del frame LLDP
        self.lldp_empty_lbl = ctk.CTkLabel(self.lldp_list_frame,
            text="Sin datos LLDP aún",
            font=ctk.CTkFont(size=11), text_color=COLORS["muted"])
        self.lldp_empty_lbl.grid(row=0, column=0, pady=8)

        # ── Separador + cabecera CSV ──────────────────────────────
        ctk.CTkFrame(sidebar, fg_color=COLORS["border"],
                     height=1, corner_radius=0,
        ).grid(row=3, column=0, sticky="ew", padx=12, pady=(4, 8))

        csv_hdr = ctk.CTkFrame(sidebar, fg_color="transparent")
        csv_hdr.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 4))
        csv_hdr.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(csv_hdr,
            text="📄  VLANs del archivo CSV",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["muted"],
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkEntry(sidebar, textvariable=self.search_var,
            placeholder_text="🔍  Buscar VLAN en CSV…",
            fg_color=COLORS["card"], border_color=COLORS["border"],
            text_color=COLORS["text"], font=ctk.CTkFont(size=12),
            height=30,
        ).grid(row=5, column=0, padx=12, pady=(0, 4), sticky="ew")

        self.vlan_list_frame = ctk.CTkScrollableFrame(sidebar,
            fg_color=COLORS["bg"],
            scrollbar_button_color=COLORS["border"],
            corner_radius=8)
        self.vlan_list_frame.grid(row=6, column=0, padx=8, pady=(0, 4),
                                  sticky="nsew")
        self.vlan_list_frame.grid_columnconfigure(0, weight=1)
        sidebar.grid_rowconfigure(6, weight=1)

        self.count_lbl = ctk.CTkLabel(sidebar, text="",
            font=ctk.CTkFont(size=11), text_color=COLORS["muted"])
        self.count_lbl.grid(row=7, column=0, padx=16, pady=(0, 8), sticky="w")

    def _build_main(self):
        main = ctk.CTkFrame(self, fg_color=COLORS["bg"], corner_radius=0)
        main.grid(row=1, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)

        # ── Panel de conexión activa ──────────────────────────────
        conn_panel = ctk.CTkFrame(main, fg_color=COLORS["panel"], corner_radius=12)
        conn_panel.grid(row=0, column=0, padx=20, pady=(16, 8), sticky="ew")
        conn_panel.grid_columnconfigure((0, 1, 2), weight=1)

        # Cabecera del panel
        hdr_row = ctk.CTkFrame(conn_panel, fg_color="transparent")
        hdr_row.grid(row=0, column=0, columnspan=3, sticky="ew", padx=16, pady=(12, 8))
        hdr_row.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(hdr_row, text="Conexión activa",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["muted"],
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkButton(hdr_row, text="✕  Desconectar", width=150,
            fg_color=COLORS["card"], hover_color="#2d1b1b",
            border_color=COLORS["error"], border_width=1,
            text_color=COLORS["error"], font=ctk.CTkFont(size=13),
            command=self._on_disconnect,
        ).grid(row=0, column=1, sticky="e")

        # ── Métrica 1: VLAN ID ────────────────
        vlan_card = ctk.CTkFrame(conn_panel, fg_color=COLORS["card"], corner_radius=8)
        vlan_card.grid(row=1, column=0, padx=(12, 4), pady=(0, 12), sticky="nsew")
        vlan_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(vlan_card, text="VLAN ID",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COLORS["muted"],
        ).grid(row=0, column=0, padx=14, pady=(10, 2), sticky="w")

        self.info_vlan_id_lbl = ctk.CTkLabel(vlan_card,
            text="—",
            font=ctk.CTkFont(family="Courier New", size=28, weight="bold"),
            text_color=COLORS["accent"],
        )
        self.info_vlan_id_lbl.grid(row=1, column=0, padx=14, pady=(0, 2), sticky="w")

        self.info_vlan_name_lbl = ctk.CTkLabel(vlan_card,
            text="Sin conexión",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["muted"],
        )
        self.info_vlan_name_lbl.grid(row=2, column=0, padx=14, pady=(0, 10), sticky="w")

        # ── Métrica 2: IP y máscara ───────────
        ip_card = ctk.CTkFrame(conn_panel, fg_color=COLORS["card"], corner_radius=8)
        ip_card.grid(row=1, column=1, padx=4, pady=(0, 12), sticky="nsew")
        ip_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(ip_card, text="Dirección IP",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COLORS["muted"],
        ).grid(row=0, column=0, padx=14, pady=(10, 2), sticky="w")

        self.info_ip_lbl = ctk.CTkLabel(ip_card,
            text="—",
            font=ctk.CTkFont(family="Courier New", size=28, weight="bold"),
            text_color=COLORS["success"],
        )
        self.info_ip_lbl.grid(row=1, column=0, padx=14, pady=(0, 2), sticky="w")

        self.info_mask_lbl = ctk.CTkLabel(ip_card,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["muted"],
        )
        self.info_mask_lbl.grid(row=2, column=0, padx=14, pady=(0, 10), sticky="w")

        # ── Métrica 3: Velocidad de enlace ────
        spd_card = ctk.CTkFrame(conn_panel, fg_color=COLORS["card"], corner_radius=8)
        spd_card.grid(row=1, column=2, padx=(4, 12), pady=(0, 12), sticky="nsew")
        spd_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(spd_card, text="Velocidad de enlace",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COLORS["muted"],
        ).grid(row=0, column=0, padx=14, pady=(10, 2), sticky="w")

        self.info_speed_lbl = ctk.CTkLabel(spd_card,
            text="—",
            font=ctk.CTkFont(family="Courier New", size=28, weight="bold"),
            text_color=COLORS["highlight"],
        )
        self.info_speed_lbl.grid(row=1, column=0, padx=14, pady=(0, 2), sticky="w")

        self.info_iface_lbl = ctk.CTkLabel(spd_card,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["muted"],
        )
        self.info_iface_lbl.grid(row=2, column=0, padx=14, pady=(0, 10), sticky="w")

        # Tarjeta selección
        select_card = ctk.CTkFrame(main, fg_color=COLORS["panel"], corner_radius=12)
        select_card.grid(row=1, column=0, padx=20, pady=(0, 8), sticky="nsew")
        select_card.grid_columnconfigure(0, weight=1)
        select_card.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(select_card, text="VLAN seleccionada",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["muted"],
        ).grid(row=0, column=0, padx=16, pady=(12, 8), sticky="w")

        self.detail_frame = ctk.CTkFrame(select_card, fg_color=COLORS["card"],
                                         corner_radius=8)
        self.detail_frame.grid(row=1, column=0, padx=16, pady=(0, 16), sticky="nsew")
        self.detail_frame.grid_columnconfigure(0, weight=1)
        self.detail_frame.grid_rowconfigure(0, weight=1)

        self.placeholder_lbl = ctk.CTkLabel(self.detail_frame,
            text="← Selecciona una VLAN de la lista",
            font=ctk.CTkFont(size=14), text_color=COLORS["muted"])
        self.placeholder_lbl.grid(row=0, column=0, pady=60)

        self.detail_id_lbl = ctk.CTkLabel(self.detail_frame, text="",
            font=ctk.CTkFont(family="Courier New", size=52, weight="bold"),
            text_color=COLORS["accent"])
        self.detail_name_lbl = ctk.CTkLabel(self.detail_frame, text="",
            font=ctk.CTkFont(size=20, weight="bold"), text_color=COLORS["text"])
        self.detail_desc_lbl = ctk.CTkLabel(self.detail_frame, text="",
            font=ctk.CTkFont(size=13), text_color=COLORS["muted"])

        # ── Panel de configuración IP ─────────────────────────
        ip_panel = ctk.CTkFrame(select_card, fg_color=COLORS["card"],
                                corner_radius=8)
        ip_panel.grid(row=2, column=0, padx=16, pady=(0, 8), sticky="ew")
        ip_panel.grid_columnconfigure((1, 3), weight=1)

        # Toggle DHCP / Estática
        self.ip_mode_var = ctk.StringVar(value="dhcp")
        ctk.CTkLabel(ip_panel, text="Modo IP:",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["muted"],
        ).grid(row=0, column=0, padx=(14, 8), pady=12, sticky="w")

        self.rb_dhcp = ctk.CTkRadioButton(ip_panel,
            text="DHCP automático", variable=self.ip_mode_var, value="dhcp",
            fg_color=COLORS["accent"], hover_color=COLORS["accent2"],
            text_color=COLORS["text"], font=ctk.CTkFont(size=13),
            command=self._on_ip_mode_change)
        self.rb_dhcp.grid(row=0, column=1, padx=4, pady=12, sticky="w")

        self.rb_static = ctk.CTkRadioButton(ip_panel,
            text="IP estática", variable=self.ip_mode_var, value="static",
            fg_color=COLORS["accent"], hover_color=COLORS["accent2"],
            text_color=COLORS["text"], font=ctk.CTkFont(size=13),
            command=self._on_ip_mode_change)
        self.rb_static.grid(row=0, column=2, padx=(4, 14), pady=12, sticky="w")

        # Campos de IP estática (ocultos por defecto)
        self.static_fields = ctk.CTkFrame(ip_panel, fg_color="transparent")
        self.static_fields.grid(row=1, column=0, columnspan=3,
                                padx=14, pady=(0, 12), sticky="ew")
        self.static_fields.grid_columnconfigure((1, 3), weight=1)
        self.static_fields.grid_remove()   # oculto hasta seleccionar estática

        ctk.CTkLabel(self.static_fields, text="IP / Máscara:",
            font=ctk.CTkFont(size=12), text_color=COLORS["muted"],
        ).grid(row=0, column=0, padx=(0, 8), sticky="w")

        self.ip_entry = ctk.CTkEntry(self.static_fields,
            placeholder_text="ej: 192.168.10.5/24",
            fg_color=COLORS["bg"], border_color=COLORS["border"],
            text_color=COLORS["text"], font=ctk.CTkFont(family="Courier New", size=13),
            width=180)
        self.ip_entry.grid(row=0, column=1, sticky="ew", padx=(0, 16))

        ctk.CTkLabel(self.static_fields, text="Gateway:",
            font=ctk.CTkFont(size=12), text_color=COLORS["muted"],
        ).grid(row=0, column=2, padx=(0, 8), sticky="w")

        self.gw_entry = ctk.CTkEntry(self.static_fields,
            placeholder_text="ej: 192.168.10.1  (opcional)",
            fg_color=COLORS["bg"], border_color=COLORS["border"],
            text_color=COLORS["text"], font=ctk.CTkFont(family="Courier New", size=13),
            width=190)
        self.gw_entry.grid(row=0, column=3, sticky="ew")

        self.connect_btn = ctk.CTkButton(select_card,
            text="⚡  Conectar a esta VLAN", height=46,
            fg_color=COLORS["accent2"], hover_color=COLORS["accent"],
            text_color="#ffffff", font=ctk.CTkFont(size=15, weight="bold"),
            state="disabled", command=self._on_connect)
        self.connect_btn.grid(row=3, column=0, padx=16, pady=(0, 16), sticky="ew")

    def _build_log(self):
        log_frame = ctk.CTkFrame(self, fg_color=COLORS["panel"],
                                 corner_radius=0, height=160)
        log_frame.grid(row=2, column=0, columnspan=2, sticky="ew")
        log_frame.grid_propagate(False)
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(1, weight=1)

        hrow = ctk.CTkFrame(log_frame, fg_color="transparent")
        hrow.grid(row=0, column=0, sticky="ew", padx=16, pady=(8, 0))
        hrow.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(hrow, text="LOG DE ACTIVIDAD",
            font=ctk.CTkFont(family="Courier New", size=11, weight="bold"),
            text_color=COLORS["muted"]).grid(row=0, column=0, sticky="w")

        ctk.CTkButton(hrow, text="Limpiar", width=70, height=22,
            fg_color="transparent", hover_color=COLORS["card"],
            border_color=COLORS["border"], border_width=1,
            text_color=COLORS["muted"], font=ctk.CTkFont(size=11),
            command=self._clear_log).grid(row=0, column=2, sticky="e")

        self.log_text = ctk.CTkTextbox(log_frame,
            fg_color=COLORS["bg"], text_color=COLORS["text"],
            font=ctk.CTkFont(family="Courier New", size=12),
            corner_radius=0, border_width=0, activate_scrollbars=True)
        self.log_text.grid(row=1, column=0, sticky="nsew", padx=8, pady=(4, 8))
        self.log_text.configure(state="disabled")

    # ── LLDP ─────────────────────────────────

    def _lldp_auto_scan(self):
        """Escaneo automático al arrancar. Si lldpd no está activo, avisa y no reintenta."""
        if not lldpd_running():
            self._log("WARNING",
                "lldpd no está activo — LLDP deshabilitado. "
                "Instala e inicia lldpd para activar la detección automática.")
            self.lldp_status_lbl.configure(
                text="lldpd no disponible", text_color=COLORS["error"])
            self.lldp_scan_btn.configure(text="⟳ Reintentar")
            return
        self._log("INFO", "Escaneando vecinos LLDP al arrancar…")
        self._run_lldp_scan()

    def _lldp_manual_scan(self):
        """Botón 'Escanear' — siempre ejecuta aunque ya haya datos."""
        if self._lldp_scanning:
            return
        self._log("INFO", "Escaneo LLDP manual iniciado…")
        self._run_lldp_scan()

    def _run_lldp_scan(self):
        """Lanza el escaneo en hilo secundario para no bloquear la UI."""
        if self._lldp_scanning:
            return
        self._lldp_scanning = True
        self.lldp_scan_btn.configure(
            state="disabled", text="⏳ Escaneando…")
        iface = self.iface_var.get().strip() or None
        threading.Thread(
            target=self._lldp_worker, args=(iface,), daemon=True).start()

    def _lldp_worker(self, iface):
        result = lldp_scan(iface)
        self.after(0, lambda: self._apply_lldp(result))

    def _apply_lldp(self, result: dict):
        """Procesa el resultado del scan y actualiza la UI."""
        self._lldp_scanning = False
        self.lldp_scan_btn.configure(state="normal", text="⟳ Escanear")

        if not result:
            self._log("WARNING",
                "LLDP: sin vecinos detectados. "
                "¿Está lldpd corriendo y ha recibido al menos una trama?")
            self.lldp_status_lbl.configure(
                text="Sin vecinos detectados", text_color=COLORS["warning"])
            return

        # Consolidar todas las VLANs de todos los vecinos en la interfaz activa
        # (en un enlace trunk solo debería haber un vecino por interfaz)
        iface_sel = self.iface_var.get().strip()
        all_vlans: list = []
        switch_info = ""

        for ifname, data in result.items():
            # Filtrar por interfaz seleccionada si se especificó
            if iface_sel and ifname != iface_sel:
                continue
            sw = data.get("switch", "")
            port = data.get("port", "")
            if sw:
                switch_info = f"{sw}  ·  puerto {port}" if port else sw
            for v in data.get("vlans", []):
                if not any(x["id"] == v["id"] for x in all_vlans):
                    all_vlans.append(v)

        all_vlans.sort(key=lambda x: x["id"])
        self.lldp_vlans = all_vlans

        # Actualizar etiqueta de switch
        if switch_info:
            self.lldp_status_lbl.configure(
                text=switch_info, text_color=COLORS["success"])
        else:
            self.lldp_status_lbl.configure(
                text=f"{len(all_vlans)} VLANs detectadas",
                text_color=COLORS["success"])

        self._log("OK",
            f"LLDP: {len(all_vlans)} VLANs detectadas"
            + (f" — {switch_info}" if switch_info else ""))

        self._render_lldp_list()

    def _render_lldp_list(self):
        """Dibuja la lista de VLANs LLDP en el sidebar."""
        for w in self.lldp_list_frame.winfo_children():
            w.destroy()

        if not self.lldp_vlans:
            lbl = ctk.CTkLabel(self.lldp_list_frame,
                text="Sin VLANs LLDP",
                font=ctk.CTkFont(size=11), text_color=COLORS["muted"])
            lbl.grid(row=0, column=0, pady=8)
            return

        for v in self.lldp_vlans:
            self._make_lldp_item(v)

    def _make_lldp_item(self, vlan: dict):
        """Tarjeta de VLAN dentro del panel LLDP — clic conecta directamente."""
        row = ctk.CTkFrame(self.lldp_list_frame,
            fg_color=COLORS["card"], corner_radius=5, cursor="hand2",
            border_color=COLORS["accent"], border_width=1)
        row.grid(sticky="ew", pady=2, padx=2)
        row.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(row, text=f"{vlan['id']:>4}",
            font=ctk.CTkFont(family="Courier New", size=13, weight="bold"),
            text_color=COLORS["accent"], width=44,
        ).grid(row=0, column=0, padx=(8, 4), pady=6)

        ctk.CTkLabel(row, text=vlan["nombre"] or f"VLAN {vlan['id']}",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text"], anchor="w",
        ).grid(row=0, column=1, sticky="ew", pady=6)

        # Badge "LLDP"
        ctk.CTkLabel(row, text="LLDP",
            font=ctk.CTkFont(size=9, weight="bold"),
            text_color=COLORS["bg"], fg_color=COLORS["accent"],
            corner_radius=3, padx=4, pady=1,
        ).grid(row=0, column=2, padx=(4, 8))

        # Clic en toda la fila → selecciona como si fuera del CSV
        for w in row.winfo_children() + [row]:
            w.bind("<Button-1>", lambda e, v=vlan: self._select_vlan_lldp(v))

    def _select_vlan_lldp(self, vlan: dict):
        """
        Selecciona una VLAN detectada por LLDP.
        Intenta enriquecer con datos del CSV si existe entrada coincidente.
        """
        csv_match = next((v for v in self.vlans if v["id"] == vlan["id"]), None)
        merged = csv_match if csv_match else {
            "id":          vlan["id"],
            "nombre":      vlan["nombre"] or f"VLAN {vlan['id']}",
            "descripcion": "Detectada vía LLDP",
        }
        self._select_vlan(merged)
        self._log("INFO",
            f"VLAN {vlan['id']} seleccionada desde LLDP"
            + (f" — enriquecida con datos CSV" if csv_match else ""))

    # ── LISTA VLANs ──────────────────────────

    def _load_vlans(self):
        self.vlans = load_vlans(VLANS_FILE)
        if not self.vlans:
            self._log("WARNING", f"vlans.csv no encontrado o vacío ({VLANS_FILE})")
        else:
            self._log("OK", f"Cargadas {len(self.vlans)} VLANs desde {VLANS_FILE.name}")
        self._render_vlan_list(self.vlans)

    def _render_vlan_list(self, vlans: list):
        for w in self.vlan_list_frame.winfo_children():
            w.destroy()
        for v in vlans:
            self._make_vlan_item(v)
        self.count_lbl.configure(text=f"{len(vlans)} de {len(self.vlans)} VLANs")

    def _make_vlan_item(self, vlan: dict):
        row = ctk.CTkFrame(self.vlan_list_frame, fg_color=COLORS["card"],
                           corner_radius=6, cursor="hand2")
        row.grid(sticky="ew", pady=2, padx=2)
        row.grid_columnconfigure(1, weight=1)

        id_lbl = ctk.CTkLabel(row, text=f"{vlan['id']:>4}",
            font=ctk.CTkFont(family="Courier New", size=14, weight="bold"),
            text_color=COLORS["accent"], width=48)
        id_lbl.grid(row=0, column=0, padx=(10, 6), pady=8)

        name_lbl = ctk.CTkLabel(row, text=vlan["nombre"],
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["text"], anchor="w")
        name_lbl.grid(row=0, column=1, sticky="ew", pady=8)

        widgets = [row, id_lbl, name_lbl]
        if vlan.get("descripcion"):
            desc_lbl = ctk.CTkLabel(row, text=vlan["descripcion"],
                font=ctk.CTkFont(size=11), text_color=COLORS["muted"], anchor="w")
            desc_lbl.grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=(0, 6))
            widgets.append(desc_lbl)

        for w in widgets:
            w.bind("<Button-1>", lambda e, v=vlan: self._select_vlan(v))

    def _on_search(self, *_):
        t = self.search_var.get().lower()
        filtered = [v for v in self.vlans
                    if t in str(v["id"]) or t in v["nombre"].lower()
                    or t in v.get("descripcion", "").lower()]
        self._render_vlan_list(filtered)

    # ── SELECCIÓN / CONEXIÓN ─────────────────

    def _select_vlan(self, vlan: dict):
        self.selected_vlan = vlan
        self.placeholder_lbl.grid_remove()
        self.detail_id_lbl.configure(text=f"VLAN {vlan['id']}")
        self.detail_id_lbl.grid(row=0, column=0, pady=(30, 4))
        self.detail_name_lbl.configure(text=vlan["nombre"])
        self.detail_name_lbl.grid(row=1, column=0, pady=2)
        self.detail_desc_lbl.configure(text=vlan.get("descripcion") or "Sin descripción")
        self.detail_desc_lbl.grid(row=2, column=0, pady=(2, 30))
        self.connect_btn.configure(state="normal")

    def _on_ip_mode_change(self):
        """Muestra u oculta los campos de IP estática según el modo seleccionado."""
        if self.ip_mode_var.get() == "static":
            self.static_fields.grid()
            self.ip_entry.focus_set()
        else:
            self.static_fields.grid_remove()

    def _on_connect(self):
        if not self.selected_vlan or self._switching:
            return
        iface = self.iface_var.get().strip()
        if not iface:
            self._log("ERROR", "Selecciona una interfaz trunk primero")
            return

        ip_cidr  = ""
        gateway  = ""

        if self.ip_mode_var.get() == "static":
            ip_cidr = self.ip_entry.get().strip()
            gateway = self.gw_entry.get().strip()
            if not ip_cidr:
                self._log("ERROR", "Introduce una dirección IP con máscara (ej: 192.168.10.5/24)")
                # Resaltar el campo en error
                self.ip_entry.configure(border_color=COLORS["error"])
                self.after(2000, lambda: self.ip_entry.configure(
                    border_color=COLORS["border"]))
                return
            if not validate_cidr(ip_cidr):
                self._log("ERROR", f"Formato de IP incorrecto: '{ip_cidr}'  →  usa x.x.x.x/prefijo")
                self.ip_entry.configure(border_color=COLORS["error"])
                self.after(2000, lambda: self.ip_entry.configure(
                    border_color=COLORS["border"]))
                return

        self._switching = True
        self.connect_btn.configure(state="disabled", text="⏳  Conectando…")
        vlan = self.selected_vlan
        threading.Thread(
            target=self._do_switch,
            args=(iface, vlan, ip_cidr, gateway),
            daemon=True
        ).start()

    def _do_switch(self, iface: str, vlan: dict,
                   ip_cidr: str = "", gateway: str = ""):
        modo = f"IP estática {ip_cidr}" if ip_cidr else "DHCP"
        self._log("INFO",
            f"Iniciando cambio → VLAN {vlan['id']} ({vlan['nombre']})  [{modo}]…")
        if not teardown_vlan(iface, log_cb=self._log):
            self._log("ERROR", "No se pudo eliminar la VLAN anterior")
            self._switching = False
            self.after(0, self._reset_connect_btn)
            return
        if not setup_vlan(iface, vlan["id"],
                          ip_cidr=ip_cidr, gateway=gateway,
                          log_cb=self._log):
            self._log("ERROR", "No se pudo configurar la nueva VLAN")
            self._switching = False
            self.after(0, self._reset_connect_btn)
            return
        time.sleep(1)
        self.after(0, self._refresh_status)
        self._log("OK", f"✔  Conectado a VLAN {vlan['id']} ({vlan['nombre']}) "
                        f"en {iface}.{vlan['id']}  [{modo}]")
        self._switching = False
        self.after(0, self._reset_connect_btn)

    def _on_disconnect(self):
        if self._switching:
            return
        iface = self.iface_var.get().strip()
        if not iface:
            self._log("ERROR", "Selecciona una interfaz trunk primero")
            return
        self._switching = True
        threading.Thread(target=self._do_disconnect, args=(iface,), daemon=True).start()

    def _do_disconnect(self, iface: str):
        self._log("INFO", f"Desconectando VLAN de {iface}…")
        teardown_vlan(iface, log_cb=self._log)
        self.after(0, self._refresh_status)
        self._switching = False

    def _reset_connect_btn(self):
        self.connect_btn.configure(
            state="normal" if self.selected_vlan else "disabled",
            text="⚡  Conectar a esta VLAN")

    # ── ESTADO ───────────────────────────────

    def _refresh_status(self):
        iface = self.iface_var.get().strip()
        active = get_active_vlan_iface(iface) if iface else None

        if active:
            # ── VLAN ID y nombre ──────────────
            # Leer el VID real desde el kernel (robusto ante nombres acortados)
            vid = None
            _, det, _ = run_cmd(["ip", "-d", "link", "show", active])
            for dline in det.splitlines():
                m = re.search(r"vlan\s+(?:protocol\s+\S+\s+)?id\s+(\d+)", dline)
                if m:
                    vid = int(m.group(1))
                    break
            # Fallback: intentar extraer del nombre (p.ej. enp0s3.10)
            if vid is None:
                try:
                    vid = int(active.split(".")[-1])
                except ValueError:
                    pass
            if vid is not None:
                vlan_info = next((v for v in self.vlans if v["id"] == vid), None)
                self.info_vlan_id_lbl.configure(
                    text=str(vid), text_color=COLORS["accent"])
                self.info_vlan_name_lbl.configure(
                    text=vlan_info["nombre"] if vlan_info else active,
                    text_color=COLORS["muted"])
            else:
                self.info_vlan_id_lbl.configure(text="?", text_color=COLORS["warning"])
                self.info_vlan_name_lbl.configure(text=active, text_color=COLORS["muted"])

            # ── IP y máscara ──────────────────
            ip, prefix = get_current_ip_and_mask(active)
            if ip:
                mask = cidr_to_mask(prefix)
                self.info_ip_lbl.configure(text=ip, text_color=COLORS["success"])
                self.info_mask_lbl.configure(
                    text=f"/{prefix}  ({mask})" if mask else f"/{prefix}",
                    text_color=COLORS["muted"])
            else:
                self.info_ip_lbl.configure(text="Sin IP", text_color=COLORS["warning"])
                self.info_mask_lbl.configure(text="DHCP pendiente…", text_color=COLORS["muted"])

            # ── Velocidad de enlace ───────────
            # Leer velocidad de la subinterfaz VLAN hereda la de la física
            spd = get_link_speed_live(iface)
            self.info_speed_lbl.configure(
                text=speed_label(spd),
                text_color=COLORS["highlight"] if spd > 0 else COLORS["muted"])
            self.info_iface_lbl.configure(text=active, text_color=COLORS["muted"])

        else:
            # Sin VLAN activa → reset de los tres paneles
            self.info_vlan_id_lbl.configure(text="—", text_color=COLORS["muted"])
            self.info_vlan_name_lbl.configure(text="Sin conexión", text_color=COLORS["muted"])
            self.info_ip_lbl.configure(text="—", text_color=COLORS["muted"])
            self.info_mask_lbl.configure(text="", text_color=COLORS["muted"])
            spd = get_link_speed_live(iface) if iface else 0
            self.info_speed_lbl.configure(
                text=speed_label(spd) if spd > 0 else "—",
                text_color=COLORS["highlight"] if spd > 0 else COLORS["muted"])
            self.info_iface_lbl.configure(
                text=iface if iface else "", text_color=COLORS["muted"])

        self.after(5000, self._refresh_status)

    # ── LOG ──────────────────────────────────

    def _log(self, level: str, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        def _write():
            self.log_text.configure(state="normal")
            self.log_text.insert("end", f"[{ts}]  [{level:<7}]  {msg}\n")
            lines = int(self.log_text.index("end-1c").split(".")[0])
            if lines > LOG_MAX_LINES:
                self.log_text.delete("1.0", f"{lines - LOG_MAX_LINES}.0")
            self.log_text.configure(state="disabled")
            self.log_text.see("end")
        self.after(0, _write)

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")


# ──────────────────────────────────────────────
# ENTRYPOINT
# ──────────────────────────────────────────────

def check_root():
    if os.geteuid() != 0:
        # Mostrar error gráfico si es posible, sin depender de la terminal
        try:
            import tkinter as tk
            from tkinter import messagebox
            r = tk.Tk(); r.withdraw()
            messagebox.showerror(
                "VLAN-Sailor — Sin permisos",
                "Esta aplicación requiere privilegios de root.\n\n"
                "Ejecútala con:\n  sudo python VLAN-Sailor.py\n\n"
                "O usa el lanzador:  bash lanzar.sh"
            )
            r.destroy()
        except Exception:
            print("⚠  Esta aplicación requiere privilegios de root.")
            print("   Ejecútala con: sudo python VLAN-Sailor.py")
        sys.exit(1)


def detach_from_terminal():
    """
    Desvincula el proceso del terminal que lo lanzó para que no quede
    una ventana de consola visible mientras la GUI está abierta.
    Solo actúa si hay un terminal controlador (TTY) adjunto.
    """
    import io
    # Si ya no hay TTY (lanzado desde lanzar.sh con pkexec/nohup), no hacer nada
    if not os.isatty(sys.stdin.fileno() if hasattr(sys.stdin, 'fileno') else -1):
        return
    try:
        # Crear nueva sesión → se desvincula del terminal controlador
        os.setsid()
    except (OSError, AttributeError):
        pass
    # Redirigir stdin/stdout/stderr a /dev/null para silenciar la consola
    try:
        devnull = open(os.devnull, 'r+b')
        os.dup2(devnull.fileno(), sys.stdin.fileno())
        os.dup2(devnull.fileno(), sys.stdout.fileno())
        os.dup2(devnull.fileno(), sys.stderr.fileno())
        devnull.close()
    except (AttributeError, OSError):
        # Fallback: reemplazar los objetos de texto de Python
        null = open(os.devnull, 'w')
        sys.stdout = null
        sys.stderr = null


if __name__ == "__main__":
    check_root()
    detach_from_terminal()
    app = VLANSailor()
    app.mainloop()
