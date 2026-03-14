"""
Compatibilidad: quien use 'Claw' sigue teniendo la misma clase.
La lógica real está en brain.autonomous_agent (Tu Autonomía).
"""
from brain.autonomous_agent import AutonomousAgent

logger = __import__("logging").getLogger("kan_core.autonomy")

# Mantener el nombre antiguo para no romper código que importe AutonomousClawAgent
AutonomousClawAgent = AutonomousAgent
