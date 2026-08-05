"""Sistema di incastri automatici fra i pezzi stampabili."""

from .connectors import (  # noqa: F401
    build_pin,
    conical_pin,
    cylindrical_pin,
    magnet_socket,
    socket_for,
    square_pin,
)
from .planner import ContactInterface, JoineryPlanner, JoineryResult  # noqa: F401
from .tolerance import (  # noqa: F401
    FIT_CLASSES,
    ConnectorDimensions,
    effective_tolerance,
    fit_class_for,
    size_connector,
)
