"""
Módulo para gestionar permisos del sistema.
Solicita y verifica permisos necesarios para el control total de la computadora.
"""
import logging
import os
import platform
import subprocess
from typing import Dict, List

logger = logging.getLogger("kan_core.permissions")


class SystemPermissionsManager:
    """Gestiona permisos del sistema necesarios para el agente."""

    def __init__(self):
        self.system = platform.system()
        self.required_permissions: List[str] = []
        self.granted_permissions: List[str] = []

    def check_accessibility_permission(self) -> bool:
        """Verifica permisos de accesibilidad (macOS)."""
        if self.system != "Darwin":
            return True  # No aplica en otros sistemas
        
        try:
            # Intentar ejecutar un comando que requiere accesibilidad
            result = subprocess.run(
                ["osascript", "-e", "tell application \"System Events\" to get name"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def check_screen_recording_permission(self) -> bool:
        """Verifica permisos de grabación de pantalla (macOS)."""
        if self.system != "Darwin":
            return True
        
        # En macOS, verificar si podemos tomar screenshots
        try:
            result = subprocess.run(
                ["screencapture", "-x", "/tmp/test_screenshot.png"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                # Limpiar archivo de prueba
                try:
                    os.remove("/tmp/test_screenshot.png")
                except Exception:
                    pass
                return True
            return False
        except Exception:
            return False

    def check_file_access_permission(self) -> bool:
        """Verifica permisos de acceso a archivos."""
        # Verificar si podemos leer archivos comunes
        test_paths = [
            os.path.expanduser("~/.bashrc"),
            os.path.expanduser("~/.zshrc"),
            os.path.expanduser("~/.profile"),
        ]
        
        for path in test_paths:
            if os.path.exists(path):
                try:
                    with open(path, "r") as f:
                        f.read(1)
                    return True
                except PermissionError:
                    return False
        
        return True  # Si no hay archivos de prueba, asumir que tenemos permisos

    def request_macos_permissions(self) -> Dict[str, bool]:
        """Solicita permisos en macOS."""
        if self.system != "Darwin":
            return {}
        
        results = {}
        
        # Accesibilidad
        if not self.check_accessibility_permission():
            logger.warning(
                "⚠️  Se necesitan permisos de Accesibilidad.\n"
                "   Ve a: Preferencias del Sistema > Seguridad y Privacidad > Accesibilidad\n"
                "   Y agrega Terminal o Python a la lista."
            )
            results["accessibility"] = False
        else:
            results["accessibility"] = True
            logger.info("✓ Permisos de Accesibilidad concedidos")
        
        # Grabación de pantalla
        if not self.check_screen_recording_permission():
            logger.warning(
                "⚠️  Se necesitan permisos de Grabación de Pantalla.\n"
                "   Ve a: Preferencias del Sistema > Seguridad y Privacidad > Grabación de Pantalla\n"
                "   Y agrega Terminal o Python a la lista."
            )
            results["screen_recording"] = False
        else:
            results["screen_recording"] = True
            logger.info("✓ Permisos de Grabación de Pantalla concedidos")
        
        return results

    def check_all_permissions(self) -> Dict[str, bool]:
        """Verifica todos los permisos necesarios."""
        results = {}
        
        if self.system == "Darwin":
            results.update(self.request_macos_permissions())
        elif self.system == "Linux":
            # Verificar si estamos en X11 o Wayland
            display = os.getenv("DISPLAY")
            if not display:
                logger.warning("⚠️  DISPLAY no configurado. El control de escritorio puede no funcionar.")
                results["display"] = False
            else:
                results["display"] = True
                logger.info("✓ DISPLAY configurado")
        
        # Verificar acceso a archivos
        results["file_access"] = self.check_file_access_permission()
        if results["file_access"]:
            logger.info("✓ Permisos de acceso a archivos concedidos")
        else:
            logger.warning("⚠️  Problemas con permisos de acceso a archivos")
        
        return results

    def print_permission_instructions(self) -> None:
        """Imprime instrucciones para conceder permisos."""
        if self.system == "Darwin":
            print("""
📋 INSTRUCCIONES PARA PERMISOS EN macOS:

1. Accesibilidad:
   - Abre Preferencias del Sistema
   - Ve a Seguridad y Privacidad > Accesibilidad
   - Haz clic en el candado para desbloquear
   - Agrega Terminal o Python a la lista

2. Grabación de Pantalla:
   - Abre Preferencias del Sistema
   - Ve a Seguridad y Privacidad > Grabación de Pantalla
   - Haz clic en el candado para desbloquear
   - Agrega Terminal o Python a la lista

3. Después de agregar los permisos, reinicia el agente.
            """)
        elif self.system == "Linux":
            print("""
📋 INSTRUCCIONES PARA PERMISOS EN LINUX:

1. Asegúrate de que DISPLAY esté configurado:
   export DISPLAY=:0

2. Si usas Wayland, puede que necesites:
   - Instalar xdotool o similar
   - Configurar permisos de X11

3. Para control de escritorio, puede que necesites:
   sudo apt-get install xdotool wmctrl  # Debian/Ubuntu
   sudo yum install xdotool wmctrl     # RHEL/CentOS
            """)
        elif self.system == "Windows":
            print("""
📋 INSTRUCCIONES PARA PERMISOS EN WINDOWS:

En Windows generalmente no se necesitan permisos especiales.
Si encuentras problemas, ejecuta como Administrador.
            """)


def check_and_request_permissions() -> Dict[str, bool]:
    """Función de conveniencia para verificar permisos."""
    manager = SystemPermissionsManager()
    results = manager.check_all_permissions()
    
    # Si faltan permisos, mostrar instrucciones
    if not all(results.values()):
        manager.print_permission_instructions()
    
    return results
